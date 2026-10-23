"""
Pindah mode paper -> demo dengan aman, SETELAH semua posisi paper tertutup.

    python tools\\switch_to_demo.py          (berjalan sampai selesai, maks 6 jam)

1. tunggu sampai bot (paper) tidak punya posisi terbuka — dicek 2x berturut-turut
2. ubah config.json: "mode": "paper" -> "mode": "demo"
3. hentikan proses bot (PID dari logs\\bot.lock) lalu jalankan lagi di jendela baru
4. restart server dashboard web (kode baru: panel Akun MT5)
Semua langkah dicatat di logs\\switch_demo.log.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "switch_demo.log"
STATE = ROOT / "logs" / "state.json"
CFG = ROOT / "config" / "config.json"
LOCK = ROOT / "logs" / "bot.lock"
NEW_CONSOLE = 0x00000010


def say(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def state():
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
        return s if time.time() - s.get("ts", 0) < 20 else None     # bot harus hidup
    except Exception:
        return None


def flat_paper():
    s = state()
    return s is not None and s.get("mode") == "paper" and not s.get("positions")


def kill(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def main():
    say("menunggu semua posisi paper tertutup ...")
    t_end = time.time() + 6 * 3600
    while time.time() < t_end:
        s = state()
        if s and s.get("mode") != "paper":
            say(f"mode sudah '{s.get('mode')}' — tidak ada yang perlu diubah")
            return
        if flat_paper():
            time.sleep(3)
            if flat_paper():
                break
        time.sleep(15)
    else:
        say("BATAL: 6 jam berlalu dan posisi paper belum tertutup — tidak ada yang diubah")
        return

    # ---- 1. config
    txt = CFG.read_text(encoding="utf-8")
    if '"mode": "paper"' not in txt:
        say('BATAL: teks "mode": "paper" tidak ditemukan di config.json')
        return
    CFG.write_text(txt.replace('"mode": "paper"', '"mode": "demo"', 1), encoding="utf-8")
    json.loads(CFG.read_text(encoding="utf-8"))
    say('config.json: "mode" -> "demo"')

    # ---- 2. restart bot
    try:
        pid = int(LOCK.read_text().strip())
        kill(pid)
        say(f"bot paper (PID {pid}) dihentikan — 0 posisi terbuka")
        time.sleep(3)
        LOCK.unlink(missing_ok=True)
    except Exception as e:
        say(f"lock bot tidak terbaca ({e}) — lanjut start")
    subprocess.Popen(["cmd", "/k", sys.executable, "-m", "bot.main"], cwd=ROOT, creationflags=NEW_CONSOLE)
    say("bot dijalankan ulang di jendela baru (mode DEMO)")

    # ---- 3. restart server dashboard web
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                          "Where-Object { $_.CommandLine -like '*web_server.py*' -and $_.CommandLine -notlike '*--port=*' } | "
                          "ForEach-Object { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    for p in out:
        kill(int(p))
        say(f"server dashboard lama (PID {p}) dihentikan")
    time.sleep(2)
    subprocess.Popen(["cmd", "/k", sys.executable, r"dashboard\web_server.py", "--no-browser"], cwd=ROOT,
                     creationflags=NEW_CONSOLE)
    say("server dashboard dijalankan ulang (http://127.0.0.1:8765 — refresh browser)")

    # ---- 4. verifikasi
    for _ in range(12):
        time.sleep(5)
        s = state()
        if s and s.get("mode") == "demo":
            a = s.get("account") or {}
            say(f"OK: bot berjalan mode DEMO di akun #{a.get('login')} {a.get('type')} equity {a.get('equity')}")
            return
    say("PERINGATAN: bot belum melaporkan mode DEMO dalam 60 dtk — cek jendela bot / logs\\bot.log")


if __name__ == "__main__":
    main()
