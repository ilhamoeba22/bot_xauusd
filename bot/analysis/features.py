"""
Fitur kondisi pasar saat entry — dipakai modul pembelajaran (live & backtest) dan panel
"Kenapa bot entry di sini?". Semua fitur biner (0/1) supaya bobotnya mudah dibaca:
"kalau kondisi X ada, peluang menang naik/turun sekian".

Fitur dihitung dari bar SINYAL (bar tertutup terakhir) — tidak ada data masa depan.
"""
import numpy as np
import pandas as pd

from ..indicators import rsi as rsi_fn, atr as atr_fn, pct_rank
from ..sessions import session_of
from . import candles, smc, structure

# nama fitur -> keterangan singkat (ditampilkan di dashboard)
FEATURES = {
    "ses_asia": "Sesi Asia",
    "ses_london": "Sesi London",
    "ses_newyork": "Sesi New York",
    "vol_tinggi": "Volatilitas tinggi (ATR persentil ≥ 70%)",
    "rsi_kuat": "RSI kuat searah (beli > 60 / jual < 40)",
    "candle_searah": "Ada pola candlestick searah dalam 3 bar terakhir",
    "candle_lawan": "Ada pola candlestick berlawanan dalam 3 bar terakhir",
    "smc_bias_searah": "Bias struktur SMC searah entry",
    "smc_choch_searah": "CHoCH terakhir searah entry (awal tren baru)",
    "ob_searah": "Harga dekat Order Block searah",
    "ob_lawan": "Harga dekat Order Block berlawanan",
    "fvg_searah": "Harga di dalam FVG searah",
    "sweep_searah": "Baru terjadi liquidity sweep searah",
    "pola_chart_searah": "Chart pattern aktif searah (double bottom, flag, dll.)",
    "sr_menghalangi": "Support/resistance menghalangi < 1 ATR di depan",
    "simbol_247": "Simbol XAUUSD247",
}


def bar_context(df: pd.DataFrame, sessions: dict, smc_k: int = 3) -> pd.DataFrame:
    """Kolom per bar yang tidak tergantung arah (dihitung sekali, vectorized/kausal)."""
    out = pd.DataFrame(index=df.index)
    out["rsi"] = rsi_fn(df.close, 14)
    out["atr"] = atr_fn(df, 14)
    out["atr_pct"] = pct_rank(out["atr"], 288)
    out["candle"] = candles.recent_bias(df, 3)
    f, _ = smc.run(df, smc_k)
    out = out.join(f)
    hrs = pd.to_datetime(df.time, unit="s").dt.hour
    out["session"] = [session_of(int(h), sessions) or "-" for h in hrs]
    return out


def entry_features(ctx_row, direction: int, symbol: str, window: pd.DataFrame = None) -> dict:
    """window = ~300 bar s/d bar sinyal, untuk chart pattern & S/R (opsional)."""
    d = direction
    x = {k: 0 for k in FEATURES}
    ses = ctx_row["session"]
    if f"ses_{ses}" in x:
        x[f"ses_{ses}"] = 1
    x["vol_tinggi"] = int(ctx_row["atr_pct"] >= 0.7) if not pd.isna(ctx_row["atr_pct"]) else 0
    x["rsi_kuat"] = int((d > 0 and ctx_row["rsi"] > 60) or (d < 0 and ctx_row["rsi"] < 40))
    x["candle_searah"] = int(ctx_row["candle"] * d > 0)
    x["candle_lawan"] = int(ctx_row["candle"] * d < 0)
    x["smc_bias_searah"] = int(ctx_row["smc_bias"] == d)
    x["smc_choch_searah"] = int(ctx_row["smc_event"] == 2 * d)
    x["ob_searah"] = int(ctx_row["near_ob"] == d)
    x["ob_lawan"] = int(ctx_row["near_ob"] == -d)
    x["fvg_searah"] = int(ctx_row["in_fvg"] == d)
    x["sweep_searah"] = int(ctx_row["sweep"] == d)
    x["simbol_247"] = int(symbol.endswith("247"))
    if window is not None and len(window) > 60:
        st = structure.analyze(window.reset_index(drop=True))
        px, a = float(window.close.iloc[-1]), st["atr"]
        x["pola_chart_searah"] = int(any(p["dir"] == d and p["status"] not in ("batal", "selesai")
                                         for p in st["patterns"]))
        for lv in st["levels"]:
            ahead = (lv["price"] - px) * d
            if 0 < ahead < a and ((d > 0 and lv["kind"] == "resistance") or (d < 0 and lv["kind"] == "support")):
                x["sr_menghalangi"] = 1
                break
    return x
