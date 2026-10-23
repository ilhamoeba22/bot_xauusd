@echo off
REM Restart engine bridge auto-draw MT5 (bot & dashboard tidak disentuh).
cd /d "%~dp0.."
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if exist "logs\bridge.lock" for /f "usebackq delims=" %%p in ("logs\bridge.lock") do taskkill /PID %%p /F >nul 2>&1
del /q logs\bridge.lock >nul 2>&1
timeout /t 2 /nobreak >nul
start "XAUUSD_EXNESS BRIDGE" /min cmd /k "%PY% -m bot.bridge"
