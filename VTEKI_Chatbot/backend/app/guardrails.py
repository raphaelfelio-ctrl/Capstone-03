"""Guardrail chat customer.

1. Input guard  : normalisasi teks (zero-width, leetspeak, huruf dipisah spasi),
                  deteksi pola prompt injection / jailbreak / klaim hak akses,
                  dan masking data rahasia (PIN, OTP, CVV, nomor kartu).
2. Output guard : jawaban AI diperiksa sebelum dikirim -- tidak boleh membocorkan
                  instruksi sistem, meminta data rahasia, mengaku sudah menjalankan
                  tindakan, mengaku petugas resmi, atau menyebut nomor/URL yang
                  tidak ada di knowledge base.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .config import GUARDRAIL_SCORE_THRESHOLD

ZERO_WIDTH = re.compile(r"[​-‏‪-‮⁠-⁤﻿­]")
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = ZERO_WIDTH.sub("", t)
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip().lower()


def _variants(text: str) -> list[str]:
    base = normalize(text)
    leet = base.translate(LEET)
    # "a b a i k a n  i n s t r u k s i" -> "abaikan instruksi"
    despaced = re.sub(r"\b(?:\w ){3,}\w\b", lambda m: m.group(0).replace(" ", ""), leet)
    # "a.b.a.i.k.a.n" / "a-b-a-i-k-a-n"
    despaced = re.sub(r"\b(?:\w[.\-_*]){3,}\w\b", lambda m: re.sub(r"[.\-_*]", "", m.group(0)), despaced)
    return list(dict.fromkeys([base, leet, despaced]))


# (kategori, bobot, pola). Skor >= GUARDRAIL_SCORE_THRESHOLD (default 3) => manipulasi.
_VERB_IGNORE_ID = r"(abaikan|abaikanlah|lupakan|lupakanlah|acuhkan|hiraukan|tinggalkan|lewati|langgar|nonaktifkan|matikan|hapus|buang|batalkan|timpa|ganti)"
_TARGET_ID = r"(instruksi|perintah|aturan|arahan|prompt|batasan|pedoman|kebijakan|sistem|guardrail|filter|program|setelan|konfigurasi|role|peran)"
PATTERNS: list[tuple[str, int, re.Pattern]] = [
    ("abaikan_instruksi", 3, re.compile(_VERB_IGNORE_ID + r"\b.{0,40}\b" + _TARGET_ID)),
    ("ignore_instructions", 3, re.compile(r"\b(ignore|disregard|forget|bypass|override|skip|drop)\b.{0,40}\b(instruction|prompt|rule|guideline|polic|filter|guardrail|system|restriction)")),
    ("instruksi_baru", 3, re.compile(r"\b(instruksi|perintah|aturan|tugas|tujuan) (baru|barumu|terbaru) (kamu|anda|mu|untukmu)?|\bnew (instructions?|rules?|task|persona)\b|\bupdated instructions?\b")),
    ("bocorkan_prompt", 3, re.compile(r"\b(tampilkan|tunjukkan|tunjukan|berikan|bocorkan|sebutkan|cetak|print|show|reveal|repeat|ulangi|tulis ulang|salin|copy|output|apa isi|beritahu)\b.{0,40}\b(system ?prompt|prompt (sistem|awal|rahasia|asli)|instruksi (sistem|awal|rahasia|asli|tersembunyi)|system (message|instruction)|hidden (prompt|instruction)|initial prompt|aturan (internal|rahasia)|konfigurasi (kamu|anda|mu))")),
    ("sebut_prompt", 2, re.compile(r"\bsystem ?prompt\b|\bprompt sistem\b|\bdeveloper (message|mode)\b|\bmode (developer|pengembang|dewa|debug|admin)\b")),
    ("ulangi_teks_atas", 3, re.compile(r"\b(ulangi|repeat|tulis|salin|terjemahkan)\b.{0,30}\b(teks|kata|kalimat|pesan|words|text)\b.{0,15}\b(di atas|sebelumnya|above|before|pertama)\b")),
    ("jailbreak", 3, re.compile(r"\bjail ?break|\bdan mode\b|\bdo anything now\b|\bgod ?mode\b|\bunrestricted\b|\buncensored\b|\btanpa (batasan|filter|sensor|aturan)\b|\bno (restrictions|limits|rules|filter)\b|\bstay in character\b|\bkeluar dari (karakter|peran)\b|\bbreak character\b")),
    ("ganti_peran", 2, re.compile(r"\b(mulai (sekarang|saat ini)|from now on|sekarang (kamu|anda|lo|lu)|(kamu|anda|lo|lu) (sekarang|kini|mulai sekarang)|you are now|anggap(lah)? (kamu|dirimu|anda|lo)|bayangkan (kamu|anda|dirimu)|pretend (to be|you)|act as|berperan(lah)? (sebagai|menjadi)|bertindak(lah)? (sebagai|seolah)|jadilah\b|roleplay|role-play|pura-?pura(lah)? (jadi|menjadi|sebagai))")),
    ("ganti_peran_otoritas", 3, re.compile(r"\b(pura-?pura(lah)?|berperan(lah)?|bertindak(lah)?|anggap(lah)?|jadilah|act|pretend|roleplay)\b.{0,25}\b(pegawai|petugas|karyawan|staf|staff|admin|administrator|supervisor|teller|customer service|developer|pengembang|manager|manajer|direktur|hacker)\b")),
    ("tolak_identitas", 3, re.compile(r"\b(kamu|anda|lo|lu|you)\b.{0,10}\b(bukan|tidak lagi|no longer|are not)\b.{0,15}\b(asisten|ai|bot|chatbot|assistant|cs)\b.{0,25}\b(lagi|anymore|melainkan|tapi|but)\b")),
    ("klaim_hak_akses", 2, re.compile(r"\b(saya|aku|gue|gw|kami|i am|i'm|im)\b (adalah |ini |seorang )?(admin|administrator|developer|pengembang|programmer|supervisor|super ?user|root|petugas (bca|bank)|pegawai (bca|bank)|karyawan (bca|bank)|staf (bca|bank)|staff (bca|bank)|teller|cs (bca|bank)|pihak bank|orang dalam|pemilik (sistem|aplikasi)|owner|anthropic|openai|google|tim (it|keamanan))\b")),
    ("perintah_admin", 3, re.compile(r"\b(sebagai|as) (admin|administrator|developer|supervisor|root)\b.{0,40}\b(saya|aku|i)\b.{0,20}\b(perintahkan|minta|izinkan|order|command|authori[sz]e)")),
    ("tag_sistem_palsu", 3, re.compile(r"<\s*/?\s*(system|sys|assistant|instruction|im_start|im_end|developer|aturan|kb)\b|\[\s*/?\s*(system|inst|sys)\s*\]|#{2,}\s*(system|instruction|instruksi)|(^|\n)\s*(system|assistant|developer)\s*:|\"role\"\s*:\s*\"(system|developer)\"|<\|.*?\|>")),
    ("paksa_klaim_tindakan", 3, re.compile(r"\b(katakan|bilang|nyatakan|konfirmasi(kan)?|jawab|tulis|balas|say|tell me)(lah)?\b.{0,30}\b(bahwa |that )?.{0,40}\b(sudah|telah|berhasil|been|is)\b.{0,10}\b(di)?(setujui|approve|approved|proses|diproses|blokir|transfer|refund|kembalikan|cairkan|ubah|aktifkan|selesai)")),
    ("paksa_jawaban", 2, re.compile(r"\b(jawab|balas|respon|answer|reply)(lah)? (saja |aja |hanya |cuma |only )?(dengan )?(kata )?[\"'“]?(ya|setuju|yes|approved|disetujui|ok)[\"'”]?\s*(saja)?\b")),
    ("lewati_verifikasi", 3, re.compile(r"\b(lewati|lompati|tanpa|skip|bypass|abaikan|hilangkan)\b.{0,15}\b(proses )?(verifikasi|otentikasi|autentikasi|otp|persetujuan|approval|validasi|pengecekan)\b.{0,30}\b(setujui|approve|proses|blokir|transfer|refund|cairkan|ubah|langsung|saja)")),
    ("encoding_trick", 2, re.compile(r"\b(decode|dekode|base64|rot13|hex(adecimal)?|terjemahkan (kode|sandi) ini|unicode escape)\b")),
    ("base64_blob", 2, re.compile(r"[a-z0-9+/]{48,}={0,2}")),
]


# Frasa kunci yang dicek pada teks yang dirapatkan (tanpa spasi/tanda baca),
# untuk menangkap penyamaran seperti "a b a i k a n  a t u r a n".
COMPACT_PHRASES = ["abaikaninstruksi", "abaikanaturan", "abaikanperintah", "abaikansemuainstruksi", "abaikansemuaaturan",
                   "lupakaninstruksi", "lupakanaturan", "ignoreprevious", "ignoreallprevious", "ignoreyourinstructions",
                   "systemprompt", "promptsistem", "jailbreak", "developermode", "donothingnow", "doanythingnow",
                   "instruksisistem", "bypassverifikasi", "lewativerifikasi"]


@dataclass
class GuardResult:
    flagged: bool
    score: int
    reasons: list[str] = field(default_factory=list)


def detect_manipulation(text: str) -> GuardResult:
    hits: dict[str, int] = {}
    for variant in _variants(text):
        for name, weight, pattern in PATTERNS:
            if name not in hits and pattern.search(variant):
                hits[name] = weight
    compact = re.sub(r"[^a-z]", "", normalize(text).translate(LEET))
    if not hits and any(p in compact for p in COMPACT_PHRASES):
        hits["frasa_terselubung"] = 3
    score = sum(hits.values())
    return GuardResult(flagged=score >= GUARDRAIL_SCORE_THRESHOLD, score=score, reasons=list(hits))


REASON_LABELS = {
    "abaikan_instruksi": "Meminta AI mengabaikan instruksi/aturan",
    "ignore_instructions": "Meminta AI mengabaikan instruksi (bahasa Inggris)",
    "instruksi_baru": "Menyisipkan instruksi baru untuk AI",
    "bocorkan_prompt": "Meminta isi instruksi/prompt sistem",
    "sebut_prompt": "Menyinggung prompt/mode developer",
    "ulangi_teks_atas": "Meminta AI mengulang teks tersembunyi",
    "jailbreak": "Pola jailbreak",
    "ganti_peran": "Mencoba mengubah peran AI",
    "klaim_hak_akses": "Mengaku admin/petugas/pengembang",
    "perintah_admin": "Memberi perintah dengan mengatasnamakan admin",
    "tag_sistem_palsu": "Menyisipkan tag/pesan sistem palsu",
    "paksa_klaim_tindakan": "Memaksa AI menyatakan tindakan sudah dilakukan",
    "paksa_jawaban": "Memaksa format/isi jawaban",
    "lewati_verifikasi": "Meminta melewati verifikasi/persetujuan",
    "encoding_trick": "Trik encoding/decoding",
    "ganti_peran_otoritas": "Meminta AI berperan sebagai petugas/admin",
    "tolak_identitas": "Meminta AI meninggalkan identitas asisten",
    "frasa_terselubung": "Frasa manipulasi yang disamarkan",
    "base64_blob": "Teks ter-encode mencurigakan",
    "llm_classifier": "Terdeteksi oleh klasifikasi AI",
}


def describe(reasons: list[str]) -> list[str]:
    return [REASON_LABELS.get(r, r) for r in reasons]


# ---------------------------------------------------------------------------
# Data sensitif
# ---------------------------------------------------------------------------
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SECRET_RE = re.compile(
    r"\b(otp|pin|cvv|cvc|kode (verifikasi|otp|keamanan|rahasia)|password|kata ?sandi|sandi|passcode|respons? keybca|keybca|token)\b"
    r"(\s*(saya|aku|ku|nya|ini|adalah|yaitu|=|:|-)\s*){0,3}\s*([A-Za-z0-9!@#$%^&*._-]{3,32})", re.I)


def _luhn(num: str) -> bool:
    total, alt = 0, False
    for d in reversed(num):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def mask_sensitive(text: str) -> tuple[str, list[str]]:
    found: list[str] = []

    def card(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append("nomor_kartu")
            return f"[NOMOR KARTU DISAMARKAN ****{digits[-4:]}]"
        return m.group(0)

    text = _CARD_RE.sub(card, text)

    def secret(m):
        value = m.group(5)
        if value.lower() in {"saya", "aku", "anda", "kamu", "nya", "ini", "itu", "apa", "yang", "untuk", "dari", "atau", "dan",
                             "aman", "boleh", "bca", "kepada", "ke", "jangan", "tidak", "lupa", "salah", "berapa", "baru", "terus",
                             "diminta", "minta", "dikirim", "masuk", "sms", "tidak", "gagal", "expired", "kadaluarsa", "ga", "gak", "nggak"}:
            return m.group(0)
        if not re.search(r"\d", value) and len(value) < 6:
            return m.group(0)
        found.append(m.group(1).lower())
        return m.group(0)[: m.start(5) - m.start(0)] + "[DISAMARKAN]"

    text = _SECRET_RE.sub(secret, text)
    return text, sorted(set(found))


# ---------------------------------------------------------------------------
# Output guard
# ---------------------------------------------------------------------------
_LEAK_RE = re.compile(r"aturan inti|atur(an)? wajib|system_instruction|<kb>|</kb>|<pesan_customer>|panduan dokumen|format keluaran|json schema", re.I)
_ASK_SECRET_RE = re.compile(r"(?<!jangan )(?<!tidak )(?<!jangan pernah )\b(kirim|berikan|sebutkan|masukkan|ketik|bagikan|informasikan|tuliskan)(kan)?\b(?:(?!jangan).){0,30}\b(pin|otp|password|kata sandi|cvv|cvc|nomor kartu (lengkap|anda)|kode (otp|verifikasi))\b", re.I)
_CLAIM_ACTION_RE = re.compile(r"\b(sudah|telah|berhasil)\s+(saya |kami |kita )?(di)?(blokir|blokirkan|proses|proseskan|transfer|kembalikan|refund|setujui|ubah|aktifkan|nonaktifkan|cairkan|batalkan)\b", re.I)
_CLAIM_OFFICIAL_RE = re.compile(r"\bsaya (adalah |merupakan )?(petugas|pegawai|karyawan|customer service|cs|agen|perwakilan) (resmi )?(dari )?(bank\b|bca\b)", re.I)
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+|\b(?:www\.)?[a-z0-9-]+\.(?:co\.id|com|id|net|org)(?:/[^\s)>\]\"']*)?", re.I)
_PHONE_RE = re.compile(r"(?<![\w/])(?:\+?62|0)?[\d][\d\s.-]{3,}\d(?![\w/])")


_NEGATION = re.compile(r"\b(jangan|tidak|tak|bukan|hindari|dilarang|jangankan|never|don't|do not)\b", re.I)
_CONDITIONAL = re.compile(r"\b(jika|apabila|bila|kalau|setelah|sesudah|belum|tidak|bukan|seandainya|ketika|agar|untuk memastikan)\b", re.I)


def _negated(text: str, pos: int, pattern: re.Pattern = _NEGATION) -> bool:
    """True bila kata kerja didahului negasi/kondisi dalam kalimat yang sama (maks 40 karakter)."""
    start = max(text.rfind(".", 0, pos) + 1, text.rfind("\n", 0, pos) + 1, pos - 40, 0)
    return bool(pattern.search(text[start:pos]))


def check_output(answer: str, context_text: str) -> list[str]:
    """Kembalikan daftar pelanggaran (kosong = aman)."""
    problems = []
    if _LEAK_RE.search(answer):
        problems.append("bocor_instruksi")
    if any(not _negated(answer, m.start()) for m in _ASK_SECRET_RE.finditer(answer)):
        problems.append("meminta_data_rahasia")
    if any(not _negated(answer, m.start(), _CONDITIONAL) for m in _CLAIM_ACTION_RE.finditer(answer)):
        problems.append("klaim_tindakan")
    if _CLAIM_OFFICIAL_RE.search(answer):
        problems.append("klaim_petugas_resmi")
    ctx = context_text.lower()
    ctx_digits = re.sub(r"\D", "", context_text)
    for m in _URL_RE.finditer(answer):
        url = m.group(0).rstrip(".,;:").lower()
        bare = re.sub(r"^https?://", "", url).removeprefix("www.").rstrip("/")
        if bare and bare not in ctx:
            problems.append(f"url_tidak_terverifikasi:{bare[:80]}")
    for m in _PHONE_RE.finditer(answer):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 5 and digits not in ctx_digits:
            # angka umum (tahun, nomor tiket) tidak dianggap nomor kontak
            if re.fullmatch(r"(19|20)\d{2}", digits) or len(digits) < 5:
                continue
            problems.append(f"nomor_tidak_terverifikasi:{digits}")
    return problems
