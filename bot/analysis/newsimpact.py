"""News Impact per simbol: kalender + headline -> kategori, tier dampak per simbol, blackout, reaksi harga terukur.

Sumber (legal, tanpa scraping; semuanya di-cache di logs/ dan tetap jalan dengan cache bila sumber gagal):
- Kalender: FairEconomy/ForexFactory JSON minggu berjalan (feed publik untuk widget; refresh >= 1 jam).
  Event disimpan kumulatif ke logs/news_events.json supaya histori bertambah.
- Headline: RSS resmi Federal Reserve (press_all.xml) & SEC press releases (domain publik pemerintah AS;
  SEC fair-access: <= 10 permintaan/detik, User-Agent wajib) — di-refresh tiap 30 menit.
- Histori 2 tahun untuk statistik reaksi: NFP (Jumat pertama tiap bulan 08:30 ET) & tanggal FOMC dari config.
Arah dampak TIDAK ditebak dari headline; yang disimpan hanya statistik reaksi terukur. Headline tidak pernah memicu entry.
"""
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from ..common import LOG_DIR, NEWS_CACHE

EVENTS = LOG_DIR / "news_events.json"
HEAD = LOG_DIR / "news_headlines.json"
STATS = LOG_DIR / "news_stats.json"
UA = "XAUUSD_EXNESS-bot/1.0 (personal research tool)"
_mem = {"head_ts": 0, "stats_ts": 0}


def _et_offset(dt):
    """Selisih ET->UTC: 4 jam saat DST AS (Minggu ke-2 Maret s/d Minggu pertama Nov), selain itu 5."""
    y = dt.year
    mar = datetime(y, 3, 8, tzinfo=timezone.utc); mar += timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(y, 11, 1, tzinfo=timezone.utc); nov += timedelta(days=(6 - nov.weekday()) % 7)
    return 4 if mar <= dt < nov else 5


def classify(title, cfg):
    t = title.lower()
    for cat, c in cfg.get("categories", {}).items():
        if any(k.lower() in t for k in c.get("keywords", [])):
            return cat
    return None


def tier(cat, sym, cfg, cal_impact=None):
    c = cfg.get("categories", {}).get(cat, {})
    return c.get("tier", {}).get(sym) or c.get("tier", {}).get("*") or (cal_impact if cal_impact in ("High", "Medium", "Low") else None)


