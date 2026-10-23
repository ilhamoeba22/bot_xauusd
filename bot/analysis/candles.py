"""
Deteksi pola candlestick. Basis: TA-Lib (fungsi CDL* yang sudah teruji luas);
Pin Bar dihitung sendiri karena tidak ada di TA-Lib.

Setiap pola membawa keterangan Bahasa Indonesia untuk tooltip dashboard.
Semua deteksi hanya memakai candle s/d bar i (tidak melihat masa depan).
"""
import numpy as np
import pandas as pd

try:
    import talib
except ImportError:          # tanpa TA-Lib hanya Pin Bar & Doji buatan sendiri yang aktif
    talib = None

# kode: (fungsi TA-Lib, nama tampilan, arah default, keterangan)
# arah: +1 bullish, -1 bearish, 0 netral; untuk fungsi dua-arah, tanda hasil TA-Lib yang menentukan
PATTERNS = {
    "doji": ("CDLDOJI", "Doji", 0,
             "Open ≈ close: pembeli & penjual seimbang. Tanda ragu-ragu; di ujung tren sering jadi "
             "peringatan awal pembalikan, tapi butuh konfirmasi candle berikutnya."),
    "hammer": ("CDLHAMMER", "Hammer", 1,
               "Badan kecil di atas, ekor bawah panjang (≥2x badan) setelah turun. Penjual sempat menekan "
               "harga jauh, tapi pembeli menariknya kembali — potensi pembalikan NAIK."),
    "hanging_man": ("CDLHANGINGMAN", "Hanging Man", -1,
                    "Bentuk sama dengan Hammer tapi muncul setelah NAIK. Ada tekanan jual besar di dalam "
                    "candle — peringatan tren naik mulai lelah."),
    "inverted_hammer": ("CDLINVERTEDHAMMER", "Inverted Hammer", 1,
                        "Ekor atas panjang setelah turun: pembeli mulai mencoba naik. Sinyal pembalikan naik "
                        "yang lemah, perlu konfirmasi."),
    "shooting_star": ("CDLSHOOTINGSTAR", "Shooting Star", -1,
                      "Ekor atas panjang setelah naik: harga ditolak dari atas. Potensi pembalikan TURUN."),
    "engulfing": ("CDLENGULFING", "Engulfing", 0,
                  "Badan candle 'menelan' seluruh badan candle sebelumnya. Bullish engulfing (hijau menelan "
                  "merah) = pembeli ambil alih; bearish engulfing = penjual ambil alih. Salah satu pola "
                  "pembalikan paling kuat."),
    "harami": ("CDLHARAMI", "Harami", 0,
               "Candle kecil di dalam badan candle besar sebelumnya: momentum melemah, pasar menunggu."),
    "piercing": ("CDLPIERCING", "Piercing Line", 1,
                 "Setelah candle merah, candle hijau buka lebih rendah lalu tutup di atas setengah badan "
                 "candle merah — pembeli kuat masuk."),
    "dark_cloud": ("CDLDARKCLOUDCOVER", "Dark Cloud Cover", -1,
                   "Kebalikan Piercing: candle merah tutup di bawah setengah badan candle hijau sebelumnya."),
    "morning_star": ("CDLMORNINGSTAR", "Morning Star", 1,
                     "3 candle: merah besar, candle kecil (ragu), hijau besar. Tekanan jual habis lalu pembeli "
                     "masuk — pola pembalikan NAIK."),
    "evening_star": ("CDLEVENINGSTAR", "Evening Star", -1,
                     "3 candle: hijau besar, candle kecil, merah besar. Pola pembalikan TURUN di puncak."),
    "three_white_soldiers": ("CDL3WHITESOLDIERS", "3 White Soldiers", 1,
                             "3 candle hijau berturut-turut, masing-masing tutup lebih tinggi. Momentum beli kuat "
                             "dan konsisten."),
    "three_black_crows": ("CDL3BLACKCROWS", "3 Black Crows", -1,
                          "3 candle merah berturut-turut, masing-masing tutup lebih rendah. Momentum jual kuat."),
    "pin_bar": (None, "Pin Bar", 0,
                "Ekor ≥ 2/3 panjang candle, badan kecil di ujung lain. Harga 'ditolak' dari level ekor. "
                "Ekor bawah = bullish pin bar, ekor atas = bearish pin bar. Paling bermakna di "
                "support/resistance atau order block."),
}
SHORT = {"doji": "Dj", "hammer": "H", "hanging_man": "HM", "inverted_hammer": "IH", "shooting_star": "SS",
         "engulfing": "E", "harami": "Hr", "piercing": "Pc", "dark_cloud": "DC", "morning_star": "MS",
         "evening_star": "ES", "three_white_soldiers": "3WS", "three_black_crows": "3BC", "pin_bar": "Pin"}


