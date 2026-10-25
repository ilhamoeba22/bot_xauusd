"""Filter sesi trading. Jam dalam UTC (server Exness = GMT+0).
Jadwal per simbol: symbols.<SYM>.sessions (menggantikan sessions global) + symbols.<SYM>.trade_days."""
DAY_NAMES = "Sen Sel Rab Kam Jum Sab Min".split()


def session_of(hour_utc: int, sessions: dict):
    for name, s in sessions.items():
        a, b = s["start_utc"], s["end_utc"]
        if (a <= hour_utc < b) if a < b else (hour_utc >= a or hour_utc < b):
            return name
    return None


def session_allowed(hour_utc: int, sessions: dict, weekday=None, days=None):
    """-> (boleh_entry, nama_sesi). weekday 0=Senin..6=Minggu; days = hari yang diizinkan (None = semua)."""
    name = session_of(hour_utc, sessions)
    ok = name is not None and sessions[name].get("enabled", True)
    # Pasar Forex/Gold buka untuk sesi Senin mulai Minggu 22:00 UTC (Senin 05:00 WIB)
    eff_weekday = 0 if (weekday == 6 and hour_utc >= 22) else weekday
    if ok and days is not None and eff_weekday is not None and eff_weekday not in days:
        return False, f"{name}, {DAY_NAMES[weekday]} tidak dijadwalkan"
    return ok, name


def symbol_session(cfg_sym, sym_conf, now):
    """Jadwal SATU simbol pada waktu now (datetime UTC): cfg_sym = sym_cfg(cfg, sym), sym_conf = cfg['symbols'][sym]."""
    return session_allowed(now.hour, cfg_sym["sessions"], now.weekday(), sym_conf.get("trade_days"))
