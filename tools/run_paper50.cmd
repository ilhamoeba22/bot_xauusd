@echo off
REM ============================================================================
REM EKSPERIMEN PAPER ">= 50 burst per hari" — uang VIRTUAL, TIDAK ada order ke broker.
REM Berjalan terpisah total dari bot demo: config profiles\paper_50burst.json, log di logs_paper50\,
REM magic 260950, dashboard http://127.0.0.1:8766 (bot demo tetap di 8765).
REM Hentikan: tutup jendela "PAPER50 BOT" dan "PAPER50 DASHBOARD" (atau Ctrl+C di masing-masing).
REM ============================================================================
cd /d "%~dp0.."
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
set "XAUUSD_CONFIG=%CD%\config\profiles\paper_50burst.json"
set "XAUUSD_LOG_DIR=%CD%\logs_paper50"
if not exist "%XAUUSD_LOG_DIR%" mkdir "%XAUUSD_LOG_DIR%"
start "PAPER50 BOT" cmd /k "%PY% -m bot.main"
timeout /t 4 /nobreak >nul
start "PAPER50 DASHBOARD" cmd /k "%PY% dashboard\web_server.py --port=8766"
echo Eksperimen paper 50 burst/hari dijalankan. Dashboard: http://127.0.0.1:8766
timeout /t 5 /nobreak >nul
