"""
Smart Money Concepts — satu lintasan KAUSAL atas data (tiap bar hanya tahu swing yang
sudah terkonfirmasi sampai bar itu), sehingga hasilnya sama untuk chart, bot live & backtest.

  BOS    Break of Structure : close menembus swing terakhir SEARAH tren -> tren berlanjut
  CHoCH  Change of Character: close menembus swing terakhir MELAWAN tren -> tren berbalik
  OB     Order Block        : candle berlawanan terakhir sebelum impuls yang memicu BOS/CHoCH
                              (jejak order besar). Batal kalau harga tutup menembus sisi jauhnya.
  FVG    Fair Value Gap     : celah 3 candle (low[i] > high[i-2] atau sebaliknya) — area
                              ketidakseimbangan yang sering 'diisi' kembali.
  LIQ    Liquidity          : equal highs / equal lows (stop-loss menumpuk di situ). Sweep = harga
                              menembus level lalu tutup kembali di dalam -> likuiditas 'disapu'.
"""
import numpy as np
import pandas as pd

from ..indicators import atr as atr_fn
from .structure import swings as find_swings

DESC = {
    "BOS": "Break of Structure: harga tutup menembus swing terakhir searah tren — tren BERLANJUT.",
    "CHoCH": "Change of Character: harga menembus swing terakhir MELAWAN tren — tanda awal tren BERBALIK.",
    "OB_bull": "Bullish Order Block: candle turun terakhir sebelum lonjakan naik yang mematahkan struktur. "
               "Area ini sering jadi 'lantai' saat harga kembali (tempat institusi menambah beli).",
    "OB_bear": "Bearish Order Block: candle naik terakhir sebelum jatuhnya harga yang mematahkan struktur. "
               "Sering jadi 'atap' saat harga kembali.",
    "FVG_bull": "Bullish Fair Value Gap: celah harga saat naik terlalu cepat. Harga cenderung kembali mengisi "
                "celah ini, lalu melanjutkan naik.",
    "FVG_bear": "Bearish Fair Value Gap: celah harga saat turun terlalu cepat; sering diisi lalu lanjut turun.",
    "LIQ_H": "Equal highs: beberapa puncak sejajar = tumpukan stop-loss penjual & order breakout di atasnya (likuiditas).",
    "LIQ_L": "Equal lows: beberapa dasar sejajar = tumpukan stop-loss pembeli di bawahnya (likuiditas).",
    "SWEEP_H": "Liquidity sweep ke atas: harga menembus equal highs lalu tutup kembali di bawahnya — stop disapu, "
               "sering diikuti gerak TURUN.",
    "SWEEP_L": "Liquidity sweep ke bawah: harga menembus equal lows lalu tutup kembali di atasnya — sering diikuti gerak NAIK.",
}


