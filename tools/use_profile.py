"""
Ganti config aktif dengan salah satu profil di config\\profiles\\ (config lama di-backup).

    python tools\\use_profile.py                 -> daftar profil
    python tools\\use_profile.py cent_burst10    -> akun Standard Cent, burst 10 x 0,045%
    python tools\\use_profile.py zero_burst3     -> akun Zero (default sebelumnya), burst 3 x 0,15%

Setelah ganti profil: RESTART bot (mode/magic/simbol hanya dibaca saat start) dan jalankan
backtest dengan config baru sebelum mode live (gerbang live memeriksa sidik jari config).
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "config.json"
PROF = ROOT / "config" / "profiles"


def main():
    names = sorted(p.stem for p in PROF.glob("*.json"))
    if len(sys.argv) < 2 or sys.argv[1] not in names:
        print("Profil tersedia:")
        for n in names:
            c = json.loads((PROF / f"{n}.json").read_text(encoding="utf-8"))
            syms = [s for s, v in c["symbols"].items() if v.get("enabled")]
            r = c["risk"]
            print(f"  {n:<16} mode {c['mode']:<5} magic {c['magic']}  simbol {', '.join(syms)}  "
                  f"burst {r['burst_size']} x {r['risk_per_trade_pct']}% ({r.get('burst_mode')})")
        return
    new = PROF / f"{sys.argv[1]}.json"
    json.loads(new.read_text(encoding="utf-8"))                     # pastikan valid
    bak = ROOT / "config" / f"config.backup-{datetime.now():%Y%m%d-%H%M%S}.json"
    shutil.copy2(CFG, bak)
    shutil.copy2(new, CFG)
    print(f"Config aktif sekarang = profil '{sys.argv[1]}'.  Backup config lama: {bak.name}")
    print("LANGKAH BERIKUT: login MT5 ke akun yang sesuai, restart bot (start_bot.cmd stop lalu all), "
          "jalankan start_bot.cmd check, dan backtest ulang sebelum live.")


if __name__ == "__main__":
    main()
