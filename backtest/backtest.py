"""
BACKTEST XAUUSD_EXNESS — wajib dijalankan sebelum bot dipakai di akun real.

Memakai modul strategi & parameter exit yang SAMA dengan bot live
(bot/strategy.py + config/config.json), data historis dari terminal MT5 Exness.

Cara pakai (dari folder D:\\Trade\\XAUUSD_EXNESS):
  python backtest\\backtest.py                      # semua simbol enabled, 12 bulan M5
  python backtest\\backtest.py --symbol XAUUSD247   # satu simbol
  python backtest\\backtest.py --months 6
  python backtest\\backtest.py --optimize           # grid kecil + uji walk-forward (in/out-of-sample)
  python backtest\\backtest.py --offline            # pakai data cache, tanpa MT5
  python backtest\\backtest.py --slippage 30       # uji ketahanan: biaya ekstra 30 poin/trade

Model eksekusi (sengaja KONSERVATIF):
  * sinyal dihitung di close bar i, entry di OPEN bar i+1
  * bar MT5 = harga BID; BUY masuk di ask (bid+spread), SELL keluar di ask
  * spread per bar dari data broker + extra_slippage_points + komisi config
  * kalau dalam 1 bar SL dan TP sama2 tersentuh -> dianggap kena SL dulu
  * trailing digeser memakai high/low bar i, baru berlaku mulai bar i+1
Keterbatasan: filter berita TIDAK disimulasikan (kalender historis tidak ada),
burst 'split' tidak mengubah hasil (lot dibagi); burst 'multiply' mengalikan risiko per sinyal.
Semua simbol aktif disimulasikan BERSAMA (equity, rem harian & batas risiko terbuka dipakai bersama).
  python backtest\\backtest.py --learning         # + seed dataset & validasi adaptive learning
"""
import argparse
import itertools
import json
import sys
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.strategy import build_signals, min_bars      # noqa: E402
from bot.sessions import session_allowed, session_of  # noqa: E402
from bot.common import sym_cfg, risk_group  # noqa: E402
from bot.analysis import smc as smc_mod  # noqa: E402

DATA_DIR = ROOT / "backtest" / "data"
RES_DIR = ROOT / "backtest" / "results"
TF_MIN = {"M1": 1, "M5": 5, "M15": 15}


# ------------------------------------------------------------------ data
def load_data(sym, tf, months, offline=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = DATA_DIR / f"{sym}_{tf}.csv"
    df = None
    if not offline:
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                mt5.symbol_select(sym, True)
                tfc = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15}[tf]
                r = None
                # terminal membatasi 'Max bars in chart' (default 100.000)
                for n in (99000, 60000, 30000):
                    r = mt5.copy_rates_from_pos(sym, tfc, 0, n)
                    if r is not None and len(r):
                        break
                info = mt5.symbol_info(sym)
                if r is not None and len(r):
                    df = pd.DataFrame(r)
                    df["point"] = info.point
                    df.to_csv(cache, index=False)
                else:
                    print(f"  ! {sym} {tf}: MT5 tidak memberi data ({mt5.last_error()})")
        except ImportError:
            print("  ! paket MetaTrader5 tidak ada, pakai cache")
    if df is None:
        if not cache.exists():
            return None
        df = pd.read_csv(cache)
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    cut = df["dt"].iloc[-1] - pd.DateOffset(months=months)
    have = (df["dt"].iloc[-1] - df["dt"].iloc[0]).days / 30.4
    if have < months - 0.5:
        print(f"  ! {sym} {tf}: data hanya {have:.1f} bulan (diminta {months})")
    # sisakan bar pemanasan indikator sebelum titik potong
    first = max(0, int(df.index[df["dt"] >= cut][0]) - 1000)
    return df.iloc[first:].reset_index(drop=True), int((df["dt"].iloc[first:] < cut).sum())


