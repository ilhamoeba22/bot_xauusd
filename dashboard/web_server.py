"""
DASHBOARD WEB XAUUSD_EXNESS — chart candlestick + semua lapisan analisis.

    python dashboard\\web_server.py            -> http://127.0.0.1:8765
    (atau start_bot.cmd web)

Server lokal (hanya 127.0.0.1), tanpa dependensi tambahan. Hanya MEMBACA data MT5 & log bot;
tidak pernah mengirim order.
  /                   halaman dashboard (dashboard/web/index.html)
  /api/state          status bot + status bridge MT5 (heartbeat engine & indikator)
  /api/chart          candle + semua objek analisis untuk simbol & timeframe TAMPILAN (?symbol=&tf=)
  /api/entries        alasan tiap entry bot (logs/entries.jsonl)
  /api/bursts         log burst per tiket (logs/burst_log.csv, timestamp milidetik)
  /api/learning       laporan adaptive learning
  /api/health         status kesehatan real-time (MT5, bot, bridge) + riwayat putus/pulih (logs/health.log)
  /api/control        POST pause / resume / emergency / burst_on / burst_off (menulis file flag)

Pemantau kesehatan berjalan di THREAD TERSENDIRI (tiap 5 dtk), terpisah dari loop trading bot:
mengecek MT5 langsung lewat API, umur state bot, dan heartbeat bridge; setiap transisi putus/pulih
dicatat dengan timestamp ke logs/health.log. Putus > dashboard.unmonitored_after_min menit ->
status "PASAR TIDAK DIPANTAU".

Kinerja: objek analisis (pola, struktur, SMC) hanya dihitung ulang saat ada candle TERTUTUP baru
untuk simbol+TF itu; di antara itu hanya candle yang sedang berjalan yang diperbarui.
"""
import csv
import json
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd                                     # noqa: E402
import MetaTrader5 as mt5                               # noqa: E402
from bot.common import (LOG_DIR, STATE_PATH, FLAG_PAUSE, FLAG_STOP, FLAG_HALT, FLAG_BURST_OFF, FLAG_BURST_NOW,  # noqa: E402
                        BURST_LOG_PATH, CONTROL_AUDIT, audit, load_config, read_active, write_active)
from bot.analysis import overlay, trade_marks           # noqa: E402
from bot import profiles as prof                        # noqa: E402
from bot import datastore                               # noqa: E402
from bot.analysis import sr_mtf, newsimpact             # noqa: E402

SYNC = {"state": "belum mulai", "detail": {}, "ts": 0}


def news_cfg(cfg):
    return dict(cfg.get("news_impact", {}), symbols=list(cfg["symbols"]))


def sr_for(symbol, cfg):
    with _mt5_lock:
        return sr_mtf.compute(symbol, dict(cfg.get("sr", {}), round_step=cfg["symbols"].get(symbol, {}).get("round_step", 50)))


def _sync_loop():
    """Cache bar 2 tahun: unduh bertahap sekali, lalu hanya bar baru tiap 60 dtk."""
    while True:
        try:
            cfg = load_config()
            syms = [s for s, v in (read_json(STATE_PATH) or {}).get("symbols", {}).items() if v.get("available")] or \
                   [s for s, v in cfg["symbols"].items() if v.get("enabled")]
            SYNC["state"] = "sinkron..."
            for sym in syms:
                for tf in datastore.TFS:
                    n = datastore.sync(sym, tf, cfg.get("history_days", 730), _mt5_lock)
                    SYNC["detail"][f"{sym}_{tf}"] = dict(datastore.info(sym, tf), new=n)
            SYNC.update(state="tersinkron", ts=time.time())
        except Exception as e:
            SYNC.update(state=f"error: {e}")
        time.sleep(60)

