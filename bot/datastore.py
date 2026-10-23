"""Cache bar MT5 lokal (SQLite) untuk histori panjang di dashboard.

- unduh bertahap (chunk per N hari) -> tidak kena batas 'Max bars in chart' per permintaan
- update inkremental: hanya bar setelah bar terakhir di cache
- window(): ambil rentang waktu, di-resample di server (gabung k bar -> 1 candle OHLC) sampai <= max_points
Sumber SELALU MT5 (copy_rates_range); histori dibatasi riwayat broker & 'Max bars in chart' terminal.
"""
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import MetaTrader5 as mt5

from .common import ROOT

DB = ROOT / "backtest" / "data" / "bars.sqlite"
TFS = {"M1": (mt5.TIMEFRAME_M1, 60, 15), "M5": (mt5.TIMEFRAME_M5, 300, 45), "M15": (mt5.TIMEFRAME_M15, 900, 150),
       "M30": (mt5.TIMEFRAME_M30, 1800, 300), "H1": (mt5.TIMEFRAME_H1, 3600, 600), "H4": (mt5.TIMEFRAME_H4, 14400, 800)}
_lock = threading.Lock()


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    c.execute("CREATE TABLE IF NOT EXISTS bars(sym TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, "
              "PRIMARY KEY(sym, tf, t)) WITHOUT ROWID")
    return c


def sync(sym, tf, days=730, mt5_lock=None):
    """Isi/perbarui cache. -> jumlah bar baru."""
    code, sec, chunk = TFS[tf]
    with _lock:
        con = _db()
        last = con.execute("SELECT MAX(t) FROM bars WHERE sym=? AND tf=?", (sym, tf)).fetchone()[0]
        now = datetime.now(timezone.utc)
        a = datetime.fromtimestamp(last, timezone.utc) if last else now - timedelta(days=days)
        new = 0
        while a < now:
            b = min(a + timedelta(days=chunk), now + timedelta(minutes=5))
            if mt5_lock:
                with mt5_lock:
                    r = mt5.copy_rates_range(sym, code, a, b)
            else:
                r = mt5.copy_rates_range(sym, code, a, b)
            if r is not None and len(r):
                con.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
                                [(sym, tf, int(x["time"]), float(x["open"]), float(x["high"]), float(x["low"]),
                                  float(x["close"]), float(x["tick_volume"])) for x in r])
                new += len(r)
            a = b
        con.commit()
        con.close()
    return new


def info(sym, tf):
    con = _db()
    n, t0, t1 = con.execute("SELECT COUNT(*), MIN(t), MAX(t) FROM bars WHERE sym=? AND tf=?", (sym, tf)).fetchone()
    con.close()
    return {"bars": n, "first": t0, "last": t1}


def window(sym, tf, t0=None, t1=None, max_points=2000):
    """-> dict candles (<= max_points, resample k bar), faktor k, rentang data tersedia."""
    con = _db()
    q = "SELECT t,o,h,l,c FROM bars WHERE sym=? AND tf=?" + (" AND t>=?" if t0 else "") + (" AND t<=?" if t1 else "") + " ORDER BY t"
    rows = con.execute(q, [sym, tf] + ([int(t0)] if t0 else []) + ([int(t1)] if t1 else [])).fetchall()
    meta = info(sym, tf)
    con.close()
    if not rows:
        return {"t": [], "o": [], "h": [], "l": [], "c": [], "k": 1, **meta}
    a = np.array(rows)
    k = max(1, int(np.ceil(len(a) / max_points)))
    if k > 1:                                    # resample di server: OHLC tiap k bar
        n = len(a) // k * k
        g = a[len(a) - n:].reshape(-1, k, 5)
        a = np.column_stack([g[:, 0, 0], g[:, 0, 1], g[:, :, 2].max(1), g[:, :, 3].min(1), g[:, -1, 4]])
    return {"t": a[:, 0].astype(int).tolist(), "o": a[:, 1].round(3).tolist(), "h": a[:, 2].round(3).tolist(),
            "l": a[:, 3].round(3).tolist(), "c": a[:, 4].round(3).tolist(), "k": k, "tf_sec": TFS[tf][1], **meta}
