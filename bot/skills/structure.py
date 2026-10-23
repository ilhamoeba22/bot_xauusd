from ..analysis import structure as struct_mod

SKILL = {"id": "structure", "order": 2, "kind": "score", "weight": 1.0, "deps": [], "layers": ["chart", "sr", "trend", "srmtf", "refs"],
         "name": "Chart Pattern & Struktur Pasar",
         "desc": "Double top/bottom, H&S, segitiga, flag, support/resistance & trendline (auto-drawing)."}


def evaluate(ctx):
    d, a = ctx["direction"], ctx["atr"]
    st = struct_mod.analyze(ctx["df"].tail(300).reset_index(drop=True), ctx["swing_k"])
    live = [p for p in st["patterns"] if p["status"] not in ("batal", "selesai")]
    lines = [f"{p['label']} ({p['status']})" for p in live]
    px = float(ctx["df"].close.iloc[-1])
    votes = [1 if p["dir"] == d else -1 for p in live if p.get("dir")]
    for lv in st["levels"][:3]:
        dist = abs(lv["price"] - px) / a if a else 99
        lines.append(f"{lv['label']} {lv['kind']} @ {lv['price']} (jarak {dist:.1f} ATR)")
        if dist < 1:                                   # level dekat: menahan (lawan) atau mendukung (searah)
            ahead = (lv["price"] > px) if d > 0 else (lv["price"] < px)
            votes.append(-1 if ahead else 1)
    return {"score": sum(votes) / len(votes) if votes else 0.0, "lines": lines}
