"""
Strategi modular: 1 komponen ENTRY + N komponen FILTER, semuanya dipilih lewat
config["strategy"]. Bot live dan backtest memanggil fungsi yang SAMA
(`build_signals`), jadi apa yang diuji = apa yang dijalankan.

Menambah indikator baru:
  1. tulis fungsi  def f(df, p) -> pd.Series (entry: -1/0/+1)
                   atau  -> (long_ok, short_ok) (filter: dua Series boolean)
  2. daftarkan di ENTRIES / FILTERS
  3. pakai namanya di config/config.json -> strategy
"""
import numpy as np
import pandas as pd

from .indicators import ema, rsi, atr, pct_rank, bollinger_bands, sma
from .analysis.smc import m15_structure_and_ob, m3_choch_and_fvg


# ----------------------------- ENTRY -----------------------------
def entry_ema_cross(df, p):
    """EMA cepat memotong EMA lambat pada bar tertutup."""
    f, s = ema(df.close, p.get("fast", 8)), ema(df.close, p.get("slow", 21))
    above = f > s
    cross_up = above & ~above.shift(1, fill_value=False)
    cross_dn = ~above & above.shift(1, fill_value=False)
    return pd.Series(np.where(cross_up, 1, np.where(cross_dn, -1, 0)), index=df.index)


def entry_rsi_cross50(df, p):
    """RSI menembus 50 searah susunan EMA (momentum lanjutan, lebih sering dari cross EMA)."""
    r = rsi(df.close, p.get("rsi_period", 7))
    up = ema(df.close, p.get("fast", 8)) > ema(df.close, p.get("slow", 21))
    lvl = p.get("level", 50)
    cu = (r > lvl) & (r.shift(1) <= lvl) & up
    cd = (r < lvl) & (r.shift(1) >= lvl) & ~up
    return pd.Series(np.where(cu, 1, np.where(cd, -1, 0)), index=df.index)


def entry_candle_break(df, p):
    """Momentum candle: candle tertutup menembus high (beli) / low (jual) candle sebelumnya,
    badannya searah & cukup besar (>= body_atr x ATR), dan searah EMA cepat. Jauh lebih sering
    dari cross EMA — cocok untuk 'masuk cepat, jaga dengan trailing'."""
    a = atr(df, p.get("atr_period", 14))
    body = df.close - df.open
    big = body.abs() >= p.get("body_atr", 0.3) * a
    up = df.close > ema(df.close, p.get("ema", 21))
    buy = (df.close > df.high.shift(1)) & (body > 0) & big & up
    sell = (df.close < df.low.shift(1)) & (body < 0) & big & ~up
    return pd.Series(np.where(buy, 1, np.where(sell, -1, 0)), index=df.index)