def run(df: pd.DataFrame, k: int = 3, keep: int = 12, near_atr: float = 0.5, sweep_memory: int = 10):
    """-> (fitur per bar: DataFrame, objek untuk chart: dict)"""
    n = len(df)
    o, h, l, c = (df[x].values.astype(float) for x in ("open", "high", "low", "close"))
    atr = atr_fn(df).values
    sw = find_swings(df, k)
    by_confirm = {}
    for s in sw:
        by_confirm.setdefault(s["confirm_i"], []).append(s)

    bias = np.zeros(n)
    near_ob = np.zeros(n)        # +1 dekat OB bullish valid, -1 dekat OB bearish, 0 tidak
    in_fvg = np.zeros(n)         # +1 di dalam FVG bullish belum terisi, -1 bearish
    sweep = np.zeros(n)          # +1 sweep equal lows baru-baru ini (bullish), -1 sweep equal highs
    last_event = np.zeros(n)     # +2 CHoCH naik, +1 BOS naik, -1 BOS turun, -2 CHoCH turun (event terakhir)

    trend = 0
    last_h = last_l = None
    conf_h, conf_l = [], []
    events, obs, fvgs, liqs = [], [], [], []
    last_sweep_i, last_sweep_dir, ev_code = -10 ** 9, 0, 0

    for i in range(n):
        a = atr[i] if atr[i] > 0 else (h[i] - l[i]) or 1e-9
        # swing yang terkonfirmasi di bar ini
        for s in by_confirm.get(i, []):
            if s["kind"] == "H":
                last_h = dict(s, broken=False)
                # equal highs dengan swing high sebelumnya?
                for p in conf_h[-6:]:
                    if abs(p["price"] - s["price"]) <= 0.15 * a and 5 <= s["i"] - p["i"] <= 200:
                        liqs.append({"dir": "H", "i0": p["i"], "i1": s["i"], "level": max(p["price"], s["price"]),
                                     "status": "aktif", "sweep_i": None})
                        break
                conf_h.append(s)
            else:
                last_l = dict(s, broken=False)
                for p in conf_l[-6:]:
                    if abs(p["price"] - s["price"]) <= 0.15 * a and 5 <= s["i"] - p["i"] <= 200:
                        liqs.append({"dir": "L", "i0": p["i"], "i1": s["i"], "level": min(p["price"], s["price"]),
                                     "status": "aktif", "sweep_i": None})
                        break
                conf_l.append(s)

        # BOS / CHoCH
        if last_h and not last_h["broken"] and c[i] > last_h["price"]:
            typ = "CHoCH" if trend == -1 else "BOS"
            trend, last_h["broken"] = 1, True
            ev_code = 2 if typ == "CHoCH" else 1
            events.append({"type": typ, "dir": 1, "from_i": last_h["i"], "i": i, "level": last_h["price"]})
            origin = last_l["i"] if last_l else max(0, i - 20)
            cand = [j for j in range(max(origin, i - 30), i + 1) if c[j] < o[j]]
            j = cand[-1] if cand else int(np.argmin(l[max(origin, i - 30):i + 1])) + max(origin, i - 30)
            obs.append({"dir": 1, "i": j, "top": float(h[j]), "bottom": float(l[j]), "status": "aktif",
                        "end_i": None, "src": typ})
        elif last_l and not last_l["broken"] and c[i] < last_l["price"]:
            typ = "CHoCH" if trend == 1 else "BOS"
            trend, last_l["broken"] = -1, True
            ev_code = -2 if typ == "CHoCH" else -1
            events.append({"type": typ, "dir": -1, "from_i": last_l["i"], "i": i, "level": last_l["price"]})
            origin = last_h["i"] if last_h else max(0, i - 20)
            cand = [j for j in range(max(origin, i - 30), i + 1) if c[j] > o[j]]
            j = cand[-1] if cand else int(np.argmax(h[max(origin, i - 30):i + 1])) + max(origin, i - 30)
            obs.append({"dir": -1, "i": j, "top": float(h[j]), "bottom": float(l[j]), "status": "aktif",
                        "end_i": None, "src": typ})

        # FVG baru
        if i >= 2:
            if l[i] > h[i - 2] and l[i] - h[i - 2] >= 0.15 * a:
                fvgs.append({"dir": 1, "i": i - 1, "top": float(l[i]), "bottom": float(h[i - 2]),
                             "status": "aktif", "end_i": None})
            elif h[i] < l[i - 2] and l[i - 2] - h[i] >= 0.15 * a:
                fvgs.append({"dir": -1, "i": i - 1, "top": float(l[i - 2]), "bottom": float(h[i]),
                             "status": "aktif", "end_i": None})

        # perbarui status OB / FVG / likuiditas dengan bar ini
        for ob in obs:
            if ob["status"] == "batal" or ob["i"] >= i:
                continue
            if (ob["dir"] == 1 and c[i] < ob["bottom"]) or (ob["dir"] == -1 and c[i] > ob["top"]):
                ob["status"], ob["end_i"] = "batal", i
            elif ob["status"] == "aktif" and l[i] <= ob["top"] and h[i] >= ob["bottom"] and i > ob["i"] + 2:
                ob["status"] = "tersentuh"
        for g in fvgs:
            if g["status"] == "terisi" or g["i"] + 1 >= i:
                continue
            if (g["dir"] == 1 and l[i] <= g["bottom"]) or (g["dir"] == -1 and h[i] >= g["top"]):
                g["status"], g["end_i"] = "terisi", i
            elif (g["dir"] == 1 and l[i] < g["top"]) or (g["dir"] == -1 and h[i] > g["bottom"]):
                g["status"] = "terisi sebagian"
        for q in liqs:
            if q["status"] != "aktif" or q["i1"] + k >= i:
                continue
            if q["dir"] == "H" and h[i] > q["level"] and c[i] < q["level"]:
                q["status"], q["sweep_i"] = "tersapu", i
                last_sweep_i, last_sweep_dir = i, -1
            elif q["dir"] == "L" and l[i] < q["level"] and c[i] > q["level"]:
                q["status"], q["sweep_i"] = "tersapu", i
                last_sweep_i, last_sweep_dir = i, 1
            elif (q["dir"] == "H" and c[i] > q["level"]) or (q["dir"] == "L" and c[i] < q["level"]):
                q["status"], q["sweep_i"] = "ditembus", i

        # buang objek lama supaya tetap ringan
        obs = [x for x in obs if x["status"] != "batal" or i - x["end_i"] < 300][-keep * 3:]
        fvgs = [x for x in fvgs if x["status"] != "terisi" or i - x["end_i"] < 100][-keep * 4:]
        liqs = liqs[-keep * 2:]

        # fitur bar ini
        bias[i] = trend
        last_event[i] = ev_code
        px = c[i]
        for ob in reversed(obs):
            if ob["status"] != "batal" and ob["i"] < i:
                dist = 0 if ob["bottom"] <= px <= ob["top"] else min(abs(px - ob["top"]), abs(px - ob["bottom"]))
                if dist <= near_atr * a:
                    near_ob[i] = ob["dir"]
                    break
        for g in reversed(fvgs):
            if g["status"] != "terisi" and g["bottom"] <= px <= g["top"]:
                in_fvg[i] = g["dir"]
                break
        if i - last_sweep_i <= sweep_memory:
            sweep[i] = last_sweep_dir

    feats = pd.DataFrame({"smc_bias": bias, "smc_event": last_event, "near_ob": near_ob,
                          "in_fvg": in_fvg, "sweep": sweep}, index=df.index)
    objects = {"events": events[-keep:], "order_blocks": obs[-keep:], "fvg": fvgs[-keep * 2:],
               "liquidity": liqs[-keep:], "bias": int(trend)}
    return feats, objects


