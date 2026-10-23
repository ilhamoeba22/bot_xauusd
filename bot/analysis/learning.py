"""
ADAPTIVE LEARNING — bisa dijelaskan, bukan black-box.

Dataset : satu baris per trade (atau per burst) tertutup: fitur kondisi pasar saat entry
          (analysis/features.py), arah, simbol, hasil (menang/kalah, R), durasi.
          Sumber: seed dari backtest (backtest --learning) + trade paper/demo/live
          (trade nyata diberi bobot 3x karena mencerminkan biaya & eksekusi asli).
Model   : 1) statistik per fitur — win rate & rata-rata R saat fitur ada, dihaluskan
             (Bayesian shrinkage ke win rate umum supaya fitur yang jarang muncul tidak
             langsung dianggap hebat/buruk);
          2) logistic regression kecil (16 fitur biner, regularisasi L2, Newton/IRLS).
             Koefisien = bobot tiap fitur; skor keyakinan entry = sigmoid(jumlah bobot).
Retrain : tiap `retrain_every` trade baru dan saat ganti hari.
Pengaruh: mode 'observe' (default) hanya menghitung & menampilkan skor.
          mode 'gate' menolak entry dengan skor < min_confidence — hanya boleh di live
          kalau validasi walk-forward backtest menunjukkan perbaikan (lihat main.safety_gate).
"""
import json
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .features import FEATURES

FEAT = list(FEATURES)


def fit_logistic(X, y, w=None, l2=2.0, iters=25):
    """Logistic regression dengan Newton-Raphson (IRLS) + L2. Intercept tidak diregularisasi."""
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.ones(n) if w is None else w
    beta = np.zeros(k + 1)
    reg = np.full(k + 1, l2)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ beta, -30, 30)))
        g = Xb.T @ (w * (p - y)) + reg * beta
        H = (Xb * (w * p * (1 - p))[:, None]).T @ Xb + np.diag(reg)
        step = np.linalg.solve(H + 1e-9 * np.eye(k + 1), g)
        beta -= step
        if np.abs(step).max() < 1e-7:
            break
    return beta


def predict_proba(beta, X):
    return 1 / (1 + np.exp(-np.clip(beta[0] + X @ beta[1:], -30, 30)))


def walk_forward(rows: pd.DataFrame, retrain_every=50, min_train=200, thresholds=(0.45, 0.5, 0.55, 0.6)):
    """Validasi jujur: skor tiap trade dihitung dari model yang HANYA dilatih trade sebelumnya.
    Membandingkan PF semua trade vs trade dengan skor >= ambang (bagian yang bisa diskor)."""
    rows = rows.sort_values("time").reset_index(drop=True)
    X, y = rows[FEAT].values.astype(float), (rows["R"] > 0).astype(float).values
    probs = np.full(len(rows), np.nan)
    beta = None
    for i in range(min_train, len(rows)):
        if beta is None or (i - min_train) % retrain_every == 0:
            beta = fit_logistic(X[:i], y[:i])
        probs[i] = predict_proba(beta, X[i:i + 1])[0]
    scored = rows.iloc[min_train:].copy()
    scored["prob"] = probs[min_train:]

    def pf(r):
        w, l = r[r > 0].sum(), -r[r <= 0].sum()
        return round(float(w / l), 3) if l else None
    res = {"n_scored": int(len(scored)), "base": {"n": int(len(scored)), "pf": pf(scored.R),
                                                  "wr": round(float((scored.R > 0).mean() * 100), 1),
                                                  "exp_R": round(float(scored.R.mean()), 4)}, "thresholds": []}
    for t in thresholds:
        s = scored[scored.prob >= t]
        res["thresholds"].append({"min_confidence": t, "n": int(len(s)), "kept_pct": round(len(s) / len(scored) * 100, 1),
                                  "pf": pf(s.R) if len(s) else None,
                                  "wr": round(float((s.R > 0).mean() * 100), 1) if len(s) else None,
                                  "exp_R": round(float(s.R.mean()), 4) if len(s) else None,
                                  "sum_R": round(float(s.R.sum()), 1)})
    res["base"]["sum_R"] = round(float(scored.R.sum()), 1)
    return res, scored


