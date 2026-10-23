"""
Struktur pasar & chart pattern (logika sendiri — tidak ada di library standar).

swing high/low  : fractal k-bar (high tertinggi di antara k bar kiri & k bar kanan).
                  Swing di bar i baru TERKONFIRMASI di bar i+k — dipakai apa adanya oleh
                  fungsi kausal (smc.bias_series) supaya backtest tidak mengintip masa depan.
support/resist. : klaster harga swing yang disentuh >= 2 kali (toleransi 0,35 x ATR).
trendline       : garis lewat dua swing low naik / dua swing high turun terakhir.
chart pattern   : double top/bottom, head & shoulders (+inverse), segitiga, bendera/pennant.
Status tiap objek: 'aktif' / 'terkonfirmasi' / 'batal' (ter-invalidate oleh harga).
"""
import numpy as np
import pandas as pd

from ..indicators import atr as atr_fn

DESC = {
    "support": "Support: area harga yang beberapa kali menahan penurunan (banyak pembeli menunggu di sini).",
    "resistance": "Resistance: area harga yang beberapa kali menahan kenaikan (banyak penjual menunggu di sini).",
    "trend_up": "Trendline naik: menghubungkan swing low yang makin tinggi. Selama harga di atas garis, tren naik utuh.",
    "trend_down": "Trendline turun: menghubungkan swing high yang makin rendah. Selama harga di bawah garis, tren turun utuh.",
    "double_top": "Double Top: dua puncak setara gagal menembus ke atas. Terkonfirmasi saat harga tutup di bawah "
                  "neckline (lembah di antaranya) — target turun ≈ tinggi pola.",
    "double_bottom": "Double Bottom: dua dasar setara gagal menembus ke bawah. Terkonfirmasi saat harga tutup di atas "
                     "neckline — target naik ≈ tinggi pola.",
    "head_shoulders": "Head & Shoulders: bahu-kepala-bahu, kepala paling tinggi. Pola pembalikan turun; konfirmasi "
                      "saat neckline (garis dua lembah) ditembus ke bawah.",
    "inv_head_shoulders": "Inverse Head & Shoulders: versi terbalik — pola pembalikan NAIK saat neckline ditembus ke atas.",
    "triangle_sym": "Segitiga simetris: puncak makin rendah & dasar makin tinggi — volatilitas menyempit, biasanya "
                    "berakhir dengan breakout kuat ke salah satu arah.",
    "triangle_asc": "Segitiga naik: resistance datar + dasar makin tinggi. Pembeli makin agresif — condong breakout ke atas.",
    "triangle_desc": "Segitiga turun: support datar + puncak makin rendah. Penjual makin agresif — condong breakout ke bawah.",
    "flag_bull": "Bull flag: lonjakan naik tajam (tiang) lalu konsolidasi turun tipis. Biasanya tren naik berlanjut.",
    "flag_bear": "Bear flag: jatuh tajam lalu konsolidasi naik tipis. Biasanya tren turun berlanjut.",
}


def swings(df: pd.DataFrame, k: int = 3):
    """-> list dict {i, price, kind 'H'/'L', confirm_i}"""
    h, l = df.high.values, df.low.values
    out = []
    for i in range(k, len(df) - k):
        if h[i] == h[i - k:i + k + 1].max() and h[i] > h[i - k:i].max():
            out.append({"i": i, "price": float(h[i]), "kind": "H", "confirm_i": i + k})
        if l[i] == l[i - k:i + k + 1].min() and l[i] < l[i - k:i].min():
            out.append({"i": i, "price": float(l[i]), "kind": "L", "confirm_i": i + k})
    out.sort(key=lambda s: s["i"])
    return out


def _alternate(sw):
    """Rapikan menjadi urutan H-L-H-L (ambil ekstrem kalau dua jenis sama berurutan)."""
    res = []
    for s in sw:
        if res and res[-1]["kind"] == s["kind"]:
            better = s["price"] > res[-1]["price"] if s["kind"] == "H" else s["price"] < res[-1]["price"]
            if better:
                res[-1] = s
        else:
            res.append(s)
    return res


def levels(df, sw, atr_now, max_levels=6):
    tol = 0.35 * atr_now
    close = df.close.values
    clusters = []
    for s in sw[-60:]:
        for c in clusters:
            if abs(c["price"] - s["price"]) <= tol:
                c["touches"].append(s)
                c["price"] = float(np.mean([t["price"] for t in c["touches"]]))
                break
        else:
            clusters.append({"price": s["price"], "touches": [s]})
    out = []
    last = close[-1]
    for c in clusters:
        if len(c["touches"]) < 2:
            continue
        kind = "resistance" if c["price"] > last else "support"
        first_i = min(t["i"] for t in c["touches"])
        out.append({"kind": kind, "price": round(c["price"], 3), "touches": len(c["touches"]),
                    "from_i": first_i, "label": f"{'R' if kind == 'resistance' else 'S'} ({len(c['touches'])}x)",
                    "desc": DESC[kind]})
    out.sort(key=lambda x: abs(x["price"] - last))
    return out[:max_levels]


