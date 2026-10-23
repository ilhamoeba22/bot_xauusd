"""
Marker posisi (entry / SL / TP) untuk chart — SATU sumber kebenaran: logs/state.json yang ditulis
bot tiap detik (daftar yang sama dengan panel "Posisi terbuka" di dashboard). Dipakai dashboard
web DAN bridge MT5, jadi keduanya tidak mungkin menampilkan posisi yang berbeda.

Posisi TERBUKA searah pada simbol yang sama (lintas-burst sekalipun) digabung jadi SATU marker
agregat "SELL x4 · avg 82.930 · 0.08 lot · floating +X" dengan pita SL (terdekat..terjauh) dan TP
(kalau ada), supaya beberapa burst yang menumpuk tidak digambar sebagai garis/label terpisah
(28 Sep, temuan: 4 posisi BTCUSD dalam ~165 poin saling menutupi). Tiap tiket individu tetap
ditandai segitiga kecil TANPA teks di titik entry aslinya (untuk konteks visual & klik detail);
rincian per burst tersimpan di field `bursts` untuk tooltip/klik.
Posisi yang sudah ditutup tetap tampil `linger_min` menit (abu-abu, per burst seperti sebelumnya)
lalu hilang — penggambar menghapusnya lewat cleanup berbasis ID.
"""


def _burst_group(rows, closed):
    """Tiket satu burst: simbol & arah sama, harga masuk sama, dibuka dalam 10 detik yang sama."""
    groups = {}
    for p in rows:
        key = (p["dir"], round(float(p["open"]), 2), int(p.get("time", 0)) // 10)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"dir": p["dir"], "open": float(p["open"]), "time": int(p.get("time", 0)),
                               "tickets": [], "volume": 0.0, "sl": [], "tp": [], "pnl": 0.0,
                               "close": [], "close_time": 0, "why": set(), "status": "closed" if closed else "open"}
        g["tickets"].append(p["ticket"])
        g["volume"] += float(p["volume"])
        if closed:
            g["pnl"] += float(p.get("pnl", 0.0))
            g["close"].append(float(p["close"]))
            g["close_time"] = max(g["close_time"], int(p["close_time"]))
            g["why"].add(str(p.get("why", "")))
        else:
            g["pnl"] += float(p.get("profit", 0.0))
            g["sl"].append(float(p.get("sl") or 0))
            g["tp"].append(float(p.get("tp") or 0))
    return list(groups.values())


def build(state, symbol, linger_min=10):
    """-> list marker untuk satu simbol: posisi terbuka (agregat per arah, id status='open_agg') +
    posisi yang baru ditutup (per burst, seperti sebelumnya, id status='closed')."""
    if not state:
        return []
    now = state.get("ts", 0)
    out = []
    open_rows = [p for p in state.get("positions", []) if p["symbol"] == symbol]
    for dr in ("BUY", "SELL"):
        bursts = _burst_group([p for p in open_rows if p["dir"] == dr], closed=False)
        if not bursts:
            continue
        vol = sum(b["volume"] for b in bursts)
        entry = sum(b["open"] * b["volume"] for b in bursts) / vol if vol else 0.0
        all_sl = sorted(x for b in bursts for x in b["sl"] if x)
        all_tp = sorted(x for b in bursts for x in b["tp"] if x)
        pnl = sum(b["pnl"] for b in bursts)
        tickets = [t for b in bursts for t in b["tickets"]]
        t0 = min(b["time"] for b in bursts)
        out.append({
            "id": f"AGG_{symbol}_{dr}", "status": "open_agg", "symbol": symbol, "dir": dr,
            "count": len(tickets), "tickets": tickets, "volume": round(vol, 2), "time": t0,
            "entry": round(entry, 3),
            "sl_lo": round(all_sl[0], 3) if all_sl else 0.0, "sl_hi": round(all_sl[-1], 3) if all_sl else 0.0,
            "tp_lo": round(all_tp[0], 3) if all_tp else 0.0, "tp_hi": round(all_tp[-1], 3) if all_tp else 0.0,
            "pnl": round(pnl, 2),
            "bursts": [{"time": b["time"], "open": round(b["open"], 3), "count": len(b["tickets"]),
                        "volume": round(b["volume"], 2),
                        "sl": round((max(b["sl"]) if dr == "BUY" else min(x for x in b["sl"] if x)) if any(b["sl"]) else 0, 3),
                        "pnl": round(b["pnl"], 2)} for b in sorted(bursts, key=lambda b: b["time"])],
        })
    for g in _burst_group([c for c in state.get("recent_closed", []) if c["symbol"] == symbol], closed=True):
        if now - g["close_time"] > linger_min * 60:
            continue
        out.append({"id": f"TRD_{symbol}_{g['dir']}_{g['time'] // 10}_x", "status": "closed", "symbol": symbol,
                    "dir": g["dir"], "count": len(g["tickets"]), "tickets": g["tickets"],
                    "volume": round(g["volume"], 2), "time": g["time"], "open": round(g["open"], 3),
                    "close": round(sum(g["close"]) / len(g["close"]), 3), "close_time": g["close_time"],
                    "pnl": round(g["pnl"], 2), "why": ", ".join(sorted(w for w in g["why"] if w))})
    return out


def label(m):
    """Teks label ringkas (dipakai web & MT5 supaya sama persis)."""
    n = f" x{m['count']}" if m["count"] > 1 else ""
    if m["status"] == "open_agg":
        tp = f"{m['tp_lo']}" if m["tp_lo"] else "- (trailing)"
        sl = m["sl_lo"] if m["sl_lo"] == m["sl_hi"] else f"{m['sl_lo']}-{m['sl_hi']}"
        return (f"{m['dir']}{n} · avg {m['entry']} · {m['volume']} lot · SL {sl} · TP {tp} · "
                f"floating {m['pnl']:+,.0f}")
    return f"TUTUP{n} {m['dir']} {m['open']} -> {m['close']} | P/L {m['pnl']:+,.0f} ({m['why']})"
