"""Path, config, logging — dipakai bot & dashboard."""
import json
import os
import logging
import sys
from collections import deque
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# XAUUSD_CONFIG: pakai file config lain (mis. profil) tanpa menimpa config\config.json
CFG_PATH = Path(os.environ.get("XAUUSD_CONFIG") or ROOT / "config" / "config.json")
# XAUUSD_LOG_DIR: folder log alternatif (untuk uji coba tanpa mengganggu bot yang sedang jalan)
LOG_DIR = Path(os.environ.get("XAUUSD_LOG_DIR") or ROOT / "logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

STATE_PATH = LOG_DIR / "state.json"          # dibaca dashboard (ditulis tiap ~1 detik)
BOTSTATE_PATH = LOG_DIR / "bot_state.json"   # memori bot lintas restart
PAPER_PATH = LOG_DIR / "paper_positions.json"
JOURNAL_PATH = LOG_DIR / "trades.csv"
BURST_LOG_PATH = LOG_DIR / "burst_log.csv"    # tiap tiket burst, timestamp milidetik
NEWS_CACHE = LOG_DIR / "news_cache.json"
LOCK_PATH = LOG_DIR / "bot.lock"
FLAG_STOP = LOG_DIR / "EMERGENCY_STOP"       # ada = tutup semua posisi bot + bot keluar
FLAG_PAUSE = LOG_DIR / "PAUSE"               # ada = tidak entry baru, posisi tetap dikelola
FLAG_HALT = LOG_DIR / "HALT"                 # dibuat bot saat max drawdown tercapai
FLAG_BURST_OFF = LOG_DIR / "BURST_OFF"       # tombol "Matikan Burst": stop entry baru, posisi tetap dikelola
FLAG_BURST_NOW = LOG_DIR / "BURST_NOW"       # tombol "Aktifkan Burst Sekarang": evaluasi sinyal SEGERA (tetap lewat semua rem)
CONTROL_AUDIT = LOG_DIR / "control_audit.log"  # jejak audit setiap tombol kontrol dipakai
SYMBOLS_ACTIVE = LOG_DIR / "symbols_active.json"  # on/off trading per simbol (tombol dashboard / start --symbols)

# bagian config global yang boleh ditimpa per simbol di symbols.<SYM>.<bagian>
SYMBOL_OVERRIDES = ("exit", "strategy", "sessions", "risk")


def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def sym_cfg(cfg, sym):
    """Config efektif untuk satu simbol: bagian global exit/strategy/sessions/risk ditimpa
    symbols.<sym>.<bagian> (kalau ada). Simbol tanpa override = persis config global.
    Catatan: 'sessions' diganti UTUH (bukan digabung) supaya jadwal simbol benar-benar independen."""
    sc = cfg["symbols"].get(sym, {})
    out = dict(cfg)
    for part in SYMBOL_OVERRIDES:
        if isinstance(sc.get(part), dict):
            over = {k: v for k, v in sc[part].items() if not k.startswith("_")}
            out[part] = over if part == "sessions" else _merge(cfg.get(part, {}), over)
    return out


def risk_group(cfg, sym):
    """Simbol dalam grup yang sama berbagi batas posisi & risiko terbuka (mis. semua emas = 'gold',
    karena harganya bergerak identik). Tanpa risk_group = grup sendiri."""
    return cfg["symbols"].get(sym, {}).get("risk_group", sym)


def read_active(cfg):
    """{simbol: bool} on/off trading saat ini. Belum ada file -> ikut symbols.<sym>.enabled."""
    act = {s: bool(v.get("enabled")) for s, v in cfg["symbols"].items()}
    try:
        act.update({k: bool(v) for k, v in json.loads(SYMBOLS_ACTIVE.read_text(encoding="utf-8")).items() if k in act})
    except Exception:
        pass
    return act


def write_active(act):
    write_json_atomic(SYMBOLS_ACTIVE, act)


def audit(action, source):
    """Catat pemakaian tombol kontrol (web / konsol / bot) dengan timestamp milidetik."""
    from datetime import datetime as _dt
    with open(CONTROL_AUDIT, "a", encoding="utf-8") as f:
        f.write(f"{_dt.now().isoformat(timespec='milliseconds')}  {source:<8} {action}\n")

_last_good_cfg = {}


def load_config():
    """Baca config. Kalau file sedang rusak (mis. salah ketik saat diedit),
    pakai versi terakhir yang valid supaya bot tidak mati di tengah jalan."""
    global _last_good_cfg
    try:
        _last_good_cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        if not _last_good_cfg:
            raise
        logging.getLogger("bot").error(f"CONFIG rusak ({e}) — memakai versi terakhir yang valid")
    return _last_good_cfg


class _Fmt(logging.Formatter):
    LV = {"INFO": " ", "WARNING": "!", "ERROR": "x", "CRITICAL": "X", "DEBUG": "."}

    def format(self, r):
        return (f"{self.formatTime(r, '%Y-%m-%d %H:%M:%S')}.{int(r.msecs):03d} "
                f"{self.LV.get(r.levelname, '?')} {r.getMessage()}")


class _Recent(logging.Handler):
    """Simpan 20 baris terakhir untuk ditampilkan dashboard."""
    def __init__(self):
        super().__init__()
        self.buf = deque(maxlen=20)

    def emit(self, r):
        self.buf.append(f"{self.formatter.formatTime(r, '%H:%M:%S')} {r.getMessage()}")


RECENT = _Recent()


def setup_logging(name="bot"):
    """name = prefix file log & nama logger (bot -> bot.log/error.log, bridge -> bridge.log/bridge_error.log)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    fmt = _Fmt()
    # log eksekusi harian: bot.log dirotasi tiap tengah malam -> bot.log.YYYY-MM-DD
    fh = TimedRotatingFileHandler(LOG_DIR / f"{name}.log", when="midnight", backupCount=90, encoding="utf-8")
    eh = TimedRotatingFileHandler(LOG_DIR / ("error.log" if name == "bot" else f"{name}_error.log"),
                                  when="midnight", backupCount=90, encoding="utf-8")
    eh.setLevel(logging.WARNING)
    sh = logging.StreamHandler(sys.stdout)
    for h in (fh, eh, sh, RECENT):
        h.setFormatter(fmt)
    lg = logging.getLogger(name)
    lg.setLevel(logging.INFO)
    for h in (fh, eh, sh, RECENT):
        lg.addHandler(h)
    return lg


def write_json_atomic(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def config_fingerprint(cfg):
    """Sidik jari bagian config yang MEMENGARUHI hasil trading. Backtest menyimpannya;
    mode live menolak jalan kalau config sekarang berbeda dari yang terakhir di-backtest."""
    import hashlib
    keys = {"strategy": cfg.get("strategy"), "exit": cfg.get("exit"),
            "risk": {k: v for k, v in cfg.get("risk", {}).items() if not k.startswith("_")},
            "smc": {k: v for k, v in cfg.get("smc", {}).items() if not k.startswith("_")},
            "sessions": cfg.get("sessions"),
            "symbols": {s: {k: v for k, v in c.items() if not k.startswith("_")}
                        for s, c in cfg.get("symbols", {}).items() if c.get("enabled")}}
    return hashlib.sha1(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:12]
