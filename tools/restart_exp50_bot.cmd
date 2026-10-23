@echo off
REM Restart HANYA bot eksperimen 50 burst/hari (profil paper_50burst.json — sejak 25 Sep mode DEMO,
REM akun sama dengan bot utama, magic 260950, log logs_paper50\). Bot utama & dashboard tidak disentuh.
cd /d "%~dp0.."
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
set "XAUUSD_CONFIG=%CD%\config\profiles\paper_50burst.json"
set "XAUUSD_LOG_DIR=%CD%\logs_paper50"
if exist "%XAUUSD_LOG_DIR%\bot.lock" for /f "usebackq delims=" %%p in ("%XAUUSD_LOG_DIR%\bot.lock") do taskkill /PID %%p /F >nul 2>&1
timeout /t 2 /nobreak >nul
start "EXP50 BOT (DEMO)" cmd /k "%PY% -m bot.main"
