"""Ganti profil / skill dari CMD saat bot BERJALAN (alur sama dengan dashboard: perintah -> bot -> ACK).

  python -m bot.control --profile AGGRESSIVE
  python -m bot.control --profile NORMAL --symbol BTCUSD      (override per simbol; DEFAULT = ikut global)
  python -m bot.control --skill smc --off                     (--on untuk menyalakan)
  python -m bot.control --status
Exit code 0 = ACK OK, 1 = ditolak / tidak ada ACK dalam 5 detik.
"""
import argparse
import json
import sys
import time

from .common import load_config
from . import profiles as prof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--strategy", help="ganti strategi aktif: bb_reversion | smc_mtf | default")
    ap.add_argument("--symbol")
    ap.add_argument("--skill")
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--mode", help="mode simbol: AUTO | RUN | BLOCK (butuh --symbol)")
    ap.add_argument("--hours", type=float, help="TETAP RUN: kedaluwarsa lebih cepat dari default")
    ap.add_argument("--timeout", type=float, default=5.0)
    a = ap.parse_args()
    if a.status or not (a.profile or a.skill or a.mode or a.strategy):
        print(json.dumps(prof.read_control(load_config()), indent=1))
        return 0
    cid = (prof.send_command("strategy", "CMD", strategy=a.strategy.lower()) if a.strategy
           else prof.send_command("override", "CMD", symbol=a.symbol, mode=a.mode.upper(), hours=a.hours) if a.mode
           else prof.send_command("profile", "CMD", profile=a.profile.upper(), symbol=a.symbol) if a.profile
           else prof.send_command("skill", "CMD", skill=a.skill, on=not a.off))
    print("MENUNGGU KONFIRMASI bot ...")
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        ack = prof.read_ack(cid)
        if ack:
            print(("AKTIF: " if ack["ok"] else "DITOLAK: ") + ack.get("msg", ""))
            for w in ack.get("warnings", []):
                print("  ! " + w)
            print("  profil global:", ack["profile"]["global"], " per simbol:", ack["profile"]["per_symbol"] or "-")
            print("  skill OFF:", ", ".join(k for k, v in ack["skills"].items() if not v) or "-")
            print("  mode simbol:", {k: v["mode"] for k, v in (ack.get("override") or {}).items()} or "semua OTOMATIS")
            return 0 if ack["ok"] else 1
        time.sleep(0.3)
    for f in prof.CMD_DIR.glob(f"{cid}.json"):
        f.unlink(missing_ok=True)                 # jangan sampai diterapkan belakangan tanpa sepengetahuan user
    print(f"GAGAL: tidak ada ACK dalam {a.timeout:.0f} detik — bot tidak berjalan / MT5 tidak merespons. Tidak ada yang diubah.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