def bias_series(df, k=3):
    return run(df, k)[0]["smc_bias"]


def describe_context(feats_row, direction):
    """Ringkasan SMC untuk panel 'Kenapa bot entry di sini?'."""
    d = direction
    txt = []
    b = int(feats_row["smc_bias"])
    txt.append(f"Bias struktur {'NAIK' if b > 0 else 'TURUN' if b < 0 else 'netral'}"
               f"{' — searah' if b == d else ' — berlawanan' if b == -d else ''}")
    ev = int(feats_row["smc_event"])
    if ev:
        txt.append(f"Event terakhir: {'CHoCH' if abs(ev) == 2 else 'BOS'} {'naik' if ev > 0 else 'turun'}")
    if feats_row["near_ob"]:
        txt.append(f"Dekat Order Block {'bullish' if feats_row['near_ob'] > 0 else 'bearish'}"
                   f"{' (searah)' if feats_row['near_ob'] == d else ' (berlawanan)'}")
    if feats_row["in_fvg"]:
        txt.append(f"Di dalam FVG {'bullish' if feats_row['in_fvg'] > 0 else 'bearish'}")
    if feats_row["sweep"]:
        txt.append(f"Baru saja sweep likuiditas {'bawah (bullish)' if feats_row['sweep'] > 0 else 'atas (bearish)'}")
    return txt


# ─────────────────────────────────────────────────────────────────────────────
# Fungsi khusus strategi SMC Multi-Timeframe (M15 + M3)
# Dipakai oleh bot/strategy.py entry engine 'smc_mtf' (mode AGGRESSIVE)
# ─────────────────────────────────────────────────────────────────────────────

