<#
.SYNOPSIS
  Launcher PowerShell XAUUSD_EXNESS.

.EXAMPLE
  .\start_bot.ps1                         # menu (sama dengan start_bot.cmd)
  .\start_bot.ps1 -Action Bot             # jalankan bot di jendela ini
  .\start_bot.ps1 -Action Dashboard       # dashboard konsol (teks)
  .\start_bot.ps1 -Action Web             # dashboard web: chart, pola, SMC, alasan entry
  .\start_bot.ps1 -Action All             # bot + bridge MT5 (jendela terpisah) + dashboard web
  .\start_bot.ps1 -Action Bridge          # engine bridge auto-draw MT5 (jendela ini)
  .\start_bot.ps1 -Action BridgeBg        # engine bridge di background (jendela minimized)
  .\start_bot.ps1 -Action StopBridge      # hentikan engine bridge
  .\start_bot.ps1 -Action InstallIndicator # salin + compile indikator MQL5 ke folder MT5
  .\start_bot.ps1 -Action Both            # bot di jendela baru + dashboard di sini
  .\start_bot.ps1 -Action Backtest
  .\start_bot.ps1 -Action Check
  .\start_bot.ps1 -Action EmergencyStop   # tutup semua posisi bot + bot berhenti
  .\start_bot.ps1 -Action Stop            # hentikan proses bot, posisi tetap (SL di server)
  .\start_bot.ps1 -Action Reset           # hapus flag EMERGENCY_STOP / HALT
  .\start_bot.ps1 -Action Setup           # buat .venv + install requirements
  .\start_bot.ps1 -Action InstallTask     # Scheduled Task: auto-start saat login & tiap hari 05:05
  .\start_bot.ps1 -Action RemoveTask
  .\start_bot.ps1 -Action TaskStatus

  Kalau PowerShell menolak menjalankan script:
  powershell -ExecutionPolicy Bypass -File .\start_bot.ps1 -Action Bot
#>
param(
    [ValidateSet("Menu", "Bot", "Dashboard", "Both", "Web", "All", "Bridge", "BridgeBg", "StopBridge", "InstallIndicator", "Backtest", "Check", "EmergencyStop", "Stop",
                 "Reset", "Setup", "InstallTask", "RemoveTask", "TaskStatus")]
    [string]$Action = "Menu",
    [string]$TaskTime = "05:05",
    # simbol yang boleh trading, mis. -Symbols XAUUSD,BTCUSD  atau  -Symbols ETHUSD
    # kosong = semua simbol dengan enabled=true di config. Simbol lain tetap dimuat (posisinya dikelola,
    # bisa dinyalakan dari switch dashboard tanpa restart).
    [string[]]$Symbols = @(),
    # profil risiko: -Profile Aggressive | Normal | Conservative
    # bot sudah jalan -> diganti LANGSUNG (perintah + ACK, tanpa restart); belum jalan -> dipakai saat start
    [ValidateSet("", "Conservative", "Normal", "Aggressive")]
    [string]$Profile = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root
$TaskName = "XAUUSD_EXNESS_Bot"
$Venv = Join-Path $Root ".venv\Scripts\python.exe"
$Py = if (Test-Path $Venv) { $Venv } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $Py -and $Action -ne "Setup") { Write-Host "Python tidak ditemukan. Install Python 3.10+ lalu jalankan -Action Setup." -ForegroundColor Red; exit 1 }
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Get-LockPid([string]$name) {
    $lock = Join-Path $Logs $name
    if (-not (Test-Path $lock)) { return $null }
    $p = [int](Get-Content $lock -Raw).Trim()
    if (Get-Process -Id $p -ErrorAction SilentlyContinue) { return $p }
    return $null
}

function Start-BridgeBg {
    if (Get-LockPid "bridge.lock") { Write-Host "Engine bridge sudah berjalan." -ForegroundColor Gray; return }
    Start-Process -FilePath $Py -ArgumentList "-m", "bot.bridge" -WorkingDirectory $Root -WindowStyle Minimized
    Write-Host "Engine bridge MT5 dijalankan di background (jendela minimized)." -ForegroundColor Green
}

function Show-IndicatorReminder {
    Write-Host ""
    Write-Host "  PENGINGAT (sekali saja): pasang indikator di chart MT5" -ForegroundColor Yellow
    Write-Host "    1. MT5 > Navigator > Indicators > XAUUSD_EXNESS > XAUUSD_Bridge_Draw" -ForegroundColor Yellow
    Write-Host "    2. Seret ke chart XAUUSD / XAUUSD247 (timeframe M1/M5/M15/M30/H1) > OK" -ForegroundColor Yellow
    Write-Host "    3. Simpan sebagai template/profile supaya otomatis terpasang lagi" -ForegroundColor Yellow
    Write-Host "    Belum ada di Navigator? jalankan: .\start_bot.ps1 -Action InstallIndicator" -ForegroundColor Yellow
    Write-Host ""
}