def _pin_bar(df):
    rng = (df.high - df.low).replace(0, np.nan)
    body = (df.close - df.open).abs()
    up = df.high - df[["open", "close"]].max(axis=1)
    dn = df[["open", "close"]].min(axis=1) - df.low
    big = rng >= rng.rolling(20, min_periods=5).mean() * 0.8      # abaikan candle mungil
    bull = big & (dn >= rng * 2 / 3) & (body <= rng / 3)
    bear = big & (up >= rng * 2 / 3) & (body <= rng / 3)
    return pd.Series(np.where(bull, 100, np.where(bear, -100, 0)), index=df.index)


def _doji(df):
    rng = (df.high - df.low).replace(0, np.nan)
    return pd.Series(np.where((df.close - df.open).abs() <= rng * 0.1, 100, 0), index=df.index)


def pattern_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Kolom per pola, nilai +1/-1/0 (arah) di bar tempat pola SELESAI terbentuk."""
    o, h, l, c = (df[k].astype(float).values for k in ("open", "high", "low", "close"))
    out = {}
    for key, (fn, _, d, _) in PATTERNS.items():
        if key == "pin_bar":
            raw = _pin_bar(df).values
        elif talib is not None:
            raw = getattr(talib, fn)(o, h, l, c)
        elif key == "doji":
            raw = _doji(df).values
        else:
            continue
        raw = np.asarray(raw)
        if d == 0:                              # pola dua arah / netral: tanda dari hasil
            val = np.sign(raw) if key not in ("doji",) else np.where(raw != 0, 0.0001, 0)
        else:
            val = np.where(raw != 0, d, 0)
        out[key] = val
    return pd.DataFrame(out, index=df.index)


def detect(df: pd.DataFrame, last_n: int = 300):
    """Daftar pola untuk digambar di chart (hanya last_n bar terakhir)."""
    m = pattern_matrix(df)
    start = max(0, len(df) - last_n)
    res = []
    for key in m.columns:
        idx = np.nonzero(m[key].values[start:])[0] + start
        for i in idx:
            v = m[key].values[i]
            d = 0 if abs(v) < 0.5 else int(np.sign(v))
            name = PATTERNS[key][1]
            if key in ("engulfing", "harami", "pin_bar"):
                name = f"{'Bullish' if d > 0 else 'Bearish'} {name}"
            res.append({"i": int(i), "time": int(df.time.iloc[i]), "key": key, "name": name,
                        "short": SHORT[key], "dir": d, "desc": PATTERNS[key][3],
                        "price": float(df.low.iloc[i] if d >= 0 else df.high.iloc[i])})
    return res


def recent_bias(df: pd.DataFrame, lookback: int = 3) -> pd.Series:
    """Skor arah pola candlestick dalam `lookback` bar terakhir (per bar, kausal).
    Dipakai sebagai fitur pembelajaran: >0 ada pola bullish, <0 bearish."""
    m = pattern_matrix(df)
    directional = m.drop(columns=[c for c in ("doji",) if c in m.columns]).clip(-1, 1)
    per_bar = directional.sum(axis=1)
    return per_bar.rolling(lookback, min_periods=1).sum()