def m15_structure_and_ob(df: pd.DataFrame, k: int = 2, min_amp_atr: float = 0.7,
                         ob_min_body_atr: float = 0.5, keep: int = 5):
    """Analisis struktur M15 dan Order Block.

    Mengembalikan dict:
      bias      : +1 (uptrend HH+HL), -1 (downtrend LL+LH), 0 (belum jelas)
      obs       : list OB aktif [{dir, top, bottom, i, src}]
      last_hh   : harga Higher High terakhir (atau None)
      last_hl   : harga Higher Low terakhir (atau None)
      last_ll   : harga Lower Low terakhir (atau None)
      last_lh   : harga Lower High terakhir (atau None)
    """
    n = len(df)
    if n < (k * 2 + 10):
        return {"bias": 0, "obs": [], "demand_obs": [], "supply_obs": [],
                "last_hh": None, "last_hl": None,
                "last_ll": None, "last_lh": None}

    atr_vals = atr_fn(df).values
    o_arr = df["open"].values.astype(float)
    h_arr = df["high"].values.astype(float)
    l_arr = df["low"].values.astype(float)
    c_arr = df["close"].values.astype(float)

    raw_swings = find_swings(df, k)

    # Filter amplitudo: swing harus >= min_amp_atr x ATR agar tidak noise
    filtered = []
    for s in raw_swings:
        ci = s["confirm_i"]
        a_val = atr_vals[ci] if ci < n else atr_vals[-1]
        if filtered:
            amp = abs(s["price"] - filtered[-1]["price"])
            if amp < min_amp_atr * a_val:
                if filtered[-1]["kind"] == s["kind"]:
                    better = (s["price"] > filtered[-1]["price"]) if s["kind"] == "H" else (s["price"] < filtered[-1]["price"])
                    if better:
                        filtered[-1] = s
                continue
        filtered.append(s)

    # Rapikan: H-L-H-L alternating
    alternated = []
    for s in filtered:
        if alternated and alternated[-1]["kind"] == s["kind"]:
            better = (s["price"] > alternated[-1]["price"]) if s["kind"] == "H" else (s["price"] < alternated[-1]["price"])
            if better:
                alternated[-1] = s
        else:
            alternated.append(s)

    # Tentukan bias dari swing terakhir
    bias = 0
    last_hh = last_hl = last_ll = last_lh = None
    highs = [s for s in alternated if s["kind"] == "H"]
    lows  = [s for s in alternated if s["kind"] == "L"]

    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1]["price"] > highs[-2]["price"]
        hl = lows[-1]["price"]  > lows[-2]["price"]
        ll = lows[-1]["price"]  < lows[-2]["price"]
        lh = highs[-1]["price"] < highs[-2]["price"]

        if hh and hl:
            bias = 1
            last_hh = highs[-1]["price"]
            last_hl = lows[-1]["price"]
        elif ll and lh:
            bias = -1
            last_ll = lows[-1]["price"]
            last_lh = highs[-1]["price"]
        elif hh:
            bias = 1
            last_hh = highs[-1]["price"]
        elif ll:
            bias = -1
            last_ll = lows[-1]["price"]

    # Deteksi live CHoCH: jika harga bar berjalan menembus LH/HL terakhir, perbarui bias seketika
    cur_close = float(c_arr[-1])
    if bias == -1 and last_lh is not None and cur_close > last_lh:
        bias = 1   # Bullish CHoCH seketika
    elif bias == 1 and last_hl is not None and cur_close < last_hl:
        bias = -1  # Bearish CHoCH seketika

    # Cari Order Block yang relevan dengan bias (sinkron 100% dengan tampilan Chart View)
    feats, obj = run(df, k=3 if k >= 2 else 2)
    smc_bias = int(obj.get("bias", 0))
    if bias == 0 and smc_bias != 0:
        bias = smc_bias

    # Cari Order Block aktif dua arah (Demand & Supply) sinkron dengan Chart View
    all_chart_obs = [ob for ob in obj.get("order_blocks", [])
                     if ob.get("status") in ("aktif", "tersentuh")]

    demand_obs = [ob for ob in all_chart_obs if ob.get("dir") == 1]
    supply_obs = [ob for ob in all_chart_obs if ob.get("dir") == -1]

    # Tambahkan juga OB lokal dari swing terdekat agar tidak tertinggal gelombang baru
    if lows:
        origin_i = lows[-1]["i"]
        search_start = max(0, origin_i - 5)
        search_end = min(n, origin_i + 20)
        best_j, best_body = None, 0.0
        for j in range(search_start, search_end):
            body_size = o_arr[j] - c_arr[j]
            atr_j = atr_vals[j] if j < n else atr_vals[-1]
            if body_size >= ob_min_body_atr * atr_j and body_size > best_body:
                best_body = body_size
                best_j = j
        if best_j is not None:
            local_ob = {"dir": 1, "i": best_j, "top": float(h_arr[best_j]), "bottom": float(l_arr[best_j]),
                        "status": "aktif", "src": "m15_hl"}
            if not any(abs(x["bottom"] - local_ob["bottom"]) < 0.5 for x in demand_obs):
                demand_obs.append(local_ob)

    if highs:
        origin_i = highs[-1]["i"]
        search_start = max(0, origin_i - 5)
        search_end = min(n, origin_i + 20)
        best_j, best_body = None, 0.0
        for j in range(search_start, search_end):
            body_size = c_arr[j] - o_arr[j]
            atr_j = atr_vals[j] if j < n else atr_vals[-1]
            if body_size >= ob_min_body_atr * atr_j and body_size > best_body:
                best_body = body_size
                best_j = j
        if best_j is not None:
            local_ob = {"dir": -1, "i": best_j, "top": float(h_arr[best_j]), "bottom": float(l_arr[best_j]),
                        "status": "aktif", "src": "m15_lh"}
            if not any(abs(x["top"] - local_ob["top"]) < 0.5 for x in supply_obs):
                supply_obs.append(local_ob)

    demand_obs = demand_obs[-keep:]
    supply_obs = supply_obs[-keep:]
    obs_out = (demand_obs if bias == 1 else supply_obs if bias == -1 else demand_obs + supply_obs)[-keep:]

    return {"bias": bias, "obs": obs_out,
            "demand_obs": demand_obs, "supply_obs": supply_obs,
            "last_hh": last_hh, "last_hl": last_hl,
            "last_ll": last_ll, "last_lh": last_lh}