def entry_interval_trend(df, p):
    """EKSPERIMEN frekuensi tinggi (profil paper_50burst): masuk setiap `every_min` menit menurut JAM candle
    (mis. 30 -> candle yang dibuka :00 dan :30), searah posisi harga terhadap EMA — TANPA menunggu sinyal.
    Backtest 12 bln: ±70 burst/hari, PF 0,98 = rugi. Hanya untuk mode paper."""
    d = np.sign(df.close - ema(df.close, p.get("ema", 21))).astype(int)
    on_clock = (df.time // 60) % p.get("every_min", 30) == 0
    return pd.Series(np.where(on_clock, d, 0), index=df.index)



def entry_smc_mtf(df, p):
    """SMC Multi-Timeframe: M15 struktur+OB → M3 CHoCH+FVG → Limit Order entry.

    df = DataFrame M5 bar yang sedang diproses (untuk referensi index saja).
    Sinyal dikembalikan sebagai Series -1/0/+1 di baris terakhir.
    Detail entry (harga limit, SL) disimpan di df['smc_entry_meta'] baris terakhir
    sehingga execute_entry() bisa membaca tanpa menghitung ulang.

    Parameter dari config (key di p):
      m15_k, m15_min_amp_atr, m15_ob_min_body_atr
      m3_k, m3_choch_min_body_atr, m3_choch_window_bars
      m3_fvg_min_gap_atr, m3_fvg_entry_depth_pct
      m3_ema_fast, m3_ema_slow
    """
    sig_series = pd.Series(0, index=df.index, dtype=int)
    meta_series = pd.Series([None] * len(df), index=df.index, dtype=object)

    # Butuh MT5 untuk ambil data M15 dan M3 — hanya jalan saat live/demo
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return sig_series  # Backtest: lewati (gunakan entry lain)

    # Ambil nama simbol dari kolom 'symbol' jika ada, atau pakai XAUUSD
    sym = p.get("symbol", "XAUUSD")

    TF_M15 = mt5.TIMEFRAME_M15
    TF_M3  = mt5.TIMEFRAME_M3

    bars_needed = 200

    r15 = mt5.copy_rates_from_pos(sym, TF_M15, 0, bars_needed)
    r3  = mt5.copy_rates_from_pos(sym, TF_M3,  0, bars_needed)

    if r15 is None or r3 is None or len(r15) < 20 or len(r3) < 10:
        return sig_series

    df15 = pd.DataFrame(r15)
    df3  = pd.DataFrame(r3)

    # ── LAYER 1: Analisis struktur dan OB di M15 (Bi-Directional) ────
    m15 = m15_structure_and_ob(
        df15,
        k=int(p.get('m15_k', 2)),
        min_amp_atr=float(p.get('m15_min_amp_atr', 0.5)),
        ob_min_body_atr=float(p.get('m15_ob_min_body_atr', 0.35)),
    )

    demand_obs = m15.get('demand_obs', [])
    supply_obs = m15.get('supply_obs', [])

    if not demand_obs and not supply_obs:
        return sig_series  # Tidak ada OB valid

    bi_directional = p.get('bi_directional', True)

    # ── LAYER 2: Pindai CHoCH & FVG di M3 untuk KEDUA SISI (BUY & SELL) ──
    buy_res, buy_ob = None, None
    if bi_directional or m15['bias'] >= 0:
        for cand_ob in reversed(demand_obs):
            res = m3_choch_and_fvg(
                df3,
                ob_top=cand_ob['top'],
                ob_bottom=cand_ob['bottom'],
                bias=1,
                k=int(p.get('m3_k', 1)),
                choch_min_body_atr=float(p.get('m3_choch_min_body_atr', 0.3)),
                choch_window_bars=int(p.get('m3_choch_window_bars', 40)),
                fvg_min_gap_atr=float(p.get('m3_fvg_min_gap_atr', 0.08)),
                fvg_entry_depth_pct=float(p.get('m3_fvg_entry_depth_pct', 50.0)),
                max_choch_age_bars=int(p.get('max_choch_age_bars', 2)),
            )
            if res['signal'] == 1:
                buy_res, buy_ob = res, cand_ob
                break

    sell_res, sell_ob = None, None
    if bi_directional or m15['bias'] <= 0:
        for cand_ob in reversed(supply_obs):
            res = m3_choch_and_fvg(
                df3,
                ob_top=cand_ob['top'],
                ob_bottom=cand_ob['bottom'],
                bias=-1,
                k=int(p.get('m3_k', 1)),
                choch_min_body_atr=float(p.get('m3_choch_min_body_atr', 0.3)),
                choch_window_bars=int(p.get('m3_choch_window_bars', 40)),
                fvg_min_gap_atr=float(p.get('m3_fvg_min_gap_atr', 0.08)),
                fvg_entry_depth_pct=float(p.get('m3_fvg_entry_depth_pct', 50.0)),
                max_choch_age_bars=int(p.get('max_choch_age_bars', 2)),
            )
            if res['signal'] == -1:
                sell_res, sell_ob = res, cand_ob
                break

    if buy_res and not sell_res:
        m3_result, ob, sig_dir = buy_res, buy_ob, 1
    elif sell_res and not buy_res:
        m3_result, ob, sig_dir = sell_res, sell_ob, -1
    elif buy_res and sell_res:
        if buy_res.get('bars_since_ob', 99) <= sell_res.get('bars_since_ob', 99):
            m3_result, ob, sig_dir = buy_res, buy_ob, 1
        else:
            m3_result, ob, sig_dir = sell_res, sell_ob, -1
    else:
        return sig_series

    # ── LAYER 3: Filter EMA di M3 (Opsional, dinonaktifkan untuk Pure SMC) ───
    if p.get("use_m3_ema", False):
        fast_p = int(p.get("m3_ema_fast", 5))
        slow_p = int(p.get("m3_ema_slow", 8))
        ema_fast = ema(df3["close"], fast_p).iloc[-1]
        ema_slow = ema(df3["close"], slow_p).iloc[-1]

        if sig_dir == 1 and ema_fast <= ema_slow:
            return sig_series  # BUY tapi EMA5 < EMA8: momentum belum bullish
        if sig_dir == -1 and ema_fast >= ema_slow:
            return sig_series  # SELL tapi EMA5 > EMA8: momentum belum bearish

    # ── Semua layer lolos: set sinyal di baris terakhir ───────────────
    idx = df.index[-1]
    sig_series.iloc[-1] = sig_dir
    meta_series.iloc[-1] = {
        "engine": "smc_mtf",
        "m15_bias": m15["bias"],
        "ob_top": ob["top"],
        "ob_bottom": ob["bottom"],
        "ob_dir": ob["dir"],
        "choch_swing": m3_result["choch_swing"],
        "fvg_top": m3_result["fvg_top"],
        "fvg_bottom": m3_result["fvg_bottom"],
        "entry_price": m3_result["entry_price"],
        "sl_price": m3_result["sl_price"],
        "bars_since_ob": m3_result["bars_since_ob"],
        "last_hh": m15.get("last_hh"),
        "last_hl": m15.get("last_hl"),
        "last_ll": m15.get("last_ll"),
        "last_lh": m15.get("last_lh"),
    }

    # Simpan meta ke df agar execute_entry bisa membaca
    if "smc_entry_meta" not in df.columns:
        df["smc_entry_meta"] = None
    df.at[idx, "smc_entry_meta"] = meta_series.iloc[-1]

    return sig_series


def entry_bb_reversion(df: pd.DataFrame, p: dict) -> pd.Series:
    """Bollinger Bands Mean-Reversion Scalper (ala Dark Venus).

    df: DataFrame dengan kolom open, high, low, close
    p: dict parameter dari config:
       - period: int (default 20, periode SMA BB)
       - deviation: float (default 2.0, pengali deviasi standar)
       - mode: 'fade_touch' (default) | 'candle_reversal' | 'close_reentry' | 'breakout'
       - first_touch_only: bool (default True - hanya bar pertama yang menyentuh band)
       - use_rsi: bool (default False)
       - rsi_period: int (default 14)
       - rsi_oversold: float (default 30.0)
       - rsi_overbought: float (default 70.0)
       - use_trend_filter: bool (default False)
       - trend_period: int (default 200)
       - min_bandwidth_atr: float (default 0.0)
    """
    n = int(p.get("period", 20))
    dev = float(p.get("deviation", 2.0))
    mode = p.get("mode", "fade_touch")
    first_touch = bool(p.get("first_touch_only", True))

    lower, mid, upper, bw, _ = bollinger_bands(df.close, n=n, dev=dev)

    # Simpan band di df agar bisa divisualisasikan / dibaca modul lain
    df["bb_lower"] = lower
    df["bb_mid"] = mid
    df["bb_upper"] = upper

    if mode == "candle_reversal":
        # Bullish rejection: Low tembus lower band & candle ditutup bullish (Close > Open)
        buy_raw = (df.low <= lower) & (df.close > df.open)
        # Bearish rejection: High tembus upper band & candle ditutup bearish (Close < Open)
        sell_raw = (df.high >= upper) & (df.close < df.open)
    elif mode == "close_reentry":
        # Close sebelumnya tembus luar band, close sekarang kembali masuk ke dalam band
        buy_raw = (df.close.shift(1) <= lower.shift(1)) & (df.close > lower)
        sell_raw = (df.close.shift(1) >= upper.shift(1)) & (df.close < upper)
    elif mode == "breakout":
        # Breakout di luar band
        buy_raw = (df.close >= upper) & (df.close.shift(1) < upper.shift(1))
        sell_raw = (df.close <= lower) & (df.close.shift(1) > lower.shift(1))
    elif mode == "smart_rejection":
        # Price Action & Candlestick Rejection Reversal:
        # Menghindari pisau jatuh (falling knife) dan marubozu solid yang menembus band.
        rng = (df.high - df.low).replace(0, 1e-6)
        lower_wick = np.where(df.close >= df.open, df.open - df.low, df.close - df.low)
        upper_wick = np.where(df.close >= df.open, df.high - df.close, df.high - df.open)
        lw_ratio = lower_wick / rng
        uw_ratio = upper_wick / rng

        min_wick = float(p.get("min_wick_ratio", 0.30))
        anti_maru_offset = float(p.get("anti_marubozu_offset", 0.20))

        # Anti-Marubozu: Close tidak boleh nempel di ujung ekstrim candle
        anti_maru_buy = ((df.close - df.low) / rng) >= anti_maru_offset
        anti_maru_sell = ((df.high - df.close) / rng) >= anti_maru_offset

        # Rejection: Sumbu penolakan panjang (Pinbar) ATAU Reversal close (Bull/Bear) ATAU Close kembali ke dalam band
        rej_buy = (lw_ratio >= min_wick) | (df.close > df.open) | (df.close > lower)
        rej_sell = (uw_ratio >= min_wick) | (df.close < df.open) | (df.close < upper)

        # Filter Bandwidth Expansion: Hindari 'walking the bands' saat volatilitas meledak
        bw_filter_ok = pd.Series(True, index=df.index)
        max_bw_exp = float(p.get("max_bw_expansion", 0.0))
        if max_bw_exp > 0:
            bw_ma = bw.rolling(10, min_periods=5).mean().replace(0, np.nan)
            bw_filter_ok = (bw / bw_ma).fillna(1.0) <= max_bw_exp

        buy_raw = (df.low <= lower) & anti_maru_buy & rej_buy & bw_filter_ok
        sell_raw = (df.high >= upper) & anti_maru_sell & rej_sell & bw_filter_ok
    else:  # 'fade_touch' (Dark Venus default)
        buy_raw = (df.low <= lower)
        sell_raw = (df.high >= upper)

    # Filter RSI ala Dark Venus
    if p.get("use_rsi", False):
        r_p = int(p.get("rsi_period", 14))
        r_os = float(p.get("rsi_oversold", 30.0))
        r_ob = float(p.get("rsi_overbought", 70.0))
        r_series = rsi(df.close, r_p)
        buy_raw &= (r_series <= r_os)
        sell_raw &= (r_series >= r_ob)

    # Filter Trend EMA ala Dark Venus
    if p.get("use_trend_filter", False):
        t_p = int(p.get("trend_period", 200))
        t_series = ema(df.close, t_p)
        buy_raw &= (df.close > t_series)
        sell_raw &= (df.close < t_series)

    # Filter Lebar Band Minimal (Bandwidth dalam ATR)
    min_bw_atr = float(p.get("min_bandwidth_atr", 0.0))
    if min_bw_atr > 0:
        a_series = atr(df, int(p.get("atr_period", 14)))
        bw_pts = upper - lower
        bw_ok = bw_pts >= (min_bw_atr * a_series)
        buy_raw &= bw_ok
        sell_raw &= bw_ok

    # Terapkan first_touch_only pada sinyal LENGKAP yang lolos konfirmasi (bukan band touch saja)
    if first_touch:
        buy_cond = buy_raw & (~buy_raw.shift(1, fill_value=False))
        sell_cond = sell_raw & (~sell_raw.shift(1, fill_value=False))
    else:
        buy_cond = buy_raw
        sell_cond = sell_raw

    buy_final = buy_cond & ~sell_cond
    sell_final = sell_cond & ~buy_final

    return pd.Series(np.where(buy_final, 1, np.where(sell_final, -1, 0)), index=df.index)



ENTRIES = {"ema_cross": entry_ema_cross, "rsi_cross50": entry_rsi_cross50,
           "candle_break": entry_candle_break, "interval_trend": entry_interval_trend,
           "smc_mtf": entry_smc_mtf, "bb_reversion": entry_bb_reversion}


# ----------------------------- FILTER ----------------------------
def filt_rsi_momentum(df, p):
    """Beli hanya kalau RSI di zona momentum naik tapi belum jenuh beli (dan sebaliknya)."""
    r = rsi(df.close, p.get("period", 14))
    return (r.between(p.get("long_min", 50), p.get("long_max", 70)),
            r.between(p.get("short_min", 30), p.get("short_max", 50)))


def filt_atr_band(df, p):
    """Volatilitas harus 'hidup' (biaya jadi porsi kecil) tapi tidak ekstrem (spike berita)."""
    pr = pct_rank(atr(df, p.get("period", 14)), p.get("window", 288))
    ok = pr.between(p.get("min_pct", 0.3), p.get("max_pct", 0.95))
    return ok, ok


def filt_trend_ema(df, p):
    """Searah tren besar: harga di atas EMA panjang untuk beli, di bawah untuk jual."""
    t = ema(df.close, p.get("period", 200))
    return df.close > t, df.close < t


def filt_bb_bandwidth(df, p):
    """Filter volatilitas Bollinger Bandwidth:
    hanya trading jika pita cukup lebar (menghindari sideways flat) dan tidak terlalu ekstrem."""
    n = int(p.get("period", 20))
    dev = float(p.get("deviation", 2.0))
    _, _, _, bw, _ = bollinger_bands(df.close, n=n, dev=dev)
    min_bw = float(p.get("min_bw_pct", 0.05)) / 100.0
    max_bw = float(p.get("max_bw_pct", 2.0)) / 100.0
    ok = bw.between(min_bw, max_bw)
    return ok, ok


FILTERS = {"rsi_momentum": filt_rsi_momentum, "atr_band": filt_atr_band,
           "trend_ema": filt_trend_ema, "bb_bandwidth": filt_bb_bandwidth}


# ----------------------------- BUILD -----------------------------
def build_signals(df: pd.DataFrame, scfg: dict) -> pd.DataFrame:
    """Tambahkan kolom `signal` (-1/0/+1) dan `atr` ke salinan df.
    df harus berisi kolom open/high/low/close (bar broker = harga BID).
    Sinyal di baris i hanya memakai data s/d close bar i (tidak ada lookahead)."""
    out = df.copy()
    ent = scfg["entry"]
    sig = ENTRIES[ent["name"]](out, ent)
    long_ok = pd.Series(True, index=out.index)
    short_ok = pd.Series(True, index=out.index)
    for f in scfg.get("filters", []):
        if not f.get("enabled", True):
            continue
        lo, so = FILTERS[f["name"]](out, f)
        long_ok &= lo.fillna(False)
        short_ok &= so.fillna(False)
    out["signal"] = np.where((sig == 1) & long_ok, 1, np.where((sig == -1) & short_ok, -1, 0))
    out["atr"] = atr(out, scfg.get("atr_period", 14))
    return out


def min_bars(scfg: dict) -> int:
    """Jumlah bar minimum agar semua indikator stabil."""
    need = [300]
    for f in scfg.get("filters", []):
        need.append(int(f.get("period", 0)) * 3)
        need.append(int(f.get("window", 0)) + 50)
    return max(need)
