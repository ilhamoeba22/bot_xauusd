from ..analysis import candles as cdl

SKILL = {"id": "candles", "order": 1, "kind": "score", "weight": 1.0, "deps": [], "layers": ["candle", "doji"],
         "name": "Candlestick Pattern Recognition",
         "desc": "Pola candle (Engulfing, Hammer, Pin Bar, Morning/Evening Star, Doji...) di 3 bar terakhir."}


def evaluate(ctx):
    d = ctx["direction"]
    pats = [p for p in cdl.detect(ctx["df"].tail(10), 10) if p["i"] >= 7]
    lines = [p["name"] + (" (searah)" if p["dir"] == d else " (berlawanan)" if p["dir"] else "") for p in pats]
    votes = [1 if p["dir"] == d else -1 for p in pats if p["dir"]]
    return {"score": sum(votes) / len(votes) if votes else 0.0, "lines": lines}