# ------------------------------------------------------------------ engine
def burst_plan(cfg, sym):
    """-> (tiket per sinyal, fraksi equity yang dipertaruhkan per sinyal, maks tiket simbol).
    split    : lot dibagi, total risiko = risk_per_trade_pct
    multiply : tiap tiket risiko penuh -> total = burst_size x risk_per_trade_pct,
               dan hanya SATU burst aktif per simbol (maks tiket = burst_size, tanpa buffer)"""
    rk, sc = sym_cfg(cfg, sym)["risk"], cfg["symbols"][sym]
    n = max(1, int(rk.get("burst_size", 1)))
    per = rk["risk_per_trade_pct"] / 100 * sc.get("risk_mult", 1.0)
    if rk.get("burst_mode", "split") == "multiply":
        return n, per * n, n
    return n, per, sc["max_positions"]


def simulate_portfolio(data, cfg):
    """data = {simbol: (df, warm)}. Semua simbol berjalan di SATU garis waktu dengan
    equity, rem harian, jeda kalah beruntun & batas risiko total yang DIPAKAI BERSAMA
    (persis seperti bot live yang menjalankan XAUUSD + XAUUSD247 serentak).
    Parameter PER SIMBOL (sama dengan bot live): exit/strategy/sessions/risk dari sym_cfg(), hari trading
    (trade_days), jepit SL/TP % harga, komisi % harga (crypto), dan batas posisi/risiko terbuka per
    risk_group. Rem harian & HALT tetap satu untuk seluruh akun."""
    rk = cfg["risk"]                          # rem akun: rugi harian
    slip = cfg["backtest"]["extra_slippage_points"]
    S, starts = {}, []
    for sym, (df, warm) in data.items():
        C = sym_cfg(cfg, sym)
        scfg = C["strategy"]
        d = build_signals(df, scfg)
        point = float(d["point"].iloc[0])
        sc = cfg["symbols"][sym]
        n, rfrac, maxt = burst_plan(cfg, sym)
        st = max(warm, min_bars(scfg))
        smode = cfg.get("smc", {}).get("filter_mode", "off")
        sb = smc_mod.run(df, cfg.get("smc", {}).get("swing_k", 3))[0]["smc_bias"].values if smode in ("prefer", "strict") else None
        S[sym] = dict(o=d.open.values, h=d.high.values, l=d.low.values, c=d.close.values,
                      sig=d.signal.values, atr=d.atr.values, t=d.time.values.astype(np.int64),
                      raw=d.spread.values * point, sp=(d.spread.values + slip * sc.get("slippage_mult", 1)) * point,
                      comm=sc.get("commission_price_units", 0.0), comm_pct=sc.get("commission_pct", 0.0),
                      maxspr=sc["max_spread_points"] * point,
                      n=n, rfrac=rfrac, maxt=min(maxt, C["risk"]["max_positions_total"]), start=st,
                      smode=smode, sbias=sb, ex=C["exit"], ses=C["sessions"], days=sc.get("trade_days"),
                      grp=risk_group(cfg, sym), rk=C["risk"],
                      sl_min=sc.get("sl_min_pct", 0) / 100, sl_max=sc.get("sl_max_pct", 0) / 100,
                      tp_min=sc.get("tp_min_pct", 0) / 100, tp_max=sc.get("tp_max_pct", 0) / 100)
        starts.append(int(d.time.values[st]))
    t0 = min(starts)
    timeline = np.unique(np.concatenate([v["t"][v["t"] >= t0] for v in S.values()]))
    for v in S.values():
        pos_of = {int(t): i for i, t in enumerate(v["t"])}
        v["idx"] = np.array([pos_of.get(int(t), -1) for t in timeline])
    eq = cfg["backtest"]["start_equity"]
    eq_curve, trades, pos = [], [], []
    day, day_start, day_halt = None, eq, False
    consec, paused_until = {}, {}              # kalah beruntun & jeda PER GRUP risiko

    def close(p, v, i, price, why):
        nonlocal eq
        cost = v["comm"] + v["comm_pct"] * p["entry"] / 100      # komisi tetap (emas) atau % harga (crypto)
        R = (p["dir"] * (price - p["entry"]) - cost) / p["risk_dist"]
        w = p["rfrac"] * R                       # perubahan equity (fraksi)
        eq *= 1 + w
        g, grk = v["grp"], v["rk"]
        consec[g] = consec.get(g, 0) + 1 if R < 0 else 0      # satu burst = satu hasil
        if grk["max_consecutive_losses"] > 0 and consec[g] >= grk["max_consecutive_losses"]:
            paused_until[g], consec[g] = int(v["t"][i]) + grk["loss_pause_minutes"] * 60, 0
        ot = int(v["t"][p["i"]])
        trades.append({"symbol": p["sym"], "open_time": pd.to_datetime(ot, unit="s"),
                       "close_time": pd.to_datetime(int(v["t"][i]), unit="s"),
                       "dir": "BUY" if p["dir"] == 1 else "SELL", "tickets": p["tickets"],
                       "entry": round(p["entry"], 3), "exit": round(price, 3), "R": round(R, 4),
                       "w": w, "why": why, "session": session_of((ot // 3600) % 24, v["ses"]) or "-",
                       "bars": i - p["i"], "equity": eq, "sig_i": p["i"] - 1})

    def open_risk(grp=None):
        # posisi yang SL-nya sudah di wilayah untung tidak lagi berisiko; grp=None = seluruh akun
        return sum(p["rfrac"] * max(0.0, p["dir"] * (p["entry"] - p["sl"])) / p["risk_dist"]
                   for p in pos if grp is None or S[p["sym"]]["grp"] == grp)

    for k, now in enumerate(timeline):
        dday = int(now) // 86400
        if dday != day:
            day, day_start, day_halt = dday, eq, False
        for sym, v in S.items():
            i = v["idx"][k]
            if i < 1 or i < v["start"]:
                continue
            o, h, l, sig, atr = v["o"], v["h"], v["l"], v["sig"], v["atr"]
            ex, grp, grk = v["ex"], v["grp"], v["rk"]
            sp = v["sp"][i]
            for p in [p for p in pos if p["sym"] == sym]:
                bye = None
                if ex["max_hold_bars"] and i - p["i"] >= ex["max_hold_bars"]:
                    bye = (o[i] if p["dir"] == 1 else o[i] + sp, "waktu")
                elif ex["close_on_opposite_signal"] and sig[i - 1] == -p["dir"]:
                    bye = (o[i] if p["dir"] == 1 else o[i] + sp, "sinyal balik")
                elif p["dir"] == 1:
                    if l[i] <= p["sl"]:
                        bye = (min(o[i], p["sl"]), "SL" if p["sl"] < p["entry"] else "trail")
                    elif p["tp"] and h[i] >= p["tp"]:
                        bye = (max(o[i], p["tp"]), "TP")
                else:
                    if h[i] + sp >= p["sl"]:
                        bye = (max(o[i] + sp, p["sl"]), "SL" if p["sl"] > p["entry"] else "trail")
                    elif p["tp"] and l[i] + sp <= p["tp"]:
                        bye = (min(o[i] + sp, p["tp"]), "TP")
                if bye:
                    close(p, v, i, bye[0], bye[1]); pos.remove(p); continue
                a = atr[i]            # trailing adaptif memakai ATR TERKINI
                fav = (h[i] - p["entry"]) if p["dir"] == 1 else (p["entry"] - (l[i] + sp))
                ext = h[i] if p["dir"] == 1 else l[i] + sp
                cand = []
                if fav >= ex["be_trigger_atr"] * a:
                    cand.append(p["entry"] + p["dir"] * ex["be_lock_atr"] * a)
                if fav >= ex["trail_start_atr"] * a:
                    kk = ex["trail_tight_atr"] if fav >= ex["trail_tight_after_atr"] * a else ex["trail_atr"]
                    cand.append(ext - p["dir"] * kk * a)
                # trailing ikut candle: SL ke low/high candle yang BARU tertutup (berlaku bar berikutnya)
                if ex.get("candle_trail") and fav >= ex.get("candle_trail_after_atr", ex["be_trigger_atr"]) * a:
                    buf = ex.get("candle_trail_buffer_atr", 0.1) * a
                    cand.append(l[i] - buf if p["dir"] == 1 else h[i] + sp + buf)
                for c in cand:
                    if (p["dir"] == 1 and c > p["sl"]) or (p["dir"] == -1 and c < p["sl"]):
                        p["sl"] = c
            if not day_halt and (eq - day_start) / day_start * 100 <= -rk["daily_loss_limit_pct"]:
                day_halt = True
            s = sig[i - 1]
            if not s or day_halt or now < paused_until.get(grp, -1) or atr[i - 1] <= 0:
                continue
            t_sym = sum(p["tickets"] for p in pos if p["sym"] == sym)
            t_grp = sum(p["tickets"] for p in pos if S[p["sym"]]["grp"] == grp)
            wday = (int(now) // 86400 + 3) % 7                      # 1 Jan 1970 = Kamis -> Senin = 0
            # jarak minimum antar-entri SEARAH (x ATR) — posisi menumpuk dalam rentang sempit = risiko
            # berkorelasi tanpa diversifikasi (Prioritas 3, 28 Sep). Sama dengan Bot.entry_blockers().
            same_dir_open = [p for p in pos if p["sym"] == sym and p["dir"] == s]
            too_close = same_dir_open and min(abs(o[i] - p["entry"]) for p in same_dir_open) < grk.get("min_spacing_atr", 0.5) * atr[i - 1]
            if (t_sym + v["n"] > v["maxt"] or t_grp + v["n"] > grk["max_positions_total"]
                    or v["raw"][i] > v["maxspr"] or too_close
                    or not session_allowed((int(now) // 3600) % 24, v["ses"], wday, v["days"])[0]
                    or open_risk(grp) + v["rfrac"] > grk.get("max_total_risk_pct", 100) / 100 + 1e-12
                    or (rk.get("daily_budget_gate", False) and
                        (eq - day_start) / day_start - open_risk() - v["rfrac"] < -rk["daily_loss_limit_pct"] / 100)):
                continue
            # konfluensi SMC (bias struktur di bar sinyal i-1)
            tickets, rfrac = v["n"], v["rfrac"]
            if v["sbias"] is not None and v["sbias"][i - 1] != s:
                if v["smode"] == "strict":
                    continue
                # prefer: melawan bias -> hanya 1 tiket (burst dikecilkan), bukan ditolak
                tickets, rfrac = 1, v["rfrac"] / v["n"]
            a = atr[i - 1]
            entry = o[i] + sp if s == 1 else o[i]
            dist = ex["sl_atr"] * a                                 # sama dengan Bot.sl_distance
            if v["sl_min"]:
                dist = max(dist, entry * v["sl_min"])
            if v["sl_max"]:
                dist = min(dist, entry * v["sl_max"])
            tpd = ex["tp_atr"] * a if ex.get("tp_atr") else 0.0
            if tpd and v["tp_min"]:
                tpd = max(tpd, entry * v["tp_min"])
            if tpd and v["tp_max"]:
                tpd = min(tpd, entry * v["tp_max"])
            pos.append({"sym": sym, "i": i, "dir": s, "entry": entry, "risk_dist": dist,
                        "sl": entry - s * dist, "tickets": tickets, "rfrac": rfrac,
                        "tp": entry + s * tpd if tpd else 0.0})
        eq_curve.append(eq)
    for p in pos:
        v = S[p["sym"]]
        close(p, v, len(v["c"]) - 1, v["c"][-1], "akhir data")
    return pd.DataFrame(trades), pd.DataFrame({"dt": pd.to_datetime(timeline, unit="s"), "equity": eq_curve})


def simulate(df, warm, cfg, sym):
    return simulate_portfolio({sym: (df, warm)}, cfg)


# ------------------------------------------------------------------ metrik
def metrics(tr, curve, start_eq):
    """PF & return dihitung dari kontribusi equity (kolom w), karena satu trade burst
    berlipat mempertaruhkan N x risiko. curve=None -> kurva dibangun dari trade tertutup."""
    if tr.empty:
        return {"trades": 0}
    if curve is None:
        curve = pd.DataFrame({"dt": pd.to_datetime(tr.close_time).values,
                              "equity": start_eq * np.cumprod(1 + tr.w.values)})
    w, lo = tr.R[tr.R > 0], tr.R[tr.R <= 0]
    gw, gl = tr.w[tr.w > 0].sum(), -tr.w[tr.w <= 0].sum()
    peak = curve.equity.cummax()
    dd = ((curve.equity - peak) / peak).min() * 100
    streak = mx = 0
    for r in tr.R:
        streak = streak + 1 if r <= 0 else 0
        mx = max(mx, streak)
    days = max(1, (curve.dt.iloc[-1] - curve.dt.iloc[0]).days)
    return {
        "trades": int(len(tr)),
        "trades_per_day": round(len(tr) / days * 7 / 5, 2),
        "win_rate_pct": round(len(w) / len(tr) * 100, 2),
        "profit_factor": round(gw / gl, 3) if gl else float("inf"),
        "expectancy_R": round(tr.R.mean(), 4),
        "avg_win_R": round(w.mean(), 3) if len(w) else 0,
        "avg_loss_R": round(lo.mean(), 3) if len(lo) else 0,
        "max_drawdown_pct": round(dd, 2),
        "return_pct": round((curve.equity.iloc[-1] / start_eq - 1) * 100, 2),
        "max_consecutive_losses": int(mx),
        "period": f"{curve.dt.iloc[0]:%Y-%m-%d} s/d {curve.dt.iloc[-1]:%Y-%m-%d}",
    }


def breakdown(tr, key):
    rows = []
    for k, g in tr.groupby(key):
        w, lo = g.w[g.w > 0].sum(), abs(g.w[g.w <= 0].sum())
        rows.append(f"    {str(k):<10} {len(g):>5} trade  WR {(g.R > 0).mean()*100:5.1f}%  "
                    f"PF {w/lo if lo else float('inf'):5.2f}  total {g.R.sum():+8.1f}R")
    return "\n".join(rows)


def verdict(m):
    if m.get("trades", 0) < 100:
        return "TIDAK CUKUP DATA (<100 trade) — jangan live."
    if m["profit_factor"] < 1.0 or m["expectancy_R"] <= 0:
        return "NEGATIF — strategi merugi setelah biaya. JANGAN live."
    if m["profit_factor"] < 1.15:
        return "TIPIS — keunggulan kecil, rentan biaya/slippage riil. Demo panjang dulu."
    return "POSITIF — lanjut uji DEMO minimal 2-4 minggu sebelum live."


def report(sym, tf, cfg, tr, curve, out):
    m = metrics(tr, curve, cfg["backtest"]["start_equity"])
    if m["trades"]:
        half = len(tr) // 2
        m["split_half_PF"] = [float(metrics(tr.iloc[:half], None, 1).get("profit_factor")),
                              float(metrics(tr.iloc[half:], None, 1).get("profit_factor"))]
    m["verdict"] = verdict(m)
    lines = [
        "=" * 72, f" BACKTEST {sym} {tf}   ({m.get('period', '-')})", "=" * 72,
        f"  Jumlah trade        : {m['trades']}  (~{m.get('trades_per_day', 0)}/hari bursa)",
        f"  Win rate            : {m.get('win_rate_pct', 0)}%",
        f"  Profit factor       : {m.get('profit_factor', 0)}",
        f"  Expectancy          : {m.get('expectancy_R', 0)} R/trade"
        f"   (rata2 menang {m.get('avg_win_R', 0)}R, kalah {m.get('avg_loss_R', 0)}R)",
        f"  Max drawdown        : {m.get('max_drawdown_pct', 0)}%",
        f"  Return              : {m.get('return_pct', 0)}%  (compounding)",
        f"  Risiko              : {cfg['risk']['risk_per_trade_pct']}%/tiket, burst {cfg['risk'].get('burst_size', 1)} "
        f"({cfg['risk'].get('burst_mode', 'split')}), risiko terbuka maks {cfg['risk'].get('max_total_risk_pct', '-')}%",
        f"  Kalah beruntun max  : {m.get('max_consecutive_losses', 0)}",
        f"  PF paruh 1 / paruh 2: {m.get('split_half_PF')}",
        "", f"  VERDICT: {m['verdict']}", "",
    ]
    if m["trades"]:
        tr = tr.copy()
        tr["bulan"] = pd.to_datetime(tr.close_time).dt.strftime("%Y-%m")
        if tr.symbol.nunique() > 1:
            lines += ["  Per simbol (PF dari kontribusi equity):", breakdown(tr, "symbol"), ""]
        lines += ["  Per sesi:", breakdown(tr, "session"), "", "  Per alasan keluar:",
                  breakdown(tr, "why"), "", "  Per bulan:", breakdown(tr, "bulan")]
    txt = "\n".join(lines)
    print(txt)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.txt").write_text(txt, encoding="utf-8")
    tr.to_csv(out / "trades.csv", index=False)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(curve.dt, curve.equity, lw=1)
        ax.set_title(f"{sym} {tf} equity — PF {m.get('profit_factor')}  DD {m.get('max_drawdown_pct')}%")
        ax.grid(alpha=.3); fig.tight_layout(); fig.savefig(out / "equity.png", dpi=110); plt.close(fig)
    except Exception:
        pass
    return m


# ------------------------------------------------------------------ optimize
GRID = {
    "entry": [{"name": "ema_cross", "fast": 5, "slow": 13}, {"name": "ema_cross", "fast": 8, "slow": 21},
              {"name": "ema_cross", "fast": 9, "slow": 34}, {"name": "rsi_cross50", "fast": 8, "slow": 21, "rsi_period": 7}],
    "sl_atr": [1.0, 1.5, 2.0],
    "trail_atr": [0.8, 1.2],
    "trend_on": [True, False],
    "atr_min": [0.2, 0.4],
}


def optimize(df, warm, cfg, sym):
    """Pilih kombinasi terbaik di 70% data AWAL, lalu nilai di 30% AKHIR yang tidak dilihat."""
    n = len(df)
    split = warm + int((n - warm) * 0.7)
    is_df, oos_df = df.iloc[:split].reset_index(drop=True), df.iloc[split - 1500:].reset_index(drop=True)
    rows = []
    keys = list(GRID)
    for combo in itertools.product(*GRID.values()):
        c = copy.deepcopy(cfg)
        g = dict(zip(keys, combo))
        c["strategy"]["entry"] = g["entry"]
        c["exit"]["sl_atr"] = g["sl_atr"]
        c["exit"]["trail_atr"] = g["trail_atr"]
        for f in c["strategy"]["filters"]:
            if f["name"] == "trend_ema": f["enabled"] = g["trend_on"]
            if f["name"] == "atr_band": f["min_pct"] = g["atr_min"]
        tr, cv = simulate(is_df, warm, c, sym)
        m = metrics(tr, cv, c["backtest"]["start_equity"])
        if m["trades"] >= 150:
            rows.append((m["profit_factor"], g, m, c))
    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"\n  WALK-FORWARD {sym}: {len(rows)} kombinasi valid; 5 terbaik in-sample -> out-of-sample")
    print(f"  {'entry':<22}{'SL':>5}{'trail':>6}{'trend':>6}{'atrmin':>7} | {'IS PF':>6}{'IS n':>6} | {'OOS PF':>7}{'OOS n':>6}{'OOS DD%':>8}")
    best = []
    for pf, g, m, c in rows[:5]:
        tr, cv = simulate(oos_df, 1500, c, sym)
        mo = metrics(tr, cv, c["backtest"]["start_equity"])
        e = g["entry"]
        name = f"{e['name']} {e.get('fast')}/{e.get('slow')}"
        print(f"  {name:<22}{g['sl_atr']:>5}{g['trail_atr']:>6}{str(g['trend_on']):>6}{g['atr_min']:>7} | "
              f"{pf:>6}{m['trades']:>6} | {mo.get('profit_factor', 0):>7}{mo.get('trades', 0):>6}{mo.get('max_drawdown_pct', 0):>8}")
        best.append({"params": g, "in_sample": m, "out_of_sample": mo})
    return best


# ------------------------------------------------------------------ learning
def learning_run(data, cfg, tr):
    """Fitur entry tiap trade backtest -> seed dataset + validasi walk-forward model."""
    from bot.analysis.features import bar_context, entry_features, FEATURES
    from bot.analysis.learning import walk_forward
    rows = []
    k = cfg.get("smc", {}).get("swing_k", 3)
    for sym, (df, warm) in data.items():
        ctx = bar_context(df, sym_cfg(cfg, sym)["sessions"], k)
        for _, t in tr[tr.symbol == sym].iterrows():
            i = int(t.sig_i)
            d = 1 if t.dir == "BUY" else -1
            f = entry_features(ctx.iloc[i], d, sym, df.iloc[max(0, i - 299):i + 1])
            rows.append({"time": t.open_time.isoformat() + "+00:00", "symbol": sym, "dir": d, **f,
                         "R": t.R, "pnl": t.w, "duration_min": t.bars * TF_MIN[cfg["backtest"]["timeframe"]],
                         "source": "backtest"})
    rows = pd.DataFrame(rows)
    rows.to_csv(RES_DIR / "learning_seed.csv", index=False)
    res, scored = walk_forward(rows)
    res["generated"] = datetime.now().isoformat(timespec="seconds")
    base = res["base"]
    print("\n" + "=" * 72 + "\n VALIDASI ADAPTIVE LEARNING (walk-forward, skor dari model yang hanya\n"
          " dilatih trade SEBELUMNYA; 200 trade pertama = pemanasan)\n" + "=" * 72)
    print(f"  semua trade      : n {base['n']:>5}  WR {base['wr']:5.1f}%  PF {base['pf']}  exp {base['exp_R']:+.4f}R  total {base['sum_R']:+.1f}R")
    best = None
    for t in res["thresholds"]:
        print(f"  skor >= {t['min_confidence']:.2f}     : n {t['n']:>5} ({t['kept_pct']:5.1f}%)  WR {t['wr']}%  PF {t['pf']}  "
              f"exp {t['exp_R']}R  total {t['sum_R']:+.1f}R")
        if t["pf"] and base["pf"] and t["pf"] > base["pf"] * 1.05 and t["kept_pct"] >= 40 and t["sum_R"] >= base["sum_R"] * 0.8:
            best = best or t
    res["improves"] = best is not None
    res["recommended_min_confidence"] = best["min_confidence"] if best else None
    print(f"\n  KESIMPULAN: {'gate MEMBANTU pada ambang ' + str(best['min_confidence']) if best else 'gate TIDAK terbukti membantu -> biarkan mode observe'}")
    # bobot model penuh untuk dibaca
    from bot.analysis.learning import fit_logistic, FEAT
    beta = fit_logistic(rows[FEAT].values.astype(float), (rows.R > 0).astype(float).values)
    print("\n  Bobot fitur (model dilatih semua data backtest; + = menaikkan peluang menang):")
    for i, f in sorted(enumerate(FEAT), key=lambda x: -beta[x[0] + 1]):
        on = rows[rows[f] == 1]
        print(f"    {beta[i + 1]:+.3f}  {FEATURES[f]:<55} n {len(on):>5}  WR {((on.R > 0).mean() * 100 if len(on) else 0):5.1f}%  exp {on.R.mean() if len(on) else 0:+.3f}R")
    json.dump(res, open(RES_DIR / "learning_validation.json", "w", encoding="utf-8"), indent=2, default=str)
    return res


# ------------------------------------------------------------------ main
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--months", type=int)
    ap.add_argument("--tf")
    ap.add_argument("--optimize", action="store_true")
    ap.add_argument("--learning", action="store_true", help="buat seed dataset & validasi adaptive learning")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--slippage", type=float, help="ganti extra_slippage_points (uji ketahanan biaya)")
    ap.add_argument("--risk", type=float, help="ganti risk_per_trade_pct")
    ap.add_argument("--burst", type=int, help="ganti burst_size")
    ap.add_argument("--burst-mode", choices=["split", "multiply"])
    ap.add_argument("--max-risk", type=float, help="ganti max_total_risk_pct")
    ap.add_argument("--daily", type=float, help="ganti daily_loss_limit_pct")
    ap.add_argument("--config", default=str(ROOT / "config" / "config.json"))
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    # Auto-deteksi broker & sesuaikan komisi jika MT5 terhubung
    try:
        import MetaTrader5 as _mt5
        if _mt5.initialize():
            from bot.broker_detect import detect_broker, apply_broker_profile
            b_info = detect_broker()
            if b_info:
                apply_broker_profile(cfg, b_info)
                print(f"BROKER (MT5): {b_info['name']} | komisi: ${b_info['commission_per_lot_usd']:.2f}/lot | simbol: {b_info['primary_gold_symbol']}")
    except Exception:
        pass
    if a.tf: cfg["backtest"]["timeframe"] = a.tf
    if a.slippage is not None: cfg["backtest"]["extra_slippage_points"] = a.slippage
    for k, v in (("risk_per_trade_pct", a.risk), ("burst_size", a.burst), ("burst_mode", a.burst_mode),
                 ("max_total_risk_pct", a.max_risk), ("daily_loss_limit_pct", a.daily)):
        if v is not None:
            cfg["risk"][k] = v
    if a.symbol:
        for s_ in a.symbol.split(","):
            cfg["symbols"][s_]["enabled"] = True
    months = a.months or cfg["backtest"]["months"]
    tf = cfg["backtest"]["timeframe"]
    syms = a.symbol.split(",") if a.symbol else [s for s, v in cfg["symbols"].items() if v.get("enabled")]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    from bot.common import config_fingerprint
    summary = {"generated": datetime.now().isoformat(timespec="seconds"), "config": a.config,
               "fingerprint": config_fingerprint(cfg) if not (a.slippage or a.risk or a.burst or a.burst_mode
                                                              or a.max_risk or a.daily or a.symbol or a.months or a.tf) else None,
               "timeframe": tf, "months": months, "results": {}}
    data = {}
    for sym in syms:
        print(f"\n>> {sym} {tf} {months} bulan")
        got = load_data(sym, tf, months, a.offline)
        if got is None:
            print(f"  ! {sym}: tidak ada data (simbol tidak ada di akun ini / cache kosong) — dilewati")
            continue
        if a.optimize:
            summary["results"][sym] = {"walk_forward": optimize(*got, cfg, sym)}
            continue
        data[sym] = got
    if data:
        # semua simbol aktif disimulasikan BERSAMA: equity, rem harian & batas risiko dipakai bersama
        name = "+".join(data)
        tr, curve = simulate_portfolio(data, cfg)
        summary["results"]["PORTFOLIO"] = report(name, tf, cfg, tr, curve, RES_DIR / f"{stamp}_{name}_{tf}")
        if a.learning and len(tr):
            summary["learning"] = learning_run(data, cfg, tr)
        for sym in data:
            sub = tr[tr.symbol == sym] if len(tr) else tr
            summary["results"][sym] = metrics(sub, None, cfg["backtest"]["start_equity"]) if len(sub) else {"trades": 0}
    RES_DIR.mkdir(parents=True, exist_ok=True)
    name = "latest_optimize.json" if a.optimize else "latest_metrics.json"
    json.dump(summary, open(RES_DIR / name, "w", encoding="utf-8"), indent=2, default=str)
    print(f"\nHasil tersimpan di {RES_DIR}")


if __name__ == "__main__":
    main()
