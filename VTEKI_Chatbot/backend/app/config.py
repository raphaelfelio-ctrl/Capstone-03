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
# --- Penyedia LLM (adapter, lihat llm.py) ---------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower() or "gemini"
LLM_FALLBACK_PROVIDERS = [p.strip().lower() for p in os.getenv("LLM_FALLBACK_PROVIDERS", "").split(",") if p.strip()]
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "").strip()          # wajib diisi bila memakai openai
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "").strip()              # mis. http://localhost:11434
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3").strip()
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

# --- Router: ambang keyakinan intent (spesifikasi §3.2) ----------------------
CONFIDENCE_HIGH = _float("CONFIDENCE_HIGH", 0.75)        # >= : langsung diproses + tiket
CONFIDENCE_CLARIFY = _float("CONFIDENCE_CLARIFY", 0.45)  # 0.45-0.74 : klarifikasi; < : handoff
MAX_CLARIFY_TURNS = _int("MAX_CLARIFY_TURNS", 2)

# --- AL0 / OTP (spesifikasi §3.1) --------------------------------------------
OTP_FAIL_LOCK_STREAK = _int("OTP_FAIL_LOCK_STREAK", 3)   # gagal OTP berturut-turut -> sesi dikunci + handoff
OTP_SESSION_LOCK_MIN = _int("OTP_SESSION_LOCK_MIN", 15)

# --- SLA berbasis kalender kerja (Asia/Jakarta, UTC+7, tanpa DST) -------------
SLA_WORK_START = _int("SLA_WORK_START", 8)     # jam mulai kerja (WIB)
SLA_WORK_END = _int("SLA_WORK_END", 17)        # jam selesai kerja (WIB)
SLA_WORKDAYS = [int(x) for x in os.getenv("SLA_WORKDAYS", "0,1,2,3,4").split(",") if x.strip().isdigit()]  # 0=Senin
SLA_HOLIDAYS = [x.strip() for x in os.getenv("SLA_HOLIDAYS", "2026-12-25,2027-01-01").split(",") if x.strip()]
# Target penyelesaian dalam MENIT JAM KERJA per prioritas
SLA_TARGET_MIN = {"Kritis": _int("SLA_KRITIS_MIN", 120), "Tinggi": _int("SLA_TINGGI_MIN", 240),
                  "Normal": _int("SLA_NORMAL_MIN", 960), "Rendah": _int("SLA_RENDAH_MIN", 1440)}
SLA_FIRST_RESPONSE_MIN = _int("SLA_FIRST_RESPONSE_MIN", 30)

# --- Bukti / evidence (spesifikasi §3.3) ---------------------------------------
STORAGE_DIR = (BACKEND_DIR / os.getenv("STORAGE_DIR", "./storage")).resolve() if not Path(os.getenv("STORAGE_DIR", "./storage")).is_absolute() else Path(os.getenv("STORAGE_DIR"))
EVIDENCE_MAX_MB = _int("EVIDENCE_MAX_MB", 5)
CLAMAV_HOST = os.getenv("CLAMAV_HOST", "").strip()       # bila diisi: pemindaian lewat daemon ClamAV (clamd)
CLAMAV_PORT = _int("CLAMAV_PORT", 3310)

# --- Rate limit (per sesi / IP, jendela 60 detik) -------------------------------
RATE_CHAT_PER_MIN = _int("RATE_CHAT_PER_MIN", 20)
RATE_GUEST_PER_MIN = _int("RATE_GUEST_PER_MIN", 10)
RATE_UPLOAD_PER_MIN = _int("RATE_UPLOAD_PER_MIN", 5)
