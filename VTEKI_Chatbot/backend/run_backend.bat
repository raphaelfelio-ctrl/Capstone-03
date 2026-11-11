@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Jalankan setup.bat di folder utama terlebih dahulu.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