function Get-BotPid {
    $lock = Join-Path $Logs "bot.lock"
    if (-not (Test-Path $lock)) { return $null }
    $p = [int](Get-Content $lock -Raw).Trim()
    if (Get-Process -Id $p -ErrorAction SilentlyContinue) { return $p }
    return $null
}

# -Symbols: dari PowerShell datang sebagai array, dari cmd (-File) sebagai satu string "A,B" -> pecah keduanya
$SymList = @($Symbols | ForEach-Object { $_ -split "[,; ]" } | Where-Object { $_ } | ForEach-Object { $_.Trim().ToUpper() -replace "C$", "c" })
$BotArgs = @("-m", "bot.main")
if ($SymList.Count) {
    $BotArgs += @("--symbols", ($SymList -join ","))
    if ($Action -eq "Menu") { $Action = "Bot" }          # .\start_bot.ps1 -Symbols ETHUSD = langsung jalankan bot
    Write-Host "Simbol trading: $($SymList -join ', ')  (lainnya OFF; bisa dinyalakan dari dashboard)" -ForegroundColor Cyan
}

if ($Profile) {
    $P = $Profile.ToUpper()
    if ((Get-BotPid) -and -not $SymList.Count) {
        & $Py -m bot.control --profile $P            # hot-switch: tunggu ACK dari bot
        exit $LASTEXITCODE
    }
    $BotArgs += @("--profile", $P)
    if ($Action -eq "Menu") { $Action = "Bot" }
}

