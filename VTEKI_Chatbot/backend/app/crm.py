"""CRM: tiket & state machine, closure checklist, mesin SLA jam kerja (Asia/Jakarta),
paket handoff 17 field, analitik supervisor, dan sampling QA (spesifikasi §3.2, §3.4, UC-03/05/07)."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from . import taxonomy
from .config import (SLA_FIRST_RESPONSE_MIN, SLA_HOLIDAYS, SLA_TARGET_MIN, SLA_WORK_END, SLA_WORK_START, SLA_WORKDAYS)
from .identity import now, parse_ts, ts

WIB = timezone(timedelta(hours=7))  # Asia/Jakarta (tanpa DST)

# ---------------------------------------------------------------------------
# Kalender kerja & SLA
# ---------------------------------------------------------------------------
def _is_workday(d) -> bool:
    return d.weekday() in SLA_WORKDAYS and d.isoformat() not in SLA_HOLIDAYS


def _windows(start_utc: datetime, end_utc: datetime | None = None, days: int = 400):
    """Rentang jam kerja (dalam UTC) mulai dari hari `start_utc` (WIB)."""
    day = start_utc.astimezone(WIB).date()
    for _ in range(days):
        if _is_workday(day):
            a = datetime(day.year, day.month, day.day, SLA_WORK_START, tzinfo=WIB).astimezone(timezone.utc)
            b = datetime(day.year, day.month, day.day, SLA_WORK_END, tzinfo=WIB).astimezone(timezone.utc)
            if end_utc and a >= end_utc:
                return
            yield a, b
        day += timedelta(days=1)


def add_business_minutes(start: datetime, minutes: int) -> datetime:
    remaining = minutes
    for a, b in _windows(start):
        a = max(a, start)
        if a >= b:
            continue
        span = (b - a).total_seconds() / 60
        if span >= remaining:
            return a + timedelta(minutes=remaining)
        remaining -= span
    return start + timedelta(minutes=minutes)  # pengaman (tidak pernah tercapai dalam praktik)


def business_minutes_between(start: datetime, end: datetime) -> int:
    if end <= start:
        return -business_minutes_between(end, start) if end < start else 0
    total = 0.0
    for a, b in _windows(start, end):
        a, b = max(a, start), min(b, end)
        if b > a:
            total += (b - a).total_seconds() / 60
    return int(round(total))


# ---------------------------------------------------------------------------
# State machine tiket
# ---------------------------------------------------------------------------
OPEN = ("Baru", "Diproses AI", "Menunggu Agen", "Ditangani Agen", "Menunggu Nasabah", "Menunggu Persetujuan", "Dibuka Kembali")
TRANSITIONS = {
    "Baru": {"Diproses AI", "Menunggu Agen", "Menunggu Persetujuan", "Ditolak"},
    "Diproses AI": {"Menunggu Agen", "Menunggu Nasabah", "Menunggu Persetujuan", "Selesai"},
    "Menunggu Agen": {"Ditangani Agen", "Menunggu Persetujuan", "Ditolak"},
    "Ditangani Agen": {"Menunggu Nasabah", "Menunggu Agen", "Menunggu Persetujuan", "Selesai", "Ditolak"},
    "Menunggu Nasabah": {"Ditangani Agen", "Menunggu Agen", "Selesai"},
    "Menunggu Persetujuan": {"Ditangani Agen", "Selesai", "Ditolak"},
    "Selesai": {"Dibuka Kembali"},
    "Ditolak": {"Dibuka Kembali"},
    "Dibuka Kembali": {"Ditangani Agen", "Menunggu Agen"},
}
PAUSED = ("Menunggu Nasabah",)   # SLA berhenti saat menunggu nasabah
CLOSED = ("Selesai", "Ditolak")
CHECKLIST = [
    ("identitas", "Identitas nasabah terverifikasi sesuai kebutuhan intent (dicek otomatis dari level AL kasus)"),
    ("bukti", "Bukti nasabah sudah ditinjau (wajib bila ada berkas bukti)"),
    ("solusi", "Solusi/hasil penanganan sudah dikomunikasikan ke nasabah"),
    ("akar_masalah", "Akar masalah sudah dikategorikan"),
    ("dokumentasi", "Catatan penutupan sudah ditulis"),
]
ROOT_CAUSES = ["Kesalahan/kurang paham nasabah", "Gangguan sistem/aplikasi", "Proses/SOP internal", "Pihak ketiga/merchant",
               "Penipuan eksternal", "Informasi produk kurang jelas", "Permintaan nasabah (bukan masalah)", "Lainnya"]


class CRMError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _event(c, ticket_id, src, dst, actor, note=""):
    c.execute("INSERT INTO ticket_events(ticket_id,from_status,to_status,actor,note,created_at) VALUES(?,?,?,?,?,?)",
              (ticket_id, src, dst, actor, note[:500], ts(now())))


def _next_id(c) -> str:
    n = c.execute("SELECT MAX(CAST(SUBSTR(id,5) AS INTEGER)) FROM tickets").fetchone()[0] or 5000
    return f"TCK-{n + 1}"


def open_ticket_for_case(c, case_id: str):
    return c.execute(f"SELECT * FROM tickets WHERE case_id=? AND status IN ({','.join('?' * len(OPEN))}) ORDER BY created_at DESC LIMIT 1",
                     (case_id, *OPEN)).fetchone()


PRIO_RANK = {"Rendah": 0, "Normal": 1, "Tinggi": 2, "Kritis": 3}


def create_or_update_ticket(c, *, case: dict, intent_code: str, confidence: float, sentiment: str, urgency: str,
                            status: str, actor: str = "AI Agent", note: str = "") -> dict:
    """Satu tiket terbuka per kasus. Bila sudah ada: prioritas/urgensi dinaikkan, status digeser bila valid."""
    priority = taxonomy.priority_of(urgency)
    existing = open_ticket_for_case(c, case["id"])
    t = now()
    if existing:
        ex = dict(existing)
        if PRIO_RANK[priority] > PRIO_RANK.get(ex["priority"], 1):
            due = add_business_minutes(parse_ts(ex["created_at"]), SLA_TARGET_MIN[priority] + (ex["paused_total_min"] or 0))
            c.execute("UPDATE tickets SET priority=?, sla_target_min=?, sla_due_at=?, urgency=? WHERE id=?",
                      (priority, SLA_TARGET_MIN[priority], ts(due), urgency, ex["id"]))
        if ex["intent_code"] in ("info_produk", "minta_agen") and intent_code not in ("info_produk", "minta_agen"):
            c.execute("UPDATE tickets SET intent_code=?, category=? WHERE id=?", (intent_code, taxonomy.label(intent_code), ex["id"]))
        if status != ex["status"] and status in TRANSITIONS.get(ex["status"], ()):
            transition(c, ex["id"], status, actor, note)
        c.execute("UPDATE tickets SET sentiment=?, updated_at=? WHERE id=?",
                  (taxonomy.worse(sentiment, ex["sentiment"] or "netral", taxonomy.SENT_RANK), ts(t), ex["id"]))
        return dict(c.execute("SELECT * FROM tickets WHERE id=?", (ex["id"],)).fetchone())
    tid = _next_id(c)
    target = SLA_TARGET_MIN[priority]
    c.execute("""INSERT INTO tickets(id,case_id,user_id,intent_code,category,agent,priority,status,sentiment,urgency,confidence,
                 channel,sla_target_min,sla_due_at,first_response_due_at,paused_total_min,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
              (tid, case["id"], case["user_id"], intent_code, taxonomy.label(intent_code), taxonomy.agent_of(intent_code),
               priority, "Baru", sentiment, urgency, round(confidence, 2), case.get("channel") or "web", target,
               ts(add_business_minutes(t, target)), ts(add_business_minutes(t, SLA_FIRST_RESPONSE_MIN)), ts(t), ts(t)))
    _event(c, tid, "", "Baru", actor, f"Tiket dibuat otomatis: {taxonomy.label(intent_code)} (keyakinan {confidence:.2f})")
    if status != "Baru":
        transition(c, tid, status, actor, note)
    return dict(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())


