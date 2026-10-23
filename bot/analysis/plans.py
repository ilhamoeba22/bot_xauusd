"""Rencana posisi (trade plan) dari sinyal bot — satu sumber untuk dashboard web & MT5 (lewat state.json).

Status: MENUNGGU (kandidat belum dieksekusi) · AKTIF (burst terbuka; SL mengikuti trailing) ·
SELESAI (burst tutup, memudar) · DITOLAK (kandidat ditolak filter/gerbang, abu-abu + alasan).
Level: SL = SL asli bot (sl_atr x ATR, dijepit per simbol); INVALIDASI = swing/SR struktur terdekat di balik
entry (+ buffer ATR); TP1/TP2 = level S/R (multi-timeframe) berikutnya searah, cadangan 1R / 2R.
Label bukan janji arah harga.
"""


def build(sym, direction, entry, sl_dist, atr, sr_levels, swings, status, reason="", t=0, key="",
          score=None, skill_scores=None, buffer_atr=0.2, trigger=None, labels=None, tf="", sl_clamped=False):
    d = 1 if direction > 0 else -1
    sl = entry - d * sl_dist
    ahead = sorted([lv for lv in sr_levels if (lv - entry) * d > 0.3 * atr], key=lambda p: abs(p - entry))
    tp1 = ahead[0] if ahead else entry + d * sl_dist
    tp2 = ahead[1] if len(ahead) > 1 else entry + d * 2 * sl_dist
    if abs(tp2 - entry) <= abs(tp1 - entry):
        tp2 = entry + d * max(2 * sl_dist, abs(tp1 - entry) * 1.5)
    behind = [p for p in swings if (entry - p) * d > 0]
    inv = (max(behind) if d > 0 else min(behind)) - d * buffer_atr * atr if behind else sl
    lab = lambda p_: (labels or {}).get(round(p_, 3)) or ("1R dari entry" if abs(p_ - entry) <= sl_dist * 1.001 else "2R / 1,5x TP1 (tanpa level struktur)")
    rr = lambda tp: round(abs(tp - entry) / sl_dist, 2) if sl_dist else None
    return {"symbol": sym, "key": key, "dir": "BUY" if d > 0 else "SELL", "status": status, "reason": reason, "t": int(t),
            "entry": round(entry, 3), "sl": round(sl, 3), "tp1": round(tp1, 3), "tp2": round(tp2, 3),
            "invalid": round(inv, 3), "trigger": round(trigger if trigger is not None else entry, 3),
            "sl_atr": round(sl_dist / atr, 2) if atr else None,
            "tp1_atr": round(abs(tp1 - entry) / atr, 2) if atr else None, "tp2_atr": round(abs(tp2 - entry) / atr, 2) if atr else None,
            "rr1": rr(tp1), "rr2": rr(tp2), "score": score, "skill_scores": skill_scores or {},
            "tf": tf, "updated": __import__("time").time(),
            # sumber tiap level (untuk panel Saran Level) — dari data yang sama, bukan rumus terpisah
            "sl_src": f"{round(sl_dist / atr, 2) if atr else '?'}xATR({tf})" + (" dijepit min/maks % harga" if sl_clamped else "") + " dari entry",
            "tp1_src": lab(tp1), "tp2_src": lab(tp2),
            "inv_src": "swing terdekat di balik entry + 0,2xATR" if behind else "= SL (tidak ada swing)"}