def m3_choch_and_fvg(df: pd.DataFrame, ob_top: float, ob_bottom: float, bias: int,
                     k: int = 1, choch_min_body_atr: float = 0.9,
                     choch_window_bars: int = 30, fvg_min_gap_atr: float = 0.15,
                     fvg_entry_depth_pct: float = 50.0, max_choch_age_bars: int = 2):
    """Cari CHoCH dan FVG di M3 setelah harga menyentuh OB M15.

    bias = +1 (cari bullish reversal) / -1 (cari bearish reversal)

    Mengembalikan dict:
      signal        : +1 / -1 / 0
      choch_found   : bool
      fvg_found     : bool
      entry_price   : harga limit order
      sl_price      : harga stop loss
      fvg_top       : batas atas FVG
      fvg_bottom    : batas bawah FVG
      choch_swing   : level yang ditembus CHoCH
      bars_since_ob : bar M3 sejak OB disentuh
    """
    result = {
        "signal": 0, "choch_found": False, "fvg_found": False,
        "entry_price": None, "sl_price": None,
        "fvg_top": None, "fvg_bottom": None,
        "choch_swing": None, "bars_since_ob": 0
    }

    n = len(df)
    if n < (k * 2 + 5) or bias == 0:
        return result

    atr_vals = atr_fn(df).values
    o_arr = df["open"].values.astype(float)
    h_arr = df["high"].values.astype(float)
    l_arr = df["low"].values.astype(float)
    c_arr = df["close"].values.astype(float)

    # Cari sentuhan OB di jendela TERAKHIR (choch_window_bars terakhir)
    recent_touches = [i for i in range(max(0, n - choch_window_bars), n)
                      if (l_arr[i] <= ob_top and h_arr[i] >= ob_bottom)]

    if not recent_touches:
        return result  # Harga belum/tidak menyentuh OB dalam jendela choch_window_bars terakhir

    # Ambil kelompok sentuhan TERAKHIR (kunjungan retest saat ini ke area OB)
    clusters = []
    for t in recent_touches:
        if not clusters or t > clusters[-1][-1] + 2:
            clusters.append([t])
        else:
            clusters[-1].append(t)
    ob_touch_i = clusters[-1][0]
    bars_since_ob = n - 1 - ob_touch_i
    result["bars_since_ob"] = bars_since_ob

    # Swing internal M3 di sekitar jendela sentuhan OB
    pre_start = max(0, ob_touch_i - choch_window_bars)
    sub_df = df.iloc[pre_start: n]
    if len(sub_df) < k * 2 + 2:
        return result

    all_swings = find_swings(sub_df, k)

    if bias == 1:  # CHoCH bullish
        # Titik terendah dari kluster sentuhan OB hingga bar saat ini
        touch_min_idx = ob_touch_i
        min_l = float(l_arr[ob_touch_i])
        for idx in range(ob_touch_i, n):
            if float(l_arr[idx]) < min_l:
                min_l = float(l_arr[idx])
                touch_min_idx = idx

        # Lower High terakhir sebelum titik terendah (titik acuan CHoCH)
        prior_highs = [s for s in all_swings if s["kind"] == "H" and (pre_start + s["i"]) < touch_min_idx]
        if not prior_highs:
            prior_highs = [s for s in all_swings if s["kind"] == "H" and (pre_start + s["i"]) <= ob_touch_i]
        if not prior_highs:
            prior_highs = [s for s in all_swings if s["kind"] == "H"]
        if not prior_highs:
            return result
        choch_level = prior_highs[-1]["price"]
        result["choch_swing"] = choch_level

        # Evaluasi CHoCH dari titik terendah (touch_min_idx)
        choch_idx = None
        for i in range(touch_min_idx, n):
            atr_i = atr_vals[i] if i < n else atr_vals[-1]
            body_size = abs(c_arr[i] - o_arr[i])
            if c_arr[i] > choch_level and body_size >= choch_min_body_atr * atr_i:
                result["choch_found"] = True
                choch_idx = i
                break

        if choch_idx is not None:
            # Filter Kesegaran Sinyal: CHoCH tidak boleh basi (maksimal max_choch_age_bars yang lalu)
            bars_since_choch = n - 1 - choch_idx
            if bars_since_choch > max_choch_age_bars:
                return result  # Sinyal CHoCH sudah kadaluwarsa

            # Filter Konfirmasi Candle:
            # 1. Lantai tidak boleh sedang ditembus ke bawah
            if c_arr[-1] < min_l:
                return result
            # 2. Candle M3 terakhir tidak boleh merah solid meluncur (mencegah catching falling knife)
            last_body = c_arr[-1] - o_arr[-1]
            last_range = h_arr[-1] - l_arr[-1]
            lower_wick = min(o_arr[-1], c_arr[-1]) - l_arr[-1]
            if last_body < 0 and last_range > 0 and lower_wick < 0.4 * last_range:
                return result

            # 1. Cari FVG yang terbentuk di gelombang impuls CHoCH
            for j in range(max(2, choch_idx - 1), n):
                gap_bottom = float(h_arr[j - 2])
                gap_top    = float(l_arr[j])
                gap_size   = gap_top - gap_bottom
                atr_ref    = atr_vals[j - 1] if (j - 1) < n else atr_vals[-1]

                if gap_size >= fvg_min_gap_atr * atr_ref and gap_top > gap_bottom:
                    result["fvg_found"] = True
                    result["fvg_top"]    = gap_top
                    result["fvg_bottom"] = gap_bottom

                    depth = fvg_entry_depth_pct / 100.0
                    entry = gap_bottom + depth * gap_size
                    result["entry_price"] = round(entry, 5)

                    # SL Adaptif Struktural: cari swing low terdekat sebelum bar CHoCH (asal dorongan impuls breakout)
                    prior_impulse_lows = [s for s in all_swings if s["kind"] == "L" and (pre_start + s["i"]) < choch_idx]
                    if prior_impulse_lows:
                        sl_base = prior_impulse_lows[-1]["price"]
                    else:
                        sl_lows = [float(l_arr[jj]) for jj in range(ob_touch_i, j + 1)]
                        sl_base = min(sl_lows) if sl_lows else float(l_arr[ob_touch_i])
                    result["sl_price"] = round(sl_base - 0.3 * atr_vals[-1], 5)
                    result["signal"] = 1
                    break

            # 2. Fallback OTE / CHoCH Retest jika FVG tertutup overlap wick
            if not result["fvg_found"]:
                impulse_low = min(float(l_arr[jj]) for jj in range(ob_touch_i, n))
                impulse_high = max(float(h_arr[jj]) for jj in range(ob_touch_i, n))
                if impulse_high > choch_level:
                    entry = impulse_low + 0.5 * (impulse_high - impulse_low)
                    result["entry_price"] = round(entry, 5)
                    prior_impulse_lows = [s for s in all_swings if s["kind"] == "L" and (pre_start + s["i"]) < (choch_idx or n)]
                    sl_base = prior_impulse_lows[-1]["price"] if prior_impulse_lows else impulse_low
                    result["sl_price"] = round(sl_base - 0.3 * atr_vals[-1], 5)
                    result["signal"] = 1

    elif bias == -1:  # CHoCH bearish
        # Titik tertinggi dari kluster sentuhan OB hingga bar saat ini
        touch_max_idx = ob_touch_i
        max_h = float(h_arr[ob_touch_i])
        for idx in range(ob_touch_i, n):
            if float(h_arr[idx]) > max_h:
                max_h = float(h_arr[idx])
                touch_max_idx = idx

        # Higher Low terakhir sebelum titik tertinggi (titik acuan CHoCH)
        prior_lows = [s for s in all_swings if s["kind"] == "L" and (pre_start + s["i"]) < touch_max_idx]
        if not prior_lows:
            prior_lows = [s for s in all_swings if s["kind"] == "L" and (pre_start + s["i"]) <= ob_touch_i]
        if not prior_lows:
            prior_lows = [s for s in all_swings if s["kind"] == "L"]
        if not prior_lows:
            return result
        choch_level = prior_lows[-1]["price"]
        result["choch_swing"] = choch_level

        # Evaluasi CHoCH dari titik tertinggi (touch_max_idx)
        choch_idx = None
        for i in range(touch_max_idx, n):
            atr_i = atr_vals[i] if i < n else atr_vals[-1]
            body_size = abs(c_arr[i] - o_arr[i])
            if c_arr[i] < choch_level and body_size >= choch_min_body_atr * atr_i:
                result["choch_found"] = True
                choch_idx = i
                break

        if choch_idx is not None:
            # Filter Kesegaran Sinyal: CHoCH tidak boleh basi (maksimal max_choch_age_bars yang lalu)
            bars_since_choch = n - 1 - choch_idx
            if bars_since_choch > max_choch_age_bars:
                return result  # Sinyal CHoCH sudah kadaluwarsa

            # Filter Konfirmasi Candle:
            # 1. Atap tidak boleh sedang ditembus ke atas
            if c_arr[-1] > max_h:
                return result
            # 2. Candle M3 terakhir tidak boleh hijau solid meluncur ke atas
            last_body = c_arr[-1] - o_arr[-1]
            last_range = h_arr[-1] - l_arr[-1]
            upper_wick = h_arr[-1] - max(o_arr[-1], c_arr[-1])
            if last_body > 0 and last_range > 0 and upper_wick < 0.4 * last_range:
                return result

            # 1. Cari FVG yang terbentuk di gelombang impuls CHoCH
            for j in range(max(2, choch_idx - 1), n):
                gap_top    = float(l_arr[j - 2])
                gap_bottom = float(h_arr[j])
                gap_size   = gap_top - gap_bottom
                atr_ref    = atr_vals[j - 1] if (j - 1) < n else atr_vals[-1]

                if gap_size >= fvg_min_gap_atr * atr_ref and gap_top > gap_bottom:
                    result["fvg_found"] = True
                    result["fvg_top"]    = gap_top
                    result["fvg_bottom"] = gap_bottom

                    depth = fvg_entry_depth_pct / 100.0
                    entry = gap_top - depth * gap_size
                    result["entry_price"] = round(entry, 5)

                    # SL Adaptif Struktural: cari swing high terdekat sebelum bar CHoCH (asal dorongan impuls breakdown)
                    prior_impulse_highs = [s for s in all_swings if s["kind"] == "H" and (pre_start + s["i"]) < choch_idx]
                    if prior_impulse_highs:
                        sl_base = prior_impulse_highs[-1]["price"]
                    else:
                        sl_highs = [float(h_arr[jj]) for jj in range(ob_touch_i, j + 1)]
                        sl_base = max(sl_highs) if sl_highs else float(h_arr[ob_touch_i])
                    result["sl_price"] = round(sl_base + 0.3 * atr_vals[-1], 5)
                    result["signal"] = -1
                    break

            # 2. Fallback OTE / CHoCH Retest jika FVG tertutup overlap wick
            if not result["fvg_found"]:
                impulse_high = max(float(h_arr[jj]) for jj in range(ob_touch_i, n))
                impulse_low = min(float(l_arr[jj]) for jj in range(ob_touch_i, n))
                if impulse_low < choch_level:
                    entry = impulse_high - 0.5 * (impulse_high - impulse_low)
                    result["entry_price"] = round(entry, 5)
                    prior_impulse_highs = [s for s in all_swings if s["kind"] == "H" and (pre_start + s["i"]) < (choch_idx or n)]
                    sl_base = prior_impulse_highs[-1]["price"] if prior_impulse_highs else impulse_high
                    result["sl_price"] = round(sl_base + 0.3 * atr_vals[-1], 5)
                    result["signal"] = -1

    return result
