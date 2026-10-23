@echo off
REM ============================================================================
REM XAUUSD_EXNESS - Launcher CMD (gaya sama dengan DASHBOARD.bat Exness_Bot)
REM Tanpa argumen  -> menu.  Dengan argumen:
REM   start_bot.cmd bot | web | bridge | indicator | dashboard | both | all | backtest | check | emergency | stop | reset | task | setup
REM Pilih simbol (lainnya OFF, bisa dinyalakan dari switch dashboard):
REM   start_bot.cmd bot XAUUSD,BTCUSD      start_bot.cmd all ETHUSD      start_bot.cmd bot XAUUSD XAUUSD247
REM   tanpa simbol = semua yang enabled=true di config\config.json
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

for /F %%a in ('echo prompt $E^| cmd') do set "ESC=%%a"
set "RESET=%ESC%[0m"
set "RED=%ESC%[91m"
set "GREEN=%ESC%[92m"
set "YELLOW=%ESC%[93m"
set "CYAN=%ESC%[96m"
set "WHITE=%ESC%[97m"
set "GRAY=%ESC%[90m"
set "BOLD=%ESC%[1m"
set "NEON_PINK=%ESC%[38;2;255;0;203m"
set "NEON_CYAN=%ESC%[38;2;0;178;255m"

if not "%~1"=="" goto ARG

:START
cls
set "MODE=?"
for /f "usebackq delims=" %%m in (`%PY% -c "import json;print(json.load(open('config/config.json',encoding='utf-8'))['mode'].upper())" 2^>nul`) do set "MODE=%%m"
echo %NEON_PINK%%BOLD%  =====================================================================%RESET%
echo %NEON_CYAN%%BOLD%  ⚡ GOLD SCALPER AI - UNIVERSAL SCALPING BOT%RESET%
echo %NEON_PINK%%BOLD%  =====================================================================%RESET%
if exist logs\bot.lock (
    echo %GREEN%%BOLD%  STATUS: bot.lock ada - bot kemungkinan BERJALAN%RESET%   %GRAY%mode config: %MODE%%RESET%
) else (
    echo %RED%%BOLD%  STATUS: X BOT TIDAK BERJALAN%RESET%   %GRAY%mode config: %MODE%%RESET%
)
if exist logs\EMERGENCY_STOP echo %RED%%BOLD%  ! EMERGENCY_STOP aktif - pilih [8] untuk reset sebelum start%RESET%
if exist logs\HALT echo %RED%%BOLD%  ! HALT max drawdown aktif - periksa log, lalu [8] reset%RESET%
echo %CYAN%  =====================================================================%RESET%
echo %YELLOW%%BOLD%  + JALANKAN:%RESET%
echo %GREEN%%BOLD%    [1]%RESET% %WHITE%BOT%RESET%              %GRAY%jendela baru, mode sesuai config (default PAPER)%RESET%
echo %GREEN%%BOLD%    [2]%RESET% %WHITE%DASHBOARD KONSOL%RESET% %GRAY%status teks realtime, emergency stop [S]%RESET%
echo %GREEN%%BOLD%    [3]%RESET% %WHITE%BOT + DASHBOARD KONSOL%RESET%
echo %GREEN%%BOLD%    [W]%RESET% %WHITE%DASHBOARD WEB%RESET%    %GRAY%chart + pola + SMC + alasan entry (browser)%RESET%
echo %GREEN%%BOLD%    [A]%RESET% %WHITE%SEMUA: BOT + BRIDGE MT5 + WEB%RESET%
echo %GREEN%%BOLD%    [B]%RESET% %WHITE%BRIDGE MT5%RESET%       %GRAY%auto-draw analisis di chart MT5 (jendela baru)%RESET%
echo %GREEN%%BOLD%    [I]%RESET% %WHITE%PASANG INDIKATOR MT5%RESET% %GRAY%salin + compile XAUUSD_Bridge_Draw ke folder MT5%RESET%
echo %YELLOW%%BOLD%  + UJI:%RESET%
echo %CYAN%%BOLD%    [4]%RESET% %WHITE%BACKTEST 12 bulan%RESET%  %GRAY%wajib sebelum live (+ validasi learning)%RESET%
echo %CYAN%%BOLD%    [5]%RESET% %WHITE%CEK AKUN ^& SINYAL%RESET%  %GRAY%lot, spread, ATR saat ini%RESET%
echo %YELLOW%%BOLD%  + KONTROL:%RESET%
echo %RED%%BOLD%    [6]%RESET% %WHITE%EMERGENCY STOP%RESET%   %GRAY%tutup SEMUA posisi bot lalu bot berhenti%RESET%
echo %RED%%BOLD%    [7]%RESET% %WHITE%STOP BOT SAJA%RESET%    %GRAY%posisi tetap terbuka (SL di server)%RESET%
echo %YELLOW%%BOLD%    [8]%RESET% %WHITE%RESET STOP/HALT%RESET%  %GRAY%hapus flag supaya bot boleh start lagi%RESET%
echo %YELLOW%%BOLD%  + SETUP:%RESET%
echo %WHITE%%BOLD%    [9]%RESET% %WHITE%SCHEDULED TASK%RESET%   %GRAY%bot auto-start saat login ^& tiap pagi 05:05%RESET%
echo %WHITE%%BOLD%    [V]%RESET% %WHITE%BUAT VIRTUAL ENV%RESET% %GRAY%.venv + requirements.txt%RESET%
echo %WHITE%%BOLD%    [0]%RESET% %WHITE%EXIT%RESET%
echo.
set "pilih="
set /p pilih="%GREEN%   Pilihan: %RESET%"
if "%pilih%"=="1" call :BOT & goto START
if "%pilih%"=="2" call :DASH & goto START
if "%pilih%"=="3" call :BOT & call :DASH & goto START
if /I "%pilih%"=="W" call :WEB & goto START
if /I "%pilih%"=="A" call :BOT & call :BRIDGE & call :WEB & goto START
if /I "%pilih%"=="B" call :BRIDGE & goto START
if /I "%pilih%"=="I" call :INDICATOR & goto START
if "%pilih%"=="4" call :BACKTEST & goto START
if "%pilih%"=="5" call :CHECK & goto START
if "%pilih%"=="6" call :EMERGENCY & goto START
if "%pilih%"=="7" call :STOP & goto START
if "%pilih%"=="8" call :RESETF & goto START
if "%pilih%"=="9" call :TASK & goto START
if /I "%pilih%"=="V" call :SETUP & goto START
if "%pilih%"=="0" exit /b 0
goto START

