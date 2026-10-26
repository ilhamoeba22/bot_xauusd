"""
Bot Scalping Gold Universal — kompatibel semua broker MT5 (Exness, RoboForex, IC Markets, Pepperstone, FBS, XM, dll).

Jalankan dari folder bot:
    python -m bot.main            (atau start_bot.cmd / start_bot.ps1)
    python -m bot.main --check    (cek akun, simbol, lot & sinyal saat ini lalu keluar)
    python -m bot.main --symbols XAUUSD,BTCUSD   (hanya simbol ini yang trading; tanpa flag = semua enabled)

Mode (config.json -> "mode"):
    paper : posisi virtual, TIDAK ada order ke broker          <- DEFAULT
    demo  : order sungguhan, hanya mau jalan di akun DEMO
    live  : akun REAL; wajib live_trading_confirmed=true + hasil backtest < 30 hari

Berhenti dengan aman:
    Ctrl+C              -> bot keluar, posisi tetap terbuka dengan SL di server (paper: disimpan)
    logs\\EMERGENCY_STOP -> bot MENUTUP semua posisinya lalu keluar (tombol [S] di dashboard)
    logs\\PAUSE          -> tidak entry baru, posisi tetap dikelola (tombol [P] di dashboard)
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from .common import (ROOT, BOTSTATE_PATH, JOURNAL_PATH, BURST_LOG_PATH, LOCK_PATH, FLAG_STOP, FLAG_PAUSE,
                     FLAG_HALT, FLAG_BURST_OFF, FLAG_BURST_NOW, STATE_PATH, RECENT, audit,
                     load_config, setup_logging, write_json_atomic, sym_cfg, risk_group, read_active, write_active)
from .broker import Connection, LiveExec, PaperExec, TF, TF_SEC, value_per_price_unit
from .broker_detect import detect_broker, apply_broker_profile
from .strategy import build_signals, min_bars, ENTRIES, FILTERS
from .indicators import atr as atr_fn
from .sessions import symbol_session
from . import profiles as prof
from . import skills as sk
from .news import NewsFilter
from .news_engine import NewsTradingEngine
from .common import LOG_DIR
from .analysis import candles as cdl
from .analysis import smc as smc_mod
from .analysis import structure as struct_mod
from .analysis.features import bar_context, entry_features
from .analysis.learning import Learner
from .analysis import plans as plan_mod, sr_mtf, newsimpact

ENTRIES_PATH = LOG_DIR / "entries.jsonl"      # alasan tiap entry, dibaca dashboard web
META_PATH = LOG_DIR / "trades_meta.csv"        # profil & kombinasi skill tiap tiket tertutup (evaluasi)

log = setup_logging()
MODE_NAMES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}


# ================================================================ util
def keep_awake(mode):
    """Cegah Windows idle-sleep / Modern Standby selama bot jalan (diagnosis 25 Sep: proses bot
    mati saat laptop standby). 'display' = sistem + layar tetap menyala (paling andal di Modern
    Standby), 'system' = sistem saja, 'off' = tidak diatur. Menutup lid / Sleep manual tetap menidurkan."""
    try:
        import ctypes
        flags = 0x80000000                                    # ES_CONTINUOUS
        if mode in ("system", "display"):
            flags |= 0x00000001                               # ES_SYSTEM_REQUIRED
        if mode == "display":
            flags |= 0x00000002                               # ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        return True
    except Exception:
        return False


def utcnow():
    return datetime.now(timezone.utc)


def cli_symbols():
    """--symbols XAUUSD,BTCUSD  atau  --symbols=XAUUSD,BTCUSD  -> daftar simbol, atau None."""
    for i, a in enumerate(sys.argv):
        if a.startswith("--symbols="):
            v = a.split("=", 1)[1]
        elif a == "--symbols" and i + 1 < len(sys.argv):
            v = sys.argv[i + 1]
        else:
            continue
        return [s.strip() for s in v.replace(";", ",").split(",") if s.strip()]
    return None


def pid_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return code.value == 259
    except Exception:
        return False


def acquire_lock():
    # pembuatan file EKSKLUSIF (O_EXCL) = atomik: dua bot yang start di detik yang sama tidak bisa sama2 lolos
    # (28 Sep: cek-lalu-tulis biasa sempat meloloskan 2 bot sekaligus)
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                old = int(LOCK_PATH.read_text().strip())
            except Exception:
                old = None
            if old and old != os.getpid() and pid_alive(old):
                if "--watchdog" in sys.argv:
                    sys.exit(0)          # watchdog Scheduled Task: bot sudah jalan -> keluar diam-diam
                log.error(f"LOCK   bot lain masih jalan (PID {old}). Keluar.")
                sys.exit(1)
            LOCK_PATH.unlink(missing_ok=True)       # lock basi (proses sudah mati) -> coba sekali lagi
    log.error("LOCK   gagal membuat bot.lock. Keluar.")
    sys.exit(1)


def release_lock():
    try:
        if LOCK_PATH.exists() and LOCK_PATH.read_text().strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except Exception:
        pass


def load_state():
    try:
        return json.loads(BOTSTATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def journal_append(mode, c):
    new = not JOURNAL_PATH.exists()
    with open(JOURNAL_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["close_time_utc", "mode", "ticket", "symbol", "dir", "volume",
                        "open", "close", "pnl", "why"])
        w.writerow([datetime.fromtimestamp(c["close_time"], timezone.utc).isoformat(timespec="seconds"),
                    mode, c["ticket"], c["symbol"], "BUY" if c["dir"] == 1 else "SELL", c["volume"],
                    c["open"], c["close"], round(c["pnl"], 2), c["why"]])


_stats_cache = {}


def journal_stats(mode, day):
    """Win rate & PF berjalan dari jurnal (semua trade mode ini, dan hari ini).
    Di-cache per waktu-modifikasi file — dipanggil tiap detik oleh write_state."""
    empty = {"trades": 0, "wins": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0}
    if not JOURNAL_PATH.exists():
        return empty, dict(empty), {}
    key = (JOURNAL_PATH.stat().st_mtime, mode, day)
    if key not in _stats_cache:
        _stats_cache.clear()
        _stats_cache[key] = _journal_stats(mode, day, empty)
    return _stats_cache[key]


def _journal_stats(mode, day, empty):
    try:
        j = pd.read_csv(JOURNAL_PATH)
    except Exception:
        return empty, dict(empty), {}
    j = j[j["mode"] == mode]

    def st(x):
        if x.empty:
            return dict(empty)
        w, lo = x.pnl[x.pnl > 0].sum(), -x.pnl[x.pnl <= 0].sum()
        return {"trades": int(len(x)), "wins": int((x.pnl > 0).sum()),
                "win_rate": round((x.pnl > 0).mean() * 100, 1),
                "pf": round(w / lo, 2) if lo else (99.0 if w else 0.0), "pnl": round(float(x.pnl.sum()), 2)}
    today = j[j.close_time_utc.str[:10] == day]
    per_sym = {sym: {"total": st(j[j.symbol == sym]), "today": st(today[today.symbol == sym])}
               for sym in j.symbol.unique()}
    return st(j), st(today), per_sym


# ================================================================ gerbang keamanan mode
def safety_gate(cfg, acc, active=None):
    """active = simbol yang akan trading (hasil --symbols / file on-off). Mode live menolak jalan
    kalau ada simbol aktif yang belum punya hasil backtest positif — termasuk crypto yang baru ditambah."""
    mode = cfg.get("mode", "paper")
    kind = MODE_NAMES.get(acc.trade_mode, "?")
    if mode not in ("paper", "demo", "live"):
        log.error(f"MODE   '{mode}' tidak dikenal (paper/demo/live)")
        sys.exit(2)
    if mode == "demo" and acc.trade_mode == 2:
        log.error("MODE   config 'demo' tetapi akun yang login adalah REAL. Bot DITOLAK jalan.")
        sys.exit(2)
    if mode == "live":
        if not cfg.get("live_trading_confirmed"):
            log.error("MODE   'live' butuh live_trading_confirmed=true di config (baca README: 'Sebelum mode live').")
            sys.exit(2)
        if acc.trade_mode != 2:
            log.error(f"MODE   'live' tetapi akun yang login adalah {kind}. Pakai mode 'demo' untuk akun demo.")
            sys.exit(2)
        bt = ROOT / "backtest" / "results" / "latest_metrics.json"
        if not bt.exists():
            log.error("MODE   'live' ditolak: belum ada hasil backtest (jalankan backtest dulu).")
            sys.exit(2)
        res = json.loads(bt.read_text(encoding="utf-8"))
        age = (datetime.now() - datetime.fromisoformat(res["generated"])).days
        if age > 30:
            log.error(f"MODE   'live' ditolak: hasil backtest sudah {age} hari. Jalankan ulang backtest.")
            sys.exit(2)
        from .common import config_fingerprint
        if res.get("fingerprint") != config_fingerprint(cfg):
            log.error("MODE   'live' ditolak: config strategi/exit/risiko/SMC/simbol BERBEDA dari yang terakhir "
                      "di-backtest. Jalankan 'start_bot.cmd backtest' dengan config ini dulu.")
            sys.exit(2)
        for sym in (active if active is not None else [s for s, v in cfg["symbols"].items() if v.get("enabled")]):
            m = res["results"].get(sym)
            if not m or m.get("profit_factor", 0) < 1.0:
                log.error(f"MODE   'live' ditolak: {sym} tidak punya hasil backtest positif (PF>=1).")
                sys.exit(2)
            log.warning(f"LIVE   backtest {sym}: {m['trades']} trade, WR {m['win_rate_pct']}%, "
                        f"PF {m['profit_factor']}, maxDD {m['max_drawdown_pct']}%")
        log.warning("LIVE   UANG SUNGGUHAN. Mulai dalam 10 detik — Ctrl+C untuk batal.")
        time.sleep(10)
    return mode


# ================================================================ bot
class Bot:
    def __init__(self):
        self.cfg = load_config()
        self.conn = Connection(self.cfg.get("mt5", {}))
        self.news = NewsFilter()
        self.st = load_state()
        self.sym_status = {}
        self.last_scan = 0.0
        self.last_state_write = 0.0
        self.last_status_log = 0.0
        self.halt_reason = ""
        self.news_state = (False, "")
        self.day_pnl = 0.0
        self.forced_scan = None        # hasil tombol "Aktifkan Burst Sekarang" terakhir
        self._last_loop = 0.0          # waktu iterasi loop terakhir (deteksi standby)
        self.intra = {}
        self._budget_fit = {}
        self.level_cards = {}           # kartu Saran Level per simbol (panel sidebar)
        self.cand_plans = {}            # rencana posisi kandidat (MENUNGGU/DITOLAK) per simbol, dihitung tiap scan
        self._news_adj = {}             # blackout berita mode 'reduce': sym -> (lot_mult, sl_mult)           # tiket burst yang masih muat anggaran rugi harian, per simbol                # sinyal candle berjalan: sym -> (bar_t, arah, pertama terlihat)
        self._burst_off_prev = None
        self.news_engine = None
        self.learner = Learner(self.cfg.get("learning", {}), LOG_DIR / "learning_dataset.csv",
                               ROOT / "backtest" / "results" / "learning_seed.csv",
                               LOG_DIR / "learning_model.json", LOG_DIR / "learning_report.json", log)

    # ---------------------------------------------------------- setup
    def start(self):
        acquire_lock()
        for f, why in ((FLAG_STOP, "EMERGENCY STOP"), (FLAG_HALT, "HALT max drawdown")):
            if f.exists() and "--watchdog" in sys.argv:
                release_lock()
                sys.exit(0)          # watchdog tidak boleh menghidupkan bot yang sengaja dihentikan
            if f.exists():
                log.error(f"START  ditolak: file {f.name} ada ({why} sebelumnya). "
                          f"Periksa dulu penyebabnya, lalu hapus lewat menu 'Reset' / start_bot.ps1 -Action Reset.")
                release_lock()
                sys.exit(3)
        while not self.conn.connect():
            log.warning("MT5    belum bisa tersambung (terminal tertutup / belum login?) — coba lagi 30 detik")
            time.sleep(30)
        acc = mt5.account_info()
        self.broker_info = detect_broker()
        if self.broker_info:
            apply_broker_profile(self.cfg, self.broker_info)
        # on/off per simbol: --symbols menang; tanpa flag = semua yang enabled di config
        want = cli_symbols()
        if want is not None:
            unknown = [s for s in want if s not in self.cfg["symbols"]]
            if unknown:
                log.error(f"--symbols: {', '.join(unknown)} tidak ada di config symbols "
                          f"(tersedia: {', '.join(self.cfg['symbols'])})")
                release_lock()
                sys.exit(2)
            act = {s: s in want for s in self.cfg["symbols"]}
        elif "--watchdog" in sys.argv or "--keep-symbols" in sys.argv:
            act = read_active(self.cfg)      # watchdog menghidupkan ulang: pertahankan pilihan terakhir (switch / -Symbols)
        else:
            act = {s: bool(v.get("enabled")) for s, v in self.cfg["symbols"].items()}
        write_active(act)
        self.mode = safety_gate(self.cfg, acc, [s for s, on in act.items() if on])
        self.load_control()
        magic, tag = self.cfg["magic"], self.cfg.get("comment_tag", "xau-scalp")
        if self.mode == "paper":
            self.ex = PaperExec(magic, tag, lambda s: self.cfg["symbols"].get(s, {}).get("commission_price_units", 0.0))
            self.ex.init_balance(acc.equity)
        else:
            self.ex = LiveExec(magic, tag)
            self.ex.poll_closed()      # isi daftar posisi yang sudah ada
        self.news_engine = NewsTradingEngine(self.cfg, self.ex)
        if self.st.get("mode") != self.mode:
            self.st = {"mode": self.mode}      # equity paper vs akun berbeda -> jangan campur baseline
        self.lock_open_positions()
        self.migrate_meta_csv()
        self.symbols = self.resolve_symbols()
        log.info("=" * 78)
        log.info(f"XAUUSD_EXNESS BOT   mode {self.mode.upper()}   magic {magic}")
        log.info(f"Akun #{acc.login} {acc.server} {acc.currency}  {MODE_NAMES.get(acc.trade_mode)}  "
                 f"equity {acc.equity:,.2f}  leverage 1:{acc.leverage}")
        rk = self.cfg["risk"]
        log.info(f"Risiko {rk['risk_per_trade_pct']}%/trade  rugi harian maks {rk['daily_loss_limit_pct']}%  "
                 f"DD maks {rk['max_drawdown_pct']}%  posisi maks {rk['max_positions_total']}  burst {rk['burst_size']}")
        on = [s for s in self.symbols if self.is_active(s)]
        log.info(f"Simbol trading: {', '.join(on) or '-'}   |   dimuat (posisi tetap dikelola, bisa "
                 f"dinyalakan dari dashboard): {', '.join(s for s in self.symbols if s not in on) or '-'}")
        for s in on:
            c, sc = self.C(s), self.cfg["symbols"][s]
            log.info(f"  {s:<10} grup {risk_group(self.cfg, s):<7} TF {sc.get('timeframe', 'M5')}  "
                     f"{c['risk']['risk_per_trade_pct']}%/tiket x{c['risk']['burst_size']}  SL {c['exit']['sl_atr']}xATR  "
                     f"trail {c['exit']['trail_atr']}xATR  hari {sc.get('trade_days', 'semua')}")
        log.info("=" * 78)
        if not self.symbols:
            log.error("Tidak ada simbol config yang tersedia di akun ini.")
            sys.exit(2)
        if not on:
            log.warning("Belum ada simbol yang dinyalakan — nyalakan dari dashboard (switch per simbol).")

    def resolve_symbols(self):
        """SEMUA simbol config yang ada di akun dimuat (supaya bisa dinyalakan dari dashboard tanpa restart
        dan posisinya tetap dikelola); boleh-tidaknya trading ditentukan is_active()."""
        ok = []
        for sym, sc in self.cfg["symbols"].items():
            if not mt5.symbol_select(sym, True) or mt5.symbol_info(sym) is None:
                if self.is_active(sym):
                    log.warning(f"SIMBOL {sym} tidak ada di akun ini — dilewati "
                                f"(XAUUSDc hanya ada di akun Cent)")
                continue
            ok.append(sym)
        return ok

    def C(self, sym):
        """Config efektif simbol = sym_cfg (override per simbol) + profil aktif simbol itu + skill ON."""
        return prof.apply(sym_cfg(self.cfg, sym), self.cfg, prof.profile_of(self.ctl, sym), self.ctl["skills"])

    def override(self, sym):
        """Mode simbol: {} (OTOMATIS) / RUN / BLOCK. TETAP RUN yang kedaluwarsa kembali OTOMATIS (dicatat)."""
        ov = self.ctl.get("override", {}).get(sym) or {}
        if ov.get("mode") == "RUN" and ov.get("until") and time.time() >= ov["until"]:
            self.ctl["override"].pop(sym, None)
            prof.write_control(self.ctl)
            self.log_switch(f"mode {sym}", "RUN", "AUTO", "kedaluwarsa", "OK")
            return {}
        return ov

    def skill(self, sid):
        return bool(self.ctl["skills"].get(sid))

    def load_control(self):
        """Profil & skill dari logs/control_state.json (+ --profile dari CMD). AGGRESSIVE ditolak di live."""
        self.ctl = prof.read_control(self.cfg)
        want = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--profile" and i + 1 < len(sys.argv)),
                    next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--profile=")), None))
        if want:
            want = want.upper()
            if want not in prof.NAMES:
                log.error(f"--profile {want} tidak dikenal ({', '.join(prof.NAMES)})")
                sys.exit(2)
            if prof.live_blocked(self.cfg, self.mode, want):
                log.error("PROFIL AGGRESSIVE DITOLAK di mode LIVE (butuh risk_profiles.allow_aggressive_live=true)")
                sys.exit(2)
            if want != self.ctl["profile"]["global"]:
                self.log_switch("profil global", self.ctl["profile"]["global"], want, "CMD", "OK")
            self.ctl["profile"]["global"] = want
        for scope, name in [("global", self.ctl["profile"]["global"])] + list(self.ctl["profile"]["per_symbol"].items()):
            if prof.live_blocked(self.cfg, self.mode, name):
                log.error(f"PROFIL AGGRESSIVE ({scope}) DITOLAK di mode LIVE — memakai NORMAL")
                if scope == "global":
                    self.ctl["profile"]["global"] = "NORMAL"
                else:
                    self.ctl["profile"]["per_symbol"].pop(scope)
        prof.write_control(self.ctl)

    @staticmethod
    def migrate_meta_csv():
        """trades_meta.csv lama tidak punya kolom 'entry' -> tambahkan (baris lama = '?': jenis entri tidak tercatat)."""
        try:
            if META_PATH.exists():
                lines = META_PATH.read_text(encoding="utf-8").splitlines()
                if lines and not lines[0].rstrip().endswith(",entry"):
                    lines = [lines[0] + ",entry"] + [ln + ",?" for ln in lines[1:]]
                    META_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            log.warning(f"META   migrasi trades_meta.csv gagal: {e}")

    def lock_open_positions(self):
        """Posisi yang sudah terbuka sebelum ada profil: kunci parameter lamanya (NORMAL = nilai dasar lama)."""
        snaps = self.st.setdefault("ticket_exit", {})
        for p in self.ex.positions():
            if str(p.ticket) not in snaps and p.symbol in self.cfg["symbols"]:
                snaps[str(p.ticket)] = dict(prof.apply(sym_cfg(self.cfg, p.symbol), self.cfg, "NORMAL", sk.defaults())["exit"],
                                            trailing=True)

    def log_switch(self, what, old, new, source, ack):
        msg = f"{what}: {old} -> {new}  sumber={source}  ACK={ack}"
        log.warning(f"KONTROL {msg}")
        audit(msg, source.lower()[:8])

    def process_control(self):
        """Perintah profil/skill dari dashboard/CMD -> validasi -> terapkan -> ACK (profil yang benar2 aktif)."""
        for f, cmd in prof.pending_commands():
            ack, old, new = prof.handle(cmd, self.cfg, self.mode, bool(self.conn.ok) and not self.simulate_down())
            f.unlink(missing_ok=True)
            write_json_atomic(prof.CMD_DIR / f"{cmd['id']}.ack.json", ack)
            if cmd["kind"] == "override":
                o = old["override"].get(cmd.get("symbol"), {}).get("mode", "AUTO")
                self.log_switch(f"mode {cmd.get('symbol')}", o, cmd.get("mode"), cmd.get("source", "?"),
                                ("OK" if ack["ok"] else "DITOLAK: " + ack["msg"]) + (f" | {ack['msg']}" if ack["ok"] else ""))
                if ack["ok"]:
                    self.ctl = new
                continue
            what = (f"profil {cmd.get('symbol') or 'global'}" if cmd["kind"] == "profile" else f"skill {cmd.get('skill')}")
            before = (old["profile"]["per_symbol"].get(cmd.get("symbol"), "default") if cmd.get("symbol")
                      else old["profile"]["global"]) if cmd["kind"] == "profile" else ("ON" if old["skills"].get(cmd.get("skill")) else "OFF")
            after = cmd.get("profile") if cmd["kind"] == "profile" else ("ON" if cmd.get("on") else "OFF")
            self.log_switch(what, before, after, cmd.get("source", "?"), ("OK" if ack["ok"] else "DITOLAK: " + ack["msg"])
                            + ("; " + " | ".join(ack["warnings"]) if ack["warnings"] else ""))
            if ack["ok"]:
                self.ctl = new
        for f in prof.CMD_DIR.glob("*.ack.json"):            # bersihkan ACK lama
            if time.time() - f.stat().st_mtime > 600:
                f.unlink(missing_ok=True)

    def simulate_down(self):
        """Uji perilaku 'MT5/bridge terputus' tanpa memutus MT5: file logs/SIMULATE_MT5_DOWN, HANYA mode paper."""
        return self.mode == "paper" and (LOG_DIR / "SIMULATE_MT5_DOWN").exists()

    def is_active(self, sym):
        """Trading simbol ini sedang ON? (switch dashboard / --symbols; dibaca ulang tiap scan)."""
        if getattr(self, "_act_ts", 0) < time.time() - 1:
            self._act, self._act_ts = read_active(self.cfg), time.time()
        return self._act.get(sym, False)

    # ---------------------------------------------------------- harian & rem
    def equity(self):
        acc = mt5.account_info()
        return self.ex.equity(acc) if acc else (None, None)

    def roll_day(self, eq):
        today = utcnow().date().isoformat()     # hari server = UTC
        if self.st.get("day") != today:
            self.st.update(day=today, day_start_equity=eq, day_closed_pnl=0.0, day_halted=False)
            log.info(f"HARI   {today}  baseline equity {eq:,.2f}")
        self.st.setdefault("peak_equity", eq)
        self.st["peak_equity"] = max(self.st["peak_equity"], eq)

    def close_all(self, why):
        for p in self.ex.positions():
            if self.ex.close(p, why):
                log.warning(f"TUTUP  {p.symbol} #{p.ticket} ({why})  P/L {p.profit:+,.2f}")

    def check_guards(self, eq):
        rk = self.cfg["risk"]
        acc = mt5.account_info() if self.mode != "paper" else None
        if acc and self.st.get("account_login") != acc.login:
            log.info(f"AKUN   Berganti login ke #{acc.login} ({acc.server}). Reset baseline equity ke {acc.equity:,.2f}.")
            self.st["account_login"] = acc.login
            self.st["peak_equity"] = acc.equity
            self.st["day_start_equity"] = acc.equity
            self.st["day_closed_pnl"] = 0.0
            self.st["day_halted"] = False
            FLAG_HALT.unlink(missing_ok=True)
        own_float = sum(p.profit for p in self.ex.positions())
        day_pnl = self.st["day_closed_pnl"] + own_float
        self.day_pnl = day_pnl
        limit = self.st["day_start_equity"] * rk["daily_loss_limit_pct"] / 100
        if not self.st.get("day_halted") and rk["daily_loss_limit_pct"] > 0 and day_pnl <= -limit:
            self.st["day_halted"] = True
            log.error(f"REM    RUGI HARIAN {day_pnl:+,.2f} <= -{limit:,.2f} ({rk['daily_loss_limit_pct']}%). "
                      f"Semua posisi ditutup, entry berhenti sampai besok (UTC).")
            self.close_all("dailyloss")
        peak = self.st["peak_equity"]
        dd = (peak - eq) / peak * 100 if peak else 0
        if rk["max_drawdown_pct"] > 0 and dd >= rk["max_drawdown_pct"] and not FLAG_HALT.exists():
            log.critical(f"HALT   DRAWDOWN {dd:.2f}% dari puncak {peak:,.2f} >= {rk['max_drawdown_pct']}%. "
                         f"Semua posisi ditutup, bot berhenti total sampai logs\\HALT dihapus manual.")
            self.close_all("maxdd")
            FLAG_HALT.write_text(f"{utcnow().isoformat()} drawdown {dd:.2f}%", encoding="utf-8")
        return day_pnl, dd

    def pause_until(self, group=None):
        """Jeda kalah beruntun berlaku PER GRUP risiko (kalah beruntun di BTC tidak menjeda emas).
        group=None -> jeda terlama di grup mana pun (untuk ringkasan dashboard)."""
        pg = self.st.get("pause_group", {})
        return pg.get(group, 0) if group else max(pg.values(), default=0)

    def record_closed(self):
        for c in self.ex.poll_closed():
            grp = risk_group(self.cfg, c["symbol"])
            rk = self.C(c["symbol"])["risk"]
            journal_append(self.mode, c)
            # evaluasi per profil & per kombinasi skill (logs/trades_meta.csv)
            meta = self.st.get("ticket_meta", {}).pop(str(c["ticket"]), None) or {}
            self.st.get("ticket_exit", {}).pop(str(c["ticket"]), None)
            self.st.get("runner_tickets", {}).pop(str(c["ticket"]), None)
            new = not META_PATH.exists()
            with open(META_PATH, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["close_time_utc", "mode", "symbol", "ticket", "profile", "skills", "pnl", "duration_min", "entry"])
                w.writerow([datetime.fromtimestamp(c["close_time"], timezone.utc).isoformat(timespec="seconds"), self.mode,
                            c["symbol"], c["ticket"], meta.get("profile", "sebelum-profil"), meta.get("skills", "-"),
                            round(c["pnl"], 2), round((c["close_time"] - c["open_time"]) / 60, 1), meta.get("entry", "?")])
            # disimpan di bot_state.json supaya marker posisi tertutup tetap ada walau bot di-restart
            linger = self.cfg.get("dashboard", {}).get("closed_marker_minutes", 10) * 60
            rc = self.st.setdefault("recent_closed", [])
            rc.append({k: c[k] for k in ("ticket", "symbol", "dir", "volume", "open", "close", "pnl", "why",
                                          "open_time", "close_time")})
            self.st["recent_closed"] = [x for x in rc if time.time() - x["close_time"] <= linger][-40:]
            self.st["day_closed_pnl"] = self.st.get("day_closed_pnl", 0.0) + c["pnl"]
            log.info(f"CLOSED {c['symbol']} #{c['ticket']} {'BUY' if c['dir'] == 1 else 'SELL'} "
                     f"{c['volume']} lot  {c['open']} -> {c['close']}  P/L {c['pnl']:+,.2f}  ({c['why']})")
            # tiket burst dihitung SATU hasil (jumlah P/L semua tiketnya) saat tiket terakhir tutup,
            # supaya 1 burst 3 tiket yang kalah tidak langsung dihitung 3 kalah beruntun
            key = self.st.setdefault("ticket_burst", {}).pop(str(c["ticket"]), None)
            b = self.st.setdefault("bursts", {}).get(key) if key else None
            if b is not None:
                b["left"] -= 1
                b["pnl"] += c["pnl"]
                if b["left"] > 0:
                    continue
                outcome = b["pnl"]
                del self.st["bursts"][key]
                pl = self.st.get("plans", {}).get(key)
                if pl:
                    pl.update(status="SELESAI", closed_at=time.time(), result=round(outcome, 2))
                if b["n"] > 1:
                    log.info(f"BURST  {key} selesai: {b['n']} tiket, total P/L {outcome:+,.2f}")
                info = self.st.setdefault("entry_info", {}).pop(key, None)
                if info and info.get("feats") and self.skill("learning") and self.cfg.get("learning", {}).get("enabled", True):
                    R = outcome / info["risk_money"] if info.get("risk_money") else 0.0
                    dur = (time.time() - info["open_ts"]) / 60
                    self.learner.record(info["feats"], info["symbol"], info["dir"], R, outcome, dur,
                                        self.mode, info["open_time"])
            else:
                outcome = c["pnl"]
            cl = self.st.setdefault("consec_group", {})
            cl[grp] = cl.get(grp, 0) + 1 if outcome < 0 else 0
            if rk["max_consecutive_losses"] and cl[grp] >= rk["max_consecutive_losses"]:
                self.st.setdefault("pause_group", {})[grp] = time.time() + rk["loss_pause_minutes"] * 60
                cl[grp] = 0
                log.warning(f"REM    grup {grp}: {rk['max_consecutive_losses']} kalah beruntun — jeda entry "
                            f"{rk['loss_pause_minutes']} menit (grup lain tetap jalan)")

    # ---------------------------------------------------------- trailing adaptif
    def manage(self):
        for p in self.ex.positions():
            sc = self.cfg["symbols"].get(p.symbol, {})
            C = self.C(p.symbol) if p.symbol in self.cfg["symbols"] else self.cfg
            # posisi dikelola dengan parameter SAAT DIBUKA (ganti profil/skill hanya berlaku untuk entry baru)
            snap = self.st.get("ticket_exit", {}).get(str(p.ticket))
            if not snap:
                is_smc = "smc" in getattr(p, "comment", "").lower()
                snap = dict(C["exit"], trailing=self.skill("trailing"), is_smc=is_smc)
                self.st.setdefault("ticket_exit", {})[str(p.ticket)] = snap
            ex = snap or C["exit"]
            tf = "M3" if (ex.get("is_smc") or "smc" in getattr(p, "comment", "").lower()) else sc.get("timeframe", "M5")
            info, t = mt5.symbol_info(p.symbol), mt5.symbol_info_tick(p.symbol)
            if not info or not t:
                continue
            if ex.get("max_hold_bars") and time.time() - p.time > ex["max_hold_bars"] * TF_SEC[tf]:
                if self.ex.close(p, "waktu"):
                    log.info(f"TUTUP  {p.symbol} #{p.ticket} batas waktu {ex['max_hold_bars']} bar")
                continue
            if snap and not snap.get("trailing", True):
                continue                       # dibuka saat skill Trailing OFF -> hanya SL awal
            r = mt5.copy_rates_from_pos(p.symbol, TF[tf], 0, 120)
            if r is None or len(r) < 50:
                continue
            a = float(atr_fn(pd.DataFrame(r), C["strategy"].get("atr_period", 14)).iloc[-1])  # ATR realtime
            cur = t.bid if p.dir == 1 else t.ask
            fav = (cur - p.price_open) * p.dir
            new = p.sl
            stage = ""
            if fav >= ex["be_trigger_atr"] * a:
                c = p.price_open + p.dir * ex["be_lock_atr"] * a
                if (c - new) * p.dir > 0 or new == 0:
                    new, stage = c, "BE-LOCK"
            if fav >= ex["trail_start_atr"] * a:
                tight = fav >= ex["trail_tight_after_atr"] * a
                k = ex["trail_tight_atr"] if tight else ex["trail_atr"]
                c = cur - p.dir * k * a
                if (c - new) * p.dir > 0:
                    new, stage = c, ("KETAT" if tight else "TRAIL")
            # Dark Venus TP: close saat harga menyentuh garis tengah Bollinger (BB Middle Band)
            # Ditambah fitur HYBRID SPLIT EXIT: tiket reguler TP di Mid, tiket runner SL+ di Mid & kejar Opposite Band
            if ex.get("close_on_bb_mid") and len(r) >= 25:
                bb_n = int(ex.get("bb_period", 20))
                bb_dev = float(ex.get("bb_dev", 2.0))
                closes = [float(x["close"]) for x in r[-bb_n:]]
                mid_val = sum(closes) / len(closes)
                variance = sum((x - mid_val) ** 2 for x in closes) / len(closes)
                std_dev = variance ** 0.5
                upper_val = mid_val + bb_dev * std_dev
                lower_val = mid_val - bb_dev * std_dev

                same_pos = [pos for pos in self.ex.positions() if pos.symbol == p.symbol and pos.dir == p.dir]
                use_runner = bool(ex.get("bb_mid_runner", True))
                runner_map = self.st.setdefault("runner_tickets", {})
                sorted_tickets = [pos.ticket for pos in sorted(same_pos, key=lambda x: x.ticket)]
                r_count = int(ex.get("bb_mid_runner_count", 1))
                single_as_runner = bool(ex.get("single_ticket_runner", True))
                if len(sorted_tickets) > 1:
                    runner_tickets_set = set(sorted_tickets[-r_count:])
                elif len(sorted_tickets) == 1 and single_as_runner:
                    runner_tickets_set = set(sorted_tickets)
                else:
                    runner_tickets_set = set()
                is_runner = use_runner and ((p.ticket in runner_tickets_set) or str(p.ticket) in runner_map)
                if is_runner:
                    runner_map[str(p.ticket)] = True

                if not is_runner:
                    if p.dir == 1 and cur >= mid_val and (cur - p.price_open) > 0:
                        if self.ex.close(p, "bb_mid"):
                            log.info(f"TUTUP  {p.symbol} #{p.ticket} TP BB Mid tercapai @ {cur:.2f} >= {mid_val:.2f}")
                            continue
                    elif p.dir == -1 and cur <= mid_val and (p.price_open - cur) > 0:
                        if self.ex.close(p, "bb_mid"):
                            log.info(f"TUTUP  {p.symbol} #{p.ticket} TP BB Mid tercapai @ {cur:.2f} <= {mid_val:.2f}")
                            continue
                else:
                    if p.dir == 1 and cur >= upper_val and (cur - p.price_open) > 0:
                        if self.ex.close(p, "bb_opposite"):
                            runner_map.pop(str(p.ticket), None)
                            log.info(f"TUTUP  {p.symbol} #{p.ticket} TP RUNNER Upper Band tercapai @ {cur:.2f} >= {upper_val:.2f} (profit {cur - p.price_open:+.2f} pts!)")
                            continue
                    elif p.dir == -1 and cur <= lower_val and (p.price_open - cur) > 0:
                        if self.ex.close(p, "bb_opposite"):
                            runner_map.pop(str(p.ticket), None)
                            log.info(f"TUTUP  {p.symbol} #{p.ticket} TP RUNNER Lower Band tercapai @ {cur:.2f} <= {lower_val:.2f} (profit {p.price_open - cur:+.2f} pts!)")
                            continue

                    buf = float(ex.get("bb_mid_runner_buffer_atr", 0.2)) * a
                    if p.dir == 1 and cur >= mid_val:
                        target_sl = round(mid_val - buf, info.digits)
                        be_sl = round(p.price_open + max(info.point * 15, 0.1 * a), info.digits)
                        if (cur - p.price_open) > 0:
                            target_sl = max(target_sl, be_sl)
                        if target_sl > p.price_open and (p.sl is None or target_sl > p.sl):
                            if self.ex.modify_sl(p, target_sl):
                                log.info(f"TRAIL  {p.symbol} #{p.ticket} SL+MID  SL {p.sl} -> {target_sl} (Runner terlindungi BE/SL+ mengejar Upper {upper_val:.2f})")
                                new = target_sl
                    elif p.dir == -1 and cur <= mid_val:
                        target_sl = round(mid_val + buf, info.digits)
                        be_sl = round(p.price_open - max(info.point * 15, 0.1 * a), info.digits)
                        if (p.price_open - cur) > 0:
                            target_sl = min(target_sl, be_sl)
                        if target_sl < p.price_open and (p.sl is None or target_sl < p.sl or p.sl == 0):
                            if self.ex.modify_sl(p, target_sl):
                                log.info(f"TRAIL  {p.symbol} #{p.ticket} SL+MID  SL {p.sl} -> {target_sl} (Runner terlindungi BE/SL+ mengejar Lower {lower_val:.2f})")
                                new = target_sl

            # trailing ikut candle: SL ke low (BUY) / high (SELL) candle yang BARU tertutup, tidak pernah mundur
            if ex.get("candle_trail") and fav >= ex.get("candle_trail_after_atr", ex["be_trigger_atr"]) * a:
                buf = ex.get("candle_trail_buffer_atr", 0.1) * a
                prev = r[-2]
                c = float(prev["low"]) - buf if p.dir == 1 else float(prev["high"]) + (t.ask - t.bid) + buf
                if (c - new) * p.dir > 0:
                    new, stage = c, "CANDLE"
            if not stage:
                continue
            min_gap = max(info.trade_stops_level, 1) * info.point
            step = max(0.05 * a, 10 * info.point)          # jangan spam modifikasi kecil
            if abs(cur - new) < min_gap or (p.sl and (new - p.sl) * p.dir < step):
                continue
            new, old = round(new, info.digits), p.sl
            if self.ex.modify_sl(p, new):
                log.info(f"TRAIL  {p.symbol} #{p.ticket} {stage:<7} SL {old} -> {new}  "
                         f"(profit {fav:+.2f}, ATR {a:.2f}, jarak {abs(cur - new):.2f})")

        # ---------------------------------------------------------- kelola pending orders (SMC TTL & invalidasi)
        for po in self.ex.pending_orders():
            C = self.C(po.symbol) if po.symbol in self.cfg["symbols"] else self.cfg
            ttl_bars = int(C["profile"].get("m3_fvg_ttl_bars", 10)) if hasattr(C["profile"], "get") else 10
            ttl_sec = ttl_bars * 3 * 60
            t = mt5.symbol_info_tick(po.symbol)
            po_time = getattr(po, "time_setup", getattr(po, "time", 0))
            if getattr(self, "mode", "") == "paper":
                age_sec = time.time() - po_time
            else:
                age_sec = (t.time - po_time) if (t and po_time) else 0

            invalidated = False
            if t and po.tp:
                otype = getattr(po, "order_type", getattr(po, "type", 0))
                # BUY_LIMIT (2): jika harga sudah menembus TP tanpa jemput limit
                if otype in (2, 4) and t.bid >= po.tp:
                    invalidated = True
                # SELL_LIMIT (3): jika harga sudah menembus TP tanpa jemput limit
                elif otype in (3, 5) and t.ask <= po.tp:
                    invalidated = True

            if age_sec > ttl_sec or invalidated:
                why = f"TTL habis ({int(age_sec//60)} mnt > {int(ttl_sec//60)} mnt)" if age_sec > ttl_sec else "harga tembus target sebelum limit tersentuh"
                if self.ex.cancel_pending(po.ticket):
                    log.info(f"BATAL  Pending order #{po.ticket} {po.symbol} dibatalkan: {why}")
                    self.st.get("ticket_exit", {}).pop(str(po.ticket), None)

    # ---------------------------------------------------------- lot
    def multiply(self, sym=None):
        rk = self.C(sym)["risk"] if sym else self.cfg["risk"]
        return rk.get("burst_mode", "split") == "multiply"

    def sl_distance(self, sym, a, price):
        """Jarak SL = sl_atr x ATR simbol, dijepit ke [sl_min_pct, sl_max_pct] % harga kalau diset di
        symbols.<sym> (crypto: ATR sesekali melonjak/menciut ekstrem). Emas: tanpa jepit = seperti dulu."""
        sc, ex = self.cfg["symbols"][sym], self.C(sym)["exit"]
        d = ex["sl_atr"] * a
        if sc.get("sl_min_pct"):
            d = max(d, price * sc["sl_min_pct"] / 100)
        if sc.get("sl_max_pct"):
            d = min(d, price * sc["sl_max_pct"] / 100)
        return d

    def tp_distance(self, sym, a, price):
        sc, ex = self.cfg["symbols"][sym], self.C(sym)["exit"]
        if not ex.get("tp_atr"):
            return 0.0
        d = ex["tp_atr"] * a
        if sc.get("tp_min_pct"):
            d = max(d, price * sc["tp_min_pct"] / 100)
        if sc.get("tp_max_pct"):
            d = min(d, price * sc["tp_max_pct"] / 100)
        return d

    def calc_lot(self, sym, sl_dist, eq, n_orders):
        """split    : target risiko dibagi ke n tiket (total = risk_per_trade_pct)
        multiply : TIAP tiket membawa risk_per_trade_pct (total = n x)
        Nilai kontrak dibaca dari broker (value_per_price_unit): emas 100 oz/lot, ETH/BTC 1 koin/lot."""
        rk, sc = self.C(sym)["risk"], self.cfg["symbols"][sym]
        info = mt5.symbol_info(sym)
        vpp = value_per_price_unit(sym)
        if vpp <= 0 or sl_dist <= 0:
            return None
        target = (eq * rk["risk_per_trade_pct"] / 100 * sc.get("risk_mult", 1.0) * self.override(sym).get("lot_mult", 1.0)
                  * self._news_adj.get(sym, (1.0, 1.0))[0])
        per_ticket = target if self.multiply(sym) else target / n_orders
        raw = per_ticket / (sl_dist * vpp)
        step = info.volume_step
        vmin = max(info.volume_min, sc.get("lot_min_override", 0) or 0)
        vmax = min(info.volume_max, sc.get("lot_max_override", 0) or info.volume_max)
        lot = min(max(np.floor(raw / step + 1e-9) * step, vmin), vmax)
        lot = round(lot, 8)
        real = sl_dist * vpp * lot                            # risiko riil per tiket
        self.last_burst_risk_pct = real * (n_orders if self.multiply(sym) else 1) / eq * 100
        if real > per_ticket * rk["max_risk_overshoot_mult"]:
            return None, f"lot min {vmin} = risiko {real / eq * 100:.2f}%/tiket (> batas)"
        # Validasi margin riil berdasarkan leverage akun broker
        try:
            t = mt5.symbol_info_tick(sym)
            if t and t.ask > 0:
                m_req = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, lot, t.ask)
                acc_info = mt5.account_info()
                if m_req and acc_info and acc_info.margin_free > 0 and (m_req * n_orders) > acc_info.margin_free * 0.9:
                    return None, f"margin tidak cukup: butuh ${m_req * n_orders:.1f} > free margin ${acc_info.margin_free:.1f} (leverage 1:{acc_info.leverage})"
        except Exception:
            pass
        return lot, (f"risiko {real / eq * 100:.2f}%/tiket, total {real * n_orders / eq * 100:.2f}%"
                     if n_orders > 1 else f"risiko {real / eq * 100:.2f}%")

    def calc_lot_smc(self, sym, sl_dist, eq, burst_size=1):
        """Opsi 3: 1 tiket limit order membawa 100% alokasi risiko burst penuh.
        Jika burst_mode multiply -> target x burst_size; jika split -> target."""
        rk, sc = self.C(sym)["risk"], self.cfg["symbols"][sym]
        info = mt5.symbol_info(sym)
        vpp = value_per_price_unit(sym)
        if vpp <= 0 or sl_dist <= 0:
            return None
        target = (eq * rk["risk_per_trade_pct"] / 100 * sc.get("risk_mult", 1.0) * self.override(sym).get("lot_mult", 1.0)
                  * self._news_adj.get(sym, (1.0, 1.0))[0])
        total_target = target * (burst_size if self.multiply(sym) else 1)
        raw = total_target / (sl_dist * vpp)
        step = info.volume_step
        vmin = max(info.volume_min, sc.get("lot_min_override", 0) or 0)
        vmax = min(info.volume_max, sc.get("lot_max_override", 0) or info.volume_max)
        lot = min(max(np.floor(raw / step + 1e-9) * step, vmin), vmax)
        lot = round(lot, 8)
        real = sl_dist * vpp * lot
        self.last_burst_risk_pct = real / eq * 100
        if real > total_target * rk["max_risk_overshoot_mult"]:
            return None, f"lot min {vmin} = risiko {real / eq * 100:.2f}% (> batas)"
        # Validasi margin riil berdasarkan leverage akun broker
        try:
            t = mt5.symbol_info_tick(sym)
            if t and t.ask > 0:
                m_req = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, lot, t.ask)
                acc_info = mt5.account_info()
                if m_req and acc_info and acc_info.margin_free > 0 and m_req > acc_info.margin_free * 0.9:
                    return None, f"margin tidak cukup: butuh ${m_req:.1f} > free margin ${acc_info.margin_free:.1f} (leverage 1:{acc_info.leverage})"
        except Exception:
            pass
        return lot, f"risiko {real / eq * 100:.2f}% (opsi 3: full risk 1 tiket)"

    def open_risk_pct(self, eq, group=None):
        """Risiko yang masih terbuka (% equity) = jarak entry->SL posisi yang SL-nya belum
        di wilayah untung. Posisi yang sudah terkunci BE/trailing tidak dihitung.
        group = hanya posisi di grup risiko itu (batas max_total_risk_pct dihitung per grup)."""
        tot = 0.0
        for p in self.ex.positions():
            if group and risk_group(self.cfg, p.symbol) != group:
                continue
            gap = max(0.0, p.dir * (p.price_open - p.sl)) if p.sl else 0.0
            tot += gap * value_per_price_unit(p.symbol) * p.volume
        return tot / eq * 100 if eq else 0.0

    # ---------------------------------------------------------- scan & entry
    def scan(self, eq):
        cfg = self.cfg
        now = utcnow()
        self.news_state = self.news.blocked(cfg["news_filter"], now)
        for sym in self.symbols:
            sc = cfg["symbols"][sym]
            if not self.is_active(sym):
                n = len(self.ex.positions(sym))
                self.sym_status[sym] = {"status": "DIHENTIKAN (switch dashboard)" + (f" — {n} posisi tetap dikelola" if n else ""),
                                        "positions": n, "ready": False, "blockers": []}
                continue
            C = self.C(sym)              # parameter efektif simbol ini (override crypto dll.)
            rk, ex, scfg = C["risk"], C["exit"], C["strategy"]
            P = C["profile"]
            # Seleksi strategi: Bollinger Bands (Dark Venus) vs SMC MTF vs Default
            act_strat = (self.ctl.get("active_strategy") or cfg.get("active_strategy") or "").lower()
            if (act_strat in ("bb_reversion", "dark_venus") or P.get("use_bb_reversion")) and cfg.get("strategy_bb_reversion"):
                scfg = dict(cfg["strategy_bb_reversion"])
                scfg["entry"] = dict(scfg["entry"], symbol=sym)
            elif (act_strat in ("smc_mtf", "smc") or (P.get("name") == "AGGRESSIVE" and P.get("use_smc_mtf"))) and cfg.get("strategy_aggressive"):
                scfg = dict(cfg["strategy_aggressive"])
                # Teruskan nama simbol ke entry engine agar bisa fetch M15/M3 dari MT5
                scfg["entry"] = dict(scfg["entry"], symbol=sym)

            grp = risk_group(cfg, sym)
            ses_ok, ses = symbol_session(C, sc, now)      # jadwal per simbol, termasuk hari (weekend)
            if not self.skill("session_news"):
                ses_ok, ses = True, f"{ses} (skill Sesi & Berita OFF)"
            total = sum(1 for p in self.ex.positions() if risk_group(cfg, p.symbol) == grp)
            tf = sc.get("timeframe", "M5")
            info, t = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
            if not info or not t:
                continue
            spread = round((t.ask - t.bid) / info.point)
            # bar 0 = candle berjalan; dipakai hanya bila profil mengizinkan entry intrabar
            r_all = mt5.copy_rates_from_pos(sym, TF[tf], 0, min_bars(scfg) + 201)
            if r_all is None or len(r_all) < min_bars(scfg) + 1:
                self.sym_status[sym] = {"status": "data kurang", "spread": spread}
                continue
            r = r_all[:-1]                                                       # bar TERTUTUP
            d = build_signals(pd.DataFrame(r), scfg)
            sig, a, bar_t = int(d.signal.iloc[-1]), float(d.atr.iloc[-1]), int(d.time.iloc[-1])
            smc_meta = d["smc_entry_meta"].iloc[-1] if "smc_entry_meta" in d.columns else None
            intrabar = ""
            if not sig and P["intrabar_entry"]:
                # sinyal di candle berjalan dianggap TERKONFIRMASI bila bertahan >= intrabar_confirm_sec
                live_sig = int(build_signals(pd.DataFrame(r_all), scfg).signal.iloc[-1])
                seen = self.intra.get(sym)
                live_t = int(r_all[-1]["time"])
                if live_sig and seen and seen[:2] == (live_t, live_sig):
                    if time.time() - seen[2] >= P["intrabar_confirm_sec"]:
                        sig, bar_t, intrabar = live_sig, live_t, " (candle berjalan)"
                elif live_sig:
                    self.intra[sym] = (live_t, live_sig, time.time())
                else:
                    self.intra.pop(sym, None)
            mine = self.ex.positions(sym)
            st = {"spread": spread, "atr": round(a, 2), "atr_pct": round(a / float(d.close.iloc[-1]) * 100, 3),
                  "signal": sig, "positions": len(mine), "group": grp, "session": ses, "session_ok": ses_ok,
                  "bar": datetime.fromtimestamp(bar_t, timezone.utc).strftime("%H:%M")}

            if getattr(self, "news_engine", None):
                try:
                    self.news_engine.process(
                        sym=sym,
                        C=C,
                        eq=eq,
                        atr=a,
                        ask=t.ask,
                        bid=t.bid,
                        spread=spread,
                        calc_lot_fn=lambda sl_dist: self.calc_lot(sym, sl_dist, eq, 1),
                        skill_on=self.skill("news_trading")
                    )
                except Exception as e:
                    log.warning(f"NEWS_ENG {sym} error: {e}")

            # sinyal berlawanan -> tutup posisi lawan
            if sig and ex.get("close_on_opposite_signal"):
                for p in mine:
                    if p.dir == -sig and self.ex.close(p, "balik"):
                        log.info(f"TUTUP  {sym} #{p.ticket} sinyal berlawanan")
                mine = self.ex.positions(sym)
                total = sum(1 for p in self.ex.positions() if risk_group(cfg, p.symbol) == grp)

            # multiply: bursts_per_symbol burst aktif per simbol (profil; NORMAL = 1)
            mult = self.multiply(sym)
            burst = max(1, int(rk.get("burst_size", 1)))
            max_sym = burst * P["bursts_per_symbol"] if mult else sc["max_positions"]
            n_need = burst if mult else 1
            per_pct = rk["risk_per_trade_pct"] * sc.get("risk_mult", 1.0)
            burst_r = per_pct * burst if mult else per_pct
            open_r = self.open_risk_pct(eq, grp)
            day_pct = self.day_pnl / self.st["day_start_equity"] * 100 if self.st.get("day_start_equity") else 0.0
            market_age = time.time() - t.time
            # Semua gerbang SELAIN sinyal dihitung SETIAP scan, supaya dashboard selalu tahu:
            # "siap entry kalau sinyal muncul" atau "TERBLOKIR karena ...".
            blockers = self.entry_blockers(sym, mine, total, n_need, max_sym, open_r, burst_r, day_pct,
                                           spread, market_age, strategy=True, ses_ok=ses_ok, ses=ses,
                                           sig=sig, px=float(d.close.iloc[-1]), atr_now=a)
            st["blockers"] = blockers
            st["ready"] = not blockers
            st["why_not"] = self.why_not(blockers, tf, bar_t, scfg=scfg)
            st["last_cross"] = self.last_cross_info(pd.DataFrame(r), scfg)
            self.cand_plans[sym] = self.candidate_plan(sym, C, st, blockers, sig, a, float(d.close.iloc[-1]), bar_t, tf, pd.DataFrame(r))
            try:
                self.level_cards[sym] = self.level_card(sym, C, scfg, blockers, sig, a, float(d.close.iloc[-1]), bar_t, tf,
                                                        pd.DataFrame(r), eq, n_need, st)
            except Exception as e:
                log.warning(f"LEVEL  kartu {sym} gagal: {e}")
            st["profile"] = P["name"]
            block = None
            if sig and self.st.get("last_entry_bar", {}).get(sym) == bar_t:
                block = "sudah entry di bar ini"
            elif blockers:
                block = "TERBLOKIR: " + blockers[0] + (f" (+{len(blockers) - 1} lagi)" if len(blockers) > 1 else "")
            elif not sig:
                block = "siap — menunggu sinyal" + (" (pantau candle berjalan)" if P["intrabar_entry"] else "")
            if block:
                if sig and blockers:
                    self.count_signal(sym, bar_t, sig, P["name"], blockers[0])
                st["status"] = block
                self.sym_status[sym] = st
                continue

            st["status"], opened = self.execute_entry(sym, sig, r, a, bar_t, eq, ses + intrabar, spread, len(mine), total,
                                                      open_r, day_pct, intrabar=bool(intrabar), smc_meta=smc_meta)
            self.count_signal(sym, bar_t, sig, P["name"], None if opened else st["status"])
            self.sym_status[sym] = st

    def candidate_plan(self, sym, C, st, blockers, sig, a, px, bar_t, tf, df=None):
        """Rencana untuk kandidat terbaru: MENUNGGU (sinyal candle berjalan / lolos semua gerbang) atau
        DITOLAK (filter atau gerbang risiko) — level sama persis dengan yang akan dipakai bot."""
        lc = st.get("last_cross") or {}
        pend = self.intra.get(sym)
        if pend and pend[0] > bar_t:
            d, status, reason, t0 = pend[1], "MENUNGGU", f"sinyal candle berjalan, konfirmasi {C['profile']['intrabar_confirm_sec']} dtk", pend[0]
        elif lc.get("time") and lc.get("bars_ago", 99) <= self.cfg.get("plans", {}).get("candidate_bars", 12):
            d = 1 if lc["dir"] == "BUY" else -1
            t0 = lc["time"]
            if lc["result"] != "SINYAL":
                status, reason = "DITOLAK", lc["result"]
            elif blockers:
                status, reason = "DITOLAK", blockers[0]
            elif self.st.get("last_entry_bar", {}).get(sym) == lc["time"]:
                return None                                   # sudah jadi burst AKTIF
            else:
                status, reason = "MENUNGGU", "lolos semua gerbang — entry di siklus berikut"
        else:
            return None
        try:
            srl = sr_mtf.compute(sym, self.cfg.get("sr"), px)
            lv, labels = sr_mtf.levels_for_plan(srl), sr_mtf.level_labels(srl)
        except Exception:
            lv, labels = [], {}
        # skor gabungan & kontribusi skill = analyze_entry() yang SAMA dipakai gerbang entry (di-cache per sinyal)
        ck = (sym, t0, d, status)
        cache = getattr(self, "_cand_score", {})
        if cache.get(sym, (None,))[0] != ck and df is not None:
            try:
                w = self.analyze_entry(sym, df, d, a)
                cache[sym] = (ck, w.get("score"), w.get("skill_scores"))
            except Exception:
                cache[sym] = (ck, None, None)
            self._cand_score = cache
        _, score, skills = cache.get(sym, (None, None, None))
        sl_d = self.sl_distance(sym, a, px)
        trig = px + d * 0.1 * a
        pl = plan_mod.build(sym, d, px, sl_d, a, lv, lv, status, reason, t0, f"cand-{sym}", score, skills, trigger=trig,
                            labels=labels, tf=tf, sl_clamped=abs(sl_d - C["exit"]["sl_atr"] * a) > 1e-9)
        pl["trigger_text"] = f"{'BUY' if d > 0 else 'SELL'} jika close {'di atas' if d > 0 else 'di bawah'} {trig:.2f}"
        return pl

    # ------------------------------------------------------------ kartu Saran Level (panel sidebar)
    @staticmethod
    def filter_detail(df, scfg, d):
        """Nilai AKTUAL vs ambang untuk filter yang menolak (dipakai teks 'TAHAN' di kartu)."""
        from .indicators import pct_rank, ema as ema_fn
        out = []
        for f in scfg.get("filters", []):
            if not f.get("enabled", True):
                continue
            if f["name"] == "atr_band":
                pr = float(pct_rank(atr_fn(df, f.get("period", 14)), f.get("window", 288)).iloc[-1])
                if not (f.get("min_pct", 0.3) <= pr <= f.get("max_pct", 0.95)):
                    out.append(f"atr_band: persentil ATR {pr:.2f} di luar rentang {f.get('min_pct', 0.3):.2f}-{f.get('max_pct', 0.95):.2f}"
                               f" (jendela {f.get('window', 288)} bar)")
            elif f["name"] == "trend_ema":
                e = float(ema_fn(df.close, f.get("period", 200)).iloc[-1]); c = float(df.close.iloc[-1])
                if (d > 0 and c <= e) or (d < 0 and c >= e):
                    out.append(f"trend_ema: close {c:,.2f} {'di bawah' if d > 0 else 'di atas'} EMA{f.get('period', 200)} {e:,.2f} "
                               f"(selisih {abs(c - e):,.2f})")
        return out

    def _scenario(self, sym, C, d, entry_lo, entry_hi, entry, a, lv, labels, tf, eq, n_need, kind, reason, t0, score, skills, trig_text, valid_bars, plan=None):
        """Satu skenario. Level = plans.build() yang SAMA dengan rencana/burst nyata (plan diberikan kalau sudah ada)."""
        sl_d = self.sl_distance(sym, a, entry)
        pl = plan or plan_mod.build(sym, d, entry, sl_d, a, lv, lv, "PANTAUAN", reason, t0, f"scn-{sym}-{d}", score, skills,
                                    trigger=entry, labels=labels, tf=tf, sl_clamped=abs(sl_d - C["exit"]["sl_atr"] * a) > 1e-9)
        sl_d = abs(pl["entry"] - pl["sl"])
        keep = getattr(self, "last_burst_risk_pct", 0)
        try:
            res = self.calc_lot(sym, sl_d, eq, n_need)
        except Exception:
            res = None
        self.last_burst_risk_pct = keep
        lot = res[0] if isinstance(res, tuple) else None
        vpp = value_per_price_unit(sym)
        risk = sl_d * vpp * lot * (n_need if self.multiply(sym) else 1) if lot and vpp else None
        rr_ref = float(self.cfg.get("plans", {}).get("rr_ref", 1.5))
        ent = pl["entry"]
        dist = lambda p: {"pts": round(abs(p - ent), 3), "pct": round(abs(p - ent) / ent * 100, 3), "atr": round(abs(p - ent) / a, 2) if a else None}
        return {"dir": "BUY" if d > 0 else "SELL", "kind": kind, "reason": reason,
                "entry": ent, "entry_lo": round(min(entry_lo, entry_hi), 3), "entry_hi": round(max(entry_lo, entry_hi), 3),
                "sl": pl["sl"], "sl_d": dist(pl["sl"]), "sl_src": pl["sl_src"],
                "tp1": pl["tp1"], "tp1_d": dist(pl["tp1"]), "tp1_src": pl["tp1_src"], "rr1": pl["rr1"],
                "tp2": pl["tp2"], "tp2_d": dist(pl["tp2"]), "tp2_src": pl["tp2_src"], "rr2": pl["rr2"],
                "rr_ref": rr_ref, "rr1_ok": bool(pl["rr1"] and pl["rr1"] >= rr_ref), "rr2_ok": bool(pl["rr2"] and pl["rr2"] >= rr_ref),
                "lot": lot, "n_orders": n_need if self.multiply(sym) else 1,
                "risk_money": round(risk, 2) if risk else None, "risk_pct": round(risk / eq * 100, 3) if risk and eq else None,
                "trigger": trig_text, "invalid": pl["invalid"], "inv_src": pl["inv_src"], "valid_bars": valid_bars,
                "score": score, "skills": skills or {}, "key": pl.get("key"), "plan_status": pl.get("status")}

    def level_card(self, sym, C, scfg, blockers, sig, a, px, bar_t, tf, df, eq, n_need, st):
        """Kartu per simbol: skenario BUY & SELL. Sumber angka = plans.build (sama dgn burst/chart/MT5)."""
        srl = sr_mtf.compute(sym, self.cfg.get("sr"), px)
        lv, labels = sr_mtf.levels_for_plan(srl), sr_mtf.level_labels(srl)
        ema_p = next((f.get("period", 200) for f in scfg.get("filters", []) if f["name"] == "trend_ema"), 200)
        from .indicators import ema as ema_fn
        bias = 1 if px > float(ema_fn(df.close, ema_p).iloc[-1]) else -1
        cand = self.cand_plans.get(sym)
        act = [p for p in self.st.get("plans", {}).values() if p.get("symbol") == sym and p.get("status") == "AKTIF"]
        act = max(act, key=lambda p: p.get("t", 0)) if act else None
        lc = st.get("last_cross") or {}
        cbars = self.cfg.get("plans", {}).get("candidate_bars", 12)
        cache = getattr(self, "_card_score", {})

        def sc_for(d):
            k = (sym, bar_t, d)
            if k not in cache:
                try:
                    w = self.analyze_entry(sym, df, d, a)
                    cache[k] = (w.get("score"), w.get("skill_scores"))
                except Exception:
                    cache[k] = (None, None)
                for old in [x for x in cache if x[0] == sym and x[1] != bar_t]:
                    cache.pop(old, None)
                self._card_score = cache
            return cache[k]

        zones = srl.get("zones", [])
        scen = {}
        for d in (1, -1):
            name = "BUY" if d > 0 else "SELL"
            sc, sk_ = sc_for(d)
            real = act if act and (act["dir"] == name) else cand if cand and cand["dir"] == name else None
            if real is not None:
                kind = {"AKTIF": "aktif", "MENUNGGU": "menunggu", "DITOLAK": "tertahan"}.get(real["status"], "menunggu")
                trig = real.get("trigger_text") or f"posisi {name} sudah berjalan"
                lo, hi = sorted([real["entry"], real.get("trigger", real["entry"])])
                reason = real.get("reason", "")
                if kind == "tertahan":
                    det = self.filter_detail(df, scfg, d)
                    reason = (reason + (" — " + "; ".join(det) if det else ""))
                vb = max(0, cbars - int(lc.get("bars_ago", 0))) if kind != "aktif" else None
                scen[name] = self._scenario(sym, C, d, lo, hi, real["entry"], a, lv, labels, tf, eq, n_need, kind, reason,
                                            real.get("t", 0), real.get("score", sc), real.get("skill_scores") or sk_, trig, vb, plan=real)
                continue
            # skenario bersyarat: zona S/R terdekat searah (BUY = support di bawah, SELL = resistance di atas)
            zs = sorted([z for z in zones if (z["price"] < px if d > 0 else z["price"] > px)], key=lambda z: abs(z["price"] - px))
            if zs:
                z = zs[0]
                entry = z["hi"] if d > 0 else z["lo"]
                lo, hi, src = z["lo"], z["hi"], z["label"]
            else:
                entry, lo, hi, src = px, px, px, "harga sekarang (tidak ada zona S/R)"
            trig = f"retest zona {src} ({lo:,.2f}-{hi:,.2f}) + EMA cross {name} lolos filter"
            scen[name] = self._scenario(sym, C, d, lo, hi, entry, a, lv, labels, tf, eq, n_need, "bersyarat",
                                        "skenario bersyarat, belum ada sinyal", int(bar_t), sc, sk_, trig, cbars, plan=None)
        # status kartu
        if act:
            code, text = "AKTIF", "SETUP AKTIF - akan dieksekusi otomatis / sedang berjalan"
        elif cand and cand["status"] == "MENUNGGU":
            code, text = "MENUNGGU", "MENUNGGU KONFIRMASI"
        elif cand and cand["status"] == "DITOLAK":
            code, text = "TAHAN", "TAHAN: " + (cand.get("reason") or "")
        elif blockers:
            code, text = "TAHAN", "TAHAN: " + blockers[0]
        else:
            code, text = "PANTAUAN", "SKENARIO PANTAUAN"
        main = "BUY" if bias > 0 else "SELL"
        if act:
            main = act["dir"]
        elif cand:
            main = cand["dir"]
        for n_, v in scen.items():
            v["role"] = "utama" if n_ == main else "alternatif"
        eta = (st.get("why_not") or {}).get("eta") or self.why_not(blockers, tf, bar_t)["eta"]
        return {"symbol": sym, "tf": tf, "tf_sec": TF_SEC[tf], "updated": time.time(), "bar_time": int(bar_t),
                "price": px, "atr": round(a, 3), "status_code": code, "status": text, "eta": eta, "bias": "BUY" if bias > 0 else "SELL",
                "bias_src": f"close vs EMA{ema_p} (filter trend_ema)", "profile": C["profile"]["name"],
                "blockers": blockers[:3], "scen": scen,
                "sumber": {"modul": "bot/analysis/plans.py build() via Bot.level_card/candidate_plan/execute_entry",
                           "sr": "bot/analysis/sr_mtf.py compute()", "sl": "Bot.sl_distance()", "lot": "Bot.calc_lot()"}}

    def position_checks(self, p):
        """SL/TP terpasang di MT5? TP=0 memang by design (exit = SL + trailing) kecuali config risk.require_tp=true."""
        req_tp = bool(self.cfg.get("risk", {}).get("require_tp", False))
        sl_ok, tp_ok = bool(p.sl), bool(p.tp)
        warn = (not sl_ok) or (req_tp and not tp_ok)
        if warn:
            seen = self.__dict__.setdefault("_chk_seen", set())
            if p.ticket not in seen:
                seen.add(p.ticket)
                log.error(f"CEK-SLTP #{p.ticket} {p.symbol}: {'TANPA SL' if not sl_ok else ''}{' TANPA TP' if not tp_ok and req_tp else ''} di MT5")
        return {"sl_ok": sl_ok, "tp_ok": tp_ok, "tp_required": req_tp, "sltp_warn": warn}

    def level_cards_out(self):
        """Semua simbol config (tersedia atau tidak) -> kartu; simbol tak tersedia = placeholder tanpa angka."""
        out = {}
        for sym in self.cfg["symbols"]:
            avail = sym in self.symbols
            c = self.level_cards.get(sym) if avail and self.is_active(sym) else None
            out[sym] = c or {"symbol": sym, "available": avail, "status_code": "NA" if not avail else "OFF",
                             "status": "Symbol tidak tersedia di akun saat ini" if not avail else "DIHENTIKAN (switch dashboard)"}
            out[sym].setdefault("available", avail)
            if c:
                tk = mt5.symbol_info_tick(sym)
                if tk and tk.bid:
                    c["live"] = tk.bid
        return out

    def all_plans(self):
        """AKTIF (SL mengikuti trailing posisi nyata) + SELESAI (memudar) + kandidat."""
        linger = self.cfg.get("dashboard", {}).get("closed_marker_minutes", 10) * 60
        pos = self.ex.positions()
        tb = self.st.get("ticket_burst", {})
        out = []
        for key, pl in list(self.st.get("plans", {}).items()):
            if pl["status"] == "SELESAI" and time.time() - pl.get("closed_at", 0) > linger:
                del self.st["plans"][key]
                continue
            if pl["status"] == "AKTIF":
                sls = [p.sl for p in pos if tb.get(str(p.ticket)) == key and p.sl]
                if sls:
                    pl["sl_now"] = max(sls) if pl["dir"] == "BUY" else min(sls)
                elif key not in self.st.get("bursts", {}):
                    pl.update(status="SELESAI", closed_at=time.time())
            out.append(pl)
        return out + [p for p in self.cand_plans.values() if p]

    @staticmethod
    def why_not(blockers, tf, bar_t, scfg=None):
        """'Kenapa belum entry?' -> tahap penyumbat aktif, alasannya, dan perkiraan kapan lanjut."""
        nxt = datetime.fromtimestamp(bar_t + 2 * TF_SEC[tf], timezone.utc)
        if not blockers:
            s_name = (scfg.get("entry", {}).get("name") if scfg else "") or ""
            if s_name == "bb_reversion":
                r_text = "menunggu harga menyentuh pita luar Bollinger Bands (20, 2.2) dengan konfirmasi RSI"
            elif s_name == "smc_mtf":
                r_text = "menunggu konfirmasi CHoCH + FVG M3 di area Order Block M15"
            else:
                r_text = "belum ada sinyal yang lolos filter"
            return {"stage": "menunggu sinyal", "reason": r_text,
                    "eta": f"dicek lagi saat bar {tf} tutup {nxt:%H:%M} UTC"}
        b = blockers[0]
        m = __import__("re").search(r"s/d (\d\d:\d\d)", b)
        for key, stage, eta in (
                ("anggaran", "anggaran harian (akun)", "reset 00:00 UTC (07:00 WIB) atau saat P/L hari ini pulih"),
                ("rem rugi", "rem rugi harian", "reset 00:00 UTC (07:00 WIB)"),
                ("HALT", "HALT drawdown", "setelah reset manual (start_bot.cmd reset)"),
                ("cooldown", "cooldown profil", f"pukul {m[1]}" if m else "setelah cooldown"),
                ("jeda kalah", "jeda kalah beruntun", f"pukul {m[1]}" if m else "setelah jeda"),
                ("burst masih aktif", "slot simbol", "saat burst terbuka tutup / terkunci"),
                ("penuh", "slot posisi", "saat posisi tutup"),
                ("risiko terbuka", "limit risiko terbuka", "saat SL posisi terkunci BE atau posisi tutup"),
                ("terlalu dekat", "jarak antar-entri", "saat harga bergerak menjauh dari posisi aktif atau posisi tutup"),
                ("pasar tutup", "pasar tutup", "saat pasar broker buka lagi"),
                ("jadwal", "jadwal / sesi", "saat jam jadwal simbol berikutnya"),
                ("spread", "spread", "saat spread kembali normal"),
                ("berita", "filter berita", "setelah jendela berita lewat"),
                ("DIHENTIKAN", "simbol OFF", "nyalakan switch simbol"),
                ("Burst dimatikan", "burst OFF", "nyalakan switch Burst"),
                ("Algo Trading", "Algo Trading MT5 OFF", "nyalakan Algo Trading di MT5")):
            if key in b:
                return {"stage": stage, "reason": b, "eta": eta}
        return {"stage": "lainnya", "reason": b, "eta": "-"}

    # ---------------------------------------------------------- sinyal terdeteksi vs dieksekusi
    REASONS = [("skor kurang", ("skor",)), ("cooldown", ("cooldown",)),
               ("slot penuh", ("penuh", "burst masih aktif")),
               ("limit risiko", ("risiko", "anggaran", "rem rugi", "HALT", "jeda kalah", "lot", "terlalu dekat")),
               ("skill dimatikan", ("skill", "SMC strict")),
               ("sesi/pasar", ("jadwal", "pasar", "spread", "berita", "tick")),
               ("dimatikan manual", ("dimatikan", "DIHENTIKAN", "PAUSE"))]

    def count_signal(self, sym, bar_t, sig, profile, reason):
        """Satu sinyal (simbol+bar) dihitung SEKALI: dieksekusi, atau alasan pertama ditolak."""
        seen = self.st.setdefault("sig_seen", {})
        key = f"{sym}@{bar_t}"
        if key in seen:
            return
        seen[key] = time.time()
        for k in [k for k, v in seen.items() if time.time() - v > 86400]:
            del seen[k]
        day = self.st.setdefault("sigstats", {}).setdefault(utcnow().date().isoformat(), {})
        s = day.setdefault(profile, {"detected": 0, "executed": 0, "reasons": {}})
        s["detected"] += 1
        if reason is None:
            s["executed"] += 1
            return
        cat = next((c for c, keys in self.REASONS if any(k in reason for k in keys)), "lainnya")
        s["reasons"][cat] = s["reasons"].get(cat, 0) + 1
        rj = self.st.setdefault("sig_rejects", [])
        rj.append({"t": time.time(), "symbol": sym, "dir": "BUY" if sig > 0 else "SELL", "profile": profile,
                   "cat": cat, "reason": reason})
        self.st["sig_rejects"] = rj[-30:]
        for d in sorted(self.st["sigstats"])[:-7]:            # simpan 7 hari
            del self.st["sigstats"][d]

    # ---------------------------------------------------------- gerbang & eksekusi (dipakai otomatis + manual)
    def entry_blockers(self, sym, mine, total, n_need, max_sym, open_r, burst_r, day_pct, spread, market_age,
                       strategy=True, ses_ok=True, ses="", sig=0, px=0.0, atr_now=0.0):
        """Semua alasan entry tidak boleh dibuka SEKARANG (tanpa melihat sinyal).
        strategy=False (entry manual dari MT5): filter strategi — burst OFF, PAUSE, sesi — diabaikan,
        tetapi SEMUA rem risiko tetap berlaku.
        total / open_r = posisi & risiko terbuka di GRUP risiko simbol ini saja; rem harian & HALT = akun."""
        C = self.C(sym)
        rk, sc, grp = C["risk"], self.cfg["symbols"][sym], risk_group(self.cfg, sym)
        b = []
        if not self.is_active(sym):
            b.append("simbol DIHENTIKAN (switch dashboard)")
        cd = C["profile"]["cooldown_min"] * 60
        last = self.st.get("last_entry_ts", {}).get(sym, 0)
        ov = self.override(sym)
        if ov.get("mode") == "BLOCK":
            b.append("DITAHAN MANUAL (TETAP BLOKIR)")
        run = ov.get("mode") == "RUN"
        if strategy and cd and time.time() - last < cd and not run:
            b.append(f"cooldown profil {C['profile']['name']} s/d {datetime.fromtimestamp(last + cd):%H:%M}")
        if strategy and FLAG_BURST_OFF.exists():
            b.append("Burst dimatikan manual (tombol)")
        if strategy and FLAG_PAUSE.exists():
            b.append("PAUSE")
        if FLAG_HALT.exists():
            b.append("HALT max drawdown")
        if self.st.get("day_halted"):
            b.append("rem rugi harian")
        if time.time() < self.pause_until(grp):
            b.append(f"jeda kalah beruntun grup {grp} s/d {datetime.fromtimestamp(self.pause_until(grp)):%H:%M}")
        if strategy and not ses_ok:
            b.append(f"di luar jadwal simbol ({ses or 'jam tidak dijadwalkan'})")
        # jarak minimum antar-entri SEARAH (posisi menumpuk dalam rentang harga sempit = risiko
        # berkorelasi tanpa diversifikasi). Selalu berlaku (bukan hanya strategy=True) — termasuk manual.
        if sig and atr_now > 0:
            same_dir = [p for p in mine if p.dir == sig]
            if same_dir:
                min_atr = C["profile"].get("min_spacing_atr", 0.5)
                min_gap = min_atr * atr_now
                nearest = min(abs(px - p.price_open) for p in same_dir)
                if nearest < min_gap:
                    b.append(f"terlalu dekat posisi aktif (jarak {nearest:.2f} < {min_gap:.2f} = "
                             f"{min_atr}xATR, profil {C['profile']['name']})")
        respect = strategy or self.cfg.get("manual", {}).get("respect_news", True)
        self._news_adj.pop(sym, None)
        if self.skill("news_impact"):
            nb = newsimpact.blackout(sym, dict(self.cfg.get("news_impact", {}), symbols=list(self.cfg["symbols"])))
            if nb[0] and nb[2] == "block" and respect:
                b.append(f"berita (blackout {sym}): {nb[1]}")
            elif nb[0]:
                self._news_adj[sym] = (nb[3], nb[4])         # mode reduce: lot dikecilkan / SL dilebarkan
        elif self.news_state[0] and self.skill("session_news") and respect:
            b.append(f"berita: {self.news_state[1]}")
        if spread > sc["max_spread_points"]:
            b.append(f"spread {spread} > {sc['max_spread_points']}")
        # Multi-broker fix: tolak juga jika spread > X% dari ATR saat ini (lebih adaptif dari batas absolut)
        _max_spread_atr_pct = sc.get("max_spread_atr_pct", 0)
        if _max_spread_atr_pct and atr_now > 0:
            _spread_atr_pct = spread / (atr_now / self.cfg["symbols"][sym].get("_point", 0.01)) * 100
            if _spread_atr_pct > _max_spread_atr_pct:
                b.append(f"spread {spread} = {_spread_atr_pct:.1f}% ATR > max {_max_spread_atr_pct}% (spread lebar)")
        if market_age > 180:
            b.append("pasar tutup / tick basi")
        if len(mine) + n_need > max_sym:
            b.append("burst masih aktif" if self.multiply(sym) and mine else "posisi simbol penuh")
        elif total + n_need > rk["max_positions_total"]:
            b.append(f"slot grup {grp} penuh ({total}/{rk['max_positions_total']})")
        elif rk.get("max_total_risk_pct") and open_r + burst_r > rk["max_total_risk_pct"] + 1e-9:
            b.append(f"risiko terbuka grup {grp} {open_r:.2f}% + {burst_r:.2f}% > {rk['max_total_risk_pct']}%")
        all_r = self.open_risk_pct(self.last_eq) if getattr(self, "last_eq", None) else open_r   # rem harian = akun
        # SATU risk manager akun: total eksposur semua simbol & grup (tidak diubah profil mana pun)
        acct_cap = self.cfg["risk"].get("max_account_open_risk_pct")
        if acct_cap and all_r + burst_r > acct_cap + 1e-9:
            b.append(f"risiko terbuka AKUN {all_r:.2f}% + {burst_r:.2f}% > {acct_cap}% (risk manager)")
        per_t = burst_r / max(1, n_need)
        room = rk["daily_loss_limit_pct"] + day_pct - all_r            # sisa anggaran rugi hari ini (% equity)
        self._budget_fit[sym] = max(0, int(room / per_t + 1e-9)) if per_t > 0 else n_need
        # rumus: P/L hari ini (AKUN) - risiko terbuka (akun) - risiko 1 tiket < -rem harian -> blokir.
        # Muat sebagian -> burst DIKECILKAN (bukan diblokir). TETAP RUN melewati gerbang ini (bukan rem harian).
        if rk.get("daily_budget_gate") and not run and day_pct - all_r - per_t < -rk["daily_loss_limit_pct"]:
            b.append(f"anggaran rugi harian habis: P/L hari ini {day_pct:+.2f}% - terbuka {all_r:.2f}% - 1 tiket "
                     f"{per_t:.2f}% = {day_pct - all_r - per_t:+.2f}% < -{rk['daily_loss_limit_pct']}%")
        if self.mode != "paper" and not mt5.terminal_info().trade_allowed:
            b.append("Algo Trading MT5 MATI")
        return b

    def execute_entry(self, sym, sig, r, a, bar_t, eq, ses, spread, n_mine, total, open_r, day_pct, manual=False,
                      intrabar=False, smc_meta=None):
        """Buka burst. -> (status, jumlah tiket terbuka). Dipakai entry otomatis DAN tombol BUY/SELL di MT5."""
        cfg = self.cfg
        C = self.C(sym)
        rk, ex, sc = C["risk"], C["exit"], cfg["symbols"][sym]
        tf = sc.get("timeframe", "M5")
        info = mt5.symbol_info(sym)
        burst = max(1, int(rk.get("burst_size", 1)))
        if self.multiply(sym):
            n = burst
        else:
            slots = min(sc["max_positions"] - n_mine, rk["max_positions_total"] - total)
            n = max(1, min(burst, slots))

        # ---- konfluensi: pola candle, struktur, SMC, skor pembelajaran (dicatat juga untuk entry manual)
        why = self.analyze_entry(sym, pd.DataFrame(r), sig, a)
        if manual:
            why["signal"] = [f"ENTRY MANUAL {'BUY' if sig > 0 else 'SELL'} dari tombol di chart MT5 "
                             "(tanpa menunggu sinyal EMA; rem risiko tetap diperiksa)"] + why["signal"][2:3]
            why["notes"].append("entry manual — filter SMC & gate learning tidak diterapkan (keputusan user)")
        else:
            P = C["profile"]
            if why["score"] is not None and why["score"] < P["min_score"]:
                return f"skor sinyal {why['score']:.0f}% < minimum {P['min_score']}% (profil {P['name']})", 0
            if intrabar:
                # audit 29 Sep: 3/3 burst intrabar AGGRESSIVE rugi (SMC -1.0, skor 26-38) -> syarat lebih ketat
                if why["score"] is not None and why["score"] < max(P["min_score"], P.get("intrabar_min_score", 0)):
                    return (f"intrabar: skor gabungan {why['score']:.0f}% < {max(P['min_score'], P.get('intrabar_min_score', 0))}% "
                            f"(syarat entri candle berjalan, profil {P['name']})"), 0
                smc_min, smc_now = P.get("intrabar_smc_min"), (why.get("skill_scores") or {}).get("smc")
                if smc_min is not None and smc_now is not None and smc_now < smc_min:
                    return f"intrabar: SMC {smc_now:+.1f} < {smc_min:+.1f} (melawan bias struktur, profil {P['name']})", 0
            smode = C["smc"].get("filter_mode", "score")
            against = why["smc_bias"] is not None and why["smc_bias"] != sig
            if smode == "strict" and against:
                return f"SMC strict (profil {P['name']}): melawan bias struktur", 0
            if smode == "prefer" and against and n > 1:
                n = 1               # melawan bias -> burst dikecilkan jadi 1 tiket, tidak ditolak
                why["notes"].append("SMC prefer: melawan bias -> hanya 1 tiket")
            lc = cfg.get("learning", {})
            if lc.get("mode") == "gate" and why["confidence"] is not None and self.learning_gate_allowed():
                if why["confidence"] < lc.get("min_confidence", 0.5):
                    return f"skor keyakinan {why['confidence']:.2f} < {lc.get('min_confidence', 0.5)}", 0
        px0 = float(pd.DataFrame(r).close.iloc[-1])
        sl_dist = self.sl_distance(sym, a, px0) * self._news_adj.get(sym, (1.0, 1.0))[1]
        tp_dist = self.tp_distance(sym, a, px0)

        # ── SMC MTF: cek apakah sinyal berasal dari engine smc_mtf ──────────────
        if smc_meta is None and "smc_entry_meta" in pd.DataFrame(r).columns:
            smc_meta = pd.DataFrame(r)["smc_entry_meta"].iloc[-1]

        ov = self.override(sym)
        fit = self._budget_fit.get(sym, n)
        if ov.get("mode") != "RUN" and rk.get("daily_budget_gate") and 0 < fit < n:
            why["notes"].append(f"burst dikecilkan {n} -> {fit} tiket agar muat anggaran rugi harian")
            n = fit

        # ── SMC MTF: Pasang LIMIT ORDER (bukan market order) ─────────────────────
        if smc_meta is not None:
            if hasattr(smc_meta, "to_dict"):
                smc_meta = smc_meta.to_dict()
            elif hasattr(smc_meta, "items"):
                smc_meta = dict(smc_meta)
        if isinstance(smc_meta, dict) and smc_meta.get("engine") == "smc_mtf":
            entry_px  = smc_meta.get("entry_price")
            sl_px     = smc_meta.get("sl_price")
            P_prof    = C["profile"]
            rr        = float(P_prof.get("rr_ratio", 1.5))
            sl_floor  = float(P_prof.get("sl_floor_atr", 1.0))
            max_sp    = int(P_prof.get("max_spread_pts", 50))

            if not entry_px or not sl_px:
                return "SMC MTF: entry_price atau sl_price tidak tersedia dari analisis FVG", 0

            # Jangan pasang ganda jika sudah ada pending order SMC aktif untuk simbol ini
            existing_po = self.ex.pending_orders(sym)
            if existing_po:
                return f"SMC MTF: sudah ada pending order aktif (#{existing_po[0].ticket})", 0

            # Cek spread saat ini sebelum pasang limit
            t_tick = mt5.symbol_info_tick(sym)
            cur_spread = round((t_tick.ask - t_tick.bid) / info.point) if t_tick else spread
            if cur_spread > max_sp:
                return f"SMC MTF: spread {cur_spread} > batas {max_sp} pts — limit order dibatalkan", 0

            sl_dist_smc = abs(entry_px - sl_px)
            # Garantikan jarak SL adaptif minimal sl_floor x ATR dan maksimal sl_max x ATR
            sl_max = float(P_prof.get("sl_max_atr", 2.2))
            if sl_dist_smc < sl_floor * a:
                sl_dist_smc = sl_floor * a
                sl_px = round(entry_px - sig * sl_dist_smc, info.digits)
            elif sl_dist_smc > sl_max * a:
                sl_dist_smc = sl_max * a
                sl_px = round(entry_px - sig * sl_dist_smc, info.digits)

            tp_dist_smc = rr * sl_dist_smc
            tp_px = round(entry_px + sig * tp_dist_smc, info.digits)

            got = self.calc_lot_smc(sym, sl_dist_smc, eq, n)
            if not got or got[0] is None:
                msg = f"SMC MTF lot ditolak: {got[1] if got else 'nilai kontrak?'}"
                log.warning(f"SKIP   {sym} {msg}")
                return msg, 0
            lot, rtxt = got
            real_r = self.last_burst_risk_pct
            if rk.get("max_total_risk_pct") and open_r + real_r > rk["max_total_risk_pct"] * rk["max_risk_overshoot_mult"] + 1e-9:
                return f"risiko riil {real_r:.2f}% + terbuka {open_r:.2f}% > batas", 0
            if rk.get("daily_budget_gate") and ov.get("mode") != "RUN" and day_pct - open_r - real_r < -rk["daily_loss_limit_pct"]:
                return f"anggaran rugi harian habis ({day_pct:+.2f}%)", 0

            key = f"{sym}@{datetime.fromtimestamp(bar_t, timezone.utc):%m%d-%H%M}-SMC"
            exec_mode = P_prof.get("execution_mode", "market")
            if exec_mode == "market":
                px_mkt = t_tick.ask if sig == 1 else t_tick.bid
                sl_real = round(px_mkt - sig * sl_dist_smc, info.digits)
                tp_real = round(px_mkt + sig * tp_dist_smc, info.digits)
                res = self.ex.open(sym, sig, lot, sl_real, tp_real, sc.get("deviation_points", 30),
                                   self.ex.tag + "-smc")
                if not res:
                    return "SMC MTF: open market gagal (broker menolak?)", 0
                ticket_num = res[0]
                self.st.setdefault("ticket_exit", {})[str(ticket_num)] = dict(
                    C["exit"], trailing=self.skill("trailing"), is_smc=True
                )
                self.st.setdefault("ticket_burst", {})[str(ticket_num)] = key
                log.info(f"SMC-MKT {sym} {'BUY' if sig == 1 else 'SELL'} "
                         f"{lot} lot @ {px_mkt:.2f}  SL {sl_real:.2f}  TP {tp_real:.2f}  "
                         f"({rr}R | ATR {a:.2f})  OB[{smc_meta['ob_bottom']:.2f}-{smc_meta['ob_top']:.2f}]  "
                         f"spread {cur_spread}  sesi {ses}")
                self.st.setdefault("last_entry_bar", {})[sym] = bar_t
                self.st.setdefault("last_entry_ts", {})[sym] = time.time()
                return f"SMC MARKET ORDER @ {px_mkt:.2f} (SL {sl_dist_smc:.2f} | TP {tp_dist_smc:.2f} | {rr}R)", 1

            # Pasang 1 limit order (SMC entry = 1 tiket presisi membawa alokasi risiko penuh Opsi 3)
            order_type = 2 if sig == 1 else 3   # ORDER_TYPE_BUY_LIMIT=2, SELL_LIMIT=3
            ttl_bars   = int(C["profile"].get("m3_fvg_ttl_bars", 10)) if hasattr(C["profile"], "get") else 10
            ttl_sec    = ttl_bars * 3 * 60       # M3 = 3 menit per bar

            try:
                po_id = self.ex.open_pending(
                    sym=sym, order_type=order_type, lot=lot,
                    price=round(entry_px, info.digits),
                    sl=round(sl_px, info.digits),
                    tp=tp_px, expiry_sec=ttl_sec,
                    tag=self.ex.tag + "-smc"
                )
            except Exception as e:
                log.warning(f"SMC MTF pending order gagal: {e}")
                return f"SMC MTF: pending order error — {e}", 0

            if not po_id:
                return "SMC MTF: open_pending gagal (broker menolak?)", 0

            ticket_num = po_id[0] if isinstance(po_id, tuple) else po_id
            # Rekam exit config untuk pending order ini (akan aktif otomatis saat terisi)
            self.st.setdefault("ticket_exit", {})[str(ticket_num)] = dict(
                C["exit"], trailing=self.skill("trailing"), is_smc=True
            )

            log.info(f"SMC-LMT {sym} {'BUY_LMT' if sig == 1 else 'SELL_LMT'} "
                     f"{lot} lot @ {entry_px:.2f}  SL {sl_px:.2f}  TP {tp_px:.2f}  "
                     f"({rr}R | ATR {a:.2f})  OB[{smc_meta['ob_bottom']:.2f}-{smc_meta['ob_top']:.2f}]  "
                     f"FVG[{smc_meta['fvg_bottom']:.2f}-{smc_meta['fvg_top']:.2f}]  "
                     f"TTL {ttl_sec//60}min  spread {cur_spread}  sesi {ses}")
            self.st.setdefault("last_entry_bar", {})[sym] = bar_t
            self.st.setdefault("last_entry_ts", {})[sym] = time.time()
            return f"SMC LIMIT ORDER @ {entry_px:.2f} (FVG 50% | SL {sl_dist_smc:.2f} | TP {tp_dist_smc:.2f} | {rr}R)", 1

        # ── Entry market order biasa (CONSERVATIVE / NORMAL / non-SMC) ─────────
        got = self.calc_lot(sym, sl_dist, eq, n)
        if not got or got[0] is None:
            msg = f"lot ditolak: {got[1] if got else 'nilai kontrak?'}"
            log.warning(f"SKIP   {sym} {msg}")
            return msg, 0
        lot, rtxt = got
        # cek ulang dengan risiko RIIL (lot minimum bisa membuat risiko > rencana)
        real_r = self.last_burst_risk_pct
        if rk.get("max_total_risk_pct") and open_r + real_r > rk["max_total_risk_pct"] * rk["max_risk_overshoot_mult"] + 1e-9:
            return f"risiko riil {real_r:.2f}% + terbuka {open_r:.2f}% > batas", 0
        if rk.get("daily_budget_gate") and ov.get("mode") != "RUN" and day_pct - open_r - real_r < -rk["daily_loss_limit_pct"]:
            return f"anggaran rugi harian habis ({day_pct:+.2f}%)", 0
        key = f"{sym}@{datetime.fromtimestamp(bar_t, timezone.utc):%m%d-%H%M}" + ("-M" if manual else "")
        opened, first_px = 0, None
        t_burst = time.time()
        for k in range(n):
            t = mt5.symbol_info_tick(sym)
            px = t.ask if sig == 1 else t.bid
            sl = round(px - sig * sl_dist, info.digits)
            tp = round(px + sig * tp_dist, info.digits) if tp_dist else 0.0
            res = self.ex.open(sym, sig, lot, sl, tp, sc.get("deviation_points", 30),
                               self.ex.tag + ("-man" if manual else ""))
            if not res:

                break
            opened += 1
            self.st.setdefault("ticket_burst", {})[str(res[0])] = key
            # parameter saat dibuka: ganti profil/skill nanti TIDAK mengubah pengelolaan posisi ini
            self.st.setdefault("ticket_exit", {})[str(res[0])] = dict(ex, trailing=self.skill("trailing"))
            self.st.setdefault("ticket_meta", {})[str(res[0])] = {
                "profile": C["profile"]["name"], "skills": sk.combo_key(self.ctl["skills"]), "open_ts": time.time(),
                # jenis entri: evaluasi profil TIDAK boleh mencampur tombol manual / entri candle berjalan dgn entri bar-close
                "entry": "manual" if manual else ("intrabar" if intrabar else "bar")}
            b = self.st.setdefault("bursts", {}).setdefault(key, {"n": 0, "left": 0, "pnl": 0.0})
            b["n"] += 1
            b["left"] += 1
            first_px = first_px or res[1]
            self.log_burst_ticket(sym, key, k + 1, n, res[0], sig, lot, res[1], sl, bar_t, tf, t_burst)
            log.info(f"ENTRY  {sym} {'BUY ' if sig == 1 else 'SELL'}{f' [{k+1}/{n}]' if n > 1 else ''} "
                     f"{lot} lot @ {res[1]}  SL {sl} ({ex['sl_atr']}xATR"
                     f"{' dijepit min/maks' if abs(sl_dist - ex['sl_atr'] * a) > 1e-9 else ''}={sl_dist:.2f})"
                     f"{f'  TP {tp}' if tp else ''}  {rtxt}  sesi {ses}  spread {spread}"
                     f"{'  [MANUAL dari MT5]' if manual else ''}")
        if opened:
            self.st.setdefault("last_entry_bar", {})[sym] = bar_t
            self.st.setdefault("last_entry_ts", {})[sym] = time.time()
            try:
                srl = sr_mtf.compute(sym, self.cfg.get("sr"), first_px)
                lv = sr_mtf.levels_for_plan(srl)
                self.st.setdefault("plans", {})[key] = plan_mod.build(
                    sym, sig, first_px, sl_dist, a, lv, lv, "AKTIF", "dieksekusi", time.time(), key,
                    why.get("score"), why.get("skill_scores"), labels=sr_mtf.level_labels(srl), tf=tf,
                    sl_clamped=abs(sl_dist - ex["sl_atr"] * a) > 1e-9)
            except Exception as e:
                log.warning(f"PLAN   {sym} gagal dibuat: {e}")
            self.st.setdefault("entry_info", {})[key] = {
                "feats": why["features"], "symbol": sym, "dir": sig, "open_ts": time.time(),
                "open_time": utcnow().isoformat(timespec="seconds"), "manual": manual,
                # risiko riil per tiket x jumlah tiket yang benar2 terbuka
                "risk_money": self.last_burst_risk_pct / 100 * eq / (n if self.multiply(sym) else 1) * opened}
            self.write_entry_reason(sym, sig, bar_t, first_px, sl_dist, opened, lot, rtxt, ses, spread, why)
            return f"ENTRY {opened}x{' (manual)' if manual else ''} @ {round(first_px, info.digits)}", opened
        return "order gagal", 0

    # ---------------------------------------------------------- perintah BUY/SELL dari indikator MT5
    def bridge_dir(self):
        """<MT5 Common>\\Files\\<bridge.folder> — tempat indikator menulis perintah & membaca hasil."""
        if getattr(self, "_bdir", None) is None:
            ti = mt5.terminal_info()
            if not ti:
                return None
            from pathlib import Path
            self._bdir = Path(ti.commondata_path) / "Files" / self.cfg.get("bridge", {}).get("folder", "XAUUSD_EXNESS")
            self._bdir.mkdir(parents=True, exist_ok=True)
        return self._bdir

    def write_bot_heartbeat(self):
        """Indikator MT5 membaca ini untuk tahu bot hidup & parameter pratinjau (SL x ATR, jumlah tiket)."""
        d = self.bridge_dir()
        if not d:
            return
        rk, ex = self.cfg["risk"], self.cfg["exit"]
        # kolom 6: profil & parameter pratinjau PER simbol (SYM=PROFIL:slATR:burst|...), kolom 7: skill OFF
        per = "|".join(f"{s}={c['profile']['name']}:{c['exit']['sl_atr']}:{int(c['risk'].get('burst_size', 1))}"
                       for s, c in ((s, self.C(s)) for s in self.symbols))
        try:
            (d / "bot_heartbeat.txt").write_text(
                f"{int(time.time())};{self.mode};{ex['sl_atr']};{int(rk.get('burst_size', 1))};"
                f"{rk['risk_per_trade_pct']};{','.join(s for s in self.symbols if self.is_active(s))};{per};"
                f"{','.join(k for k, v in self.ctl['skills'].items() if not v) or '-'}\n", encoding="ascii")
        except Exception:
            pass

    def process_commands(self, eq):
        d = self.bridge_dir()
        if not d:
            return
        for f in sorted(d.glob("cmd_*.txt"), key=lambda x: x.stat().st_mtime):
            try:
                txt = f.read_text(encoding="ascii", errors="replace").strip()
                f.unlink()
            except Exception:
                continue                                  # indikator masih menulis -> siklus berikut
            parts = txt.split(";")
            act = parts[0].upper() if parts else ""
            sym = parts[1] if len(parts) > 1 else ""
            ok, msg = self.manual_entry(act, sym, int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0, eq)
            try:
                (d / f"cmdres_{sym or 'x'}.txt").write_text(
                    f"{int(time.time())};{'OK' if ok else 'DITOLAK'};{act} {sym}: {msg.replace('—', '-')}\n", encoding="ascii",
                    errors="replace")
            except Exception:
                pass
            (log.warning if ok else log.error)(f"MANUAL {act} {sym} (tombol MT5) -> {'OK' if ok else 'DITOLAK'}: {msg}")
            audit(f"MT5 manual {act} {sym} -> {'OK' if ok else 'DITOLAK'}: {msg}", "mt5")

    def manual_entry(self, act, sym, ts, eq):
        """BUY/SELL dari indikator MT5: rem risiko sama persis dengan entry otomatis."""
        if act not in ("BUY", "SELL"):
            return False, f"aksi tidak dikenal '{act}'"
        if sym not in self.symbols or not self.is_active(sym):
            return False, (f"simbol {sym} tidak aktif di bot (aktif: "
                           f"{', '.join(s for s in self.symbols if self.is_active(s))}) — nyalakan dari dashboard")
        mc = self.cfg.get("manual", {})
        if not mc.get("enabled", True):
            return False, "entry manual dimatikan di config (manual.enabled=false)"
        if not ts or abs(time.time() - ts) > mc.get("max_age_sec", 30):
            return False, "perintah kedaluwarsa (bot tidak memprosesnya tepat waktu) — klik lagi"
        last = self.st.setdefault("manual_last", {}).get(sym, 0)
        if time.time() - last < mc.get("cooldown_sec", 10):
            return False, f"terlalu cepat — tunggu {mc.get('cooldown_sec', 10)} dtk antar perintah"
        self.st["manual_last"][sym] = time.time()
        C, sc, grp = self.C(sym), self.cfg["symbols"][sym], risk_group(self.cfg, sym)
        rk, scfg = C["risk"], C["strategy"]
        info, t = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        if not info or not t:
            return False, "harga simbol tidak tersedia"
        tf = sc.get("timeframe", "M5")
        r = mt5.copy_rates_from_pos(sym, TF[tf], 1, min_bars(scfg) + 200)
        if r is None or len(r) < min_bars(scfg):
            return False, "data candle kurang"
        a = float(atr_fn(pd.DataFrame(r), scfg.get("atr_period", 14)).iloc[-1])
        bar_t = int(r[-1]["time"])
        sig = 1 if act == "BUY" else -1
        spread = round((t.ask - t.bid) / info.point)
        mine = self.ex.positions(sym)
        total = sum(1 for p in self.ex.positions() if risk_group(self.cfg, p.symbol) == grp)
        mult = self.multiply(sym)
        burst = max(1, int(rk.get("burst_size", 1)))
        max_sym = burst if mult else sc["max_positions"]
        n_need = burst if mult else 1
        per_pct = rk["risk_per_trade_pct"] * sc.get("risk_mult", 1.0)
        burst_r = per_pct * burst if mult else per_pct
        open_r = self.open_risk_pct(eq, grp)
        day_pct = self.day_pnl / self.st["day_start_equity"] * 100 if self.st.get("day_start_equity") else 0.0
        self.news_state = self.news.blocked(self.cfg["news_filter"], utcnow())
        blockers = self.entry_blockers(sym, mine, total, n_need, max_sym, open_r, burst_r, day_pct, spread,
                                       time.time() - t.time, strategy=False, sig=sig, px=float(r[-1]["close"]), atr_now=a)
        mb = mc.get("max_bursts_per_symbol", 0)
        if mb and sum(1 for k in self.st.get("bursts", {}) if k.startswith(sym + "@") and k.endswith("-M")) >= mb:
            blockers.append(f"sudah ada {mb} burst manual terbuka di {sym} (manual.max_bursts_per_symbol)")
        if mc.get("require_h1_trend"):
            h = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 1, 200)
            if h is not None and len(h) >= 60:
                e = pd.Series(h["close"]).ewm(span=mc.get("h1_ema", 50)).mean()
                up = e.iloc[-1] > e.iloc[-4]
                if (sig > 0) != up:
                    blockers.append(f"melawan tren H1 (EMA{mc.get('h1_ema', 50)} {'naik' if up else 'turun'}) — "
                                    "post-mortem: entry manual melawan tren H1 PF 0,39")
        if blockers:
            return False, "; ".join(blockers)
        ses = symbol_session(C, sc, utcnow())[1]
        status, opened = self.execute_entry(sym, sig, r, a, bar_t, eq, ses, spread, len(mine), total,
                                            open_r, day_pct, manual=True)
        return opened > 0, status

    # ---------------------------------------------------------- diagnosis non-entry
    def last_cross_info(self, df, scfg):
        """Kapan kandidat entry terakhir muncul dan filter mana yang menolaknya (untuk dashboard)."""
        try:
            cand = ENTRIES[scfg["entry"]["name"]](df, scfg["entry"])
            idx = cand[cand != 0].index
            if not len(idx):
                return None
            i = idx[-1]
            d = int(cand[i])
            failed = []
            for f in scfg.get("filters", []):
                if not f.get("enabled", True):
                    continue
                lo, so = FILTERS[f["name"]](df, f)
                if not bool((lo if d > 0 else so).fillna(False).loc[i]):
                    failed.append(f["name"])
            return {"time": int(df.time.loc[i]), "dir": "BUY" if d > 0 else "SELL",
                    "bars_ago": int(len(df) - 1 - df.index.get_loc(i)),
                    "result": "SINYAL" if not failed else "ditolak filter: " + ", ".join(failed)}
        except Exception as e:
            return {"error": str(e)}

    def alerts(self, ses_ok, ses):
        """Peringatan eksplisit untuk banner dashboard: apa yang MENGHALANGI entry sekarang."""
        a = []
        if FLAG_HALT.exists():
            a.append(["critical", "HALT: max drawdown tercapai — bot tidak entry sampai di-reset (start_bot.cmd reset)"])
        if self.st.get("day_halted"):
            a.append(["error", f"Rem rugi harian {self.cfg['risk']['daily_loss_limit_pct']}% tercapai — "
                               "tidak entry sampai hari UTC berikutnya (07:00 WIB)"])
        if FLAG_BURST_OFF.exists():
            a.append(["warn", "Burst DIMATIKAN manual — klik 'Aktifkan Burst' untuk menyalakan lagi"])
        if FLAG_PAUSE.exists():
            a.append(["warn", "PAUSE aktif — tidak ada entry baru"])
        for g, until in self.st.get("pause_group", {}).items():
            if time.time() < until:
                a.append(["warn", f"Jeda kalah beruntun grup {g} sampai {datetime.fromtimestamp(until):%H:%M} "
                                  "(grup lain tetap jalan)"])
        g = (self.st.get("gaps") or [None])[-1]
        if g and time.time() - g["to"] < 3600:
            a.append(["warn", f"Bot BARU BANGUN: tidak berjalan {(g['to'] - g['from']) / 60:.0f} menit sampai "
                              f"{datetime.fromtimestamp(g['to']):%H:%M} (standby laptop) — selama itu hanya SL server yang menjaga posisi"])
        if not self.conn.ok:
            a.append(["error", "MT5 TERPUTUS — bot mencoba menyambung ulang"])
        elif self.mode != "paper" and not mt5.terminal_info().trade_allowed:
            a.append(["error", "Algo Trading MT5 OFF — order akan ditolak broker"])
        if self.news_state[0]:
            a.append(["info", f"Filter berita aktif: {self.news_state[1]}"])
        if not ses_ok:
            a.append(["info", f"Semua simbol yang ON sedang di luar jadwalnya ({ses or '-'})"])
        return a

    # ---------------------------------------------------------- log burst (evaluasi frekuensi)
    def log_burst_ticket(self, sym, burst_id, k, n, ticket, direction, lot, price, sl, bar_t, tf, t_burst):
        """Satu baris per tiket: waktu UTC milidetik, jeda dari penutupan bar sinyal (latensi
        keputusan) dan jeda dari tiket pertama burst (kecepatan eksekusi burst)."""
        now = time.time()
        new = not BURST_LOG_PATH.exists()
        with open(BURST_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time_utc_ms", "mode", "symbol", "burst_id", "ticket_no", "burst_size", "ticket",
                            "dir", "lot", "price", "sl", "signal_bar_utc", "exec_tf",
                            "ms_since_bar_close", "ms_since_burst_start"])
            w.writerow([datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds"), self.mode,
                        sym, burst_id, k, n, ticket, "BUY" if direction == 1 else "SELL", lot, price, sl,
                        datetime.fromtimestamp(bar_t, timezone.utc).isoformat(timespec="seconds"), tf,
                        int((now - (bar_t + TF_SEC[tf])) * 1000), int((now - t_burst) * 1000)])

    def burst_status(self):
        """-> dict status Burst Mode untuk dashboard (ACTIVE hanya kalau burst benar-benar bisa menembak)."""
        rk = self.cfg["risk"]
        size, mode = int(rk.get("burst_size", 1)), rk.get("burst_mode", "split")
        why = ""
        if size <= 1:
            why = "burst_size = 1"
        elif FLAG_BURST_OFF.exists():
            why = "dimatikan manual (tombol)"
        elif FLAG_HALT.exists():
            why = "HALT max drawdown"
        elif FLAG_PAUSE.exists():
            why = "PAUSE"
        elif self.st.get("day_halted"):
            why = "rem rugi harian"
        elif not any(self.is_active(s) for s in self.symbols):
            why = "semua simbol dihentikan (switch)"
        stats = {"today": 0, "total": 0, "last": None, "avg_gap_min": None}
        try:
            mt = BURST_LOG_PATH.stat().st_mtime
            if getattr(self, "_burst_cache", (None,))[0] == (mt, self.st.get("day")):
                stats = self._burst_cache[1]
                raise StopIteration          # pakai cache (dipanggil tiap detik)
            b = pd.read_csv(BURST_LOG_PATH)
            b = b[(b["mode"] == self.mode) & (b["ticket_no"] == 1)]
            t = pd.to_datetime(b["time_utc_ms"], format="ISO8601")
            today = t[t.dt.strftime("%Y-%m-%d") == self.st.get("day", "")]
            stats = {"today": int(len(today)), "total": int(len(t)),
                     "last": t.iloc[-1].isoformat() if len(t) else None,
                     "avg_gap_min": round(float(t.diff().dt.total_seconds().mean() / 60), 1) if len(t) > 1 else None}
            self._burst_cache = ((mt, self.st.get("day")), stats)
        except (StopIteration, FileNotFoundError):
            pass
        except Exception as e:
            log.warning(f"BURST  gagal membaca burst_log: {e}")
        return {"active": not why, "reason": why, "size": size, "mode": mode,
                "risk_per_ticket_pct": rk["risk_per_trade_pct"],
                "exec_tf": sorted({self.cfg["symbols"][s].get("timeframe", "M5") for s in self.symbols if self.is_active(s)}),
                **stats}

    # ---------------------------------------------------------- analisis entry (explainable)
    def analyze_entry(self, sym, df, direction, atr_now):
        """Kumpulkan SEMUA alasan pendukung/penentang entry: dipakai filter SMC, skor pembelajaran
        dan panel 'Kenapa bot entry di sini?'. df = bar tertutup."""
        cfg = self.C(sym)           # modul skill generik OHLC — sama untuk emas & crypto
        k = cfg.get("smc", {}).get("swing_k", 3)
        ctx_df = bar_context(df, cfg["sessions"], k)
        row = ctx_df.iloc[-1]
        px = float(df.close.iloc[-1])
        # skill penilai yang ON saja yang dipanggil; bobotnya dinormalisasi otomatis (bot/skills/__init__.py)
        ctx = {"df": df, "row": row, "direction": direction, "atr": atr_now, "sym": sym, "swing_k": k,
               "learner": self.learner, "features": {}, "factors": [], "confidence": None}
        score, res = sk.score(ctx, self.ctl["skills"])
        L = lambda sid: res[sid]["lines"] if sid in res else [f"(skill {sk.REGISTRY[sid]['name']} OFF)"]
        rsi_v = float(row["rsi"])
        return {
            "features": ctx["features"], "confidence": ctx["confidence"], "factors": ctx["factors"],
            "score": score, "skill_scores": {s: r.get("score") for s, r in res.items()},
            "smc_bias": res["smc"].get("bias") if "smc" in res else None, "smc": L("smc"),
            "candles": L("candles"), "chart": L("structure"),
            "signal": [self.entry_text(cfg["strategy"]["entry"], direction),
                       (f"RSI(14) {rsi_v:.1f} di zona momentum" if any(f["name"] == "rsi_momentum" and f.get("enabled", True)
                                                                       for f in cfg["strategy"]["filters"])
                        else f"RSI(14) {rsi_v:.1f} (filter RSI nonaktif — mode risk taker)"),
                       f"ATR {atr_now:.2f} = {atr_now / px * 100:.2f}% harga (persentil {row['atr_pct']:.0%})",
                       f"Harga {'di atas' if direction > 0 else 'di bawah'} EMA200 (searah tren besar)"],
            "notes": [],
        }

    @staticmethod
    def entry_text(e, direction):
        """Deskripsi pemicu entry sesuai jenis entry yang dipakai config."""
        arah = "naik" if direction > 0 else "turun"
        if e["name"] == "ema_cross":
            return f"EMA {e.get('fast', 8)}/{e.get('slow', 21)} cross {arah} di bar tertutup"
        if e["name"] == "rsi_cross50":
            return f"RSI({e.get('rsi_period', 7)}) menembus 50 {arah}, searah EMA {e.get('fast', 8)}/{e.get('slow', 21)}"
        if e["name"] == "candle_break":
            return f"Candle tertutup menembus {'high' if direction > 0 else 'low'} candle sebelumnya (momentum)"
        if e["name"] == "interval_trend":
            return (f"EKSPERIMEN: entry terjadwal tiap {e.get('every_min', 30)} menit, searah harga vs "
                    f"EMA{e.get('ema', 21)} ({arah}) — tanpa sinyal")
        if e["name"] == "smc_mtf":
            mode = "Breakout Market" if e.get("execution_mode") == "market" else "Retest Limit FVG"
            return f"SMC MTF: Konfirmasi CHoCH M3 + {mode} ({arah}) di zona Order Block M15"
        if e["name"] == "bb_reversion":
            band = "bawah" if direction > 0 else "atas"
            dev = e.get("deviation", 2.0)
            p = e.get("period", 20)
            m = e.get("mode", "fade_touch")
            return f"Bollinger Bands Fade ({arah}): Sentuh pita {band} BB({p}, {dev}) [{m}] — antisipasi Mean-Reversion"
        return f"entry '{e['name']}' {arah}"

    def learning_gate_allowed(self):
        """Mode gate hanya boleh memengaruhi LIVE kalau validasi walk-forward backtest membuktikan manfaatnya."""
        if self.mode != "live":
            return True
        try:
            v = json.loads((ROOT / "backtest" / "results" / "learning_validation.json").read_text(encoding="utf-8"))
            fresh = (datetime.now() - datetime.fromisoformat(v["generated"])).days <= 30
            return bool(v.get("improves")) and fresh
        except Exception:
            return False

    def write_entry_reason(self, sym, direction, bar_t, price, sl_dist, tickets, lot, rtxt, ses, spread, why):
        rec = {"time": utcnow().isoformat(timespec="seconds"), "bar_time": bar_t, "symbol": sym,
               "dir": "BUY" if direction > 0 else "SELL", "price": price, "sl_dist": round(sl_dist, 3),
               "tickets": tickets, "lot": lot, "risk": rtxt, "session": ses, "spread": spread,
               "signal": why["signal"], "candles": why["candles"], "smc": why["smc"], "chart": why["chart"],
               "confidence": None if why["confidence"] is None else round(why["confidence"], 3),
               "factors": why["factors"], "notes": why["notes"], "mode": self.mode,
               "smc_mode": self.C(sym)["smc"].get("filter_mode", "score"),
               "learning_mode": self.cfg.get("learning", {}).get("mode", "observe"),
               "profile": self.C(sym)["profile"]["name"], "score": why.get("score"),
               "skill_scores": why.get("skill_scores"), "skills": sk.combo_key(self.ctl["skills"])}
        with open(ENTRIES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        conf = f"{why['confidence']:.0%}" if why["confidence"] is not None else "belum ada model"
        comp = f"{why['score']:.0f}%" if why.get("score") is not None else "-"
        smcs = (why.get("skill_scores") or {}).get("smc")
        # 'skor gabungan' = yang dibandingkan dgn min_score profil; 'model belajar' = peluang menang model learning saja
        log.info(f"WHY    {sym} {rec['dir']}: skor gabungan {comp} (min {self.C(sym)['profile']['min_score']}%; "
                 f"model belajar {conf}; SMC {'-' if smcs is None else f'{smcs:+.1f}'}) | SMC: {'; '.join(why['smc'][:2])} | "
                 f"candle: {', '.join(why['candles']) or '-'}")

    def control_state(self):
        """Profil, skill & statistik sinyal untuk dashboard."""
        eff = {}
        for s in self.symbols:
            c = self.C(s)
            eff[s] = {"profile": c["profile"]["name"], "risk_pct": c["risk"]["risk_per_trade_pct"],
                      "burst": c["risk"]["burst_size"], "bursts_per_symbol": c["profile"]["bursts_per_symbol"],
                      "group_slots": c["risk"]["max_positions_total"], "group_risk_pct": c["risk"].get("max_total_risk_pct"),
                      "min_score": c["profile"]["min_score"], "cooldown_min": c["profile"]["cooldown_min"],
                      "intrabar": c["profile"]["intrabar_entry"], "smc": c["smc"].get("filter_mode"),
                      "trail_atr": c["exit"]["trail_atr"]}
        day = self.st.get("sigstats", {}).get(utcnow().date().isoformat(), {})
        dp = self.day_pnl / self.st["day_start_equity"] * 100 if self.st.get("day_start_equity") else 0.0
        lim = self.cfg["risk"]["daily_loss_limit_pct"]
        return {"profile": self.ctl["profile"], "skills": self.ctl["skills"], "effective": eff,
                "override": self.ctl.get("override", {}), "force_run": self.cfg.get("force_run", {}),
                "active_strategy": self.ctl.get("active_strategy", self.cfg.get("active_strategy", "smc_mtf")),
                "budget": {"day_pct": round(dp, 3), "limit_pct": lim, "used_pct": round(max(0.0, -dp) / lim * 100, 1)},
                "registry": {k: {kk: v.get(kk) for kk in ("name", "desc", "kind", "deps", "layers", "warn_off", "weight")}
                             for k, v in sk.REGISTRY.items()},
                "layers": sk.layers_enabled(self.ctl["skills"]),
                "signals_today": day, "rejects": self.st.get("sig_rejects", [])[-15:],
                "account_cap_pct": self.cfg["risk"].get("max_account_open_risk_pct"),
                "open_risk_account_pct": self.open_risk_pct(self.last_eq) if getattr(self, "last_eq", None) else 0}

    # ---------------------------------------------------------- state utk dashboard
    def write_state(self, eq, bal, day_pnl, dd):
        acc = mt5.account_info()
        tot, today, per_sym = journal_stats(self.mode, self.st.get("day", ""))
        rk = self.cfg["risk"]
        positions = self.ex.positions()
        zero = {"trades": 0, "wins": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0}
        symbols = {}
        now = utcnow()
        in_ses = []
        for sym, sc in self.cfg["symbols"].items():
            C = self.C(sym)
            avail, on = sym in self.symbols, self.is_active(sym)
            mine = [p for p in positions if p.symbol == sym]
            t = mt5.symbol_info_tick(sym) if avail else None
            s_ok, s_name = symbol_session(C, sc, now)
            # risiko searah gabungan (Prioritas 3): per arah, kalau SEMUA posisi kena SL sekarang
            vpp = value_per_price_unit(sym) if avail and mine else 0
            risk_same_dir = {}
            for dr, positions_dir in (("BUY", [p for p in mine if p.dir == 1]), ("SELL", [p for p in mine if p.dir == -1])):
                if not positions_dir:
                    continue
                vol = sum(p.volume for p in positions_dir)
                risk_money = sum(abs(p.price_open - p.sl) * p.volume * vpp for p in positions_dir if p.sl)
                risk_same_dir[dr] = {"count": len(positions_dir), "volume": round(vol, 2),
                                     "avg_entry": round(sum(p.price_open * p.volume for p in positions_dir) / vol, 3) if vol else 0,
                                     "risk_if_all_sl": round(risk_money, 2),
                                     "risk_if_all_sl_pct": round(risk_money / eq * 100, 3) if eq else 0}
            if on and avail and s_ok:
                in_ses.append(sym)
            symbols[sym] = dict(self.sym_status.get(sym, {}) if on else {"status": "DIHENTIKAN (switch dashboard)"},
                                enabled=sc.get("enabled", False), active=on, available=avail,
                                group=risk_group(self.cfg, sym), timeframe=sc.get("timeframe", "M5"),
                                session=s_name, session_ok=s_ok, trade_days=sc.get("trade_days"),
                                params={"risk_pct": C["risk"]["risk_per_trade_pct"], "burst": C["risk"].get("burst_size", 1),
                                        "sl_atr": C["exit"]["sl_atr"], "trail_atr": C["exit"]["trail_atr"],
                                        "max_pos_group": C["risk"]["max_positions_total"]},
                                override=self.override(sym) if avail else {}, budget_fit=self._budget_fit.get(sym),
                                risk_same_dir=risk_same_dir,
                                positions=len(mine), floating=sum(p.profit for p in mine),
                                direction=(("BUY" if mine[0].dir == 1 else "SELL") if mine else ""),
                                tick_age=(time.time() - t.time) if t else None,
                                stats=per_sym.get(sym, {"total": zero, "today": zero}))
        ses_ok = bool(in_ses)
        ses = ", ".join(in_ses) if in_ses else "tidak ada simbol ON di jadwalnya"
        data = {
            "ts": time.time(), "pid": os.getpid(), "mode": self.mode,
            "broker": getattr(self, "broker_info", None) or self.cfg.get("broker"),
            "account": {"login": acc.login, "server": acc.server, "currency": acc.currency,
                        "type": MODE_NAMES.get(acc.trade_mode), "balance": acc.balance,
                        "equity": acc.equity} if acc else None,
            "bot_equity": eq, "bot_balance": bal,
            "connection": {"ok": self.conn.ok, "last_ok": self.conn.last_ok,
                           "reconnects": self.conn.reconnects,
                           "algo_trading": bool(mt5.terminal_info().trade_allowed) if self.conn.ok else False},
            "day": {"date": self.st.get("day"), "start_equity": self.st.get("day_start_equity"),
                    "pnl": day_pnl, "pnl_pct": day_pnl / self.st["day_start_equity"] * 100 if self.st.get("day_start_equity") else 0,
                    "limit_pct": self.cfg["risk"]["daily_loss_limit_pct"], "halted": self.st.get("day_halted", False)},
            "drawdown_pct": dd, "max_drawdown_pct": self.cfg["risk"]["max_drawdown_pct"],
            "stats_total": tot, "stats_today": today,
            "positions": [{"ticket": p.ticket, "symbol": p.symbol, "dir": "BUY" if p.dir == 1 else "SELL",
                           "volume": p.volume, "open": p.price_open, "sl": p.sl, "tp": p.tp, "time": int(p.time),
                           "profit": p.profit, "age_min": (time.time() - p.time) / 60, **self.position_checks(p)}
                          for p in positions],
            # posisi yang baru ditutup — marker tetap tampil `closed_marker_minutes` lalu hilang
            "recent_closed": [{"ticket": c["ticket"], "symbol": c["symbol"], "dir": "BUY" if c["dir"] == 1 else "SELL",
                               "volume": c["volume"], "open": c["open"], "close": c["close"], "pnl": c["pnl"],
                               "why": c["why"], "time": int(c["open_time"]), "close_time": int(c["close_time"])}
                              for c in self.st.get("recent_closed", [])
                              if time.time() - c["close_time"] <= self.cfg.get("dashboard", {}).get("closed_marker_minutes", 10) * 60],
            "alerts": self.alerts(ses_ok, ses),
            "forced_scan": self.forced_scan,
            "symbols": symbols,
            "risk": {"open_pct": self.open_risk_pct(eq), "cap_pct": rk.get("max_total_risk_pct"),
                     "burst_size": rk.get("burst_size", 1), "burst_mode": rk.get("burst_mode", "split"),
                     "risk_per_ticket_pct": rk["risk_per_trade_pct"],
                     "bursts_open": len(self.st.get("bursts", {})),
                     # batas posisi & risiko terbuka dihitung PER GRUP (emas bersama, tiap crypto sendiri)
                     "groups": {g: {"open_pct": self.open_risk_pct(eq, g),
                                    "cap_pct": self.C(s)["risk"].get("max_total_risk_pct"),
                                    "positions": sum(1 for p in positions if risk_group(self.cfg, p.symbol) == g),
                                    "max_positions": self.C(s)["risk"]["max_positions_total"]}
                                for g, s in {risk_group(self.cfg, s): s for s in self.symbols}.items()}},
            "burst": self.burst_status(),
            "control": self.control_state(),
            "news_engine": self.news_engine.state() if getattr(self, "news_engine", None) else {},
            "plans": self.all_plans(),
            "levels": self.level_cards_out(),
            "guards": {"pause": FLAG_PAUSE.exists(), "halt": FLAG_HALT.exists(),
                       "loss_pause_until": self.pause_until(), "loss_pause_group": self.st.get("pause_group", {}),
                       "session": ses, "session_ok": ses_ok,
                       "news_block": self.news_state[0], "news_info": self.news_state[1],
                       "news_next": self.news.upcoming(self.cfg["news_filter"])},
            "recent": list(RECENT.buf),
        }
        try:
            write_json_atomic(STATE_PATH, data)
            write_json_atomic(BOTSTATE_PATH, self.st)
        except Exception:
            pass    # dashboard sedang membaca file; tulis lagi siklus berikut

    # ---------------------------------------------------------- loop utama
    def run(self):
        self.start()
        if "--watchdog" in sys.argv:
            log.warning("WATCHDOG bot dinyalakan otomatis oleh Scheduled Task (sebelumnya tidak berjalan)")
        ka = self.cfg.get("loop", {}).get("keep_awake", "display")
        if ka != "off":
            log.info(f"POWER  keep-awake '{ka}' {'aktif' if keep_awake(ka) else 'GAGAL'} — Windows tidak idle-sleep "
                     "selama bot jalan (tutup lid / Sleep manual tetap menidurkan)")
        while True:
            try:
                if FLAG_STOP.exists():
                    try:
                        audit(f"bot menerima EMERGENCY_STOP (isi flag: {FLAG_STOP.read_text(encoding='utf-8', errors='replace').strip()[:60]!r}, "
                              f"dibuat {datetime.fromtimestamp(FLAG_STOP.stat().st_mtime):%H:%M:%S})", "bot")
                    except Exception:
                        pass
                    log.critical("STOP   EMERGENCY STOP diterima — menutup semua posisi bot")
                    for _ in range(3):
                        self.close_all("emergency")
                        if not self.ex.positions():
                            break
                        time.sleep(2)
                    left = self.ex.positions()
                    log.critical(f"STOP   bot berhenti. Posisi tersisa: {len(left)}"
                                 f"{' — TUTUP MANUAL di MT5!' if left else ''}")
                    self.write_state(*self.equity(), 0, 0)
                    break
                loop = self.cfg["loop"]
                now_ts = time.time()
                if self._last_loop and now_ts - self._last_loop > 120:
                    gap = {"from": self._last_loop, "to": now_ts}
                    log.warning(f"TIDUR  bot tidak berjalan {(now_ts - self._last_loop) / 60:.0f} menit "
                                "(laptop standby/hibernate/lid ditutup?) — posisi terbuka hanya dijaga SL di server "
                                "selama itu; trailing/BE dilanjutkan sekarang")
                    self.st["gaps"] = (self.st.get("gaps", []) + [gap])[-20:]
                    self.intra.clear()                 # timer konfirmasi intrabar dari sebelum tidur tidak berlaku
                self._last_loop = now_ts
                if not self.conn.check():
                    time.sleep(loop["manage_interval_sec"])
                    continue
                eq, bal = self.equity()
                if eq is None:
                    continue
                self.last_eq = eq
                self.roll_day(eq)
                if self.cfg.get("learning", {}).get("enabled", True):
                    self.learner.maybe_daily()
                self.ex.update()
                self.record_closed()
                day_pnl, dd = self.check_guards(eq)
                self.manage()
                # ---- tombol dashboard: Matikan / Aktifkan Burst
                off = FLAG_BURST_OFF.exists()
                if off != self._burst_off_prev:
                    if self._burst_off_prev is not None:
                        log.warning(f"KONTROL burst {'DIMATIKAN — tidak ada entry baru, posisi tetap dikelola' if off else 'DIAKTIFKAN lagi'}")
                        audit("bot melihat BURST " + ("OFF" if off else "ON"), "bot")
                    self._burst_off_prev = off
                self.process_commands(eq)          # tombol BUY/SELL dari indikator MT5
                self.process_control()             # ganti profil / skill dari dashboard & CMD (+ ACK)
                forced = FLAG_BURST_NOW.exists()
                if forced:
                    FLAG_BURST_NOW.unlink(missing_ok=True)
                    log.warning("KONTROL 'Aktifkan Burst Sekarang' — evaluasi sinyal SEGERA (semua rem risiko tetap berlaku)")
                if forced or time.time() - self.last_scan >= loop["scan_interval_sec"]:
                    self.last_scan = time.time()
                    self.cfg = load_config()       # perubahan config aktif tanpa restart
                    if getattr(self, "broker_info", None):
                        apply_broker_profile(self.cfg, self.broker_info)
                    self.scan(eq)
                    if forced:
                        self.forced_scan = {"time": time.time(),
                                            "result": {k: v.get("status") for k, v in self.sym_status.items()}}
                        log.warning("KONTROL hasil evaluasi segera: " +
                                    "  ".join(f"{k}: {v}" for k, v in self.forced_scan["result"].items()))
                        audit("hasil evaluasi segera: " + json.dumps(self.forced_scan["result"], ensure_ascii=False), "bot")
                if time.time() - self.last_state_write >= loop.get("state_write_sec", 1):
                    self.last_state_write = time.time()
                    self.write_state(eq, bal, day_pnl, dd)
                    self.write_bot_heartbeat()
                if time.time() - self.last_status_log >= 60:
                    self.last_status_log = time.time()
                    s = "  ".join(f"{k}:{v.get('status', '-')}" for k, v in self.sym_status.items())
                    log.info(f"STATUS equity {eq:,.2f}  hari ini {day_pnl:+,.2f}  posisi {len(self.ex.positions())}  |  {s}")
                time.sleep(loop["manage_interval_sec"])
            except KeyboardInterrupt:
                log.info("STOP   Ctrl+C — bot berhenti. Posisi terbuka TETAP ada (SL terpasang"
                         f"{' di server' if self.mode != 'paper' else ', disimpan untuk sesi paper berikut'}).")
                break
            except Exception as e:
                log.exception(f"ERROR  {e}")
                time.sleep(5)
        release_lock()
        mt5.shutdown()


# ================================================================ --check
def check():
    cfg = load_config()
    c = Connection(cfg.get("mt5", {}))
    if not c.connect():
        print("MT5 tidak tersambung. Buka terminal MT5 & login dulu.")
        return
    acc = mt5.account_info()
    b_info = detect_broker()
    if b_info:
        apply_broker_profile(cfg, b_info)
        print(f"BROKER {b_info['name']} (server: {acc.server}) | komisi: ${b_info['commission_per_lot_usd']:.2f}/lot | spread: {b_info['typical_spread_xauusd']} pts")
        print(f"SIMBOL emas tersedia: {b_info['available_gold_symbols']} -> utama: {b_info['primary_gold_symbol']}")
    print(f"AKUN   #{acc.login} {acc.server} {acc.currency} {MODE_NAMES.get(acc.trade_mode)} "
          f"equity {acc.equity:,.2f}  leverage 1:{acc.leverage}")
    ti = mt5.terminal_info()
    print(f"MT5    terhubung={ti.connected}  AlgoTrading={'ON' if ti.trade_allowed else 'OFF (nyalakan tombol Algo Trading!)'}")
    print(f"MODE   config: {cfg['mode']}   live_trading_confirmed={cfg.get('live_trading_confirmed')}")
    b = Bot.__new__(Bot)
    b.cfg = cfg
    b.ctl = prof.read_control(cfg)
    b._news_adj = {}
    print(f"PROFIL global {b.ctl['profile']['global']}  per simbol {b.ctl['profile']['per_symbol'] or '-'}  "
          f"skill OFF: {', '.join(k for k, v in b.ctl['skills'].items() if not v) or '-'}")
    act = read_active(cfg)
    now = utcnow()
    print(f"\n{'simbol':10}{'ON':>4}{'ada':>5}{'spread':>8}{'ATR':>10}{'SL':>9}{'lot':>7}  {'risiko':<34}{'jadwal':<30}sinyal")
    for sym, sc in cfg["symbols"].items():
        ok = mt5.symbol_select(sym, True) and mt5.symbol_info(sym) is not None
        if not ok:
            print(f"{sym:10}{'ya' if act.get(sym) else '-':>4}{'TIDAK':>5}")
            continue
        C = b.C(sym)
        info, t = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        r = mt5.copy_rates_from_pos(sym, TF[sc['timeframe']], 1, min_bars(C["strategy"]) + 200)
        d = build_signals(pd.DataFrame(r), C["strategy"])
        a = float(d.atr.iloc[-1])
        sl = b.sl_distance(sym, a, float(d.close.iloc[-1]))
        lot, txt = b.calc_lot(sym, sl, acc.equity, C["risk"]["burst_size"]) or (None, "-")
        sig = {1: "BUY", -1: "SELL", 0: "-"}[int(d.signal.iloc[-1])]
        s_ok, s_name = symbol_session(C, sc, now)
        closed = time.time() - t.time > 180                  # pasar broker tutup (mis. XAUUSD saat weekend)
        jadwal = f"PASAR TUTUP (tick {int((time.time() - t.time) / 60)} mnt)" if closed else \
            ("BUKA " if s_ok else "di luar jadwal ") + str(s_name)
        print(f"{sym:10}{'ya' if act.get(sym) else '-':>4}{'ya':>5}{round((t.ask - t.bid) / info.point):>8}{a:>10.2f}"
              f"{sl:>9.2f}{str(lot):>7}  {txt:<34}{jadwal:<30}{sig}")
    mt5.shutdown()


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        Bot().run()
