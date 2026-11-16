"""Tingkat autentikasi customer (Authentication Level, AL0-AL3).

  AL0  Anonim             belum login                         -> tidak bisa memakai chat
  AL1  Login akun         email + kata sandi                  -> info umum, membuat & melihat tiket sendiri
  AL2  Terverifikasi      AL1 + OTP ke nomor HP terdaftar     -> lihat saldo & mutasi rekening sendiri
  AL3  Terverifikasi kuat AL2 + PIN transaksi                 -> mengajukan tindakan berisiko (blokir, ubah data)
                                                                 yang boleh disetujui admin

Prinsip:
- Level disimpan per SESI login (tabel sessions) dan kedaluwarsa otomatis; logout = kembali AL0.
- OTP & PIN tidak pernah lewat chat/LLM; diverifikasi lewat endpoint khusus, disimpan dalam bentuk hash.
- OTP terikat pada sesi yang memintanya, berlaku singkat, dan dibatasi jumlah percobaan/permintaan.
- Data rekening dibaca langsung dari database oleh kode (bukan oleh Gemini) dan tidak disimpan di riwayat chat.

PROTOTIPE: data rekening adalah data demo fiktif, tidak terhubung ke core banking.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from .config import (AL2_TTL_MIN, AL3_TTL_MIN, APP_SECRET, OTP_DEMO_MODE, OTP_MAX_ATTEMPTS, OTP_MAX_REQUESTS,
                     OTP_TTL_SEC, PIN_LOCK_MIN, PIN_MAX_ATTEMPTS)
from .db import connect

log = logging.getLogger("vteki.identity")

LEVELS = {
    0: {"code": "AL0", "name": "Anonim", "how": "Belum login.",
        "allows": "Tidak dapat memakai chat."},
    1: {"code": "AL1", "name": "Login akun", "how": "Login dengan email dan kata sandi.",
        "allows": "Informasi umum dari basis pengetahuan, membuat dan melihat tiket sendiri."},
    2: {"code": "AL2", "name": "Terverifikasi OTP", "how": "AL1 + kode OTP yang dikirim ke nomor HP terdaftar.",
        "allows": "Melihat saldo dan mutasi terakhir rekening milik sendiri."},
    3: {"code": "AL3", "name": "Terverifikasi kuat", "how": "AL2 + PIN transaksi.",
        "allows": "Mengajukan tindakan berisiko (blokir kartu, ubah data, refund) yang dapat disetujui admin."},
}
# Kebutuhan level per jenis permintaan di chat
REQUIRED_LEVEL = {"account_inquiry": 2, "account_action": 3}


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hash_code(code: str, salt: str) -> str:
    return hmac.new(APP_SECRET.encode(), f"{salt}:{code}".encode(), hashlib.sha256).hexdigest()


def mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone or "" if ch.isdigit())
    return (digits[:4] + "****" + digits[-4:]) if len(digits) >= 8 else "****"


def mask_account(number: str) -> str:
    return "•••• " + (number or "")[-4:]


def rupiah(amount: int) -> str:
    return ("-" if amount < 0 else "") + "Rp " + f"{abs(int(amount)):,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Level sesi
# ---------------------------------------------------------------------------
def session_level(session: dict | None) -> int:
    """Level efektif sebuah sesi saat ini (memperhitungkan masa berlaku)."""
    if not session:
        return 0
    t = now()
    al2 = parse_ts(session.get("al2_until"))
    al3 = parse_ts(session.get("al3_until"))
    if al2 and al2 > t:
        return 3 if (al3 and al3 > t) else 2
    return 1


def level_info(user: dict) -> dict:
    level = session_level(user)
    expires = user.get("al3_until") if level == 3 else user.get("al2_until") if level == 2 else None
    with connect() as c:
        u = c.execute("SELECT phone, txn_pin_hash, pin_locked_until FROM users WHERE id=?", (user["id"],)).fetchone()
        n_acc = c.execute("SELECT COUNT(*) FROM bank_accounts WHERE user_id=?", (user["id"],)).fetchone()[0]
    locked = parse_ts(u["pin_locked_until"]) if u else None
    return {
        "level": level, "code": LEVELS[level]["code"], "name": LEVELS[level]["name"],
        "expires_at": expires, "allows": LEVELS[level]["allows"],
        "phone_hint": mask_phone(u["phone"]) if u and u["phone"] else "",
        "can_verify_otp": bool(u and u["phone"]), "can_verify_pin": bool(u and u["txn_pin_hash"]),
        "pin_locked_until": ts(locked) if locked and locked > now() else None,
        "linked_accounts": n_acc, "demo_mode": OTP_DEMO_MODE,
        "levels": [{"level": k, **v} for k, v in LEVELS.items()],
    }


def _set_session(token: str, **fields):
    cols = ",".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE sessions SET {cols} WHERE token=?", (*fields.values(), token))


def revoke(user: dict):
    _set_session(user["session_token"], al2_until=None, al3_until=None)


# ---------------------------------------------------------------------------
# AL2: OTP
# ---------------------------------------------------------------------------
def request_otp(user: dict) -> dict:
    with connect() as c:
        u = c.execute("SELECT phone FROM users WHERE id=?", (user["id"],)).fetchone()
        if not u or not u["phone"]:
            raise AuthError("Nomor HP belum terdaftar pada akun ini, sehingga verifikasi OTP tidak tersedia. "
                            "Silakan hubungi kanal resmi bank untuk memperbarui data.", 409)
        since = ts(now() - timedelta(minutes=10))
        recent = c.execute("SELECT COUNT(*) FROM otp_challenges WHERE user_id=? AND created_at>=?",
                           (user["id"], since)).fetchone()[0]
        if recent >= OTP_MAX_REQUESTS:
            raise AuthError("Terlalu banyak permintaan OTP. Coba lagi dalam 10 menit.", 429)
        code = f"{secrets.randbelow(10**6):06d}"
        salt = secrets.token_hex(8)
        # OTP lama milik sesi ini tidak berlaku lagi
        c.execute("UPDATE otp_challenges SET used=1 WHERE session_token=? AND used=0", (user["session_token"],))
        cur = c.execute("""INSERT INTO otp_challenges(user_id,session_token,code_hash,salt,expires_at,created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (user["id"], user["session_token"], _hash_code(code, salt), salt,
                         ts(now() + timedelta(seconds=OTP_TTL_SEC)), ts(now())))
    hint = mask_phone(u["phone"])
    # Produksi: kirim lewat SMS/WhatsApp gateway. Prototipe: tampilkan di layar (mode demo) & log server.
    log.info("OTP demo untuk %s (%s): %s", user["email"], hint, code if OTP_DEMO_MODE else "******")
    out = {"challenge_id": cur.lastrowid, "channel": f"SMS ke {hint}", "expires_in": OTP_TTL_SEC}
    if OTP_DEMO_MODE:
        out["demo_otp"] = code
    return out


