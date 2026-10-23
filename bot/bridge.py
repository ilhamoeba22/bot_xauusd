"""
BRIDGE Python -> MT5 (auto-draw di chart MT5).

    python -m bot.bridge          (atau start_bot.ps1 -Action Bridge / start_bot.cmd bridge)

Proses kontinu yang tiap `interval_sec` detik:
  1. menulis heartbeat  <Common>\\Files\\XAUUSD_EXNESS\\heartbeat.txt  (epoch UTC)
  2. untuk tiap simbol x timeframe: kalau ada candle baru tertutup / entry bot baru,
     menghitung ulang objek (bot/analysis/overlay.py — sama dengan dashboard web) dan menulis
     <Common>\\Files\\XAUUSD_EXNESS\\bridge_<SIMBOL>_<TF>.csv
Indikator mql5\\XAUUSD_Bridge_Draw.mq5 membaca file itu dengan FileOpen(..., FILE_COMMON)
— folder Common bisa dibaca MQL5 tanpa DLL. Kalau proses ini mati, heartbeat berhenti dan
indikator menampilkan "Bridge disconnected" serta berhenti mengubah objek.

Format file (ASCII, pemisah ';', satu objek per baris):
  HDR;1;<simbol>;<tf>;<epoch_dibuat>;<jumlah_objek>
  OBJ;<id>;<TREND|RECT|ARROW|TEXT>;<t1>;<p1>;<t2>;<p2>;<R,G,B>;<style>;<width>;<fill/ray/anchor>;<teks>;<tooltip>
  (ARROW: t2 = kode panah Wingdings; TEXT: t2/p2 tidak dipakai, fill = 1 label di atas harga / 0 di bawah)
"""
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import MetaTrader5 as mt5

from .common import LOG_DIR, STATE_PATH, load_config, setup_logging, write_json_atomic, read_active
from . import profiles as prof
from .analysis import sr_mtf, newsimpact
from . import skills as sk
from .broker import Connection
from .analysis import overlay, trade_marks

