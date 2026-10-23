from ..analysis import smc as smc_mod

SKILL = {"id": "smc", "order": 3, "kind": "score", "weight": 1.5, "deps": [], "layers": ["ob", "fvg", "bos", "liq"],
         "name": "Order Block / SMC",
         "desc": "Bias struktur, BOS/CHoCH, Order Block, FVG & liquidity sweep. Profil CONSERVATIVE mewajibkan searah bias."}


def evaluate(ctx):
    row, d = ctx["row"], ctx["direction"]
    bias = int(row["smc_bias"])
    return {"score": float(bias * d), "lines": smc_mod.describe_context(row, d), "bias": bias}