:ARG
REM argumen ke-2 dst = daftar simbol (cmd memecah koma jadi argumen terpisah -> digabung lagi)
REM kata CONSERVATIVE / NORMAL / AGGRESSIVE di antara argumen = profil risiko
set "SYMS="
set "PROF="
for %%s in (%2 %3 %4 %5 %6 %7 %8 %9) do (
    if /I "%%~s"=="CONSERVATIVE" (set "PROF=CONSERVATIVE") else if /I "%%~s"=="NORMAL" (set "PROF=NORMAL") else if /I "%%~s"=="AGGRESSIVE" (set "PROF=AGGRESSIVE") else if defined SYMS (set "SYMS=!SYMS!,%%~s") else (set "SYMS=%%~s")
)
set "BOTARGS="
if defined SYMS (
    set "BOTARGS= --symbols !SYMS!"
    if /I not "%~1"=="skill" if /I not "%~1"=="profile" echo %CYAN%  Simbol trading: !SYMS!  ^(lainnya OFF; bisa dinyalakan dari dashboard^)%RESET%
)
if defined PROF set "BOTARGS=!BOTARGS! --profile !PROF!"
REM start_bot.cmd profile AGGRESSIVE [BTCUSD] = ganti profil bot yang SEDANG jalan (perintah + ACK, tanpa restart)
if /I "%~1"=="profile" (
    if defined SYMS (%PY% -m bot.control --profile !PROF! --symbol !SYMS!) else (%PY% -m bot.control --profile !PROF!)
    exit /b
)
REM start_bot.cmd skill smc off|on = matikan / nyalakan skill (perintah + ACK)
if /I "%~1"=="skill" (
    if /I "%~3"=="off" (%PY% -m bot.control --skill %~2 --off) else (%PY% -m bot.control --skill %~2 --on)
    exit /b
)
if /I "%~1"=="bot" call :BOTFG & exit /b
if /I "%~1"=="dashboard" call :DASH & exit /b
if /I "%~1"=="both" call :BOT & call :DASH & exit /b
if /I "%~1"=="web" call :WEB & exit /b
if /I "%~1"=="all" call :BOT & call :BRIDGE & call :WEB & exit /b
if /I "%~1"=="bridge" call :BRIDGE & exit /b
if /I "%~1"=="indicator" call :INDICATOR & exit /b
if /I "%~1"=="backtest" call :BACKTEST & exit /b
if /I "%~1"=="check" call :CHECK & exit /b
if /I "%~1"=="emergency" call :EMERGENCY & exit /b
if /I "%~1"=="stop" call :STOP & exit /b
if /I "%~1"=="reset" call :RESETF & exit /b
if /I "%~1"=="task" call :TASK & exit /b
if /I "%~1"=="setup" call :SETUP & exit /b
echo Argumen tidak dikenal: %~1
exit /b 1