log = setup_logging("bridge")
TFC = {"M1": mt5.TIMEFRAME_M1, "M3": mt5.TIMEFRAME_M3, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
       "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}
STATUS_PATH = LOG_DIR / "bridge_status.json"
LOCK = LOG_DIR / "bridge.lock"

# warna (R,G,B) — sama dengan dashboard web
COL = {"up": "38,166,154", "down": "239,83,80", "gold": "227,179,65", "blue": "79,140,255", "purple": "179,127,235",
       "teal": "46,196,182", "orange": "240,140,58", "gray": "154,164,177", "cyan": "63,193,217", "pink": "227,107,168",
       "ob_up": "20,70,62", "ob_down": "90,32,32", "fvg_up": "18,70,68", "fvg_down": "88,55,22", "white": "255,255,255",
       "closed": "120,128,140"}
SOLID, DASH, DOT, DASHDOT = 0, 1, 2, 3
ASCII = str.maketrans({"≈": "~", "—": "-", "–": "-", "≥": ">=", "≤": "<=", "×": "x", "→": "->", "↑": "^", "↓": "v",
                       "’": "'", "‘": "'", "“": '"', "”": '"', ";": ",", "\n": " "})


def common_dir(cfg=None):
    """<Terminal Common>\\Files\\<folder> — lokasi yang bisa dibaca MQL5 dengan FILE_COMMON."""
    ti = mt5.terminal_info()
    base = Path(ti.commondata_path) if ti else Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common"
    d = base / "Files" / (cfg or load_config()).get("bridge", {}).get("folder", "XAUUSD_EXNESS")
    d.mkdir(parents=True, exist_ok=True)
    return d


def clean(s):
    return str(s).translate(ASCII).encode("ascii", "replace").decode("ascii")[:250]


def obj(id_, typ, t1, p1, t2=0, p2=0.0, col="white", style=SOLID, width=1, flag=0, text="", tip=""):
    return (f"OBJ;{clean(id_)};{typ};{int(t1)};{float(p1):.3f};{int(t2)};{float(p2):.3f};{COL.get(col, col)};"
            f"{style};{width};{int(flag)};{clean(text)};{clean(tip)}")


TF_STYLE = {"W1": SOLID, "D1": DASH, "H4": DOT, "H1": DASHDOT}
TIER_COL = {"High": "down", "Medium": "255,190,60", "Low": "closed"}


def entry_lines(plans, t_now):
    """Titik entry BUY/SELL saja — satu garis + label, tanpa SL/TP/zona (layer 'entryline').
    Semua market ditampilkan, termasuk DITOLAK (abu-abu putus-titik), supaya tiap simbol selalu
    punya titik referensi, bukan hanya simbol yang kebetulan sedang punya posisi terbuka."""
    L = []
    for pl in plans:
        if pl["status"] == "SELESAI":
            continue
        buy, rej = pl["dir"] == "BUY", pl["status"] == "DITOLAK"
        col = "closed" if rej else ("up" if buy else "down")
        pid = "EP_" + pl["key"].replace("@", "_")
        tip = f"Titik entry {pl['status']} {pl['dir']} @ {pl['entry']}" + (f" — {pl['reason']}" if rej else "")
        L.append(obj(pid + "_e", "TREND", pl["t"], pl["entry"], t_now, pl["entry"], col,
                     DASH if pl["status"] == "MENUNGGU" else DOT if rej else SOLID, 1 if rej else 2, 0, tip=tip))
        mark = "x" if rej else ("^" if buy else "v")
        L.append(obj(pid + "_t", "TEXT", t_now, pl["entry"], 0, 0, col, flag=1,
                     text=f"{mark} ENTRY {pl['dir']} {pl['entry']}" + (" (ditolak)" if rej else ""), tip=tip))
    return L


def plan_lines(plans, t_now, show_rejected=True):
    """Rencana posisi — sama persis dengan dashboard (state.json 'plans'). ID unik per rencana."""
    L = []
    for pl in plans:
        st = pl["status"]
        if st == "DITOLAK" and not show_rejected:
            continue
        buy = pl["dir"] == "BUY"
        col = "closed" if st in ("DITOLAK", "SELESAI") else ("up" if buy else "down")
        sty = DASH if st == "MENUNGGU" else DOT if st in ("DITOLAK", "SELESAI") else SOLID
        pid = "PL_" + pl["key"].replace("@", "_")
        t0, sl = pl["t"], pl.get("sl_now") or pl["sl"]
        tag = f"{st} {pl['dir']}" + (f" - {pl['reason']}" if st == "DITOLAK" else "")
        tip = (f"{tag} | entry {pl['entry']} SL {sl} ({pl['sl_atr']} ATR) TP1 {pl['tp1']} (1:{pl['rr1']}) TP2 {pl['tp2']} (1:{pl['rr2']})"
               f" | invalidasi {pl['invalid']} | skor {pl.get('score')} | label bukan janji arah harga")
        L.append(obj(pid + "_e", "TREND", t0, pl["entry"], t_now, pl["entry"], col, sty, 2 if st == "AKTIF" else 1, 0, tip=tip))
        if st not in ("DITOLAK",):
            L.append(obj(pid + "_rz", "RECT", t0, pl["entry"], t_now, sl, "ob_down", tip="zona risiko " + tip))
            L.append(obj(pid + "_wz", "RECT", t0, pl["entry"], t_now, pl["tp2"], "ob_up", tip="zona reward " + tip))
        for k, v, c in (("sl", sl, "down"), ("tp1", pl["tp1"], "up"), ("tp2", pl["tp2"], "up"), ("inv", pl["invalid"], "closed")):
            L.append(obj(f"{pid}_{k}", "TREND", t0, v, t_now, v, "closed" if st == "DITOLAK" else c, sty, 1, 0, tip=tip))
        L.append(obj(pid + "_t", "TEXT", t_now, pl["entry"], 0, 0, col, flag=1,
                     text=f"{tag[:60]} | SL {sl} TP1 {pl['tp1']} 1:{pl['rr1']}", tip=tip))
    return L


def sr_lines(sr, t_start, t_now, lay):
    L = []
    if lay.get("srmtf", True):
        for i, z in enumerate(sr.get("zones", [])):
            w = 1 + int(z["strength"] * 3)
            L.append(obj(f"SRM_{i}", "TREND", t_start, z["price"], t_now, z["price"], "up" if z["kind"] == "S" else "down",
                         TF_STYLE.get(z["top_tf"], DOT), w, 0, tip=f"{z['label']} zona {z['lo']}-{z['hi']} kekuatan {z['strength']}"))
            L.append(obj(f"SRM_{i}_t", "TEXT", t_start, z["price"], 0, 0, "up" if z["kind"] == "S" else "down", text=z["label"]))
    if lay.get("refs", True):
        for i, r in enumerate(sr.get("refs", [])):
            L.append(obj(f"REF_{i}", "TREND", t_start, r["price"], t_now, r["price"], "closed", DOT, 1, 0, tip=f"{r['label']} {r['price']}"))
            L.append(obj(f"REF_{i}_t", "TEXT", t_start, r["price"], 0, 0, "closed", text=r["label"]))
    return L


def news_lines(events, lo, hi):
    L = []
    for e in events:
        col = TIER_COL.get(e["tier"], "closed")
        rea = e.get("reaction") or {}
        tip = (f"{e['title']} | {e['source']} | tier {e['tier']} | terdampak {','.join(e['affected'])}"
               + (f" | reaksi ATR +5 {rea.get('+5')} +15 {rea.get('+15')} +60 {rea.get('+60')}" if rea else ""))
        nid = f"NEWS_{e['t']}_{abs(hash(e['title'])) % 10000}"
        L.append(obj(nid, "TREND", e["t"], lo, e["t"], hi, col, DOT, 1, 0, tip=tip))
        L.append(obj(nid + "_t", "TEXT", e["t"], hi, 0, 0, col, text=f"{e['icon']} {e['title'][:28]}", tip=tip))
    return L


def mark_lines(marks, t_now):
    """Posisi bot: panah entry, garis SL (merah) & TP (hijau) putus-putus dari titik entry, label.
    Posisi yang baru ditutup: abu-abu dengan garis entry -> exit; hilang setelah masa tampil habis
    (tidak ada lagi di file -> indikator menghapusnya lewat cleanup berbasis ID)."""
    L = []
    for m in marks:
        buy = m["dir"] == "BUY"
        lab = trade_marks.label(m)
        if m["status"] == "open_agg":
            # SATU garis ringkas untuk semua posisi searah (lintas-burst): entry rata2, pita SL/TP,
            # label tunggal. Tiap tiket individu hanya segitiga kecil TANPA teks di titik entrynya.
            L.append(obj(m["id"] + "_e", "TREND", m["time"], m["entry"], t_now, m["entry"], "up" if buy else "down",
                         SOLID, 2, 0, tip=lab))
            if m["sl_lo"]:
                L.append(obj(m["id"] + "_slband", "RECT", m["time"], m["sl_lo"], t_now, m["sl_hi"] or m["sl_lo"],
                             "ob_down", tip=f"pita SL {m['sl_lo']}-{m['sl_hi']}  ({lab})"))
                L.append(obj(m["id"] + "_slt", "TEXT", t_now, m["sl_hi"] or m["sl_lo"], 0, 0, "down", flag=1,
                             text=f"SL {m['sl_lo']}" + (f"-{m['sl_hi']}" if m["sl_hi"] != m["sl_lo"] else ""), tip=lab))
            if m["tp_lo"]:
                L.append(obj(m["id"] + "_tpband", "RECT", m["time"], m["tp_lo"], t_now, m["tp_hi"] or m["tp_lo"],
                             "ob_up", tip=f"pita TP {m['tp_lo']}-{m['tp_hi']}  ({lab})"))
                L.append(obj(m["id"] + "_tpt", "TEXT", t_now, m["tp_lo"], 0, 0, "up", flag=1,
                             text=f"TP {m['tp_lo']}" + (f"-{m['tp_hi']}" if m["tp_hi"] != m["tp_lo"] else ""), tip=lab))
            L.append(obj(m["id"] + "_t", "TEXT", t_now, m["entry"], 0, 0, "up" if buy else "down", flag=1, text=lab, tip=lab))
            for i, b in enumerate(m.get("bursts", [])):        # tiket individu: segitiga kecil, tanpa teks
                btip = f"burst {b['count']}x {b['volume']} lot @ {b['open']}  SL {b['sl']}  P/L {b['pnl']:+,.0f}"
                L.append(obj(f"{m['id']}_b{i}", "ARROW", b["time"], b["open"], 233 if buy else 234, 0,
                             "up" if buy else "down", width=1, flag=0 if buy else 1, tip=btip))
        else:
            L.append(obj(m["id"] + "_e", "ARROW", m["time"], m["open"], 233 if buy else 234, 0, "closed",
                         width=2, flag=0 if buy else 1, tip=lab))
            L.append(obj(m["id"] + "_x", "TREND", m["time"], m["open"], m["close_time"], m["close"], "closed", DOT,
                         1, 0, tip=lab))
            L.append(obj(m["id"] + "_t", "TEXT", m["close_time"], m["close"], 0, 0, "closed", flag=1, text=lab,
                         tip=lab))
    return L


def to_lines(ov, entries, lay, max_cdl, marks=()):
    """overlay.build(...) -> baris OBJ untuk indikator MQL5."""
    L, sec, t_now = [], ov["tf_sec"], ov["t_now"]
    if lay.get("trades", True):
        L += mark_lines(marks, t_now)
    if lay.get("candle", True):
        pats = [p for p in ov["candle_patterns"] if lay.get("doji", False) or p["key"] != "doji"][-max_cdl:]
        for p in pats:
            up = p["dir"] >= 0
            code = 233 if p["dir"] > 0 else 234 if p["dir"] < 0 else 159
            col = "up" if p["dir"] > 0 else "down" if p["dir"] < 0 else "gray"
            y = p["low"] if up else p["high"]
            tip = f"{p['name']}: {p['desc']}"
            L.append(obj(p["id"], "ARROW", p["time"], y, code, 0, col, flag=0 if up else 1, tip=tip))
            gap = (p["high"] - p["low"]) * 0.9 or 0.1                 # label sedikit menjauhi panah
            L.append(obj(p["id"] + "_t", "TEXT", p["time"], y - gap if up else y + gap, 0, 0, col,
                         flag=0 if up else 1, text=p["short"], tip=tip))
    S = ov["structure"]
    if lay.get("sr", True):
        for lv in S["levels"][:4]:
            col = "up" if lv["kind"] == "support" else "down"
            L.append(obj(lv["id"], "TREND", lv["t0"], lv["price"], t_now, lv["price"], col, DOT, 1, 1, tip=lv["desc"]))
            L.append(obj(lv["id"] + "_t", "TEXT", t_now, lv["price"], 0, 0, col, flag=1, text=lv["label"], tip=lv["desc"]))
    if lay.get("trend", True):
        for t in S["trendlines"]:
            if t["status"] == "batal":
                continue
            col = "cyan" if t["kind"] == "trend_up" else "pink"
            L.append(obj(t["id"], "TREND", t["t0"], t["y0"], t["t1"], t["y1"], col, SOLID, 2, 1, tip=t["desc"]))
            L.append(obj(t["id"] + "_t", "TEXT", t["t1"], t["y1"], 0, 0, col, flag=1, text=t["label"], tip=t["desc"]))
    if lay.get("chart", True):
        for p in S["patterns"]:
            if not overlay.is_live(p["status"]):
                continue
            lab = f"{p['label']} ({p['status']})"
            if "points" in p:
                pts = p["points"]
                for j in range(len(pts) - 1):
                    L.append(obj(f"{p['id']}_s{j}", "TREND", pts[j][0], pts[j][1], pts[j + 1][0], pts[j + 1][1],
                                 "pink", SOLID, 2, 0, tip=p["desc"]))
                top = max(pts, key=lambda q: q[1])
                L.append(obj(p["id"] + "_t", "TEXT", top[0], top[1], 0, 0, "pink", flag=1, text=lab, tip=p["desc"]))
            if "neckline" in p:
                a, b = p["neckline"]
                L.append(obj(p["id"] + "_n", "TREND", a[0], a[1], b[0], b[1], "pink", DASH, 1, 0, tip="neckline"))
            if "lines" in p:
                for j, (a, b) in enumerate(p["lines"]):
                    L.append(obj(f"{p['id']}_l{j}", "TREND", a[0], a[1], b[0], b[1], "pink", SOLID, 2, 0, tip=p["desc"]))
                a = p["lines"][0][0]
                L.append(obj(p["id"] + "_t", "TEXT", a[0], a[1], 0, 0, "pink", flag=1, text=lab, tip=p["desc"]))
            if "box" in p:
                b = p["box"]
                L.append(obj(p["id"] + "_b", "RECT", b[0], b[1], b[2], b[3], "pink", DOT, 1, 0, tip=p["desc"]))
                a, c = p["pole"]
                L.append(obj(p["id"] + "_p", "TREND", a[0], a[1], c[0], c[1], "pink", SOLID, 2, 0, tip=p["desc"]))
                L.append(obj(p["id"] + "_t", "TEXT", b[0], b[3], 0, 0, "pink", flag=1, text=lab, tip=p["desc"]))
    M = ov["smc"]
    if lay.get("ob", True):
        for b in [x for x in M["order_blocks"] if overlay.is_live(x["status"])][-6:]:
            col = "ob_up" if b["dir"] > 0 else "ob_down"
            tip = M["desc"]["OB_bull" if b["dir"] > 0 else "OB_bear"]
            L.append(obj(b["id"], "RECT", b["t0"], b["bottom"], b["t1"], b["top"], col, SOLID, 1, 1, tip=tip))
            L.append(obj(b["id"] + "_t", "TEXT", b["t0"], b["top"], 0, 0, "up" if b["dir"] > 0 else "down", flag=1,
                         text=f"OB{'+' if b['dir'] > 0 else '-'} {b['status']}", tip=tip))
    if lay.get("fvg", True):
        for g in [x for x in M["fvg"] if overlay.is_live(x["status"])][-8:]:
            col = "fvg_up" if g["dir"] > 0 else "fvg_down"
            tip = M["desc"]["FVG_bull" if g["dir"] > 0 else "FVG_bear"]
            L.append(obj(g["id"], "RECT", g["t0"], g["bottom"], g["t1"], g["top"], col, DOT, 1, 1, tip=tip))
            L.append(obj(g["id"] + "_t", "TEXT", g["t0"], g["bottom"], 0, 0, "teal" if g["dir"] > 0 else "orange",
                         flag=0, text="FVG", tip=tip))
    if lay.get("bos", True):
        for e in M["events"][-5:]:
            col = "purple" if e["type"] == "CHoCH" else "blue"
            L.append(obj(e["id"], "TREND", e["t_from"], e["level"], e["t"], e["level"], col,
                         DASHDOT if e["type"] == "CHoCH" else SOLID, 1, 0, tip=M["desc"][e["type"]]))
            L.append(obj(e["id"] + "_t", "TEXT", e["t"], e["level"], 0, 0, col, flag=1 if e["dir"] > 0 else 0,
                         text=f"{e['type']}{'^' if e['dir'] > 0 else 'v'}", tip=M["desc"][e["type"]]))
    if lay.get("liq", True):
        for q in [x for x in M["liquidity"] if overlay.is_live(x["status"]) or x["status"] == "tersapu"][-6:]:
            tip = M["desc"]["LIQ_" + q["dir"]]
            L.append(obj(q["id"], "TREND", q["t0"], q["level"], q["t1"], q["level"], "gold", DASH, 1, 0, tip=tip))
            L.append(obj(q["id"] + "_t", "TEXT", q["t0"], q["level"], 0, 0, "gold", flag=1 if q["dir"] == "H" else 0,
                         text=f"{'EQH' if q['dir'] == 'H' else 'EQL'} {q['status']}", tip=tip))
            if q["t_sweep"]:
                L.append(obj(q["id"] + "_x", "ARROW", q["t_sweep"], q["level"], 251, 0, "gold", flag=1,
                             tip=M["desc"]["SWEEP_" + q["dir"]]))
    if lay.get("entry", True):
        mark_times = [m["time"] for m in marks]
        for e in entries:
            if any(abs(e["t_entry"] - t) < 600 for t in mark_times):
                continue        # burst yang masih tampil sebagai marker posisi -> jangan gambar panah ganda
            up = e["dir"] == "BUY"
            tip = (f"ENTRY {e['dir']} x{e['tickets']} @ {e['price']} | skor "
                   f"{'-' if e.get('confidence') is None else round(e['confidence'] * 100)}% | "
                   + " | ".join(e.get("smc", [])[:2]))
            L.append(obj(f"ENT_{e['symbol']}_{e['bar_time']}", "ARROW", e["t_entry"], e["price"],
                         241 if up else 242, 0, "white", width=2, flag=0 if up else 1, tip=tip))
    return L


def read_entries(symbol, t_start):
    p = LOG_DIR / "entries.jsonl"
    if not p.exists():
        return []
    out = []
    cfg = load_config()
    sec = overlay.TF_SEC[cfg["symbols"].get(symbol, {}).get("timeframe", "M5")]
    for line in p.read_text(encoding="utf-8").splitlines()[-500:]:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("symbol") == symbol and e.get("price"):
            e["t_entry"] = int(e["bar_time"]) + sec            # entry di candle setelah bar sinyal
            if e["t_entry"] >= t_start:
                out.append(e)
    return out


def bridge_symbols(cfg, out_dir, fresh_sec=120):
    """Simbol yang digambar: yang trading-nya ON (switch dashboard / --symbols) ATAU yang chart MT5-nya
    sedang dipasangi indikator (indikator menulis ea_<SYM>_<TF>.txt) — tidak lagi hanya enabled=true,
    supaya BTCUSD/ETHUSD yang dinyalakan dari dashboard ikut tergambar."""
    act = read_active(cfg)
    syms = [s for s in cfg["symbols"] if act.get(s)]
    now = time.time()
    for f in out_dir.glob("ea_*_*.txt"):
        sym = f.stem[3:].rsplit("_", 1)[0]
        if sym in cfg["symbols"] and sym not in syms and now - f.stat().st_mtime < fresh_sec:
            syms.append(sym)
    return syms


def write_atomic(path, text):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="ascii", errors="replace")
    for _ in range(10):                  # indikator mungkin sedang membaca -> coba lagi sebentar
        try:
            tmp.replace(path)
            return True
        except PermissionError:
            time.sleep(0.05)
    return False


