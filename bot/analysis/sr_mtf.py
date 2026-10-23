"""Support/Resistance multi-timeframe (H1, H4, D1, W1) + level referensi.

- swing high/low (fractal) per TF -> level; level berdekatan digabung jadi ZONA (lebar = zone_atr x ATR H1)
- kekuatan = jumlah sentuhan x bobot TF + kedekatan waktu + rejection wick -> 0..1 (ketebalan/opasitas)
- flip: level asal swing low yang kini DI ATAS harga = S->R (dan sebaliknya)
- referensi: PDH/PDL, PWH/PWL, high/low sesi Asia/London/NY hari ini, level bulat psikologis
Hasil di-cache per simbol (refresh_sec) supaya tidak dihitung ulang tiap tick.
"""
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

TFS = {"H1": (mt5.TIMEFRAME_H1, 500, 1.0), "H4": (mt5.TIMEFRAME_H4, 400, 2.0),
       "D1": (mt5.TIMEFRAME_D1, 300, 3.0), "W1": (mt5.TIMEFRAME_W1, 150, 4.0)}
SESS = {"Asia": (0, 7), "London": (7, 12), "NY": (12, 20)}
_cache = {}


def _rates(sym, tf, n):
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    return pd.DataFrame(r) if r is not None and len(r) else None


def compute(sym, cfg_sr=None, price=None):
    c = dict({"k": 3, "zone_atr": 0.35, "max_levels": 6, "round_step": 50, "refresh_sec": 300}, **(cfg_sr or {}))
    hit = _cache.get(sym)
    if hit and time.time() - hit[0] < c["refresh_sec"]:
        return hit[1]
    h1 = _rates(sym, mt5.TIMEFRAME_H1, 200)
    if h1 is None:
        return {"zones": [], "refs": []}
    atr = float((h1.high - h1.low).tail(50).mean())
    px = price or float(h1.close.iloc[-1])
    now = time.time()
    pts = []                                   # (harga, tf, bobot, umur_hari, rejection, jenis L/H)
    for name, (tf, n, w) in TFS.items():
        df = _rates(sym, tf, n)
        if df is None or len(df) < 2 * c["k"] + 1:
            continue
        k = c["k"]
        hi, lo = df.high.values, df.low.values
        rng = np.maximum(df.high - df.low, 1e-9).values
        for i in range(k, len(df) - k):
            win_h, win_l = hi[i - k:i + k + 1], lo[i - k:i + k + 1]
            age = (now - int(df.time.iloc[i])) / 86400
            if hi[i] == win_h.max():
                rej = (hi[i] - max(df.open.iloc[i], df.close.iloc[i])) / rng[i]
                pts.append((hi[i], name, w, age, rej, "H"))
            if lo[i] == win_l.min():
                rej = (min(df.open.iloc[i], df.close.iloc[i]) - lo[i]) / rng[i]
                pts.append((lo[i], name, w, age, rej, "L"))
    pts.sort()
    zones, width = [], c["zone_atr"] * atr
    for p in pts:                              # gabung level berdekatan jadi zona
        if zones and p[0] - zones[-1]["hi"] <= width:
            z = zones[-1]
            z["hi"] = max(z["hi"], p[0]); z["pts"].append(p)
        else:
            zones.append({"lo": p[0], "hi": p[0], "pts": [p]})
    out = []
    for z in zones:
        P = z["pts"]
        mid = (z["lo"] + z["hi"]) / 2
        score = sum(w * (1 + 0.5 * rej) * (1.0 if age < 30 else 0.6 if age < 180 else 0.3) for _, _, w, age, rej, _ in P)
        tfs = sorted({p[1] for p in P}, key=lambda t: list(TFS).index(t))
        origin = "S" if sum(1 for p in P if p[5] == "L") >= sum(1 for p in P if p[5] == "H") else "R"
        now_role = "S" if mid < px else "R"
        out.append({"lo": round(z["lo"] - width / 4, 3), "hi": round(z["hi"] + width / 4, 3), "price": round(mid, 3),
                    "kind": now_role, "touches": len(P), "tfs": tfs, "top_tf": tfs[-1], "score": round(score, 2),
                    "flip": origin != now_role, "dist_atr": round(abs(mid - px) / atr, 2)})
    mx = max((z["score"] for z in out), default=1)
    for z in out:
        z["strength"] = round(z["score"] / mx, 2)
        z["label"] = (f"{'S' if z['kind'] == 'S' else 'R'} {'·'.join(z['tfs'])} x{z['touches']}"
                      + (f" (flip {'R->S' if z['kind'] == 'S' else 'S->R'})" if z["flip"] else ""))
    # maksimal N zona DI ATAS & N DI BAWAH harga (bukan N campur), N = max_levels//2 (default 3+3)
    half = max(1, c["max_levels"] // 2)
    out = (sorted([z for z in out if z["kind"] == "S"], key=lambda z: z["dist_atr"])[:half]
          + sorted([z for z in out if z["kind"] == "R"], key=lambda z: z["dist_atr"])[:half])

    refs = []
    d1, w1 = _rates(sym, mt5.TIMEFRAME_D1, 3), _rates(sym, mt5.TIMEFRAME_W1, 3)
    if d1 is not None and len(d1) >= 2:
        refs += [("PDH", float(d1.high.iloc[-2])), ("PDL", float(d1.low.iloc[-2]))]
    if w1 is not None and len(w1) >= 2:
        refs += [("PWH", float(w1.high.iloc[-2])), ("PWL", float(w1.low.iloc[-2]))]
    m15 = _rates(sym, mt5.TIMEFRAME_M15, 96)
    if m15 is not None:
        m15["dt"] = pd.to_datetime(m15.time, unit="s", utc=True)
        today = m15[m15.dt.dt.date == datetime.now(timezone.utc).date()]
        for s, (a, b) in SESS.items():
            x = today[(today.dt.dt.hour >= a) & (today.dt.dt.hour < b)]
            if len(x):
                refs += [(f"{s} H", float(x.high.max())), (f"{s} L", float(x.low.min()))]
    step = c["round_step"]
    base = round(px / step) * step
    refs += [(f"bulat {base + i * step:g}", float(base + i * step)) for i in (-1, 0, 1) if base + i * step]
    refs = [{"label": n, "price": round(p, 3), "dist_atr": round(abs(p - px) / atr, 2)} for n, p in refs]
    res = {"zones": out, "refs": sorted(refs, key=lambda r: r["dist_atr"])[:c.get("max_refs", 8)], "atr_h1": round(atr, 3)}
    _cache[sym] = (time.time(), res)
    return res


def levels_for_plan(res):
    """Harga level (zona + referensi) untuk target TP rencana posisi."""
    return [z["price"] for z in res.get("zones", [])] + [r["price"] for r in res.get("refs", []) if not r["label"].startswith("bulat")]


def level_labels(res):
    """{harga(3 desimal): label} untuk sumber TP di plan."""
    out = {round(z["price"], 3): z["label"] for z in res.get("zones", [])}
    out.update({round(r["price"], 3): r["label"] for r in res.get("refs", []) if not r["label"].startswith("bulat")})
    return out