switch ($Action) {
    "Menu" { & cmd /c "`"$Root\start_bot.cmd`""; break }

    "Bot" { & $Py @BotArgs; break }

    "Dashboard" { & $Py (Join-Path $Root "dashboard\dashboard.py"); break }

    "Both" {
        Start-Process -FilePath $Py -ArgumentList $BotArgs -WorkingDirectory $Root
        Start-Sleep 3
        & $Py (Join-Path $Root "dashboard\dashboard.py")
        break
    }

    "Web" { & $Py (Join-Path $Root "dashboard\web_server.py"); break }

    "All" {
        if (-not (Get-BotPid)) { Start-Process -FilePath $Py -ArgumentList $BotArgs -WorkingDirectory $Root }
        Start-BridgeBg
        Show-IndicatorReminder
        Start-Sleep 3
        & $Py (Join-Path $Root "dashboard\web_server.py")
        break
    }

    "Bridge" { Show-IndicatorReminder; & $Py -m bot.bridge; break }

    "BridgeBg" { Start-BridgeBg; Show-IndicatorReminder; break }

    "StopBridge" {
        $p = Get-LockPid "bridge.lock"
        if ($p) {
            Stop-Process -Id $p -Force
            Remove-Item (Join-Path $Logs "bridge.lock") -ErrorAction SilentlyContinue
            Write-Host "Engine bridge (PID $p) dihentikan. Indikator MT5 akan menampilkan 'Bridge disconnected'." -ForegroundColor Green
        } else { Write-Host "Engine bridge tidak berjalan." -ForegroundColor Gray }
        break
    }

    "InstallIndicator" {
        $data = (& $Py -c "import MetaTrader5 as m; m.initialize(); print(m.terminal_info().data_path)").Trim()
        $term = (& $Py -c "import MetaTrader5 as m; m.initialize(); print(m.terminal_info().path)").Trim()
        if (-not $data) { Write-Host "Terminal MT5 tidak ditemukan - buka MT5 dulu." -ForegroundColor Red; break }
        $dst = Join-Path $data "MQL5\Indicators\XAUUSD_EXNESS"
        New-Item -ItemType Directory -Force $dst | Out-Null
        Copy-Item (Join-Path $Root "mql5\XAUUSD_Bridge_Draw.mq5") $dst -Force
        $me = Join-Path $term "MetaEditor64.exe"
        $src = Join-Path $dst "XAUUSD_Bridge_Draw.mq5"
        $log = Join-Path $Root "mql5\compile.log"
        if (Test-Path $me) {
            Start-Process -FilePath $me -ArgumentList "/compile:`"$src`"", "/log:`"$log`"" -Wait
            $res = (Get-Content $log -Encoding Unicode | Select-String "Result:").Line
            Write-Host "Compile: $res" -ForegroundColor Cyan
        } else {
            Copy-Item (Join-Path $Root "mql5\XAUUSD_Bridge_Draw.ex5") $dst -Force
            Write-Host "MetaEditor tidak ditemukan - .ex5 hasil compile bawaan disalin." -ForegroundColor Yellow
        }
        Write-Host "Terpasang di: $dst" -ForegroundColor Green
        Write-Host "Di MT5: klik kanan Navigator > Refresh, lalu Indicators > XAUUSD_EXNESS > XAUUSD_Bridge_Draw." -ForegroundColor Green
        break
    }

    "Backtest" { & $Py (Join-Path $Root "backtest\backtest.py") --learning; break }

    "Check" { & $Py -m bot.main --check; break }

    "EmergencyStop" {
        $ans = Read-Host "TUTUP SEMUA POSISI BOT dan hentikan bot? [Y/N]"
        if ($ans -match '^[Yy]') {
            Set-Content -Path (Join-Path $Logs "EMERGENCY_STOP") -Value (Get-Date -Format s) -Encoding utf8
            Add-Content -Path (Join-Path $Logs "control_audit.log") -Value "$(Get-Date -Format s)  ps1      EMERGENCY STOP (start_bot.ps1 -Action EmergencyStop)"
            Write-Host "Flag EMERGENCY_STOP dibuat. Bot menutup posisi dalam ~2 detik." -ForegroundColor Yellow
        }
        break
    }

    "Stop" {
        $p = Get-BotPid
        if ($p) {
            Stop-Process -Id $p -Force
            Remove-Item (Join-Path $Logs "bot.lock") -ErrorAction SilentlyContinue
            Write-Host "Bot PID $p dihentikan. Posisi terbuka tetap ada dengan SL-nya." -ForegroundColor Green
        } else { Write-Host "Tidak ada bot berjalan." -ForegroundColor Gray }
        break
    }

    "Reset" {
        $halt = Join-Path $Logs "HALT"
        if (Test-Path $halt) { Write-Host "HALT: $(Get-Content $halt -Raw)" -ForegroundColor Red }
        Remove-Item (Join-Path $Logs "EMERGENCY_STOP"), $halt -ErrorAction SilentlyContinue
        Write-Host "Flag EMERGENCY_STOP / HALT dihapus. Bot boleh dijalankan lagi." -ForegroundColor Green
        break
    }

    "Setup" {
        $sys = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $sys) { Write-Host "Python tidak ditemukan di PATH." -ForegroundColor Red; exit 1 }
        if (-not (Test-Path $Venv)) { & $sys -m venv (Join-Path $Root ".venv") }
        & $Venv -m pip install --upgrade pip
        & $Venv -m pip install -r (Join-Path $Root "requirements.txt")
        Write-Host "OK - .venv siap. Launcher otomatis memakai .venv." -ForegroundColor Green
        break
    }

    "InstallTask" {
        # Bot butuh terminal MT5 yang login di sesi user yang SAMA -> task berjalan
        # sebagai user ini, hanya saat user login (bukan Windows Service / sesi 0).
        # pythonw = tanpa jendela konsol (tidak berkedip tiap 5 menit). --watchdog: kalau bot sudah jalan,
        # atau ada EMERGENCY_STOP / HALT, proses langsung keluar diam-diam tanpa menulis error.
        $Pyw = Join-Path (Split-Path $Py) "pythonw.exe"
        if (-not (Test-Path $Pyw)) { $Pyw = $Py }
        $act = New-ScheduledTaskAction -Execute $Pyw -Argument "-m bot.main --watchdog" -WorkingDirectory $Root
        $watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
        $trig = @(
            (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"),
            # emas buka Minggu ~22:00 UTC = Senin 05:00 WIB
            (New-ScheduledTaskTrigger -Daily -At $TaskTime),
            # WATCHDOG (diagnosis 25 Sep): tiap 5 menit cek; bot mati -> dinyalakan lagi
            $watch
        )
        $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 5)
        $pr = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trig -Settings $set -Principal $pr `
            -Description "XAUUSD_EXNESS scalping bot (mode sesuai config\config.json)" -Force | Out-Null
        Write-Host "OK - Scheduled Task '$TaskName' terpasang: saat login, tiap hari $TaskTime, dan WATCHDOG tiap 5 menit." -ForegroundColor Green
        Write-Host "   Pastikan: terminal MT5 ikut auto-start & login, Algo Trading ON, laptop tidak sleep." -ForegroundColor Yellow
        break
    }

    "RemoveTask" {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Scheduled Task '$TaskName' dihapus." -ForegroundColor Green
        break
    }

    "TaskStatus" {
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $t) { Write-Host "Task belum terpasang."; break }
        $i = $t | Get-ScheduledTaskInfo
        Write-Host "Task  : $TaskName  ($($t.State))"
        Write-Host "Terakhir jalan: $($i.LastRunTime)  hasil $($i.LastTaskResult)   Berikut: $($i.NextRunTime)"
        $p = Get-BotPid
        Write-Host "Bot   : $(if ($p) { "BERJALAN (PID $p)" } else { 'tidak berjalan' })"
        break
    }
}
