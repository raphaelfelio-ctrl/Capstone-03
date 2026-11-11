"""Tes cepat koneksi Gemini. Jalankan dari folder backend:  python test_gemini.py"""
from google import genai
from app.config import GEMINI_API_KEY, GEMINI_MODELS

if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("isi_"):
    raise SystemExit("GEMINI_API_KEY belum diisi di backend/.env")

client = genai.Client(api_key=GEMINI_API_KEY)
for model in GEMINI_MODELS:
    try:
        r = client.models.generate_content(model=model, contents="Balas satu kata saja: siap")
        print(f"[OK]    {model}: {r.text.strip()}")
    except Exception as e:
        print(f"[GAGAL] {model}: {type(e).__name__}: {str(e)[:200]}")