:BOT
start "XAUUSD_EXNESS BOT" cmd /k "%PY% -m bot.main%BOTARGS%"
echo %GREEN%  OK bot dijalankan di jendela baru "XAUUSD_EXNESS BOT"%RESET%
timeout /t 2 /nobreak >nul
exit /b

:BOTFG
%PY% -m bot.main%BOTARGS%
exit /b

:DASH
%PY% dashboard\dashboard.py
exit /b

:BRIDGE
REM engine sendiri menolak jalan dobel (cek PID di logs\bridge.lock), jadi aman dipanggil ulang
start "XAUUSD_EXNESS BRIDGE" /min cmd /k "%PY% -m bot.bridge"
echo %GREEN%  OK engine bridge MT5 dijalankan (jendela minimized "XAUUSD_EXNESS BRIDGE")%RESET%
echo %YELLOW%  PENGINGAT: pasang indikator XAUUSD_Bridge_Draw di chart MT5 sekali saja (lihat README / menu [I]).%RESET%
timeout /t 3 /nobreak >nul
exit /b

:INDICATOR
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1" -Action InstallIndicator
pause
exit /b

:WEB
echo %CYAN%  Dashboard web: http://127.0.0.1:8765  (browser terbuka otomatis; Ctrl+C untuk menutup server)%RESET%
%PY% dashboard\web_server.py
exit /b

:BACKTEST
%PY% backtest\backtest.py --learning
echo.
echo %CYAN%  Hasil lengkap: backtest\results\   [ENTER untuk kembali]%RESET%
pause >nul
exit /b

:CHECK
%PY% -m bot.main --check
echo.
pause
exit /b

:EMERGENCY
set "yn="
set /p yn="%RED%%BOLD%  TUTUP SEMUA POSISI BOT dan hentikan bot? [Y/N]: %RESET%"
if /I not "%yn%"=="Y" exit /b
echo %date% %time% > logs\EMERGENCY_STOP
echo %date% %time%  cmd      EMERGENCY STOP (start_bot.cmd menu [6]) >> logs\control_audit.log
echo %YELLOW%  [*] Flag EMERGENCY_STOP dibuat. Bot akan menutup posisi dalam ~2 detik.%RESET%
timeout /t 5 /nobreak >nul
exit /b

:STOP
if not exist logs\bot.lock (
    echo %GREEN%  OK - tidak ada bot berjalan%RESET%
    timeout /t 2 /nobreak >nul
    exit /b
)
set /p BOTPID=<logs\bot.lock
taskkill /PID %BOTPID% /F >nul 2>&1
del /q logs\bot.lock >nul 2>&1
echo %GREEN%  OK - bot (PID %BOTPID%) dihentikan. Posisi terbuka tetap ada dengan SL-nya.%RESET%
timeout /t 3 /nobreak >nul
exit /b

:RESETF
if exist logs\HALT (
    echo %RED%  Isi HALT:%RESET%
    type logs\HALT
    echo.
)
set "yn="
set /p yn="%YELLOW%  Hapus EMERGENCY_STOP / HALT? [Y/N]: %RESET%"
if /I not "%yn%"=="Y" exit /b
del /q logs\EMERGENCY_STOP >nul 2>&1
del /q logs\HALT >nul 2>&1
echo %GREEN%  OK - bot boleh dijalankan lagi.%RESET%
timeout /t 2 /nobreak >nul
exit /b

:TASK
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1" -Action InstallTask
pause
exit /b

:SETUP
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1" -Action Setup
pause
exit /b