def confirm_otp(user: dict, challenge_id: int, code: str) -> dict:
    code = (code or "").strip()
    error = None  # catatan: connect() hanya commit bila tidak ada exception -> error dilempar SETELAH blok
    with connect() as c:
        ch = c.execute("SELECT * FROM otp_challenges WHERE id=? AND user_id=?", (challenge_id, user["id"])).fetchone()
        if not ch or ch["session_token"] != user["session_token"]:
            error = AuthError("Kode verifikasi tidak ditemukan untuk sesi ini. Minta kode baru.", 404)
        elif ch["used"]:
            error = AuthError("Kode ini sudah tidak berlaku. Minta kode baru.", 410)
        elif parse_ts(ch["expires_at"]) <= now():
            c.execute("UPDATE otp_challenges SET used=1 WHERE id=?", (challenge_id,))
            error = AuthError("Kode OTP sudah kedaluwarsa. Minta kode baru.", 410)
        elif not hmac.compare_digest(_hash_code(code, ch["salt"]), ch["code_hash"]):
            attempts = ch["attempts"] + 1
            c.execute("UPDATE otp_challenges SET attempts=?, used=? WHERE id=?",
                      (attempts, 1 if attempts >= OTP_MAX_ATTEMPTS else 0, challenge_id))
            left = OTP_MAX_ATTEMPTS - attempts
            error = AuthError(f"Kode OTP salah. Sisa percobaan: {left}." if left > 0 else
                              "Kode OTP salah dan sudah tidak berlaku. Minta kode baru.", 401)
        else:
            c.execute("UPDATE otp_challenges SET used=1 WHERE id=?", (challenge_id,))
            c.execute("UPDATE sessions SET al2_until=?, al3_until=NULL WHERE token=?",
                      (ts(now() + timedelta(minutes=AL2_TTL_MIN)), user["session_token"]))
    if error:
        raise error
    return {"level": 2}