def _line_at(p1, p2, i):
    return p1["price"] + (p2["price"] - p1["price"]) * (i - p1["i"]) / (p2["i"] - p1["i"])


def trendlines(df, sw, atr_now):
    out = []
    close, n = df.close.values, len(df)
    lows = [s for s in sw if s["kind"] == "L"][-4:]
    highs = [s for s in sw if s["kind"] == "H"][-4:]
    for pts, kind in ((lows, "trend_up"), (highs, "trend_down")):
        if len(pts) < 2:
            continue
        p1, p2 = pts[-2], pts[-1]
        if kind == "trend_up" and p2["price"] <= p1["price"]:
            continue
        if kind == "trend_down" and p2["price"] >= p1["price"]:
            continue
        # batal kalau ada close yang menembus garis > 0,3 ATR setelah titik kedua
        broken = False
        for i in range(p2["i"] + 1, n):
            v = _line_at(p1, p2, i)
            if (kind == "trend_up" and close[i] < v - 0.3 * atr_now) or (kind == "trend_down" and close[i] > v + 0.3 * atr_now):
                broken = True
                break
        out.append({"kind": kind, "i0": p1["i"], "y0": p1["price"], "i1": n - 1,
                    "y1": round(_line_at(p1, p2, n - 1), 3), "status": "batal" if broken else "aktif",
                    "label": ("Trendline naik" if kind == "trend_up" else "Trendline turun") + (" (patah)" if broken else ""),
                    "desc": DESC[kind]})
    return out


