# Panduan Menjalankan V-TEKI Chatbot (Gemini API Gratis)

## 1. Persiapan (sekali saja)
1. Pasang **Python 3.11+** dari python.org (centang *Add Python to PATH*). Cek: `py --version`.
2. Buat **API key gratis**: buka https://aistudio.google.com/apikey → login Google → *Create API key*. Tidak perlu kartu kredit.

## 2. Instalasi
**Cara cepat (Windows):** klik dua kali `setup.bat`. Ia membuat `.venv`, memasang dependensi, dan menyalin `.env.example` → `backend\.env`.

**Cara manual:**
```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```
(Mac/Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env`)

## 3. Isi konfigurasi `backend/.env`
```
GEMINI_API_KEY=AIza...key_anda
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-2.5-flash-lite
APP_SECRET=string_acak_panjang
```
- Tulis tanpa tanda kutip dan tanpa spasi di sekitar `=`.
- Jangan commit `.env` atau menaruh key di `index.html`.
- Model gratis & kuotanya bisa berubah; cek di https://aistudio.google.com/rate-limit lalu sesuaikan `GEMINI_MODEL`. Jika model utama kena batas kuota (error 429) atau tidak ada (404), backend otomatis mencoba model cadangan.

## 4. Tes koneksi Gemini
```powershell
cd backend
.\.venv\Scripts\python.exe test_gemini.py
```
Harus muncul `[OK] gemini-2.5-flash: siap`. Jika `[GAGAL]`, lihat bagian Troubleshooting.

## 5. Jalankan aplikasi
**Cara cepat:** klik dua kali `start_all.bat` (membuka backend, frontend, dan browser).

**Manual (dua terminal):**
```powershell
# Terminal 1
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# Terminal 2
cd frontend
py -m http.server 5500
```
Buka **http://127.0.0.1:5500** (jangan buka `index.html` via `file://`). Dokumentasi API: http://127.0.0.1:8000/docs. Cek status: http://127.0.0.1:8000/api/health (`gemini_configured` harus `true`).

## 6. Akun demo
| Peran | Email | Sandi |
|---|---|---|
| Admin | admin@vteki.id | admin123 |
| Customer | customer@vteki.id | cust1234 |

Coba login Customer → tanya "Berapa biaya tahunan kartu Gold?" → harus dijawab Gemini berdasarkan Basis Pengetahuan. Coba "tolong blokir kartu saya" → status *Menunggu persetujuan*, lalu setujui dari akun Admin.

## 7. Troubleshooting
| Gejala | Penyebab / solusi |
|---|---|
| `GEMINI_API_KEY belum diisi` | Isi `backend/.env`, lalu restart backend. |
| `API key Gemini ditolak` | Key salah/dicabut; buat ulang di AI Studio. |
| Error 429 / kuota habis | Batas gratis (per menit/hari) tercapai; tunggu, atau gunakan model cadangan. Kuota harian reset tengah malam waktu Pasifik. |
| 404 model not found | Ganti `GEMINI_MODEL` dengan model yang tertera di AI Studio. |
| Frontend "Failed to fetch" | Backend belum jalan, atau frontend dibuka lewat `file://`; pakai port 5500 (CORS hanya mengizinkan 5500/8000). |
| `py` tidak dikenali | Python belum di PATH; instal ulang dengan opsi *Add to PATH*. |
| `.venv` lama error | Hapus folder `backend\.venv` (dibuat di PC lain/versi Python lain), jalankan `setup.bat` lagi. |

## 8. Catatan
- Pada free tier Google, data input dapat dipakai untuk meningkatkan produk Google; **jangan masukkan data nasabah asli**.
- Untuk produksi: HTTPS, rate limiting, ganti akun demo, dan gunakan paket berbayar/penyedia lain.

## Perubahan dari versi sebelumnya
- Model default `gemini-2.5-flash` + model cadangan otomatis (`GEMINI_FALLBACK_MODELS`).
- Aturan sistem dipindah ke `system_instruction`; `thinking` dimatikan untuk 2.5 Flash dan batas output 1024 token agar jawaban tidak kosong.
- Error API key / kuota ditampilkan jelas, kode komentar lama dibersihkan.
- Ditambah `setup.bat`, `start_all.bat`, `test_gemini.py`.
