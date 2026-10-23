"""Indikator dasar (vectorized pandas). Dipakai bersama oleh bot live & backtest."""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([df.high - df.low,
                    (df.high - df.close.shift()).abs(),
                    (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def pct_rank(s: pd.Series, window: int) -> pd.Series:
    """Persentil nilai terakhir di jendela bergulir (0..1)."""
    return s.rolling(window, min_periods=window // 2).rank(pct=True)


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n // 2).mean()


def bollinger_bands(s: pd.Series, n: int = 20, dev: float = 2.0):
    """Bollinger Bands: (lower, mid, upper, bandwidth, pct_b).
    mid = SMA(n)
    upper = mid + dev * std
    lower = mid - dev * std
    bandwidth = (upper - lower) / mid
    pct_b = (s - lower) / (upper - lower)
    """
    mid = s.rolling(n, min_periods=n // 2).mean()
    std = s.rolling(n, min_periods=n // 2).std(ddof=0)
    upper = mid + dev * std
    lower = mid - dev * std
    span = (upper - lower).replace(0, np.nan)
    bw = span / mid.replace(0, np.nan)
    pct_b = (s - lower) / span
    return lower, mid, upper, bw, pct_b