def chart_patterns(df, sw, atr_now):
    """Deteksi pola pada swing terakhir. Mengembalikan objek siap gambar (garis + label)."""
    alt = _alternate(sw)
    close, n = df.close.values, len(df)
    last = close[-1]
    tol = 0.5 * atr_now
    out = []

    def status_break(level, direction, after_i, invalid_level):
        """terkonfirmasi kalau close menembus level searah pola; batal kalau menembus invalid_level."""
        for i in range(after_i + 1, n):
            if (direction < 0 and close[i] > invalid_level) or (direction > 0 and close[i] < invalid_level):
                return "batal", i
            if (direction < 0 and close[i] < level) or (direction > 0 and close[i] > level):
                return "terkonfirmasi", i
        return "aktif", None

    # ---- double top / bottom: 3 swing terakhir X-Y-X
    for j in range(len(alt) - 3, max(len(alt) - 8, -1), -1):
        a, b, c = alt[j:j + 3]
        if a["kind"] == "H" and abs(a["price"] - c["price"]) <= tol and a["price"] - b["price"] >= 1.5 * atr_now:
            st, _ = status_break(b["price"], -1, c["i"], max(a["price"], c["price"]) + tol)
            out.append({"kind": "double_top", "label": "Double Top", "dir": -1, "status": st,
                        "points": [[a["i"], a["price"]], [b["i"], b["price"]], [c["i"], c["price"]]],
                        "neckline": [[b["i"], b["price"]], [n - 1, b["price"]]], "desc": DESC["double_top"]})
            break
        if a["kind"] == "L" and abs(a["price"] - c["price"]) <= tol and b["price"] - a["price"] >= 1.5 * atr_now:
            st, _ = status_break(b["price"], 1, c["i"], min(a["price"], c["price"]) - tol)
            out.append({"kind": "double_bottom", "label": "Double Bottom", "dir": 1, "status": st,
                        "points": [[a["i"], a["price"]], [b["i"], b["price"]], [c["i"], c["price"]]],
                        "neckline": [[b["i"], b["price"]], [n - 1, b["price"]]], "desc": DESC["double_bottom"]})
            break

    # ---- head & shoulders: 5 swing  S1 N1 H N2 S2
    for j in range(len(alt) - 5, max(len(alt) - 10, -1), -1):
        s1, n1, hd, n2, s2 = alt[j:j + 5]
        if s1["kind"] == "H" and hd["price"] > max(s1["price"], s2["price"]) + 0.5 * atr_now \
                and abs(s1["price"] - s2["price"]) <= 1.0 * atr_now:
            neck = [n1, n2]
            neck_now = _line_at(n1, n2, n - 1) if n2["i"] != n1["i"] else n2["price"]
            st, _ = status_break(neck_now, -1, s2["i"], hd["price"])
            out.append({"kind": "head_shoulders", "label": "Head & Shoulders", "dir": -1, "status": st,
                        "points": [[p["i"], p["price"]] for p in (s1, n1, hd, n2, s2)],
                        "neckline": [[n1["i"], n1["price"]], [n - 1, round(neck_now, 3)]], "desc": DESC["head_shoulders"]})
            break
        if s1["kind"] == "L" and hd["price"] < min(s1["price"], s2["price"]) - 0.5 * atr_now \
                and abs(s1["price"] - s2["price"]) <= 1.0 * atr_now:
            neck_now = _line_at(n1, n2, n - 1) if n2["i"] != n1["i"] else n2["price"]
            st, _ = status_break(neck_now, 1, s2["i"], hd["price"])
            out.append({"kind": "inv_head_shoulders", "label": "Inverse H&S", "dir": 1, "status": st,
                        "points": [[p["i"], p["price"]] for p in (s1, n1, hd, n2, s2)],
                        "neckline": [[n1["i"], n1["price"]], [n - 1, round(neck_now, 3)]], "desc": DESC["inv_head_shoulders"]})
            break

    # ---- segitiga: 2 swing high & 2 swing low terakhir
    hs = [s for s in alt if s["kind"] == "H"][-2:]
    ls = [s for s in alt if s["kind"] == "L"][-2:]
    if len(hs) == 2 and len(ls) == 2 and min(hs[0]["i"], ls[0]["i"]) > n - 120:
        dh, dl = hs[1]["price"] - hs[0]["price"], ls[1]["price"] - ls[0]["price"]
        flat = 0.3 * atr_now
        kind = None
        if dh < -flat and dl > flat:
            kind = "triangle_sym"
        elif abs(dh) <= flat and dl > flat:
            kind = "triangle_asc"
        elif dh < -flat and abs(dl) <= flat:
            kind = "triangle_desc"
        if kind:
            top, bot = _line_at(hs[0], hs[1], n - 1), _line_at(ls[0], ls[1], n - 1)
            st = "aktif"
            if top > bot:
                if last > top + 0.3 * atr_now:
                    st = "breakout naik"
                elif last < bot - 0.3 * atr_now:
                    st = "breakout turun"
            else:
                st = "selesai"
            out.append({"kind": kind, "label": {"triangle_sym": "Segitiga Simetris", "triangle_asc": "Segitiga Naik",
                                                 "triangle_desc": "Segitiga Turun"}[kind],
                        "dir": {"triangle_sym": 0, "triangle_asc": 1, "triangle_desc": -1}[kind], "status": st,
                        "lines": [[[hs[0]["i"], hs[0]["price"]], [n - 1, round(top, 3)]],
                                  [[ls[0]["i"], ls[0]["price"]], [n - 1, round(bot, 3)]]],
                        "desc": DESC[kind]})

    # ---- flag: tiang >= 3 ATR dalam <= 10 bar, lalu konsolidasi 5-25 bar retrace <= 50%
    c = close
    for pole_end in range(n - 6, max(n - 40, 12), -1):
        for pole_len in range(3, 11):
            s0 = pole_end - pole_len
            move = c[pole_end] - c[s0]
            if abs(move) < 3 * atr_now:
                continue
            cons = df.iloc[pole_end:n]
            if not 5 <= len(cons) <= 25:
                continue
            d = 1 if move > 0 else -1
            retr = (c[pole_end] - cons.low.min()) if d > 0 else (cons.high.max() - c[pole_end])
            rng = cons.high.max() - cons.low.min()
            if retr <= 0.5 * abs(move) and rng <= 0.6 * abs(move):
                st = "aktif"
                if (d > 0 and last > cons.high.iloc[:-1].max()) or (d < 0 and last < cons.low.iloc[:-1].min()):
                    st = "breakout"
                out.append({"kind": "flag_bull" if d > 0 else "flag_bear", "label": "Bull Flag" if d > 0 else "Bear Flag",
                            "dir": d, "status": st, "pole": [[s0, float(c[s0])], [pole_end, float(c[pole_end])]],
                            "box": [pole_end, float(cons.low.min()), n - 1, float(cons.high.max())],
                            "desc": DESC["flag_bull" if d > 0 else "flag_bear"]})
                break
        else:
            continue
        break
    return out


def analyze(df: pd.DataFrame, k: int = 3):
    a = float(atr_fn(df).iloc[-1])
    sw = swings(df, k)
    return {"atr": a, "swings": [{"i": s["i"], "price": s["price"], "kind": s["kind"]} for s in sw],
            "levels": levels(df, sw, a), "trendlines": trendlines(df, sw, a),
            "patterns": chart_patterns(df, sw, a)}