def transition(c, ticket_id: str, to: str, actor: str, note: str = "") -> dict:
    tk = c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not tk:
        raise CRMError("Tiket tidak ditemukan.", 404)
    tk = dict(tk)
    if to == tk["status"]:
        return tk
    if to not in TRANSITIONS.get(tk["status"], ()):
        raise CRMError(f"Transisi status tidak valid: {tk['status']} → {to}. "
                       f"Yang diizinkan: {', '.join(sorted(TRANSITIONS.get(tk['status'], ()))) or '-'}.", 409)
    t = now()
    fields = {"status": to, "updated_at": ts(t)}
    if tk["status"] in PAUSED and to not in PAUSED and tk["paused_since"]:
        paused = business_minutes_between(parse_ts(tk["paused_since"]), t)
        total = (tk["paused_total_min"] or 0) + max(paused, 0)
        fields.update(paused_since=None, paused_total_min=total,
                      sla_due_at=ts(add_business_minutes(parse_ts(tk["created_at"]), tk["sla_target_min"] + total)))
    if to in PAUSED:
        fields["paused_since"] = ts(t)
    if to == "Ditangani Agen" and not tk["first_response_at"]:
        fields["first_response_at"] = ts(t)
    if to in CLOSED:
        fields["resolved_at"] = ts(t)
    if to == "Dibuka Kembali":
        fields.update(resolved_at=None, reopened_count=(tk["reopened_count"] or 0) + 1)
    c.execute(f"UPDATE tickets SET {','.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), ticket_id))
    _event(c, ticket_id, tk["status"], to, actor, note)
    return dict(c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())


def close_ticket(c, ticket_id: str, *, actor: str, checklist: dict, root_cause: str, note: str) -> dict:
    tk = c.execute("SELECT t.*, c.verified_level FROM tickets t JOIN cases c ON c.id=t.case_id WHERE t.id=?", (ticket_id,)).fetchone()
    if not tk:
        raise CRMError("Tiket tidak ditemukan.", 404)
    problems = []
    need = taxonomy.required_level(tk["intent_code"])
    if need >= 2 and int(tk["verified_level"] or 0) < need:
        problems.append(f"identitas nasabah baru AL{tk['verified_level']}, intent ini butuh AL{need}")
    has_evidence = c.execute("SELECT COUNT(*) FROM evidence WHERE case_id=? AND status='Bersih'", (tk["case_id"],)).fetchone()[0]
    for key, text in CHECKLIST:
        if key == "bukti" and not has_evidence:
            continue
        if key == "identitas" and need < 2:
            continue
        if not checklist.get(key):
            problems.append(f"checklist belum dicentang: {text}")
    if root_cause not in ROOT_CAUSES:
        problems.append("akar masalah wajib dipilih dari daftar")
    if len((note or "").strip()) < 10:
        problems.append("catatan penutupan minimal 10 karakter")
    if problems:
        raise CRMError("Tiket belum bisa ditutup: " + "; ".join(problems) + ".", 422)
    if tk["status"] not in ("Ditangani Agen", "Menunggu Nasabah", "Menunggu Persetujuan", "Diproses AI"):
        if "Ditangani Agen" in TRANSITIONS.get(tk["status"], ()):
            transition(c, ticket_id, "Ditangani Agen", actor, "Diambil alih saat penutupan")
    closure = {k: bool(checklist.get(k)) for k, _ in CHECKLIST} | {"bukti_ada": bool(has_evidence), "identitas_level": tk["verified_level"]}
    c.execute("UPDATE tickets SET root_cause=?, closure=?, resolution_note=? WHERE id=?",
              (root_cause, json.dumps(closure), note.strip()[:2000], ticket_id))
    return transition(c, ticket_id, "Selesai", actor, f"Ditutup: {root_cause}")


def sla_view(tk: dict) -> dict:
    t = now()
    due = parse_ts(tk.get("sla_due_at"))
    target = tk.get("sla_target_min") or 0
    if tk.get("status") in CLOSED:
        resolved = parse_ts(tk.get("resolved_at")) or t
        state = "terpenuhi" if due and resolved <= due else "terlewati"
        return {"state": state, "due_at": tk.get("sla_due_at"), "remaining_min": None}
    if tk.get("status") in PAUSED:
        return {"state": "dijeda", "due_at": tk.get("sla_due_at"), "remaining_min": None}
    remaining = business_minutes_between(t, due) if due else None
    state = "terlewati" if due and t > due else "berisiko" if remaining is not None and remaining < target * 0.25 else "aman"
    return {"state": state, "due_at": tk.get("sla_due_at"), "remaining_min": remaining}


def ticket_dict(row) -> dict:
    d = dict(row)
    d["closure"] = json.loads(d.get("closure") or "{}")
    d["sla"] = sla_view(d)
    d["allowed_next"] = sorted(TRANSITIONS.get(d["status"], ()))
    return d


# ---------------------------------------------------------------------------
# Paket handoff 17 field (spesifikasi §3.4)
# ---------------------------------------------------------------------------
HANDOFF_FIELDS = ["handoff_id", "case_id", "ticket_id", "channel", "customer", "auth_level", "intent", "intent_confidence",
                  "sentiment", "urgency", "priority_sla", "ai_actions", "handoff_reason", "summary", "transcript",
                  "evidence", "recommendation"]
REASON_LABEL = {
    "keyakinan_rendah": "Keyakinan intent di bawah 0.45", "klarifikasi_habis": "Klarifikasi 2x belum memperjelas maksud",
    "otp_gagal_3x": "OTP gagal 3x berturut-turut (sesi dikunci 15 menit)", "minta_agen": "Nasabah meminta agen manusia",
    "tidak_ada_knowledge": "Tidak ada knowledge yang relevan", "knowledge_tidak_cukup": "Knowledge tidak cukup untuk menjawab",
    "jawaban_diblok_guardrail": "Jawaban AI diblok output guard", "upaya_manipulasi_ai": "Upaya manipulasi AI",
    "ai_tidak_tersedia": "LLM tidak tersedia", "eskalasi_intent": "Intent wajib ditangani agen (keluhan/fraud/sengketa)",
    "perlu_persetujuan": "Tindakan berisiko perlu persetujuan supervisor", "perlu_agen": "Perlu tindak lanjut agen",
}
RECOMMEND = {
    "penipuan": "Prioritaskan: pastikan akses/kartu nasabah sudah diamankan, minta kronologi & bukti, eskalasi ke tim fraud sesuai SOP.",
    "kartu_hilang": "Cek status blokir kartu di sistem, konfirmasi ke nasabah, tawarkan penerbitan kartu pengganti.",
    "transaksi_gagal": "Cek status transaksi di sistem, minta bukti transfer, sampaikan estimasi pengembalian dana bila gagal.",
    "sanggahan_transaksi": "Minta bukti tagihan/struk, buka proses sanggahan/chargeback sesuai SOP, informasikan estimasi waktu.",
    "keluhan_layanan": "Tunjukkan empati, gali akar masalah, tindak lanjuti dan informasikan hasilnya.",
    "kartu_tertelan_rusak": "Arahkan pengambilan/penggantian kartu di cabang; pastikan kartu lama diblokir bila perlu.",
    "permintaan_tindakan": "Pastikan nasabah AL3, tinjau di menu Persetujuan, lalu setujui atau tolak.",
    "limit_kartu": "Pastikan nasabah AL3, tinjau kelayakan perubahan limit di menu Persetujuan.",
    "otp_gagal_3x": "Waspadai pengambilalihan akun: hubungi nasabah lewat kontak resmi terdaftar sebelum membantu lebih lanjut.",
    "keyakinan_rendah": "Maksud nasabah belum jelas: tanyakan kebutuhannya secara terbuka.",
    "klarifikasi_habis": "Maksud nasabah belum jelas setelah klarifikasi: tanyakan kebutuhannya secara terbuka.",
    "tidak_ada_knowledge": "Jawab berdasarkan SOP internal dan pertimbangkan menambah dokumen knowledge.",
    "upaya_manipulasi_ai": "Tinjau kejadian di menu Guardrail sebelum melanjutkan percakapan.",
}


def _mask_email(email: str) -> str:
    if "@" not in (email or ""):
        return email or ""
    name, dom = email.split("@", 1)
    return (name[:2] + "***@" + dom) if not dom.endswith("guest.local") else "(tamu)"


def build_handoff(c, *, case_id: str, ticket: dict | None, reason: str, auth_level: int, intent_code: str,
                  confidence: float, sentiment: str, urgency: str, ai_actions: list[str]) -> dict:
    case = dict(c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone())
    user = c.execute("SELECT name,email,is_guest,phone FROM users WHERE id=?", (case["user_id"],)).fetchone()
    msgs = c.execute("SELECT sender,content,meta,created_at FROM messages WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
    transcript = []
    for m in msgs[-40:]:
        meta = json.loads(m["meta"] or "{}")
        who = {"customer": "Nasabah", "assistant": "AI", "agent": "Agen", "system": "Sistem"}.get(m["sender"], m["sender"])
        text = m["content"] + (" [ditandai manipulasi]" if meta.get("flagged") else "")
        transcript.append({"at": m["created_at"], "from": who, "text": text[:1000]})
    customer_msgs = [t["text"] for t in transcript if t["from"] == "Nasabah"]
    evidence = [dict(r) for r in c.execute("SELECT id,filename,status,scanner,created_at FROM evidence WHERE case_id=? ORDER BY id", (case_id,))]
    summary = (f"Nasabah menghubungi lewat {case.get('channel') or 'web'} dengan maksud '{taxonomy.label(intent_code)}' "
               f"(keyakinan {confidence:.2f}, sentimen {sentiment}, urgensi {urgency}). "
               f"Pesan pertama: \"{(customer_msgs[0] if customer_msgs else '-')[:200]}\". "
               + (f"Pesan terakhir: \"{customer_msgs[-1][:200]}\". " if len(customer_msgs) > 1 else "")
               + f"Alasan dialihkan: {REASON_LABEL.get(reason, reason)}.")
    package = {
        "handoff_id": None, "case_id": case_id, "ticket_id": ticket["id"] if ticket else None,
        "channel": case.get("channel") or "web",
        "customer": {"name": user["name"] if user else "-", "contact": _mask_email(user["email"]) if user else "-",
                     "guest": bool(user and user["is_guest"])},
        "auth_level": f"AL{auth_level}", "intent": {"code": intent_code, "label": taxonomy.label(intent_code),
                                                    "agent": taxonomy.AGENT_LABEL.get(taxonomy.agent_of(intent_code), "")},
        "intent_confidence": round(confidence, 2), "sentiment": sentiment, "urgency": urgency,
        "priority_sla": {"priority": ticket["priority"] if ticket else taxonomy.priority_of(urgency),
                         "sla_due_at": ticket["sla_due_at"] if ticket else None,
                         "first_response_due_at": ticket["first_response_due_at"] if ticket else None},
        "ai_actions": ai_actions, "handoff_reason": {"code": reason, "label": REASON_LABEL.get(reason, reason)},
        "summary": summary, "transcript": transcript, "evidence": evidence,
        "recommendation": RECOMMEND.get(reason) or RECOMMEND.get(intent_code) or "Tinjau percakapan dan tindak lanjuti sesuai SOP.",
    }
    cur = c.execute("INSERT INTO handoffs(case_id,ticket_id,reason,package,created_at) VALUES(?,?,?,?,?)",
                    (case_id, package["ticket_id"], reason, "{}", ts(now())))
    package["handoff_id"] = f"HO-{cur.lastrowid}"
    assert list(package) == HANDOFF_FIELDS and len(package) == 17
    c.execute("UPDATE handoffs SET package=? WHERE id=?", (json.dumps(package, ensure_ascii=False), cur.lastrowid))
    return package


# ---------------------------------------------------------------------------
# Analitik supervisor & QA
# ---------------------------------------------------------------------------
QA_CRITERIA = [("akurasi", "Jawaban akurat & sesuai knowledge"), ("kepatuhan", "Patuh SOP & keamanan data"),
               ("empati", "Bahasa sopan & empatik"), ("penyelesaian", "Masalah tuntas / diarahkan tepat"),
               ("verifikasi", "Verifikasi identitas sesuai kebutuhan")]


def analytics(c) -> dict:
    tickets = [ticket_dict(r) for r in c.execute("SELECT * FROM tickets")]
    closed = [t for t in tickets if t["status"] in CLOSED]
    open_ = [t for t in tickets if t["status"] not in CLOSED]
    met = sum(1 for t in closed if t["sla"]["state"] == "terpenuhi")
    frt = [business_minutes_between(parse_ts(t["created_at"]), parse_ts(t["first_response_at"]))
           for t in tickets if t.get("first_response_at")]
    count = lambda key, rows: sorted(((k, sum(1 for r in rows if (r.get(key) or "-") == k)) for k in {(r.get(key) or "-") for r in rows}),
                                     key=lambda x: -x[1])
    cases = [dict(r) for r in c.execute("SELECT status, channel FROM cases")]
    handoffs = [dict(r) for r in c.execute("SELECT reason FROM handoffs")]
    qa = [dict(r) for r in c.execute("SELECT total FROM qa_reviews")]
    return {
        "tickets_total": len(tickets), "tickets_open": len(open_),
        "sla_compliance_pct": round(100 * met / len(closed), 1) if closed else None,
        "sla_breached_open": [t["id"] for t in open_ if t["sla"]["state"] == "terlewati"],
        "sla_at_risk_open": [t["id"] for t in open_ if t["sla"]["state"] == "berisiko"],
        "avg_first_response_business_min": round(sum(frt) / len(frt), 1) if frt else None,
        "by_intent": count("category", tickets), "by_priority": count("priority", tickets),
        "by_channel": count("channel", cases), "root_causes": count("root_cause", closed),
        "handoff_reasons": [(REASON_LABEL.get(k, k), n) for k, n in count("reason", handoffs)],
        "ai_auto_resolved_pct": round(100 * sum(1 for x in cases if x["status"] == "Selesai otomatis") / len(cases), 1) if cases else None,
        "qa_reviews": len(qa), "qa_avg_score": round(sum(q["total"] for q in qa) / len(qa), 2) if qa else None,
    }


def qa_sample(c, n: int = 5, seed: int | None = None) -> list[dict]:
    """Sampel acak kasus yang sudah selesai & belum dinilai, seimbang antara ditangani AI dan manusia."""
    rows = [dict(r) for r in c.execute("""SELECT c.id, c.subject, c.intent, c.status, c.channel, c.updated_at,
                 EXISTS(SELECT 1 FROM messages m WHERE m.case_id=c.id AND m.sender='agent') AS human
                 FROM cases c WHERE c.status IN ('Selesai','Selesai otomatis','Ditolak')
                 AND NOT EXISTS(SELECT 1 FROM qa_reviews q WHERE q.case_id=c.id)""")]
    rnd = random.Random(seed)
    ai = [r for r in rows if not r["human"]]
    hu = [r for r in rows if r["human"]]
    rnd.shuffle(ai)
    rnd.shuffle(hu)
    out = []
    while len(out) < n and (ai or hu):
        for pool in (hu, ai):
            if pool and len(out) < n:
                out.append(pool.pop())
    return out


def qa_submit(c, case_id: str, reviewer: str, scores: dict, notes: str) -> dict:
    vals = {}
    for key, _ in QA_CRITERIA:
        v = scores.get(key)
        if not isinstance(v, (int, float)) or not 1 <= v <= 5:
            raise CRMError(f"Skor '{key}' wajib 1-5.", 422)
        vals[key] = int(v)
    total = round(sum(vals.values()) / len(vals), 2)
    c.execute("INSERT INTO qa_reviews(case_id,reviewer,scores,total,notes,created_at) VALUES(?,?,?,?,?,?)",
              (case_id, reviewer, json.dumps(vals), total, (notes or "")[:2000], ts(now())))
    return {"case_id": case_id, "scores": vals, "total": total}