class Learner:
    def __init__(self, cfg, dataset_path, seed_path, model_path, report_path, log=None):
        self.cfg = cfg
        self.dataset_path, self.seed_path = dataset_path, seed_path
        self.model_path, self.report_path = model_path, report_path
        self.log = log
        self.beta = None
        self.since_retrain = 0
        self.trained_day = None
        try:
            m = json.loads(model_path.read_text(encoding="utf-8"))
            if m.get("features") == FEAT:
                self.beta = np.array(m["beta"])
        except Exception:
            pass

    # ------------------------------------------------------------ data
    def load(self):
        frames = []
        for p, src_w in ((self.seed_path, 1.0), (self.dataset_path, self.cfg.get("live_weight", 3.0))):
            try:
                d = pd.read_csv(p)
                d["weight"] = src_w
                frames.append(d)
            except Exception:
                pass
        if not frames:
            return pd.DataFrame(columns=FEAT + ["time", "R", "weight"])
        d = pd.concat(frames, ignore_index=True)
        for f in FEAT:
            if f not in d:
                d[f] = 0
        d["time"] = pd.to_datetime(d["time"], utc=True, format="mixed")
        return d

    def record(self, features: dict, symbol, direction, R, pnl, duration_min, mode, open_time):
        row = {"time": open_time, "symbol": symbol, "dir": direction, **{f: int(features.get(f, 0)) for f in FEAT},
               "R": round(float(R), 4), "pnl": round(float(pnl), 2), "duration_min": round(duration_min, 1),
               "source": mode}
        new = not self.dataset_path.exists()
        pd.DataFrame([row]).to_csv(self.dataset_path, mode="a", header=new, index=False)
        self.since_retrain += 1
        if self.since_retrain >= self.cfg.get("retrain_every", 20):
            self.train("tiap %d trade" % self.cfg.get("retrain_every", 20))

    # ------------------------------------------------------------ latih & skor
    def train(self, why="manual"):
        d = self.load()
        if len(d) < self.cfg.get("min_samples", 100):
            self.write_report(d)
            return False
        X, y = d[FEAT].values.astype(float), (d["R"] > 0).astype(float).values
        self.beta = fit_logistic(X, y, d["weight"].values, l2=self.cfg.get("l2", 2.0))
        self.since_retrain = 0
        self.trained_day = datetime.now(timezone.utc).date()
        self.model_path.write_text(json.dumps({"features": FEAT, "beta": self.beta.tolist(),
                                               "trained": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                               "n": int(len(d)), "why": why}), encoding="utf-8")
        self.write_report(d)
        if self.log:
            self.log.info(f"LEARN  model dilatih ulang ({why}): {len(d)} sampel")
        return True

    def maybe_daily(self):
        if self.trained_day != datetime.now(timezone.utc).date():
            self.train("harian")

    def score(self, features: dict):
        """-> (probabilitas menang, kontribusi tiap fitur yang aktif) atau (None, [])."""
        if self.beta is None:
            return None, []
        x = np.array([float(features.get(f, 0)) for f in FEAT])
        p = float(predict_proba(self.beta, x[None, :])[0])
        contrib = sorted(((FEATURES[f], round(float(self.beta[i + 1]), 3)) for i, f in enumerate(FEAT) if x[i]),
                         key=lambda t: -abs(t[1]))
        return p, contrib

    # ------------------------------------------------------------ laporan untuk dashboard
    def write_report(self, d=None):
        d = self.load() if d is None else d
        now = datetime.now(timezone.utc)
        rep = {"generated": now.isoformat(timespec="seconds"), "n": int(len(d)),
               "n_live": int((d.get("source", pd.Series(dtype=str)) != "backtest").sum()) if len(d) else 0,
               "mode": self.cfg.get("mode", "observe"), "min_confidence": self.cfg.get("min_confidence", 0.5),
               "trained": self.beta is not None, "features": []}
        if len(d):
            base_wr = float((d.R > 0).mean())
            a = 20.0          # kekuatan shrinkage
            wk = d[d.time >= now - timedelta(days=7)]
            pw = d[(d.time < now - timedelta(days=7)) & (d.time >= now - timedelta(days=14))]
            rep["base_wr"] = round(base_wr * 100, 1)
            rep["base_exp_R"] = round(float(d.R.mean()), 4)
            for i, f in enumerate(FEAT):
                on = d[d[f] == 1]
                n, wins = len(on), int((on.R > 0).sum())
                wr_s = (wins + a * base_wr) / (n + a)

                def wr(x):
                    s = x[x[f] == 1]
                    return (round(float((s.R > 0).mean() * 100), 1), int(len(s))) if len(s) else (None, 0)
                w_now, n_now = wr(wk)
                w_prev, n_prev = wr(pw)
                trend = "–"
                if w_now is not None and w_prev is not None and n_now >= 5 and n_prev >= 5:
                    trend = "membaik" if w_now - w_prev >= 10 else "memburuk" if w_prev - w_now >= 10 else "stabil"
                rep["features"].append({
                    "key": f, "name": FEATURES[f], "n": n,
                    "wr": round(wins / n * 100, 1) if n else None, "wr_smooth": round(wr_s * 100, 1),
                    "exp_R": round(float(on.R.mean()), 4) if n else None,
                    "weight": round(float(self.beta[i + 1]), 3) if self.beta is not None else None,
                    "wr_week": w_now, "n_week": n_now, "wr_prev_week": w_prev, "n_prev_week": n_prev, "trend": trend})
            if self.beta is not None:
                rep["intercept"] = round(float(self.beta[0]), 3)
        try:
            v = json.loads((self.seed_path.parent / "learning_validation.json").read_text(encoding="utf-8"))
            rep["validation"] = v
        except Exception:
            pass
        self.report_path.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        return rep
