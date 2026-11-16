import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "ya", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


# --- Gemini -----------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if GEMINI_API_KEY.startswith("isi_"):
    GEMINI_API_KEY = ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
# Model cadangan (dipisah koma) bila model utama kena batas kuota / tidak tersedia.
_fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash-lite").split(",") if m.strip()]
GEMINI_MODELS = [GEMINI_MODEL] + [m for m in _fallbacks if m != GEMINI_MODEL]
# Klasifikasi intent + deteksi manipulasi dengan LLM (1 panggilan tambahan per pesan).
# Matikan (false) bila kuota gratis sering habis; aturan berbasis pola tetap berjalan.
LLM_CLASSIFIER = _bool("LLM_CLASSIFIER", True)

# --- Aplikasi ---------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "dev-only-change-this-secret")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./vteki.db")
# Nama organisasi CADANGAN untuk sapaan & prompt. Nama utama diambil otomatis dari dokumen
# knowledge yang aktif (baris "**Organisasi:** ..." atau judul "... Bank X").
ORG_NAME = os.getenv("ORG_NAME", "BCA").strip() or "BCA"
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Asisten AI V-TEKI").strip() or "Asisten AI V-TEKI"

# --- Knowledge base ---------------------------------------------------------
_kdir = os.getenv("KNOWLEDGE_DIR", "../knowledge").strip()
KNOWLEDGE_DIR = (BACKEND_DIR / _kdir).resolve() if not Path(_kdir).is_absolute() else Path(_kdir)
# Saat server start, file baru/berubah di folder knowledge/ otomatis diimpor.
KNOWLEDGE_AUTO_SYNC = _bool("KNOWLEDGE_AUTO_SYNC", True)
KB_TOP_K = _int("KB_TOP_K", 4)
# Skor BM25 minimum agar sebuah bagian dianggap relevan. Naikkan bila AI terlalu
# mudah memakai artikel yang kurang cocok; turunkan bila terlalu sering "tidak tahu".
KB_MIN_SCORE = _float("KB_MIN_SCORE", 3.0)
# Bagian lain ikut dipakai hanya bila skornya >= rasio ini x skor tertinggi.
KB_RELATIVE_CUTOFF = _float("KB_RELATIVE_CUTOFF", 0.45)
# KB_MIN_SCORE berlaku penuh bila knowledge aktif >= sekian bagian; untuk knowledge yang
# lebih kecil ambang diturunkan proporsional (skor BM25 secara alami lebih kecil).
KB_SCORE_REF_DOCS = _int("KB_SCORE_REF_DOCS", 20)
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 5)
CHUNK_MAX_CHARS = _int("CHUNK_MAX_CHARS", 1800)

# --- Guardrail --------------------------------------------------------------
# Setelah sejumlah peringatan ini, AI dinonaktifkan untuk customer tsb dan semua
# pesannya langsung diteruskan ke admin sampai admin memulihkan akses.
GUARDRAIL_MAX_STRIKES = _int("GUARDRAIL_MAX_STRIKES", 3)
# Ambang skor deteksi pola (lihat guardrails.py). 3 = satu pola kuat.
GUARDRAIL_SCORE_THRESHOLD = _int("GUARDRAIL_SCORE_THRESHOLD", 3)
# Keyakinan minimal LLM untuk menandai manipulasi yang tidak tertangkap pola.
GUARDRAIL_LLM_CONFIDENCE = _float("GUARDRAIL_LLM_CONFIDENCE", 0.8)

# --- Tingkat autentikasi customer (AL0-AL3), lihat identity.py ---------------
AL2_TTL_MIN = _int("AL2_TTL_MIN", 15)          # masa berlaku verifikasi OTP (AL2)
AL3_TTL_MIN = _int("AL3_TTL_MIN", 5)           # masa berlaku verifikasi PIN (AL3)
OTP_TTL_SEC = _int("OTP_TTL_SEC", 180)         # masa berlaku satu kode OTP
OTP_MAX_ATTEMPTS = _int("OTP_MAX_ATTEMPTS", 3)  # salah sekian kali -> kode hangus
OTP_MAX_REQUESTS = _int("OTP_MAX_REQUESTS", 3)  # maks. permintaan OTP per 10 menit
PIN_MAX_ATTEMPTS = _int("PIN_MAX_ATTEMPTS", 3)  # salah sekian kali -> PIN dikunci
PIN_LOCK_MIN = _int("PIN_LOCK_MIN", 15)
# Prototipe tanpa SMS gateway: kode OTP ditampilkan di layar. WAJIB false di produksi.
OTP_DEMO_MODE = _bool("OTP_DEMO_MODE", True)
