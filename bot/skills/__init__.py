"""Registry skill bot.

Setiap skill = SATU file di folder ini yang berisi dict SKILL (entri registry) dan, untuk skill
penilai (kind="score"), fungsi evaluate(ctx) -> {"score": -1..1, "lines": [...]}.
Registry menemukan file otomatis (pkgutil) — menambah skill TIDAK perlu mengubah engine:
lihat README "Menambah skill".

SKILL = {"id", "name", "desc", "kind": "score"|"behavior", "weight" (skill penilai),
         "deps": [id skill yang dibutuhkan], "layers": [lapisan chart/MT5 milik skill ini],
         "order": urutan tampil}
Skor sinyal = rata-rata berbobot skor skill penilai yang ON (bobot dinormalisasi otomatis),
dipetakan ke 0..100%.
"""
import importlib
import pkgutil

REGISTRY = {}
_MODULES = {}
for _m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    mod = importlib.import_module(f"{__name__}.{_m.name}")
    if hasattr(mod, "SKILL"):
        REGISTRY[mod.SKILL["id"]] = mod.SKILL
        _MODULES[mod.SKILL["id"]] = mod
REGISTRY = dict(sorted(REGISTRY.items(), key=lambda kv: kv[1].get("order", 99)))


def defaults():
    return {k: s.get("default", True) for k, s in REGISTRY.items()}


def dependents(sid):
    """Skill yang (langsung/tidak langsung) membutuhkan sid."""
    out = set()
    for k, s in REGISTRY.items():
        if sid in s.get("deps", []):
            out |= {k} | dependents(k)
    return out


def normalize(state):
    """Skill yang dependensinya OFF ikut OFF (tidak bisa jalan tanpa datanya)."""
    st = dict(defaults(), **{k: bool(v) for k, v in (state or {}).items() if k in REGISTRY})
    changed = True
    while changed:
        changed = False
        for k, s in REGISTRY.items():
            if st[k] and any(not st.get(d, False) for d in s.get("deps", [])):
                st[k], changed = False, True
    return st


def score(ctx, state):
    """-> (skor 0..100 atau None, {id: {"score", "lines"}}). Skill OFF tidak dipanggil sama sekali."""
    res, num, den = {}, 0.0, 0.0
    for k, s in REGISTRY.items():
        if s.get("kind") != "score" or not state.get(k):
            continue
        try:
            r = _MODULES[k].evaluate(ctx)
        except Exception as e:                       # satu skill error tidak boleh mematikan entry
            r = {"score": None, "lines": [f"{s['name']}: error {e}"]}
        res[k] = r
        if r.get("score") is not None:
            w = float(s.get("weight", 1.0))
            num, den = num + w * max(-1.0, min(1.0, r["score"])), den + w
    return (None if not den else round((num / den + 1) / 2 * 100, 1)), res


def layers_enabled(state):
    """{layer: bool} untuk chart web & auto-draw MT5: lapisan skill yang OFF = False."""
    out = {}
    for k, s in REGISTRY.items():
        for lay in s.get("layers", []):
            out[lay] = out.get(lay, False) or bool(state.get(k))
    return out


def combo_key(state):
    """Kode kombinasi skill ON untuk evaluasi, mis. 'burst+candles+smc+trailing'."""
    return "+".join(k for k in sorted(REGISTRY) if state.get(k)) or "-"
