"""Profil risiko CONSERVATIVE / NORMAL / AGGRESSIVE + status skill + perintah dashboard/CMD dengan ACK.

Semua angka profil ada di config.json -> risk_profiles.profiles.<NAMA>. Profil hanya MENGALIKAN /
mengganti kunci yang di-whitelist di sini; rem akun (daily_loss_limit_pct, max_drawdown_pct,
max_account_open_risk_pct) TIDAK bisa disentuh profil mana pun.

Pilihan aktif disimpan di logs/control_state.json (dipulihkan saat restart):
  {"profile": {"global": "AGGRESSIVE", "per_symbol": {"BTCUSD": "NORMAL"}}, "skills": {"smc": true, ...}}
Perintah: web/CMD menulis logs/commands/<id>.json -> bot memvalidasi & menerapkan di siklus berikut ->
ACK di logs/commands/<id>.ack.json (profil/skill yang BENAR-BENAR aktif).
"""
import copy
import json
import time

from .common import LOG_DIR, write_json_atomic
from . import skills as sk

NAMES = ("CONSERVATIVE", "NORMAL", "AGGRESSIVE")
CONTROL_PATH = LOG_DIR / "control_state.json"
CMD_DIR = LOG_DIR / "commands"
CMD_DIR.mkdir(parents=True, exist_ok=True)


def rp(cfg):
    return cfg.get("risk_profiles", {})


def read_control(cfg):
    try:
        s = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    prof = s.get("profile") or {}
    g = prof.get("global") or rp(cfg).get("default", "NORMAL")
    return {"profile": {"global": g if g in NAMES else "NORMAL",
                        "per_symbol": {k: v for k, v in (prof.get("per_symbol") or {}).items() if v in NAMES}},
            "skills": sk.normalize(s.get("skills")),
            "active_strategy": s.get("active_strategy", cfg.get("active_strategy", "smc_mtf")),
            # mode per simbol: {"SYM": {"mode": "RUN"|"BLOCK", "until": epoch|None, "lot_mult": x, "set_at": epoch}}
            "override": {k: v for k, v in (s.get("override") or {}).items() if v.get("mode") in ("RUN", "BLOCK")}}


