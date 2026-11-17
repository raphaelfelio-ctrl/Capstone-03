"""Taksonomi 14 intent, skor keyakinan, sentimen/urgensi, dan ekstraksi parameter (spesifikasi §3.2 & §1-5 agent).

Setiap intent punya: label, sub-agent penanganannya, tingkat autentikasi minimum, apakah membuat tiket CRM,
dan "keluarga" rute (coarse) yang dipakai graph LangGraph.

Ambang keyakinan (spesifikasi §3.2):
  >= 0.75       -> langsung diproses sub-agent (dan tiket CRM dibuat bila intent memerlukan)
  0.45 - 0.74   -> loop klarifikasi terarah, maksimal 2 giliran per kasus
  <  0.45       -> handoff ke agen manusia
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import CONFIDENCE_CLARIFY, CONFIDENCE_HIGH

# kode: (label, sub-agent, AL minimum untuk data/aksi, buat tiket?, keluarga rute)
INTENTS: dict[str, tuple[str, str, int, bool, str]] = {
    "sapaan":              ("Sapaan & Penutup", "router", 0, False, "greeting"),
    "info_produk":         ("Informasi Produk & Layanan", "faq", 0, False, "inquiry"),
    "cek_saldo":           ("Cek Saldo", "account", 2, False, "account_inquiry"),
    "mutasi_transaksi":    ("Mutasi & Riwayat Transaksi", "account", 2, False, "account_inquiry"),
    "transaksi_gagal":     ("Transfer/Pembayaran Gagal atau Pending", "account", 2, True, "complaint"),
    "kartu_hilang":        ("Kartu Hilang/Dicuri & Blokir Darurat", "card", 2, True, "fraud_report"),
    "kartu_tertelan_rusak": ("Kartu Tertelan/Rusak", "card", 0, True, "complaint"),
    "aktivasi_kartu":      ("Aktivasi & Pengajuan Kartu", "card", 0, False, "inquiry"),
    "limit_kartu":         ("Pengaturan Limit Kartu", "card", 3, True, "account_action"),
    "permintaan_tindakan": ("Ubah Data/Refund/Buka Blokir (perlu persetujuan)", "account", 3, True, "account_action"),
    "keluhan_layanan":     ("Keluhan Layanan", "dispute", 0, True, "complaint"),
    "penipuan":            ("Indikasi Penipuan/Fraud", "dispute", 0, True, "fraud_report"),
    "sanggahan_transaksi": ("Sanggahan Transaksi/Chargeback", "dispute", 0, True, "complaint"),
    "status_tiket":        ("Status Keluhan/Tiket", "dispute", 2, False, "ticket_status"),
}
# Di luar 14 taksonomi (bukan intent layanan):
EXTRA = {
    "di_luar_topik": ("Di luar topik", "router", 0, False, "out_of_scope"),
    "minta_agen": ("Minta Agen Manusia", "dispute", 0, True, "handoff"),
}
ALL = {**INTENTS, **EXTRA}
AGENT_LABEL = {"router": "Router Agent", "faq": "FAQ Agent", "account": "Account Agent", "card": "Card Agent",
               "dispute": "Dispute & Escalation Agent"}


def label(code: str) -> str:
    return ALL.get(code, ("Lainnya",))[0]


def agent_of(code: str) -> str:
    return ALL.get(code, ("", "faq"))[1]


def required_level(code: str) -> int:
    return ALL.get(code, ("", "", 0))[2]


def makes_ticket(code: str) -> bool:
    return ALL.get(code, ("", "", 0, False))[3]


def family(code: str) -> str:
    return ALL.get(code, ("", "", 0, False, "inquiry"))[4]


def band(conf: float) -> str:
    return "tinggi" if conf >= CONFIDENCE_HIGH else "klarifikasi" if conf >= CONFIDENCE_CLARIFY else "rendah"


# ---------------------------------------------------------------------------
# Klasifikasi berbasis aturan (dipakai bila LLM tidak tersedia & sebagai pengaman)
# ---------------------------------------------------------------------------
_PRON = r"(saya|aku|gue|gw|ku|kami)"
_FILLER = (r"(?:[\s,.!?~]+(?:kak|kakak|ka|min|mimin|admin|gan|sis|bro|pak|bu|mas|mbak|bang|semua|semuanya|ya|yaa|yah|dong|deh|"
           r"sih|nih|lagi|ini|banyak|banget|bgt|sekali|atas|bantuannya|infonya|informasinya|penjelasannya|jawabannya|ok|oke|okay|"
           r"baik|siap|sip|mantap|terima ?kasih|makasih|thanks|thank you|thx|selamat|pagi|siang|sore|malam|cs|halo|hai))*")
RE_GREETING = re.compile(r"^\W*(halo|hallo|hai|hi|hello|hey|selamat (pagi|siang|sore|malam)|pagi|siang|sore|malam|assalamu.?alaikum|permisi|test|tes)\b"
                         + _FILLER + r"\W*$", re.I)
RE_THANKS = re.compile(r"^\W*(terima ?kasih|makasih|trims|thanks|thank you|thx|ok(e|ay)?|baik|siap|mantap|sip)\b" + _FILLER + r"\W*$", re.I)

_ACCOUNT_KEY = r"(saldo|mutasi|isi rekening|transaksi (terakhir|terbaru)|riwayat transaksi|histori transaksi)"
RE_ACCOUNT_HINT = re.compile(rf"{_ACCOUNT_KEY}|transaksi", re.I)
RE_ACCOUNT_PERSONAL = re.compile(
    rf"\b{_ACCOUNT_KEY}\w*\s+(\w+\s+)?{_PRON}\b|\b(saldo|mutasi|rekening)ku\b"
    rf"|\b(cek|lihat|tampilkan|tunjukkan|tunjukin|info|berapa|sisa)\s+(sisa\s+)?{_ACCOUNT_KEY}"
    rf"|\btransaksi\s+(\w+\s+){{0,2}}(minggu|bulan|hari|kemarin)\b.*\b{_PRON}\b|\b\d+\s+transaksi\b", re.I)
RE_ACCOUNT_PRODUCT = re.compile(r"\bsaldo\s+(minimum|minimal|mengendap|awal|rata)|\bbiaya\b|\bsyarat\b|\bcara (buka|membuka)\b", re.I)

RULES: list[tuple[str, float, re.Pattern]] = [
    # (kode, keyakinan, pola) — urutan = prioritas; pola berisiko/aksi didahulukan
    ("minta_agen", 0.95, re.compile(r"\b(bicara|ngobrol|hubungkan|sambungkan|minta|mau|ingin)\b.{0,25}\b(cs|agen|petugas|manusia|operator|orang asli|customer service)\b(?!.*\b(nomor|kontak|telepon)\b)", re.I)),
    ("kartu_hilang", 0.93, re.compile(r"\bkartu\b.{0,30}\b(hilang|dicuri|kecurian|dicopet|jatuh)\b|\b(hilang|dicuri|kecurian)\b.{0,20}\bkartu\b|\bblokir(kan)?\b.{0,20}\bkartu\b|\bkartu\b.{0,20}\b(di)?blokir(kan)?\b(?!.{0,20}\b(buka|dibuka)\b)", re.I)),
    ("kartu_tertelan_rusak", 0.9, re.compile(r"\bkartu\b.{0,30}\b(tertelan|ketelan|tersangkut|nyangkut|rusak|patah|tidak terbaca|gak terbaca|tidak bisa dipakai)\b", re.I)),
    ("limit_kartu", 0.88, re.compile(r"\b(naik(kan)?|turun(kan)?|ubah|ganti|atur|tambah)\b.{0,15}\blimit\b|\blimit\b.{0,20}\b(dinaikkan|diturunkan|diubah)\b", re.I)),
    ("permintaan_tindakan", 0.88, re.compile(r"\b(refund|pengembalian dana|kembalikan (dana|uang)|buka blokir|ubah data|ganti (nomor|email|alamat|data)|transfer(kan)? (uang|dana)|tutup rekening|reset (pin|password)|batalkan transaksi|aktifkan (kembali )?(kartu|rekening))\b", re.I)),
    ("penipuan", 0.92, re.compile(r"\b(penipuan|ditipu|tertipu|kena tipu|transaksi (yang )?(tidak|tak|gak|nggak) (saya )?(kenal|dikenal|lakukan)|dibobol|kebobolan|saldo (saya )?(hilang|berkurang|terkuras)|akun (saya )?(diretas|dibajak|di-?hack)|(sudah|terlanjur) (kasih|memberikan|membagikan|kirim) (kode )?(otp|pin|password)|phishing|link palsu)\b", re.I)),
    ("sanggahan_transaksi", 0.86, re.compile(r"\b(sanggah|sanggahan|chargeback|dispute|klaim (dana|transaksi)|terdebet (dua|2) kali|ganda|double debit|tagihan (salah|tidak sesuai))\b", re.I)),
    ("transaksi_gagal", 0.87, re.compile(r"\b(transfer|transaksi|pembayaran|bayar|top ?up|tarik tunai)\b.{0,30}\b(gagal|pending|tertunda|belum (masuk|sampai|diterima)|tidak (masuk|sampai)|tapi saldo (terpotong|berkurang)|terpotong)\b", re.I)),
    ("status_tiket", 0.9, re.compile(r"\b(status|perkembangan|progres|kelanjutan|update)\b.{0,25}\b(keluhan|tiket|laporan|pengaduan|komplain|kasus)\b|\b(tiket|laporan|pengaduan|keluhan) (saya )?(sudah|sampai) (mana|sejauh mana)\b", re.I)),
    ("keluhan_layanan", 0.85, re.compile(r"\b(komplain|keluhan|mengeluh|kecewa|tidak puas|kesal|lambat sekali|lama sekali|pelayanan (buruk|jelek)|parah|aplikasi (error|eror|gangguan|down))\b", re.I)),
    ("aktivasi_kartu", 0.84, re.compile(r"\b(aktivasi|mengaktifkan|aktifkan|buat|bikin|ajukan|pengajuan|daftar|cetak) (kartu|atm|kartu kredit|kartu debit)\b|\bkartu (baru|pengganti)\b", re.I)),
]
RE_OUT_OF_SCOPE = re.compile(r"\b(resep|masak|puisi|pantun|lirik|tugas sekolah|pr matematika|politik|pemilu|cuaca|zodiak|ramalan|film|game|sepak ?bola)\b", re.I)


def is_account_inquiry(text: str) -> bool:
    if not RE_ACCOUNT_PERSONAL.search(text or ""):
        return False
    has_pronoun = re.search(rf"\b{_ACCOUNT_KEY}\w*\s+(\w+\s+)?{_PRON}\b|ku\b|\b{_PRON}\b", text, re.I)
    return bool(has_pronoun) or not RE_ACCOUNT_PRODUCT.search(text)


def rule_classify(text: str) -> tuple[str, float]:
    """(kode intent, keyakinan) dari pola. Tanpa kecocokan -> info_produk dengan keyakinan sedang-tinggi."""
    t = text or ""
    for code, conf, pat in RULES:
        if pat.search(t):
            return code, conf
    if is_account_inquiry(t):
        return ("mutasi_transaksi" if re.search(r"mutasi|transaksi|riwayat|histori", t, re.I) else "cek_saldo"), 0.9
    if RE_GREETING.match(t) or RE_THANKS.match(t):
        return "sapaan", 0.95
    if RE_OUT_OF_SCOPE.search(t):
        return "di_luar_topik", 0.8
    words = re.findall(r"\w+", t)
    if len(words) <= 1:
        return "info_produk", 0.5  # satu kata ("kartu") -> perlu klarifikasi
    return "info_produk", 0.8


def is_thanks(text: str) -> bool:
    return bool(RE_THANKS.match(text or "")) and not RE_GREETING.match(text or "")


# ---------------------------------------------------------------------------
# Sentimen & urgensi (Dispute & Escalation Agent)
# ---------------------------------------------------------------------------
RE_ANGRY = re.compile(r"\b(kesal|marah|kecewa|parah|bodoh|goblok|payah|lambat sekali|lama banget|tidak becus|nyebelin|bangsat|anjing|sialan|keterlaluan|mengecewakan)\b|!{2,}", re.I)
RE_PANIC = re.compile(r"\b(segera|darurat|cepat|panik|bahaya|urgent|mendesak|sekarang juga)\b", re.I)
RE_POSITIVE = re.compile(r"\b(terima ?kasih|makasih|mantap|bagus|puas|membantu|keren)\b", re.I)
SENT_RANK = {"positif": 0, "netral": 1, "negatif": 2, "marah": 3}
URG_RANK = {"rendah": 0, "sedang": 1, "tinggi": 2, "kritis": 3}


def sentiment_urgency(text: str, code: str) -> tuple[str, str]:
    t = text or ""
    caps = sum(1 for w in re.findall(r"[A-Za-z]{4,}", t) if w.isupper()) >= 3
    if RE_ANGRY.search(t) or caps:
        sentiment = "marah"
    elif code in ("keluhan_layanan", "penipuan", "transaksi_gagal", "sanggahan_transaksi", "kartu_hilang") or RE_PANIC.search(t):
        sentiment = "negatif"
    elif RE_POSITIVE.search(t):
        sentiment = "positif"
    else:
        sentiment = "netral"
    if code in ("penipuan", "kartu_hilang"):
        urgency = "kritis"
    elif sentiment == "marah" or RE_PANIC.search(t) or code in ("transaksi_gagal", "sanggahan_transaksi", "permintaan_tindakan", "limit_kartu"):
        urgency = "tinggi"
    elif code in ("keluhan_layanan", "kartu_tertelan_rusak", "minta_agen", "status_tiket"):
        urgency = "sedang"
    else:
        urgency = "rendah"
    return sentiment, urgency


def worse(a: str, b: str, rank: dict) -> str:
    return a if rank.get(a, 0) >= rank.get(b, 0) else b


def priority_of(urgency: str) -> str:
    return {"kritis": "Kritis", "tinggi": "Tinggi", "sedang": "Normal", "rendah": "Rendah"}.get(urgency, "Normal")


# ---------------------------------------------------------------------------
# Ekstraksi parameter (Account Agent): rentang tanggal, jumlah, pihak lawan, nominal
# ---------------------------------------------------------------------------
@dataclass
class TxQuery:
    limit: int = 5
    start: datetime | None = None
    end: datetime | None = None
    counterparty: str = ""
    min_amount: int = 0
    direction: str = ""          # masuk | keluar | ""
    description: list = field(default_factory=list)

    def describe(self) -> str:
        parts = []
        if self.start or self.end:
            parts.append(f"{self.start:%d %b %Y} – {(self.end - timedelta(seconds=1)):%d %b %Y}" if self.start and self.end else "")
        if self.counterparty:
            parts.append(f"terkait '{self.counterparty}'")
        if self.min_amount:
            parts.append(f"nominal ≥ Rp {self.min_amount:,}".replace(",", "."))
        if self.direction:
            parts.append(f"dana {self.direction}")
        return ", ".join(p for p in parts if p)


def _nominal(num: str, unit: str) -> int:
    n = float(num.replace(".", "").replace(",", "."))
    return int(n * {"rb": 1e3, "ribu": 1e3, "k": 1e3, "jt": 1e6, "juta": 1e6}.get((unit or "").lower(), 1))


def extract_tx_query(text: str, now: datetime) -> TxQuery:
    t = (text or "").lower()
    q = TxQuery()
    m = re.search(r"\b(\d{1,2})\s+(transaksi|mutasi)", t)
    if m:
        q.limit = max(1, min(int(m.group(1)), 20))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "hari ini" in t:
        q.start, q.end = today, today + timedelta(days=1)
    elif "kemarin" in t:
        q.start, q.end = today - timedelta(days=1), today
    elif "minggu lalu" in t:
        monday = today - timedelta(days=today.weekday())
        q.start, q.end = monday - timedelta(days=7), monday
    elif "minggu ini" in t:
        q.start, q.end = today - timedelta(days=today.weekday()), today + timedelta(days=1)
    elif "bulan lalu" in t:
        first = today.replace(day=1)
        q.start, q.end = (first - timedelta(days=1)).replace(day=1), first
    elif "bulan ini" in t:
        q.start, q.end = today.replace(day=1), today + timedelta(days=1)
    else:
        m = re.search(r"(\d{1,3})\s+hari\s+(terakhir|belakangan)", t)
        if m:
            q.start, q.end = today - timedelta(days=int(m.group(1)) - 1), today + timedelta(days=1)
    if (q.start or q.end) and not m_limit(t):
        q.limit = 20
    m = re.search(r"\b(ke|dari|kepada|untuk)\s+([a-z][a-z.]{2,20}(?:\s[a-z][a-z.]{2,20})?)", t)
    if m and m.group(2).split()[0] not in ("rekening", "saya", "aku", "kartu", "atm", "bank", "sini", "mana", "cs"):
        q.counterparty = m.group(2).split()[0]
        q.direction = "keluar" if m.group(1) in ("ke", "kepada", "untuk") else "masuk"
    m = re.search(r"(di ?atas|lebih dari|minimal|>)\s*(rp\.?\s*)?([\d.,]+)\s*(rb|ribu|k|jt|juta)?", t)
    if m:
        q.min_amount = _nominal(m.group(3), m.group(4))
    if re.search(r"\b(uang|dana) masuk|\bpemasukan\b", t):
        q.direction = "masuk"
    elif re.search(r"\b(uang|dana) keluar|\bpengeluaran\b", t):
        q.direction = "keluar"
    return q


def m_limit(t: str) -> bool:
    return bool(re.search(r"\b\d{1,2}\s+(transaksi|mutasi)", t))
