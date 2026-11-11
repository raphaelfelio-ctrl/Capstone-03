@echo off
start "V-TEKI Backend" cmd /k "%~dp0backend\run_backend.bat"
start "V-TEKI Frontend" cmd /k "%~dp0frontend\run_frontend.bat"
timeout /t 4 >nul
start http://127.0.0.1:5500
