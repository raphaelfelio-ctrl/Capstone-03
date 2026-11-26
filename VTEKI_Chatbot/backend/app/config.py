import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
# Model cadangan (dipisah koma) bila model utama kena batas kuota / tidak tersedia.
_fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.8-flash").split(",") if m.strip()]
GEMINI_MODELS = [GEMINI_MODEL] + [m for m in _fallbacks if m != GEMINI_MODEL]
APP_SECRET = os.getenv("APP_SECRET", "dev-only-change-this-secret")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./vteki.db")