# ---------------------------------------------------------------------------
# AL3: PIN transaksi (step-up dari AL2)
# ---------------------------------------------------------------------------
def verify_pin(user: dict, pin: str, case_id: str | None = None) -> dict:
    from .security import verify_password
    if session_level(user) < 2:
        raise AuthError("Selesaikan verifikasi OTP (AL2) terlebih dahulu.", 403)
    error, updated = None, []
    with connect() as c:  # error dilempar setelah blok agar penghitung percobaan tersimpan
        u = c.execute("SELECT txn_pin_hash, pin_failed, pin_locked_until FROM users WHERE id=?", (user["id"],)).fetchone()
        locked = parse_ts(u["pin_locked_until"]) if u else None
        if not u or not u["txn_pin_hash"]:
            error = AuthError("PIN transaksi belum diatur untuk akun ini.", 409)
        elif locked and locked > now():
            error = AuthError(f"Verifikasi PIN dikunci sampai {ts(locked)} UTC karena terlalu banyak percobaan salah.", 423)
        elif not verify_password((pin or "").strip(), u["txn_pin_hash"]):
            failed = (u["pin_failed"] or 0) + 1
            if failed >= PIN_MAX_ATTEMPTS:
                c.execute("UPDATE users SET pin_failed=0, pin_locked_until=? WHERE id=?",
                          (ts(now() + timedelta(minutes=PIN_LOCK_MIN)), user["id"]))
                # PIN salah berulang = sinyal risiko: cabut juga verifikasi sesi ini.
                c.execute("UPDATE sessions SET al2_until=NULL, al3_until=NULL WHERE token=?", (user["session_token"],))
                error = AuthError(f"PIN salah {PIN_MAX_ATTEMPTS} kali. Verifikasi PIN dikunci {PIN_LOCK_MIN} menit dan "
                                  "status verifikasi sesi ini dicabut.", 423)
                error.locked_now = True
            else:
                c.execute("UPDATE users SET pin_failed=? WHERE id=?", (failed, user["id"]))
                error = AuthError(f"PIN salah. Sisa percobaan: {PIN_MAX_ATTEMPTS - failed}.", 401)
        else:
            c.execute("UPDATE users SET pin_failed=0, pin_locked_until=NULL WHERE id=?", (user["id"],))
            al2 = parse_ts(user.get("al2_until"))
            al3 = min(now() + timedelta(minutes=AL3_TTL_MIN), al2) if al2 else now() + timedelta(minutes=AL3_TTL_MIN)
            c.execute("UPDATE sessions SET al3_until=? WHERE token=?", (ts(al3), user["session_token"]))
            if case_id:
                cur = c.execute("""UPDATE cases SET verified_level=MAX(verified_level,3), updated_at=CURRENT_TIMESTAMP
                                   WHERE id=? AND user_id=? AND status NOT IN ('Selesai','Ditolak')""", (case_id, user["id"]))
                if cur.rowcount:
                    updated.append(case_id)
    if error:
        raise error
    return {"level": 3, "cases_verified": updated}


