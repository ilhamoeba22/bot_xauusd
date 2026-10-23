from ..analysis.features import entry_features

SKILL = {"id": "learning", "order": 4, "kind": "score", "weight": 1.0, "deps": ["candles", "structure", "smc"],
         "layers": [],
         "name": "Adaptive Learning",
         "desc": "Model belajar dari hasil trade (re-weight fitur setelah trade tutup); butuh data skill deteksi."}


def evaluate(ctx):
    feats = entry_features(ctx["row"], ctx["direction"], ctx["sym"], ctx["df"].tail(300))
    prob, contrib = ctx["learner"].score(feats)
    ctx["features"], ctx["factors"], ctx["confidence"] = feats, contrib[:8], prob
    return {"score": None if prob is None else (prob - 0.5) * 2,
            "lines": [f"peluang menang model {prob:.0%}" if prob is not None else "model belum cukup data"]}
