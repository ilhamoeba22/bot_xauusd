@echo off
REM Restart dashboard web (8765 = bot demo, 8766 = eksperimen paper50) agar kode terbaru dimuat.
REM Bot TIDAK disentuh — hanya proses web_server.py yang dihentikan lalu dijalankan ulang.
cd /d "%~dp0.."
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*web_server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul
start "DASHBOARD 8765" cmd /k "%PY% dashboard\web_server.py"
REM dashboard 8766 hanya kalau bot eksperimen memang sedang berjalan (PID di logs_paper50\bot.lock hidup)
set "EXPPID="
if exist "logs_paper50\bot.lock" set /p EXPPID=<logs_paper50\bot.lock
set "EXPRUN="
if defined EXPPID for /f %%p in ('tasklist /FI "PID eq %EXPPID%" /NH ^| findstr /r "[0-9]"') do set "EXPRUN=1"
if defined EXPRUN start "PAPER50 DASHBOARD" cmd /k "set XAUUSD_CONFIG=%CD%\config\profiles\paper_50burst.json&& set XAUUSD_LOG_DIR=%CD%\logs_paper50&& %PY% dashboard\web_server.py --port=8766"
