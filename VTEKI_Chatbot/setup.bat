@echo off
cd /d "%~dp0backend"
echo [1/3] Membuat virtual environment...
py -m venv .venv || goto :err
echo [2/3] Menginstal dependensi...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :err
echo [3/3] Menyiapkan file .env...
if not exist .env copy .env.example .env
echo.
echo SELESAI. Buka backend\.env lalu isi GEMINI_API_KEY.
echo Tes koneksi: backend\.venv\Scripts\python.exe backend\test_gemini.py
echo Jalankan aplikasi: start_all.bat
pause
exit /b 0
:err
echo Terjadi error. Pastikan Python 3.11+ terpasang ("py --version").
pause
exit /b 1