def pid_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return code.value == 259
    except Exception:
        return False


def main():
    if LOCK.exists():
        try:
            old = int(LOCK.read_text().strip())
            if old != os.getpid() and pid_alive(old):
                log.error(f"BRIDGE engine lain masih jalan (PID {old}). Keluar.")
                sys.exit(1)
        except (ValueError, Exception):
            pass
    LOCK.write_text(str(os.getpid()))
    cfg = load_config()
    conn = Connection(cfg.get("mt5", {}))
    while not conn.connect():
        log.warning("BRIDGE MT5 belum tersambung — coba lagi 30 detik")
        time.sleep(30)
    out_dir = common_dir(cfg)
    log.info(f"BRIDGE engine jalan -> {out_dir}")
    last_key, prev, ov_cache = {}, {}, {}
    try:
        while True:
            t0 = time.time()
            cfg = load_config()
            bc = cfg.get("bridge", {})
            if not conn.check():
                time.sleep(bc.get("interval_sec", 2))
                continue          # heartbeat TIDAK ditulis -> indikator & dashboard melihat "disconnected"
            syms = bc.get("symbols") or bridge_symbols(cfg, out_dir)
            # lapisan milik skill yang OFF tidak digambar -> indikator menghapus objeknya dari chart
            lay = dict(bc.get("layers", {}), **{k: False for k, v in sk.layers_enabled(prof.read_control(cfg)["skills"]).items()
                                                if not v})
            tfs = bc.get("timeframes", ["M1", "M3", "M5", "M15", "M30", "H1"])
            ent_m = (LOG_DIR / "entries.jsonl").stat().st_mtime if (LOG_DIR / "entries.jsonl").exists() else 0
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = None
            linger = cfg.get("dashboard", {}).get("closed_marker_minutes", 10)
            pairs = {}
            for sym in syms:
                if not mt5.symbol_select(sym, True):
                    continue
                for tf in tfs:
                    r = mt5.copy_rates_from_pos(sym, TFC[tf], 0, bc.get("bars", 300) + 700)
                    if r is None or len(r) < 400:
                        continue
                    last_closed = int(r[-2]["time"])
                    marks = trade_marks.build(state, sym, linger)
                    # AKTIF (posisi nyata) sudah tergambar ringkas lewat 'marks' (agregat open_agg) --
                    # kalau ikut digambar lagi di sini jadi dobel dgn detail zona/SL/TP per-burst lama
                    # (28-29 Sep, temuan audit: entry_lines/plan_lines belum disesuaikan saat 'plan'
                    # web dibatasi ke kandidat saja). Di sini hanya kandidat: MENUNGGU / DITOLAK.
                    plans = [x for x in (state or {}).get("plans", []) if x.get("symbol") == sym
                            and x.get("status") in ("MENUNGGU", "DITOLAK")]
                    key = (last_closed, ent_m, json.dumps(lay, sort_keys=True), json.dumps(plans, sort_keys=True, default=str),
                           json.dumps(marks, sort_keys=True))
                    name = f"bridge_{sym}_{tf}.csv"
                    if last_key.get(name) != key:
                        # analisis berat hanya dihitung ulang saat ada candle tertutup baru;
                        # perubahan posisi (SL digeser trailing) cukup menulis ulang marker
                        hit = ov_cache.get(name)
                        if hit and hit[0] == last_closed:
                            ov = hit[1]
                        else:
                            ov = overlay.build(pd.DataFrame(r), tf, bc.get("bars", 300), cfg.get("smc", {}).get("swing_k", 3))
                            ov_cache[name] = (last_closed, ov)
                        lines = to_lines(ov, read_entries(sym, ov["t_start"]), lay,
                                         bc.get("max_candle_labels", 40), marks)
                        # rencana posisi, S/R multi-TF & berita — sumber sama dengan dashboard web
                        t_now = ov["t_now"]
                        if lay.get("entryline", True):
                            lines += entry_lines(plans, t_now)
                        if lay.get("plan", True):
                            lines += plan_lines(plans, t_now, lay.get("rejected", True))
                        try:
                            lines += sr_lines(sr_mtf.compute(sym, dict(cfg.get("sr", {}), round_step=cfg["symbols"][sym].get("round_step", 50))),
                                              ov["t_start"], t_now, lay)
                            if lay.get("news", True):
                                hs, ls = ov["candles"]["h"], ov["candles"]["l"]
                                lines += news_lines(newsimpact.events_for(sym, dict(cfg.get("news_impact", {}), symbols=list(cfg["symbols"])),
                                                                          ov["t_start"], t_now + 86400), min(ls), max(hs))
                        except Exception as e:
                            log.warning(f"BRIDGE {sym} lapisan S/R/berita gagal: {e}")
                        body = "\n".join([f"HDR;1;{sym};{tf};{int(time.time())};{len(lines)}"] + lines) + "\n"
                        if write_atomic(out_dir / name, body):
                            last_key[name] = key
                            prev[name] = len(lines)
                            pairs[f"{sym}_{tf}"] = {"objects": len(lines), "updated": time.time()}
                    else:
                        pairs[f"{sym}_{tf}"] = {"objects": prev.get(name), "updated": None}
            write_atomic(out_dir / "heartbeat.txt", f"{int(time.time())}\n")
            try:
                write_json_atomic(STATUS_PATH, {"ts": time.time(), "pid": os.getpid(), "dir": str(out_dir),
                                                "symbols": syms, "timeframes": tfs, "pairs": pairs,
                                                "loop_ms": int((time.time() - t0) * 1000)})
            except Exception:
                pass
            time.sleep(max(0.2, bc.get("interval_sec", 2) - (time.time() - t0)))
    except KeyboardInterrupt:
        log.info("BRIDGE dihentikan (Ctrl+C) — indikator MT5 akan menampilkan 'Bridge disconnected'")
    finally:
        try:
            LOCK.unlink()
        except Exception:
            pass
        mt5.shutdown()


if __name__ == "__main__":
    main()