# ------------------------------------------------------------------ sumber
def calendar_events(cfg):
    """Gabungan cache kalender minggu ini (NewsFilter) + histori kumulatif."""
    hist = {}
    try:
        hist = json.loads(EVENTS.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        raw = json.loads(NEWS_CACHE.read_text(encoding="utf-8"))["events"]
        for e in raw:
            dt = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            key = f"{dt:%Y%m%d%H%M}|{e.get('title', '')}"
            hist[key] = {"t": int(dt.timestamp()), "title": e.get("title", ""), "country": e.get("country", ""),
                         "impact": e.get("impact", ""), "forecast": e.get("forecast", ""), "previous": e.get("previous", ""),
                         "actual": e.get("actual", ""), "source": "Kalender FairEconomy"}
        EVENTS.write_text(json.dumps(hist), encoding="utf-8")
    except Exception:
        pass
    return [e for e in hist.values() if e.get("country") in cfg.get("currencies", ["USD"])]


def headlines(cfg):
    try:
        cache = json.loads(HEAD.read_text(encoding="utf-8"))
    except Exception:
        cache = {"ts": 0, "items": {}, "errors": {}}
    if time.time() - cache.get("ts", 0) > cfg.get("headline_refresh_min", 30) * 60 and time.time() - _mem["head_ts"] > 300:
        _mem["head_ts"] = time.time()
        for name, url in cfg.get("rss", {}).items():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                root = ET.fromstring(urllib.request.urlopen(req, timeout=15).read())
                for it in root.iter("item"):
                    title = (it.findtext("title") or "").strip()
                    pub = it.findtext("pubDate") or ""
                    try:
                        dt = datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
                    except Exception:
                        continue
                    cache["items"][f"{name}|{title}"] = {"t": int(dt.timestamp()), "title": title, "source": name,
                                                         "link": it.findtext("link") or ""}
                cache["errors"].pop(name, None)
            except Exception as e:
                cache["errors"][name] = f"{type(e).__name__}: {e}"[:120]
        cache["ts"] = time.time()
        cut = time.time() - 30 * 86400
        cache["items"] = {k: v for k, v in cache["items"].items() if v["t"] > cut}
        HEAD.write_text(json.dumps(cache), encoding="utf-8")
    return cache


# ------------------------------------------------------------------ reaksi harga
def _bars(sym, tf, t0, t1):
    r = mt5.copy_rates_range(sym, tf, datetime.fromtimestamp(t0, timezone.utc), datetime.fromtimestamp(t1, timezone.utc))
    return pd.DataFrame(r) if r is not None and len(r) else None


def reaction(sym, t):
    """Gerak harga setelah event dalam satuan ATR(M5,14 bar sebelum event): +5, +15, +60 menit."""
    if t > time.time() - 60 * 60:
        return None
    df = _bars(sym, mt5.TIMEFRAME_M5, t - 20 * 300, t + 3700)
    if df is None or len(df) < 20:
        df, bar = _bars(sym, mt5.TIMEFRAME_M15, t - 20 * 900, t + 3700), 900
    else:
        bar = 300
    if df is None or len(df) < 16:
        return None
    pre = df[df.time < t]
    post = df[df.time >= t]
    if len(pre) < 14 or not len(post):
        return None
    atr = float((pre.high - pre.low).tail(14).mean()) or 1e-9
    o = float(post.open.iloc[0])
    out = {}
    for m in (5, 15, 60):
        x = post[post.time < t + m * 60]
        if len(x) and (m >= 15 or bar == 300):
            out[f"+{m}"] = round((float(x.close.iloc[-1]) - o) / atr, 2)
    return out or None


def backfill_times(cfg, days=730):
    """Waktu event historis (untuk statistik): NFP aturan kalender + FOMC dari config."""
    now = datetime.now(timezone.utc)
    out = []
    d = datetime(now.year - days // 365 - 1, 1, 1, tzinfo=timezone.utc)
    while d < now:
        f = d + timedelta(days=(4 - d.weekday()) % 7)          # Jumat pertama
        t = f.replace(hour=8 + _et_offset(f), minute=30)
        if now - timedelta(days=days) < t < now:
            out.append(("nfp", int(t.timestamp())))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    for s in cfg.get("fomc_dates", []):
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        t = dt.replace(hour=14 + _et_offset(dt), minute=0)
        if now - timedelta(days=days) < t < now:
            out.append(("fomc", int(t.timestamp())))
    return out


def stats(syms, cfg, force=False):
    """Statistik reaksi per jenis event per simbol (rata2 gerak ATR, % naik, n). Dihitung ulang maks 1x/hari."""
    try:
        st = json.loads(STATS.read_text(encoding="utf-8"))
        if not force and time.time() - st.get("ts", 0) < 86400:
            return st
    except Exception:
        pass
    ev = backfill_times(cfg) + [(classify(e["title"], cfg), e["t"]) for e in calendar_events(cfg) if e["t"] < time.time() - 3600]
    res = {"ts": time.time(), "by": {}}
    for sym in syms:
        for cat in {c for c, _ in ev if c}:
            rs = [reaction(sym, t) for c, t in ev if c == cat]
            rs = [r for r in rs if r]
            if not rs:
                continue
            row = {"n": len(rs)}
            for m in ("+5", "+15", "+60"):
                v = [r[m] for r in rs if m in r]
                if v:
                    row[m] = {"mean_abs": round(float(np.mean(np.abs(v))), 2), "up_pct": round(float(np.mean(np.array(v) > 0)) * 100, 0),
                              "n": len(v)}
            row["sampel_sedikit"] = len(rs) < cfg.get("min_samples", 8)
            res["by"].setdefault(sym, {})[cat] = row
    STATS.write_text(json.dumps(res), encoding="utf-8")
    return res


# ------------------------------------------------------------------ API untuk bot / dashboard / bridge
def events_for(sym, cfg, t0, t1, with_reaction=True):
    out = []
    items = [dict(e, kind="kalender") for e in calendar_events(cfg)] + \
            [dict(h, kind="headline") for h in headlines(cfg).get("items", {}).values()]
    for e in items:
        if not (t0 <= e["t"] <= t1):
            continue
        cat = classify(e["title"], cfg)
        tr = tier(cat, sym, cfg, e.get("impact")) if cat else (e.get("impact") if e["kind"] == "kalender" else None)
        if not tr or (e["kind"] == "headline" and not cat):
            continue
        c = cfg.get("categories", {}).get(cat, {})
        aff = [s for s in cfg.get("symbols", []) if tier(cat, s, cfg, e.get("impact"))] if cat else []
        x = {"t": e["t"], "title": e["title"], "source": e["source"], "kind": e["kind"], "cat": cat or "lainnya",
             "icon": c.get("icon", "•"), "tier": tr, "affected": aff,
             "forecast": e.get("forecast", ""), "previous": e.get("previous", ""), "actual": e.get("actual", "")}
        if with_reaction and e["t"] < time.time() - 3600:
            x["reaction"] = reaction(sym, e["t"])
        out.append(x)
    return sorted(out, key=lambda e: e["t"])


def blackout(sym, cfg, now=None):
    """-> (aktif, keterangan, aksi{'block'|'reduce'}, lot_mult, sl_mult). Hanya event tier yang dikonfigurasi."""
    b = dict(cfg.get("blackout", {}).get("*", {}), **cfg.get("blackout", {}).get(sym, {}))
    now = now or time.time()
    for e in events_for(sym, cfg, now - b.get("after", 15) * 60, now + b.get("before", 15) * 60, with_reaction=False):
        if e["kind"] == "kalender" and e["tier"] in b.get("tiers", ["High"]):
            return (True, f"{e['icon']} {e['title']} {datetime.fromtimestamp(e['t'], timezone.utc):%H:%M}Z ({e['tier']})",
                    b.get("action", "block"), b.get("lot_mult", 0.5), b.get("sl_mult", 1.0))
    return False, "", None, 1.0, 1.0
