@echo off
REM Restart bot XAUUSD_EXNESS: hentikan proses lama (PID di logs\bot.lock) lalu jalankan lagi di
REM jendela sendiri. Posisi terbuka aman: SL ada di server broker, bot baru mengambil alih lewat magic.
cd /d "%~dp0.."
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if exist logs\bot.lock (
    set /p BOTPID=<logs\bot.lock
)
if defined BOTPID (
    echo Menghentikan bot lama PID %BOTPID% ...
    taskkill /PID %BOTPID% /F >nul 2>&1
    timeout /t 3 /nobreak >nul
    del /q logs\bot.lock >nul 2>&1
)
title XAUUSD_EXNESS BOT
echo Menjalankan bot ...
REM --keep-symbols: simbol ON/OFF, profil & skill terakhir dipertahankan (tidak kembali ke default config)
%PY% -m bot.main --keep-symbols
echo.
echo Bot berhenti. Tekan tombol apa saja untuk menutup jendela ini.
pause >nul