import re                                              # noqa: E402
_ALLOWED_HOST = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\]|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|.*\.trycloudflare\.com|.*\.localhost\.run|.*\.pinggy\.link)(:\d+)?$", re.I)
_ALLOWED_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\]|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|.*\.trycloudflare\.com|.*\.localhost\.run|.*\.pinggy\.link)(:\d+)?$", re.I)
_LOCAL_HOST = _ALLOWED_HOST
_LOCAL_ORIGIN = _ALLOWED_ORIGIN
WEB = Path(__file__).resolve().parent / "web"
PORT = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--port=")), 8765))
TF = {"M1": mt5.TIMEFRAME_M1, "M3": mt5.TIMEFRAME_M3, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
      "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}
BRIDGE_STALE = 15
_mt5_lock = threading.Lock()
_analysis = {}          # (symbol, tf, bars) -> (last_closed_time, overlay dict)


def mt5_ready():
    ti = mt5.terminal_info()
    if ti and ti.connected:
        return True
    mt5.shutdown()
    return mt5.initialize() and bool(mt5.terminal_info())


# ------------------------------------------------------------------ chart
def chart_payload(symbol, tf="M5", bars=300):
    if tf not in TF:
        return {"error": f"timeframe {tf} tidak didukung"}
    with _mt5_lock:
        if not mt5_ready():
            return {"error": "MT5 tidak tersambung — buka terminal MT5 & login"}
        mt5.symbol_select(symbol, True)
        r = mt5.copy_rates_from_pos(symbol, TF[tf], 0, bars + 700)
    if r is None or len(r) < bars + 50:
        return {"error": f"data {symbol} {tf} tidak tersedia"}
    full = pd.DataFrame(r)
    cfg = load_config()
    key = (symbol, tf, bars)
    last_closed = int(full.time.iloc[-2])
    hit = _analysis.get(key)
    if hit and hit[0] == last_closed:
        ov = dict(hit[1])                     # objek sama; perbarui candle berjalan saja
        win = full.iloc[-bars:]
        ov["candles"] = {"t": win.time.astype(int).tolist(), "o": win.open.round(3).tolist(),
                         "h": win.high.round(3).tolist(), "l": win.low.round(3).tolist(),
                         "c": win.close.round(3).tolist()}
    else:
        ov = overlay.build(full, tf, bars, cfg.get("smc", {}).get("swing_k", 3))
        _analysis[key] = (last_closed, ov)
    exec_tf = cfg["symbols"].get(symbol, {}).get("timeframe", "M5")
    marks = trade_marks.build(read_json(STATE_PATH), symbol, cfg.get("dashboard", {}).get("closed_marker_minutes", 10))
    for m in marks:
        m["label"] = trade_marks.label(m)
    ov = dict(ov, symbol=symbol, exec_tf=exec_tf, trade_marks=marks,
              # rev berubah kalau candle baru tertutup ATAU posisi/SL berubah -> browser menggambar ulang
              rev=f"{last_closed}-{hash(json.dumps(marks, sort_keys=True))}",
              entries=entries_for(symbol, ov["t_start"], exec_tf))
    st = read_json(STATE_PATH) or {}
    ov["plans"] = [x for x in st.get("plans", []) if x.get("symbol") == symbol]
    try:
        ov["sr_mtf"] = sr_for(symbol, cfg)
    except Exception as e:
        ov["sr_mtf"] = {"zones": [], "refs": [], "error": str(e)}
    try:
        with _mt5_lock:
            ov["news"] = newsimpact.events_for(symbol, news_cfg(cfg), ov["t_start"], time.time() + 86400)
    except Exception as e:
        ov["news"] = []
    return ov


def entries_for(symbol, t_start, exec_tf):
    sec = overlay.TF_SEC[exec_tf]
    out = []
    for e in read_entries(symbol, 200):
        t_entry = int(e.get("bar_time", 0)) + sec          # entry terjadi di candle setelah bar sinyal
        if t_entry >= t_start and e.get("price"):
            out.append(dict(e, t_entry=t_entry))
    return out


def read_entries(symbol=None, n=50):
    p = LOG_DIR / "entries.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines()[-2000:]:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if symbol is None or e.get("symbol") == symbol:
            rows.append(e)
    return rows[-n:]


def read_bursts(n=30):
    if not BURST_LOG_PATH.exists():
        return []
    with open(BURST_LOG_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:][::-1]


def read_json(path):
    for _ in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            time.sleep(0.05)
    return None


# ------------------------------------------------------------------ status bridge MT5
def bridge_status():
    """Engine Python (heartbeat) + tiap indikator MT5 yang terpasang (ea_<SIMBOL>_<TF>.txt)."""
    now = time.time()
    st = read_json(LOG_DIR / "bridge_status.json") or {}
    folder = Path(st["dir"]) if st.get("dir") else None
    if folder is None:
        ti = mt5.terminal_info()
        if ti:
            folder = Path(ti.commondata_path) / "Files" / load_config().get("bridge", {}).get("folder", "XAUUSD_EXNESS")
    hb = None
    try:
        hb = int((folder / "heartbeat.txt").read_text().strip())
    except Exception:
        pass
    engine_ok = hb is not None and now - hb <= BRIDGE_STALE
    charts = []
    if folder and folder.exists():
        for f in sorted(folder.glob("ea_*.txt")):
            try:
                ts, status, n, sym, tf = f.read_text().strip().split(";")[:5]
                age = now - int(ts)
                charts.append({"symbol": sym, "tf": tf, "status": status if age <= BRIDGE_STALE else "indikator tidak aktif",
                               "objects": int(n), "age": round(age)})
            except Exception:
                continue
    return {"engine_ok": engine_ok, "heartbeat_age": None if hb is None else round(now - hb),
            "loop_ms": st.get("loop_ms"), "timeframes": st.get("timeframes"), "charts": charts}


# ------------------------------------------------------------------ akun MT5 (langsung dari terminal)
OTHER_MAGICS = {234601: "Exness_Bot", 0: "Manual"}


def mt5_account():
    """Data persis seperti di terminal MT5: akun, SEMUA posisi (diberi label pemilik per magic),
    deal yang tertutup hari ini (hari server = UTC), dan cek sinkron posisi bot vs MT5."""
    from datetime import datetime, timezone, timedelta
    cfg = load_config()
    my_magic = cfg["magic"]
    names = {**OTHER_MAGICS, my_magic: "XAUUSD_EXNESS"}
    owner = lambda m: names.get(m, f"magic {m}")          # noqa: E731
    day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with _mt5_lock:
        if not mt5_ready():
            return {"error": "MT5 tidak tersambung"}
        acc = mt5.account_info()
        pos = mt5.positions_get() or []
        deals = mt5.history_deals_get(day0, datetime.now(timezone.utc) + timedelta(days=1)) or []
    positions = [{"ticket": p.ticket, "symbol": p.symbol, "dir": "BUY" if p.type == 0 else "SELL",
                  "volume": p.volume, "open": p.price_open, "current": p.price_current, "sl": p.sl, "tp": p.tp,
                  "profit": round(p.profit + p.swap, 2), "magic": p.magic, "owner": owner(p.magic),
                  "age_min": round((time.time() - p.time) / 60, 1), "comment": p.comment} for p in pos]
    # deal penutupan hari ini (entry OUT/INOUT) + komisi pembukaannya -> P/L riil per pemilik
    per_pos = {}
    for d in deals:
        if d.type not in (0, 1):                               # hanya buy/sell (bukan balance/credit)
            continue
        r = per_pos.setdefault(d.position_id, {"pnl": 0.0, "closed": False, "magic": d.magic, "symbol": d.symbol})
        r["pnl"] += d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
        if d.entry in (1, 3):
            r["closed"] = True
            r["magic"] = r["magic"] or d.magic
    summary = {}
    for r in per_pos.values():
        if not r["closed"]:
            continue
        s = summary.setdefault(owner(r["magic"]), {"trades": 0, "wins": 0, "pnl": 0.0})
        s["trades"] += 1
        s["wins"] += r["pnl"] > 0
        s["pnl"] = round(s["pnl"] + r["pnl"], 2)
    # sinkron: posisi yang dikelola bot (state.json) vs posisi magic bot di MT5
    st = read_json(STATE_PATH) or {}
    mine_mt5 = sorted(p["ticket"] for p in positions if p["magic"] == my_magic)
    mine_bot = sorted(p["ticket"] for p in st.get("positions", []))
    if st.get("mode") == "paper":
        sync = {"ok": None, "text": "mode PAPER: posisi bot virtual, memang tidak ada di MT5"}
    elif mine_mt5 == mine_bot:
        sync = {"ok": True, "text": f"sinkron — {len(mine_mt5)} posisi bot sama persis dengan MT5"}
    else:
        sync = {"ok": False, "text": f"BEDA — bot: {mine_bot} · MT5: {mine_mt5} (bot memperbarui tiap 1 dtk; "
                                     "kalau menetap, cek log)"}
    return {"account": {"login": acc.login, "server": acc.server, "name": acc.name, "currency": acc.currency,
                        "type": {0: "", 1: "CONTEST", 2: "REAL"}.get(acc.trade_mode, ""),
                        "balance": acc.balance, "equity": acc.equity, "profit": acc.profit, "margin": acc.margin,
                        "margin_free": acc.margin_free, "margin_level": acc.margin_level, "leverage": acc.leverage},
            "positions": positions, "today": summary, "sync": sync, "bot_magic": my_magic,
            "day": day0.date().isoformat()}


# ------------------------------------------------------------------ pemantau kesehatan (thread terpisah)
BOT_DEAD_SEC = 30
HEALTH_PATH = LOG_DIR / "health.log"
HEALTH = {}                 # nama -> {"ok": bool|None, "since": epoch, "detail": str}
_health_lock = threading.Lock()


def _health_log(line):
    with open(HEALTH_PATH, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")


def _set_health(name, ok, detail):
    now = time.time()
    with _health_lock:
        prev = HEALTH.get(name)
        if prev is None or prev["ok"] != ok:
            if prev is not None and prev["ok"] is not None and ok is not None:
                dur = now - prev["since"]
                _health_log(f"{name.upper():<6} {'PULIH' if ok else 'PUTUS'}  ({detail})  "
                            f"setelah {int(dur // 60)}m{int(dur % 60):02d}s {'terputus' if ok else 'normal'}")
            elif prev is None:
                _health_log(f"{name.upper():<6} awal: {'OK' if ok else ('tidak dipakai' if ok is None else 'PUTUS')}  ({detail})")
            HEALTH[name] = {"ok": ok, "since": now, "detail": detail}
        else:
            prev["detail"] = detail


def health_check_once():
    """Cek langsung — TIDAK mengandalkan status yang dilaporkan bot."""
    # MT5: koneksi terminal <-> server broker, dilihat dari proses dashboard sendiri
    with _mt5_lock:
        ok = mt5_ready()
        ti = mt5.terminal_info() if ok else None
        acc = mt5.account_info() if ok else None
    if ti and ti.connected and acc:
        _set_health("mt5", True, f"akun {acc.login} {acc.server}, ping {ti.ping_last / 1000:.0f} ms")
        _set_health("algo", bool(ti.trade_allowed), "Algo Trading " + ("ON" if ti.trade_allowed else "OFF"))
    else:
        err = mt5.last_error()
        _set_health("mt5", False, f"terminal={'ada' if ti else 'tidak ada'}, server={'ya' if ti and ti.connected else 'tidak'}, "
                                  f"last_error={err}")
    # bot: umur state.json
    st = read_json(STATE_PATH) or {}
    age = time.time() - st.get("ts", 0)
    _set_health("bot", age < BOT_DEAD_SEC, f"state {age:.0f} dtk lalu" if st.get("ts") else "belum pernah jalan")
    # bridge: hanya dinilai kalau setup INI memakai bridge (engine menulis bridge_status.json ke LOG_DIR ini)
    if (LOG_DIR / "bridge_status.json").exists():
        b = bridge_status()
        _set_health("bridge", bool(b["engine_ok"]), f"heartbeat {b['heartbeat_age']} dtk lalu"
                    if b["heartbeat_age"] is not None else "heartbeat tidak ada")
    else:
        _set_health("bridge", None, "setup ini tidak memakai bridge gambar MT5")


def _health_loop():
    while True:
        try:
            health_check_once()
        except Exception as e:
            _health_log(f"ERROR pemantau: {e}")
        time.sleep(5)


def health_snapshot():
    cfg = load_config()
    lim = cfg.get("dashboard", {}).get("unmonitored_after_min", 2) * 60
    now = time.time()
    with _health_lock:
        h = {k: dict(v, age=now - v["since"]) for k, v in HEALTH.items()}
    down = [k for k in ("mt5", "bot") if h.get(k, {}).get("ok") is False]
    since = min((h[k]["since"] for k in down), default=None)
    return {"items": h, "monitored": not (down and now - since > lim), "down": down,
            "unmonitored_since": since if down and now - since > lim else None,
            "unmonitored_after_min": lim / 60}


def dash_name():
    """Nama bot di pemilih dashboard: config dashboard.name, kalau tidak ada ditebak dari magic."""
    cfg = load_config()
    return cfg.get("dashboard", {}).get("name") or {260950: "Eksperimen 50"}.get(cfg.get("magic"), "Bot utama")


def read_health_log(n=12):
    if not HEALTH_PATH.exists():
        return []
    return HEALTH_PATH.read_text(encoding="utf-8").splitlines()[-n:][::-1]


# ------------------------------------------------------------------ status + peringatan


def full_state():
    """state.json bot + hal yang hanya bisa dilihat dari LUAR bot: bot mati (state basi), flag
    EMERGENCY_STOP, status tombol burst seketika, dan status bridge."""
    s = read_json(STATE_PATH) or {}
    s["bridge"] = bridge_status()
    s["health"] = health_snapshot()
    alerts = list(s.get("alerts", []))
    age = time.time() - s.get("ts", 0)
    s["bot_running"] = bool(s) and age < BOT_DEAD_SEC
    if not s.get("ts"):
        alerts.insert(0, ["critical", "BOT BELUM PERNAH JALAN — jalankan start_bot.cmd all"])
    elif not s["bot_running"]:
        since = time.strftime("%d %b %H:%M:%S", time.localtime(s["ts"]))
        alerts = [["critical", f"BOT TIDAK BERJALAN sejak {since} ({age / 60:.0f} menit) — tidak ada entry "
                               "maupun trailing. Jalankan start_bot.cmd all dari terminalmu (bukan dari sesi lain), "
                               "atau pasang Scheduled Task watchdog (start_bot.cmd task)."]]
    if FLAG_STOP.exists():
        alerts.insert(0, ["critical", "EMERGENCY STOP aktif — bot menutup posisi & menolak start sampai "
                                      "start_bot.cmd reset"])
    if FLAG_HALT.exists() and not any("HALT" in a[1] for a in alerts):
        alerts.insert(0, ["critical", "HALT max drawdown aktif — reset: start_bot.cmd reset"])
    H = s["health"]
    hb = H["items"].get("bridge", {})
    s["bridge"]["expected"] = hb.get("ok") is not None
    if hb.get("ok") is False:
        alerts.append(["error", f"Bridge MT5 PUTUS sejak {time.strftime('%H:%M:%S', time.localtime(hb['since']))} — "
                                "gambar di chart MT5 dibekukan; trading bot TIDAK terpengaruh (start_bot.cmd bridge)"])
    hm = H["items"].get("mt5", {})
    if hm.get("ok") is False:
        alerts.insert(0, ["critical", f"MT5 TERPUTUS sejak {time.strftime('%H:%M:%S', time.localtime(hm['since']))} "
                                      f"({hm.get('detail')}) — auto-reconnect berjalan"])
    if not H["monitored"]:
        alerts.insert(0, ["critical", "PASAR TIDAK DIPANTAU sejak "
                          f"{time.strftime('%H:%M:%S', time.localtime(H['unmonitored_since']))} "
                          f"({', '.join(H['down'])} putus > {H['unmonitored_after_min']:.0f} menit) — "
                          "data di layar BASI, tidak ada entry maupun trailing"])
    s["alerts"] = alerts
    b = s.setdefault("burst", {})
    if FLAG_BURST_OFF.exists():                          # tombol berlaku seketika di dashboard
        b.update(active=False, reason="dimatikan manual (tombol)")
    elif str(b.get("reason", "")).startswith("dimatikan manual"):
        # tombol baru saja menyalakan lagi; state.json bot belum sempat diperbarui (<= 2 dtk)
        b.update(active=True, reason="")
    if not s["bot_running"]:
        b.update(active=False, reason="bot tidak berjalan")
    s["burst_now_pending"] = FLAG_BURST_NOW.exists()
    # switch per simbol berlaku seketika di layar (bot membaca file yang sama <= 1 dtk kemudian)
    act = read_active(load_config())
    for sym, v in s.get("symbols", {}).items():
        if sym in act:
            v["active"] = act[sym]
    return s


def evaluation():
    """Metrik per profil & per kombinasi skill dari logs/trades_meta.csv (mode akun saat ini)."""
    p = LOG_DIR / "trades_meta.csv"
    if not p.exists():
        return {"profile": [], "skills": []}
    j = pd.read_csv(p)
    mode = (read_json(STATE_PATH) or {}).get("mode")
    j = j[j["mode"] == mode] if mode else j

    def agg(key):
        out = []
        for k, x in j.groupby(key):
            cum = x.pnl.cumsum()
            w, lo = x.pnl[x.pnl > 0].sum(), -x.pnl[x.pnl <= 0].sum()
            out.append({"key": k, "trades": int(len(x)), "win_rate": round((x.pnl > 0).mean() * 100, 1),
                        "pf": round(w / lo, 2) if lo else None, "pnl": round(float(x.pnl.sum()), 2),
                        "max_dd": round(float((cum.cummax().clip(lower=0) - cum).max()), 2),
                        "avg_min": round(float(x.duration_min.mean()), 1),
                        "since": str(x.close_time_utc.min())[:16]})
        return sorted(out, key=lambda r: -r["trades"])
    if "entry" in j.columns:            # profil x jenis entri (bar/intrabar/manual/?) — jangan campur manual ke profil
        j = j.assign(profile_entry=j["profile"] + " · " + j["entry"].fillna("?"))
        return {"profile": agg("profile_entry"), "skills": agg("skills")}
    return {"profile": agg("profile"), "skills": agg("skills")}


def read_audit(n=15):
    if not CONTROL_AUDIT.exists():
        return []
    return CONTROL_AUDIT.read_text(encoding="utf-8").splitlines()[-n:][::-1]


# ------------------------------------------------------------------ HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cors=False):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if cors and _LOCAL_ORIGIN.match(origin):   # hanya /api/ping & hanya dari dashboard lokal (bukan '*')
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, post=False):
        """Pertahanan CSRF & DNS-rebinding (audit 29 Sep: POST lintas-origin dulu diterima HTTP 200).
        - Host harus 127.0.0.1/localhost/[::1] (tolak DNS-rebinding)
        - Origin (kalau ada) harus origin lokal
        - POST wajib Content-Type: application/json -> browser memaksa preflight untuk lintas-origin,
          dan server ini tidak menjawab OPTIONS, jadi halaman web lain tidak bisa mengirim perintah."""
        host = self.headers.get("Host", "")
        if not _LOCAL_HOST.match(host):
            self._send(403, {"error": "Host tidak diizinkan"}); return False
        origin = self.headers.get("Origin")
        if origin and not _LOCAL_ORIGIN.match(origin):
            self._send(403, {"error": "Origin tidak diizinkan"}); return False
        if post and not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._send(415, {"error": "Content-Type harus application/json"}); return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._send(200, full_state())
            if u.path == "/api/chart":
                return self._send(200, chart_payload(q.get("symbol", "XAUUSD"), q.get("tf", "M5"), int(q.get("bars", 300))))
            if u.path == "/api/entries":
                return self._send(200, read_entries(q.get("symbol"), int(q.get("n", 30)))[::-1])
            if u.path == "/api/health":
                return self._send(200, {"snapshot": health_snapshot(), "log": read_health_log(int(q.get("n", 12)))})
            if u.path == "/api/news":
                cfg = load_config()
                sym = q.get("symbol", "XAUUSD")
                nc = news_cfg(cfg)
                with _mt5_lock:
                    ev = newsimpact.events_for(sym, nc, time.time() - 86400, time.time() + 86400)
                    stt = newsimpact.stats([sym], nc).get("by", {}).get(sym, {})
                    bo = newsimpact.blackout(sym, nc)
                hd = newsimpact.headlines(nc)
                return self._send(200, {"events": ev, "stats": stt, "blackout": bo[:2],
                                        "sources": {"kalender": cfg.get("news_filter", {}).get("url"), "rss": nc.get("rss"),
                                                    "rss_error": hd.get("errors"), "headline_cache": len(hd.get("items", {}))}})
            if u.path == "/api/bars":
                t0, t1 = q.get("t0"), q.get("t1")
                return self._send(200, datastore.window(q.get("symbol", "XAUUSD"), q.get("tf", "M5"), t0 and int(t0),
                                                        t1 and int(t1), int(q.get("max", 2000))))
            if u.path == "/api/datastatus":
                return self._send(200, SYNC)
            if u.path == "/api/stream":
                return self.stream(q.get("symbol", "XAUUSD"), q.get("tf", "M5"))
            if u.path == "/api/ack":
                cid = q.get("id", "")
                a = prof.read_ack(cid) if re.fullmatch(r"\d{10,16}_[a-z]+", cid) else {"error": "id tidak valid"}
                return self._send(200, a or {"pending": True})
            if u.path == "/api/evaluation":
                return self._send(200, evaluation())
            if u.path == "/api/audit":
                return self._send(200, read_audit(int(q.get("n", 15))))
            if u.path == "/api/mt5":
                return self._send(200, mt5_account())
            if u.path == "/api/bursts":
                return self._send(200, read_bursts(int(q.get("n", 30))))
            if u.path == "/api/learning":
                return self._send(200, read_json(LOG_DIR / "learning_report.json") or {})
            if u.path == "/api/ping":
                st = read_json(STATE_PATH) or {}
                return self._send(200, {"port": PORT, "name": dash_name(), "mode": st.get("mode"),
                                        "running": time.time() - st.get("ts", 0) < BOT_DEAD_SEC,
                                        "positions": len(st.get("positions", [])),
                                        "day_pnl": (st.get("day") or {}).get("pnl")}, cors=True)
            if u.path == "/api/config":
                cfg = load_config()
                with _mt5_lock:           # chart bisa dibuka untuk SEMUA simbol config yang ada di akun
                    ready = mt5_ready()
                    syms = [s for s in cfg["symbols"] if ready and mt5.symbol_select(s, True) and mt5.symbol_info(s)]
                return self._send(200, {"name": dash_name(), "port": PORT,
                                        "peers": cfg.get("dashboard", {}).get("peers", [8765, 8766]),
                                        "symbols": syms, "all_symbols": list(cfg["symbols"]), "chart_tfs": list(TF),
                                        "exec_tf": {s: cfg["symbols"][s].get("timeframe", "M5") for s in cfg["symbols"]},
                                        "smc_mode": cfg.get("smc", {}).get("filter_mode"),
                                        "learning_mode": cfg.get("learning", {}).get("mode")})
            return self._send(404, {"error": "tidak ada"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def stream(self, symbol, tf):
        """SSE: candle berjalan + tick, maks 1x/detik (throttle). Histori TIDAK dikirim lewat sini."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = None
        try:
            for _ in range(3600):                       # sambungan diperbarui browser tiap <= 1 jam
                with _mt5_lock:
                    r = mt5.copy_rates_from_pos(symbol, TF.get(tf, mt5.TIMEFRAME_M5), 0, 1)
                    t = mt5.symbol_info_tick(symbol)
                if r is not None and len(r):
                    b = r[-1]
                    msg = {"t": int(b["time"]), "o": float(b["open"]), "h": float(b["high"]), "l": float(b["low"]),
                           "c": float(b["close"]), "bid": t.bid if t else None, "ts": time.time()}
                    if msg != last:
                        self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
                        self.wfile.flush()
                        last = msg
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        if not self._guard(post=True):
            return
        if urlparse(self.path).path != "/api/control":
            return self._send(404, {"error": "tidak ada"})
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        act = body.get("action")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if act in ("profile", "skill", "override"):
            # alur: perintah -> bot memvalidasi & menerapkan di siklus berikut -> ACK (dibaca lewat /api/ack)
            st = full_state()
            if not st.get("bot_running"):
                return self._send(200, {"ok": False, "error": "bot tidak berjalan — perintah tidak bisa dikonfirmasi"})
            if st["health"]["items"].get("mt5", {}).get("ok") is False:
                return self._send(200, {"ok": False, "error": "MT5/bridge TERPUTUS — perubahan ditolak"})
            kw = ({"profile": body.get("profile"), "symbol": body.get("symbol") or None} if act == "profile"
                  else {"symbol": body.get("symbol"), "mode": body.get("mode"), "hours": body.get("hours"),
                        "available": [k for k, v in st.get("symbols", {}).items() if v.get("available")]} if act == "override"
                  else {"skill": body.get("skill"), "on": bool(body.get("on"))})
            return self._send(200, {"ok": True, "id": prof.send_command(act, "dashboard", **kw)})
        if act == "symbol":
            # switch per simbol: OFF = tidak ada entry baru di simbol ini; posisinya tetap dikelola SL/trailing
            cfg = load_config()
            sym, on = body.get("symbol"), bool(body.get("on"))
            if sym not in cfg["symbols"]:
                return self._send(400, {"error": f"simbol {sym} tidak ada di config"})
            a = read_active(cfg)
            a[sym] = on
            write_active(a)
            audit(f"Simbol {sym} {'ON' if on else 'OFF'}", "web")
            return self._send(200, {"ok": True, "action": act, "state": full_state()})
        if act == "pause":
            FLAG_PAUSE.write_text(now)
        elif act == "resume":
            FLAG_PAUSE.unlink(missing_ok=True)
        elif act == "emergency":
            FLAG_STOP.write_text(now)
        elif act == "burst_on":
            # nyalakan lagi + minta evaluasi sinyal SEGERA; semua rem risiko tetap diperiksa bot
            FLAG_BURST_OFF.unlink(missing_ok=True)
            FLAG_BURST_NOW.write_text(now)
        elif act == "burst_off":
            # stop entry baru; posisi terbuka TETAP dikelola (SL/trailing), tidak ditutup paksa
            FLAG_BURST_OFF.write_text(now)
            FLAG_BURST_NOW.unlink(missing_ok=True)
        else:
            return self._send(400, {"error": "aksi tidak dikenal"})
        audit({"burst_on": "Aktifkan Burst Sekarang", "burst_off": "Matikan Burst", "pause": "Pause",
               "resume": "Resume", "emergency": "EMERGENCY STOP"}[act], "web")
        return self._send(200, {"ok": True, "action": act, "state": full_state()})


def main():
    HOST = next((a.split("=")[1] for a in sys.argv if a.startswith("--host=")), "0.0.0.0")
    srv = ThreadingHTTPServer((HOST, PORT), H)
    threading.Thread(target=_health_loop, daemon=True, name="health").start()
    threading.Thread(target=_sync_loop, daemon=True, name="datasync").start()
    url = f"http://127.0.0.1:{PORT}"
    lan_url = f"http://192.168.1.83:{PORT}"
    print(f"Dashboard web aktif di {HOST}:{PORT}")
    print(f"  • Akses Lokal   : {url}")
    print(f"  • Akses Jaringan: {lan_url} (dapat diakses dari 10.5.99.15)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