def force_run_until(cfg, now=None):
    """TETAP RUN kedaluwarsa: akhir hari server (00:00 UTC) atau max_hours, mana yang lebih dulu."""
    import datetime as _dt
    fr = cfg.get("force_run", {})
    now = now or time.time()
    day_end = (_dt.datetime.fromtimestamp(now, _dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
               + _dt.timedelta(days=1)).timestamp()
    cands = [now + fr.get("max_hours", 8) * 3600]
    if fr.get("expire_at_day_end", True):
        cands.append(day_end)
    return min(cands)


def write_control(ctl):
    write_json_atomic(CONTROL_PATH, ctl)


def profile_of(ctl, sym):
    return ctl["profile"]["per_symbol"].get(sym) or ctl["profile"]["global"]


def live_blocked(cfg, mode, name):
    """AGGRESSIVE di akun live hanya dengan flag eksplisit risk_profiles.allow_aggressive_live=true."""
    return mode == "live" and name == "AGGRESSIVE" and not rp(cfg).get("allow_aggressive_live", False)


def apply(c_sym, cfg, name, skills_on):
    """c_sym = sym_cfg(cfg, sym) -> config efektif simbol dengan profil `name` & skill yang ON."""
    p = rp(cfg).get("profiles", {}).get(name)
    c = copy.deepcopy(c_sym)
    if p:
        rk, ex = c["risk"], c["exit"]
        cap = rp(cfg).get("risk_per_trade_cap_pct", 0.30)
        rk["risk_per_trade_pct"] = round(min(rk["risk_per_trade_pct"] * p.get("risk_mult", 1.0), cap), 4)
        slots = max(1, round(rk["max_positions_total"] * p.get("positions_mult", 1.0)))
        rk["max_positions_total"] = slots
        rk["burst_size"] = max(1, min(int(rk.get("burst_size", 1)), slots))
        rk["max_total_risk_pct"] = round(rk.get("max_total_risk_pct", 100) * p.get("risk_mult", 1.0)
                                         * p.get("positions_mult", 1.0), 3)
        for k in ("trail_atr", "trail_tight_atr"):                  # trailing tidak bisa dimatikan profil
            ex[k] = round(ex[k] * p.get("trail_atr_mult", 1.0), 3)
        c["smc"] = dict(c.get("smc", {}), filter_mode="strict" if p.get("smc_required") else "score")
    c["profile"] = dict(name=name, min_score=(p or {}).get("min_score", 0), cooldown_min=(p or {}).get("cooldown_min", 0),
                        intrabar_entry=bool((p or {}).get("intrabar_entry")),
                        intrabar_confirm_sec=(p or {}).get("intrabar_confirm_sec", 20),
                        bursts_per_symbol=max(1, int((p or {}).get("bursts_per_symbol", 1))),
                        min_spacing_atr=max(0.1, float((p or {}).get("min_spacing_atr", 0.5))),
                        intrabar_min_score=(p or {}).get("intrabar_min_score", 0),
                        intrabar_smc_min=(p or {}).get("intrabar_smc_min"),
                        use_smc_mtf=bool((p or {}).get("use_smc_mtf")),
                        use_bb_reversion=bool((p or {}).get("use_bb_reversion")),
                        rr_ratio=float((p or {}).get("rr_ratio", 1.5)),
                        sl_floor_atr=float((p or {}).get("sl_floor_atr", 1.0)),
                        max_spread_pts=int((p or {}).get("max_spread_pts", 50)))
    if not skills_on.get("burst", True):
        c["risk"]["burst_size"] = 1
    if not skills_on.get("smc", True):
        c["smc"] = dict(c.get("smc", {}), filter_mode="off")
    return c


# ------------------------------------------------------------------ perintah + ACK
def send_command(kind, source, **kw):
    """Dipakai web_server & CMD. -> id perintah."""
    cid = f"{int(time.time() * 1000)}_{kind}"
    write_json_atomic(CMD_DIR / f"{cid}.json", dict(kw, id=cid, kind=kind, source=source, ts=time.time()))
    return cid


def read_ack(cid):
    try:
        return json.loads((CMD_DIR / f"{cid}.ack.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def pending_commands():
    out = []
    for f in sorted(CMD_DIR.glob("*.json")):
        if f.name.endswith(".ack.json"):
            continue
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def handle(cmd, cfg, mode, mt5_ok):
    """Validasi + terapkan satu perintah. -> ack dict. Perubahan disimpan ke control_state.json."""
    ctl = read_control(cfg)
    old = copy.deepcopy(ctl)
    ack = {"id": cmd["id"], "kind": cmd["kind"], "ok": False, "warnings": [], "ts": time.time()}
    if time.time() - cmd.get("ts", 0) > 30:
        ack["msg"] = "perintah kedaluwarsa (>30 dtk) — tidak diterapkan"
    elif not mt5_ok:
        ack["msg"] = "MT5/bridge TERPUTUS — perubahan ditolak supaya dashboard, bot & MT5 tidak berbeda"
    elif cmd["kind"] == "strategy":
        strat = str(cmd.get("strategy", "")).lower()
        if strat in ("bb_reversion", "dark_venus", "smc_mtf", "default"):
            ctl["active_strategy"] = strat
            ack.update(ok=True, msg=f"strategi aktif diubah ke '{strat}' untuk entry berikutnya")
        else:
            ack["msg"] = f"strategi '{strat}' tidak dikenal (opsi: smc_mtf, bb_reversion, default)"
    elif cmd["kind"] == "profile":
        name, sym = str(cmd.get("profile", "")).upper(), cmd.get("symbol")
        if name not in NAMES and not (sym and name in ("", "DEFAULT")):
            ack["msg"] = f"profil '{name}' tidak dikenal"
        elif live_blocked(cfg, mode, name):
            ack["msg"] = "AGGRESSIVE DITOLAK di mode LIVE (set risk_profiles.allow_aggressive_live=true bila benar-benar disengaja)"
        else:
            if sym:
                if sym not in cfg["symbols"]:
                    ack["msg"] = f"simbol {sym} tidak ada di config"
                    return ack, old, ctl
                if name in ("", "DEFAULT"):
                    ctl["profile"]["per_symbol"].pop(sym, None)
                else:
                    ctl["profile"]["per_symbol"][sym] = name
            else:
                ctl["profile"]["global"] = name
            ack.update(ok=True, msg="profil diterapkan untuk entry berikutnya (posisi terbuka tetap parameter lama)")
    elif cmd["kind"] == "override":
        sym, mode = cmd.get("symbol"), str(cmd.get("mode", "")).upper()
        if sym not in cfg["symbols"] or sym not in (cmd.get("available") or cfg["symbols"]):
            ack["msg"] = f"simbol {sym} tidak ada / tidak tersedia di akun"
        elif mode not in ("AUTO", "RUN", "BLOCK"):
            ack["msg"] = f"mode '{mode}' tidak dikenal (AUTO/RUN/BLOCK)"
        else:
            if mode == "AUTO":
                ctl["override"].pop(sym, None)
            else:
                fr = cfg.get("force_run", {})
                until = force_run_until(cfg) if mode == "RUN" else None
                if mode == "RUN" and cmd.get("hours"):
                    until = min(until, time.time() + float(cmd["hours"]) * 3600)
                ctl["override"][sym] = {"mode": mode, "until": until, "set_at": time.time(),
                                        "lot_mult": fr.get("lot_mult", 0.5) if mode == "RUN" else 1.0}
                if mode == "RUN":
                    ack["warnings"].append(f"TETAP RUN hanya melewati anggaran harian & cooldown; lot x{fr.get('lot_mult', 0.5)}; "
                                           "rem rugi harian, HALT drawdown, margin, spread & batas posisi TETAP berlaku")
            ack.update(ok=True, msg=f"{sym} -> {dict(AUTO='OTOMATIS', RUN='TETAP RUN', BLOCK='TETAP BLOKIR')[mode]}"
                                    + (f" s/d {time.strftime('%d/%m %H:%M', time.localtime(ctl['override'][sym]['until']))}"
                                       if mode == "RUN" else ""))
    elif cmd["kind"] == "skill":
        sid, on = cmd.get("skill"), bool(cmd.get("on"))
        if sid not in sk.REGISTRY:
            ack["msg"] = f"skill '{sid}' tidak dikenal"
        else:
            s = dict(ctl["skills"])
            miss = [d for d in sk.REGISTRY[sid].get("deps", []) if on and not s.get(d)]
            if miss:
                ack["msg"] = f"butuh skill {', '.join(sk.REGISTRY[d]['name'] for d in miss)} ON dulu"
            else:
                s[sid] = on
                if not on:
                    dep = [d for d in sk.dependents(sid) if s.get(d)]
                    for d in dep:
                        s[d] = False
                    if dep:
                        ack["warnings"].append("ikut dimatikan (bergantung): " + ", ".join(sk.REGISTRY[d]["name"] for d in dep))
                    if sk.REGISTRY[sid].get("warn_off"):
                        ack["warnings"].append(sk.REGISTRY[sid]["warn_off"])
                ctl["skills"] = sk.normalize(s)
                ack.update(ok=True, msg=f"skill {sk.REGISTRY[sid]['name']} {'ON' if on else 'OFF'} (berlaku siklus berikut)")
    else:
        ack["msg"] = f"jenis perintah '{cmd['kind']}' tidak dikenal"
    if ack["ok"]:
        write_control(ctl)
    ack.update(profile=ctl["profile"] if ack["ok"] else old["profile"],
               skills=ctl["skills"] if ack["ok"] else old["skills"],
               override=ctl["override"] if ack["ok"] else old["override"])
    return ack, old, ctl
