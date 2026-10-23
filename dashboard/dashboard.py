"""
DASHBOARD XAUUSD_EXNESS — status realtime di konsol (CMD / PowerShell).
Gaya tampilan mengikuti DASHBOARD.bat Exness_Bot (ANSI: cyan/hijau/merah/kuning).

Membaca logs\\state.json yang ditulis bot tiap ~1 detik, jadi bekerja sama baiknya
untuk mode paper, demo, maupun live. Dashboard TIDAK mengirim order sendiri.

Tombol:
  [S] EMERGENCY STOP  -> bot menutup semua posisinya lalu berhenti (minta konfirmasi Y)
  [P] PAUSE / RESUME  -> berhenti/lanjut entry baru (posisi tetap dikelola)
  [Q] keluar dashboard (bot tetap jalan)
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs"
STATE, FLAG_STOP, FLAG_PAUSE, FLAG_HALT = (LOG / "state.json", LOG / "EMERGENCY_STOP",
                                           LOG / "PAUSE", LOG / "HALT")
REFRESH = 1.0

os.system("")          # aktifkan ANSI di konsol Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
E = "\x1b["
RESET, BOLD = E + "0m", E + "1m"
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE, GRAY = (E + f"{c}m" for c in (91, 92, 93, 94, 95, 96, 97, 90))
NEON_PINK = E + "38;2;255;0;203m"
NEON_CYAN = E + "38;2;0;178;255m"
W = 78

try:
    import msvcrt
except ImportError:
    msvcrt = None


def money(v, cur=""):
    if v is None:
        return "-"
    return f"{v:,.0f}" if abs(v) >= 100000 else f"{v:,.2f}"


def signed(v):
    col = NEON_CYAN if v > 0 else NEON_PINK if v < 0 else WHITE
    return f"{col}{'+' if v > 0 else ''}{money(v)}{RESET}"


def bar(frac, width=20, col=NEON_CYAN):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return f"{col}{'#' * n}{GRAY}{'.' * (width - n)}{RESET}"


def rule(ch="="):
    return f"{NEON_PINK}{BOLD}  {ch * (W - 2)}{RESET}"


def render(s, msg):
    L = []
    now = datetime.now()
    L.append(rule())
    L.append(f"{NEON_CYAN}{BOLD}  ⚡ GOLD SCALPER AI — STATUS REALTIME{RESET}   {GRAY}{now:%d-%m-%Y %H:%M:%S}{RESET}")
    L.append(rule())
    if not s:
        L.append(f"{RED}{BOLD}  STATUS: X BOT BELUM PERNAH JALAN (logs\\state.json tidak ada){RESET}")
        return L
    age = time.time() - s["ts"]
    mode = s["mode"].upper()
    mcol = {"PAPER": YELLOW, "DEMO": CYAN, "LIVE": RED}.get(mode, WHITE)
    if FLAG_STOP.exists():
        stat = f"{RED}{BOLD}X EMERGENCY STOP{RESET}"
    elif age < 10:
        stat = f"{GREEN}{BOLD}OK BOT SEDANG BERJALAN{RESET} {GRAY}(PID {s['pid']}){RESET}"
    else:
        stat = f"{RED}{BOLD}X BOT TIDAK BERJALAN{RESET} {GRAY}(update terakhir {int(age)} dtk lalu){RESET}"
    L.append(f"  STATUS: {stat}    MODE: {mcol}{BOLD}{mode}{RESET}")
    c = s["connection"]
    ctxt = f"{GREEN}TERHUBUNG{RESET}" if c["ok"] else f"{RED}{BOLD}TERPUTUS{RESET}"
    algo = f"{GREEN}ON{RESET}" if c.get("algo_trading") else f"{RED}OFF{RESET}"
    stale = f"  {GRAY}(info terakhir dari bot){RESET}" if age >= 10 else ""
    L.append(f"  MT5   : {ctxt}  reconnect {c['reconnects']}x   Algo Trading {algo}{stale}")
    a = s.get("account") or {}
    cur = a.get("currency", "")
    L.append(f"  AKUN  : #{a.get('login')} {a.get('server')} {a.get('type')} {cur}")
    L.append(rule("-"))

    # ---- equity & PnL
    L.append(f"{YELLOW}{BOLD}  + EQUITY{RESET}")
    L.append(f"    Akun   balance {WHITE}{money(a.get('balance'))}{RESET}   equity {WHITE}{BOLD}{money(a.get('equity'))}{RESET} {cur}")
    if s["mode"] == "paper":
        L.append(f"    Paper  balance {WHITE}{money(s['bot_balance'])}{RESET}   equity {WHITE}{BOLD}{money(s['bot_equity'])}{RESET}  {GRAY}(virtual){RESET}")
    d = s["day"]
    lim = d["limit_pct"] or 1
    used = max(0.0, -d["pnl_pct"]) / lim
    L.append(f"    P/L hari ini {signed(d['pnl'])} ({d['pnl_pct']:+.2f}%)   rem rugi harian "
             f"{bar(used, 16, RED if used > .7 else YELLOW)} {used*100:.0f}% dari -{lim}%"
             f"{f'  {RED}{BOLD}[AKTIF]{RESET}' if d['halted'] else ''}")
    ddf = s["drawdown_pct"] / (s["max_drawdown_pct"] or 1)
    L.append(f"    Drawdown dari puncak {s['drawdown_pct']:.2f}%   {bar(ddf, 16, RED if ddf > .7 else YELLOW)} batas {s['max_drawdown_pct']}%")
    t, td = s["stats_total"], s["stats_today"]
    L.append(f"    Win rate hari ini {WHITE}{BOLD}{td['win_rate']}%{RESET} ({td['wins']}/{td['trades']})   "
             f"total {WHITE}{BOLD}{t['win_rate']}%{RESET} ({t['wins']}/{t['trades']})  PF {t['pf']}  "
             f"P/L tertutup {signed(t['pnl'])}")
    L.append(rule("-"))

    # ---- simbol
    L.append(f"{YELLOW}{BOLD}  + SIMBOL{RESET}   {GRAY}{'simbol':<11}{'spread':>7}{'ATR':>7}{'sinyal':>8}{'pos':>5}  status{RESET}")
    for sym, v in s["symbols"].items():
        sig = {1: f"{GREEN}BUY {RESET}", -1: f"{RED}SELL{RESET}"}.get(v.get("signal"), f"{GRAY}  - {RESET}")
        stc = GREEN if str(v.get("status", "")).startswith("ENTRY") else GRAY
        L.append(f"             {WHITE}{sym:<11}{RESET}{v.get('spread', '-'):>7}{v.get('atr', '-'):>7}    {sig}"
                 f"{v.get('positions', 0):>5}  {stc}{v.get('status', '-')}{RESET}")
    g = s["guards"]
    ses = f"{GREEN}{g['session']}{RESET}" if g["session_ok"] else f"{RED}{g['session'] or '-'} (nonaktif){RESET}"
    news = f"{RED}{BOLD}BLOKIR: {g['news_info']}{RESET}" if g["news_block"] else f"{GREEN}aman{RESET}"
    L.append(f"    Sesi {ses}   Berita {news}   Pause {f'{YELLOW}{BOLD}YA{RESET}' if g['pause'] else 'tidak'}"
             f"{f'   {RED}{BOLD}HALT (max DD){RESET}' if g['halt'] else ''}")
    if g["loss_pause_until"] > time.time():
        L.append(f"    {YELLOW}Jeda kalah beruntun sampai {datetime.fromtimestamp(g['loss_pause_until']):%H:%M}{RESET}")
    if g.get("news_next"):
        L.append(f"    {GRAY}Berita berikut: {' | '.join(g['news_next'])}{RESET}")
    L.append(rule("-"))

    # ---- posisi
    pos = s["positions"]
    L.append(f"{YELLOW}{BOLD}  + POSISI TERBUKA ({len(pos)}){RESET}")
    if not pos:
        L.append(f"    {GRAY}tidak ada{RESET}")
    for p in pos:
        dc = GREEN if p["dir"] == "BUY" else RED
        L.append(f"    #{p['ticket']:<11} {p['symbol']:<10}{dc}{p['dir']:<5}{RESET}{p['volume']:>6}  @{p['open']:<10}"
                 f" SL {p['sl']:<10} {int(p['age_min']):>3}m  {signed(p['profit'])}")
    L.append(rule("-"))
    L.append(f"{YELLOW}{BOLD}  + LOG TERAKHIR{RESET}")
    for line in s["recent"][-7:]:
        col = RED if " x " in line or "HALT" in line or "REM" in line else GRAY
        L.append(f"    {col}{line[:W - 4]}{RESET}")
    L.append(rule())
    L.append(f"  {RED}{BOLD}[S]{RESET} EMERGENCY STOP   {YELLOW}{BOLD}[P]{RESET} "
             f"{'RESUME' if g['pause'] else 'PAUSE'} entry   {WHITE}{BOLD}[Q]{RESET} keluar dashboard")
    if msg:
        L.append(f"  {MAGENTA}{BOLD}{msg}{RESET}")
    return L


def read_state():
    for _ in range(3):
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            time.sleep(0.05)     # file sedang ditulis
    return None


def main():
    msg = ""
    sys.stdout.write(E + "2J")
    while True:
        s = read_state()
        out = "\n".join(l + E + "K" for l in render(s, msg))
        sys.stdout.write(E + "H" + out + "\n" + E + "J")
        sys.stdout.flush()
        end = time.time() + REFRESH
        while time.time() < end:
            if msvcrt and msvcrt.kbhit():
                k = msvcrt.getch().lower()
                if k == b"q":
                    sys.stdout.write(RESET + "\n")
                    return
                if k == b"p":
                    if FLAG_PAUSE.exists():
                        FLAG_PAUSE.unlink(); msg = "Entry DILANJUTKAN."
                    else:
                        FLAG_PAUSE.write_text(datetime.now().isoformat()); msg = "PAUSE: tidak ada entry baru."
                if k == b"s":
                    sys.stdout.write(f"\n  {RED}{BOLD}EMERGENCY STOP: tutup SEMUA posisi bot & hentikan bot? [Y/N] {RESET}")
                    sys.stdout.flush()
                    if msvcrt.getch().lower() == b"y":
                        FLAG_STOP.write_text(datetime.now().isoformat())
                        with open(LOG / "control_audit.log", "a", encoding="utf-8") as _f:   # jejak audit (29 Sep)
                            _f.write(f"{datetime.now().isoformat(timespec='milliseconds')}  konsol   EMERGENCY STOP (dashboard.py [S])\n")
                        msg = "EMERGENCY STOP dikirim. Bot akan menutup posisi & berhenti."
                    else:
                        msg = "Emergency stop dibatalkan."
                    sys.stdout.write(E + "2J")
                break
            time.sleep(0.05)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(RESET)
