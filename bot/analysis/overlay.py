"""
Satu sumber untuk semua objek gambar (dipakai dashboard web DAN bridge MT5).

Semua koordinat dalam WAKTU BAR (epoch detik, waktu server MT5) + harga, dan tiap objek
punya `id` STABIL (jenis + waktu titik awal + arah), sehingga penggambar (browser / indikator
MQL5) bisa memperbarui objek yang sama dan menghapus objek yang sudah tidak ada/batal.

Analisis hanya memakai bar TERTUTUP; candle yang sedang berjalan cuma ikut digambar.
"""
import numpy as np
import pandas as pd

from ..indicators import ema
from . import candles as cdl, structure, smc

TF_SEC = {"M1": 60, "M3": 180, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
INACTIVE = ("batal", "terisi", "ditembus", "selesai")


def build(full: pd.DataFrame, tf: str, bars: int = 300, swing_k: int = 3, struct_bars: int = 300):
    """full = rates MT5 (bar terakhir = candle berjalan). -> dict objek berbasis waktu."""
    sec = TF_SEC[tf]
    closed = full.iloc[:-1].reset_index(drop=True)
    T = closed.time.values.astype(np.int64)
    t_now = int(full.time.iloc[-1]) + sec                    # ujung kanan (akhir candle berjalan)
    t_win = int(full.time.iloc[max(0, len(full) - bars)])    # awal jendela yang ditampilkan

    def tt(i):
        return int(T[int(i)]) if i is not None else t_now

    win = full.iloc[-bars:]
    out = {"tf": tf, "tf_sec": sec, "t_start": t_win, "t_now": t_now,
           "candles": {"t": win.time.astype(int).tolist(), "o": win.open.round(3).tolist(),
                       "h": win.high.round(3).tolist(), "l": win.low.round(3).tolist(),
                       "c": win.close.round(3).tolist()},
           "ema": {str(n): ema(full.close, n).iloc[-bars:].round(3).tolist() for n in (5, 8, 21)}}

    # ---- pola candlestick
    out["candle_patterns"] = [
        {"id": f"CDL_{p['key']}_{p['time']}", "time": p["time"], "key": p["key"], "name": p["name"],
         "short": p["short"], "dir": p["dir"], "desc": p["desc"], "price": p["price"],
         "high": float(closed.high.iloc[p["i"]]), "low": float(closed.low.iloc[p["i"]])}
        for p in cdl.detect(closed, bars) if p["time"] >= t_win]

    # ---- struktur & chart pattern
    base = max(0, len(closed) - struct_bars)
    sub = closed.iloc[base:].reset_index(drop=True)
    st = structure.analyze(sub, swing_k)
    ts = lambda i: int(sub.time.iloc[int(i)])                # noqa: E731
    S = {"swings": [{"time": ts(s["i"]), "price": s["price"], "kind": s["kind"]} for s in st["swings"]],
         "levels": [], "trendlines": [], "patterns": [], "desc": structure.DESC}
    for lv in st["levels"]:
        S["levels"].append({"id": f"SR_{lv['kind'][0]}_{round(lv['price'], 1)}", "kind": lv["kind"],
                            "price": lv["price"], "touches": lv["touches"], "t0": ts(lv["from_i"]),
                            "label": lv["label"], "desc": lv["desc"]})
    for t in st["trendlines"]:
        S["trendlines"].append({"id": f"TL_{t['kind']}_{ts(t['i0'])}", "kind": t["kind"], "t0": ts(t["i0"]),
                                "y0": t["y0"], "t1": ts(t["i1"]), "y1": t["y1"], "status": t["status"],
                                "label": t["label"], "desc": t["desc"]})
    for p in st["patterns"]:
        q = {k: v for k, v in p.items() if k not in ("points", "neckline", "pole", "lines", "box")}
        first = None
        for k in ("points", "neckline", "pole"):
            if k in p:
                q[k] = [[ts(a), b] for a, b in p[k]]
                first = first or q[k][0][0]
        if "lines" in p:
            q["lines"] = [[[ts(a), b] for a, b in seg] for seg in p["lines"]]
            first = first or q["lines"][0][0][0]
        if "box" in p:
            b = p["box"]
            q["box"] = [ts(b[0]), b[1], ts(b[2]) + sec, b[3]]
        q["id"] = f"CP_{p['kind']}_{first}"
        S["patterns"].append(q)
    out["structure"] = S

    # ---- SMC (lintasan kausal seluruh bar tertutup)
    feats, obj = smc.run(closed, swing_k)
    M = {"bias": obj["bias"], "desc": smc.DESC, "now": {k: float(v) for k, v in feats.iloc[-1].items()}}
    M["events"] = [{"id": f"{e['type']}_{e['dir']}_{tt(e['i'])}", "type": e["type"], "dir": e["dir"],
                    "t_from": tt(e["from_i"]), "t": tt(e["i"]), "level": e["level"]}
                   for e in obj["events"] if tt(e["i"]) >= t_win]
    M["order_blocks"] = [{"id": f"OB_{b['dir']}_{tt(b['i'])}", "dir": b["dir"], "t0": tt(b["i"]),
                          "t1": tt(b["end_i"]) + sec if b["end_i"] is not None else t_now,
                          "top": b["top"], "bottom": b["bottom"], "status": b["status"], "src": b["src"]}
                         for b in obj["order_blocks"]]
    M["fvg"] = [{"id": f"FVG_{g['dir']}_{tt(g['i'])}", "dir": g["dir"], "t0": tt(g["i"]),
                 "t1": tt(g["end_i"]) + sec if g["end_i"] is not None else t_now,
                 "top": g["top"], "bottom": g["bottom"], "status": g["status"]} for g in obj["fvg"]]
    M["liquidity"] = [{"id": f"LIQ_{q['dir']}_{tt(q['i0'])}", "dir": q["dir"], "t0": tt(q["i0"]),
                       "t1": tt(q["sweep_i"]) + sec if q["sweep_i"] is not None else t_now,
                       "t_sweep": tt(q["sweep_i"]) if q["sweep_i"] is not None else None,
                       "level": q["level"], "status": q["status"]} for q in obj["liquidity"]]
    for k in ("order_blocks", "fvg", "liquidity"):                # hanya yang masih relevan di jendela
        M[k] = [x for x in M[k] if x["t1"] >= t_win]
    out["smc"] = M
    return out


def is_live(status):
    return status not in INACTIVE