# ---------------------------------------------------------------------------
# Data rekening (dibaca kode, BUKAN LLM)
# ---------------------------------------------------------------------------
def accounts(user_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute("""SELECT id, account_number, product, holder_name, balance, currency, status, updated_at
                            FROM bank_accounts WHERE user_id=? ORDER BY id""", (user_id,)).fetchall()
    return [{**dict(r), "account_masked": mask_account(r["account_number"]), "balance_text": rupiah(r["balance"])}
            for r in rows]


def transactions(user_id: int, account_id: int, limit: int = 5) -> list[dict]:
    with connect() as c:
        own = c.execute("SELECT 1 FROM bank_accounts WHERE id=? AND user_id=?", (account_id, user_id)).fetchone()
        if not own:
            raise AuthError("Rekening tidak ditemukan.", 404)
        rows = c.execute("""SELECT posted_at, description, amount FROM bank_transactions WHERE account_id=?
                            ORDER BY posted_at DESC, id DESC LIMIT ?""", (account_id, limit)).fetchall()
    return [{**dict(r), "amount_text": ("+" if r["amount"] > 0 else "") + rupiah(r["amount"])} for r in rows]


def account_reply(user_id: int, want_mutations: bool) -> tuple[str, dict]:
    accs = accounts(user_id)
    if not accs:
        return ("Identitas Anda sudah terverifikasi, tetapi belum ada rekening yang terhubung dengan akun ini. "
                "Silakan hubungi kanal resmi bank."), {"accounts": 0}
    lines = ["Berikut informasi rekening Anda (data demo, bukan rekening nyata):"]
    for a in accs:
        lines.append(f"• {a['product']} {a['account_masked']} a.n. {a['holder_name']}: saldo {a['balance_text']} "
                     f"(per {a['updated_at']} UTC)")
        if want_mutations:
            for t in transactions(user_id, a["id"]):
                lines.append(f"   – {t['posted_at'][:10]} {t['description']}: {t['amount_text']}")
    lines.append("Data ini hanya ditampilkan untuk Anda dan tidak disimpan di riwayat percakapan.")
    return "\n".join(lines), {"accounts": len(accs)}


# ---------------------------------------------------------------------------
# Seed demo
# ---------------------------------------------------------------------------
def seed_demo(c):
    from .security import hash_password
    u = c.execute("SELECT id, phone, txn_pin_hash, name FROM users WHERE email='customer@vteki.id'").fetchone()
    if not u:
        return
    if not u["phone"]:
        c.execute("UPDATE users SET phone=? WHERE id=?", ("081234567890", u["id"]))
    if not u["txn_pin_hash"]:
        c.execute("UPDATE users SET txn_pin_hash=? WHERE id=?", (hash_password("246810"), u["id"]))
    if c.execute("SELECT 1 FROM bank_accounts WHERE user_id=?", (u["id"],)).fetchone():
        return
    cur = c.execute("""INSERT INTO bank_accounts(user_id,account_number,product,holder_name,balance,updated_at)
                       VALUES(?,?,?,?,?,?)""", (u["id"], "8830127790", "Tabungan Demo", u["name"], 12_450_000, ts(now())))
    acc = cur.lastrowid
    base = now() - timedelta(days=6)
    for i, (desc, amount) in enumerate([("Setoran tunai", 5_000_000), ("Transfer ke Andi", -750_000),
                                        ("Pembayaran listrik", -325_000), ("Gaji bulanan", 8_000_000),
                                        ("Belanja QRIS", -185_000), ("Tarik tunai ATM", -500_000)]):
        c.execute("INSERT INTO bank_transactions(account_id,posted_at,description,amount) VALUES(?,?,?,?)",
                  (acc, ts(base + timedelta(days=i)), desc, amount))
