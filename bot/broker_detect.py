"""
Auto-deteksi profil broker dan simbol dari koneksi MT5 yang aktif.

Sistem ini bekerja 100% OTOMATIS:
  1. Membaca company & server dari MT5 (account_info).
  2. Mengenali broker (Exness, IC Markets, Pepperstone, RoboForex, FBS, XM, FP Markets, OctaFX, dll).
  3. Mendeteksi simbol emas yang benar-benar ada di broker tersebut
     (XAUUSD, GOLD, XAUUSD.raw, XAUUSD.ecn, XAUUSD+, XAUUSDm, XAUUSD247, dll).
  4. Menghitung komisi aktual (dari database profil atau taksiran history deals).
  5. Menyesuaikan konfigurasi bot secara dinamis tanpa perlu edit config.json manual!
"""
import logging
import re
import time

import MetaTrader5 as mt5

log = logging.getLogger("bot")

# ------------------------------------------------------------------ database broker
# Format: (pattern_company, pattern_server) -> (nama_tampil, komisi_usd_per_lot, spread_points)
KNOWN_BROKERS = [
    # Exness
    (r"exness",        r"zero",     "Exness Zero",          3.5,  15),
    (r"exness",        r"standard", "Exness Standard",      0.0,  50),
    (r"exness",        r"ecn|pro",  "Exness Pro",           3.5,  15),
    (r"exness",        None,        "Exness",               2.0,  20),

    # IC Markets
    (r"icmarket|ic market",  r"raw", "IC Markets Raw",      7.0,  20),
    (r"icmarket|ic market",  None,   "IC Markets",          7.0,  20),

    # Pepperstone
    (r"pepperstone",   r"razor",    "Pepperstone Razor",    7.0,  18),
    (r"pepperstone",   r"standard", "Pepperstone Standard", 0.0,  70),
    (r"pepperstone",   None,        "Pepperstone",          5.0,  25),

    # RoboForex
    (r"roboforex|rforex", r"pro",   "RoboForex Pro",        0.0,  25),
    (r"roboforex|rforex", r"ecn",   "RoboForex ECN",        5.0,  15),
    (r"roboforex|rforex", r"prime", "RoboForex Prime",      4.0,  12),
    (r"roboforex|rforex", None,     "RoboForex",            0.0,  25),

    # FBS
    (r"fbs",           r"zero",     "FBS Zero",             5.0,  10),
    (r"fbs",           None,        "FBS Standard",         0.0, 100),

    # XM
    (r"xm\b|xm ",      r"zero|ultra", "XM Ultra Low / Zero", 3.5, 12),
    (r"xm\b|xm ",      None,        "XM Standard",          0.0,  45),

    # FP Markets
    (r"fpmarket|fp market", r"raw", "FP Markets Raw",       6.0,  15),
    (r"fpmarket|fp market", None,   "FP Markets",           0.0,  35),

    # Lainnya
    (r"axitrader|axi\b|axi ", None, "Axi Prime",            6.0,  18),
    (r"admiralmarket|admiral market", r"zero", "Admiral Zero", 3.0, 12),
    (r"admiralmarket|admiral market", None, "Admiral",      0.0,  40),
    (r"octafx|octa\b", None,        "OctaFX",               0.0,  30),
    (r"hotforex|hfmarket", r"zero", "HFM Zero",             4.0,  15),
    (r"hotforex|hfmarket", None,    "HFM Standard",         0.0,  60),
    (r"fxpro",         r"raw",      "FxPro Raw",            4.5,  20),
    (r"fxpro",         None,        "FxPro",                0.0,  50),
    (r"tickmill",      r"pro",      "Tickmill Pro",         4.0,  15),
    (r"tickmill",      None,        "Tickmill",             0.0,  35),
    (r"amarkets",      None,        "AMarkets",             3.0,  20),
]


def _available_gold_symbols():
    """
    Cari semua simbol emas yang ada di broker ini.
    Mendukung berbagai format: XAUUSD, GOLD, XAUUSD.raw, XAUUSD_pro, XAUUSD+, XAUUSDm, dsb.
    """
    raw_symbols = mt5.symbols_get() or []
    all_names = [s.name for s in raw_symbols]

    candidates = []
    for name in all_names:
        upper = name.upper()
        # Cari simbol yang mengandung XAU atau GOLD dan berbasis USD
        if "XAU" in upper or "GOLD" in upper:
            candidates.append(name)

    # Sort prioritas:
    # 1. Exact 'XAUUSD'
    # 2. Exact 'GOLD'
    # 3. Simbol berakhiran USD atau mengandung XAUUSD
    # 4. Sisanya (mis. XAUEUR, XAUAUD)
    def priority(sym):
        u = sym.upper()
        if u == "XAUUSD":
            return (0, len(sym))
        if u == "GOLD":
            return (1, len(sym))
        if "XAUUSD" in u:
            return (2, len(sym))
        if "GOLD" in u and "USD" in u:
            return (3, len(sym))
        if "XAU" in u and "USD" in u:
            return (4, len(sym))
        return (5, len(sym))

    candidates.sort(key=priority)
    return candidates


def _estimate_commission_from_history(primary_sym="XAUUSD"):
    """Taksir komisi per lot dari riwayat deals 30 hari terakhir."""
    try:
        now = int(time.time())
        deals = mt5.history_deals_get(now - 86400 * 30, now) or []
        gold_deals = [d for d in deals if (primary_sym in d.symbol or "XAU" in d.symbol)
                      and d.commission != 0 and d.volume > 0]
        if not gold_deals:
            return 0.0
        total_comm = abs(sum(d.commission for d in gold_deals))
        total_vol = sum(d.volume for d in gold_deals)
        per_lot = total_comm / total_vol if total_vol else 0.0
        log.info(f"BROKER auto-detect komisi dari history ({len(gold_deals)} deals): ${per_lot:.2f}/lot")
        return round(per_lot, 2)
    except Exception as e:
        log.warning(f"BROKER gagal taksir komisi history: {e}")
        return 0.0


def detect_broker():
    """
    Deteksi broker & simbol dari sesi MT5 yang aktif.

    Return:
      dict berisi nama broker, komisi per lot, spread tipikal, simbol emas tersedia & utama.
    """
    acc = mt5.account_info()
    if not acc:
        log.warning("BROKER auto-detect: MT5 account_info belum tersedia.")
        return None

    company = (acc.company or "").lower()
    server = (acc.server or "").lower()

    # 1. Deteksi profil dari database
    matched = None
    for (pat_co, pat_sv, name, comm, spread) in KNOWN_BROKERS:
        if re.search(pat_co, company):
            if pat_sv is None or re.search(pat_sv, server):
                matched = (name, comm, spread)
                break

    # 2. Deteksi simbol emas yang tersedia di broker ini
    gold_syms = _available_gold_symbols()
    primary = gold_syms[0] if gold_syms else "XAUUSD"

    # Pastikan simbol utama terpilih di Market Watch
    try:
        mt5.symbol_select(primary, True)
    except Exception:
        pass

    # Baca spread live saat ini jika simbol tersedia
    live_spread = 25
    tick = mt5.symbol_info_tick(primary)
    info = mt5.symbol_info(primary)
    if tick and info and info.point and tick.ask > tick.bid:
        live_spread = round((tick.ask - tick.bid) / info.point)

    if matched:
        name, comm, typical_spread = matched
        log.info(f"BROKER OTOMATIS: {name} (server: {acc.server}) | Komisi: ${comm:.1f}/lot | "
                 f"Spread live: {live_spread}pts (tipikal ~{typical_spread}pts) | Simbol emas: {gold_syms}")
        return {
            "name": name,
            "commission_per_lot_usd": comm,
            "typical_spread_xauusd": live_spread if live_spread > 0 else typical_spread,
            "available_gold_symbols": gold_syms,
            "primary_gold_symbol": primary,
            "detected": True,
            "company": acc.company,
            "server": acc.server,
            "currency": acc.currency,
        }

    # Jika broker belum ada di database, taksir komisi dari history deals
    comm_hist = _estimate_commission_from_history(primary)
    detected_name = f"{acc.company} ({acc.server})"
    log.info(f"BROKER OTOMATIS (Generik): {detected_name} | Komisi taksiran: ${comm_hist:.2f}/lot | "
             f"Spread live: {live_spread}pts | Simbol emas: {gold_syms}")
    return {
        "name": detected_name,
        "commission_per_lot_usd": comm_hist,
        "typical_spread_xauusd": live_spread,
        "available_gold_symbols": gold_syms,
        "primary_gold_symbol": primary,
        "detected": False,
        "company": acc.company,
        "server": acc.server,
        "currency": acc.currency,
    }


def apply_broker_profile(cfg, broker_info):
    """
    Terapkan hasil auto-deteksi ke konfigurasi bot secara in-place.
    Tidak perlu edit config manual saat berganti broker/akun MT5.
    """
    if not broker_info:
        return

    b = cfg.setdefault("broker", {})
    auto = b.get("auto_detect", True)
    if b.get("name") in ("", "auto", None):
        auto = True

    # 1. Update informasi broker di config
    if auto:
        b["name"] = broker_info["name"]
        b["commission_per_lot_usd"] = broker_info["commission_per_lot_usd"]
        b["typical_spread_xauusd"] = broker_info["typical_spread_xauusd"]
    else:
        # Mode manual: hanya isi jika user mengosongkannya
        if not b.get("name"):
            b["name"] = broker_info["name"]

    b["primary_gold_symbol"] = broker_info["primary_gold_symbol"]
    b["available_gold_symbols"] = broker_info["available_gold_symbols"]
    b["_auto_detected"] = broker_info["detected"]

    # 2. Komisi per oz untuk perhitungan backtest / simulasi (1 lot = 100 oz)
    comm_per_oz = broker_info["commission_per_lot_usd"] / 100.0

    available_set = set(broker_info["available_gold_symbols"])
    primary = broker_info["primary_gold_symbol"]

    # 3. Sinkronkan simbol di cfg['symbols']
    # Jika simbol di config tidak ada di broker aktif (mis. XAUUSD247 atau XAUUSDc di RoboForex/IC Markets),
    # matikan secara otomatis agar tidak error!
    for sym, scfg in list(cfg.get("symbols", {}).items()):
        is_gold = "XAU" in sym.upper() or "GOLD" in sym.upper()
        if not is_gold:
            continue

        scfg["commission_price_units"] = round(comm_per_oz, 4)

        if sym not in available_set:
            if scfg.get("enabled", False):
                scfg["enabled"] = False
                log.info(f"BROKER simbol '{sym}' tidak tersedia di broker {broker_info['name']} — dinonaktifkan otomatis")

    # 4. Pastikan simbol emas utama broker aktif di config
    if primary in cfg.get("symbols", {}):
        cfg["symbols"][primary]["enabled"] = True
    else:
        # Jika broker menggunakan nama unik (misal 'GOLD' atau 'XAUUSD.raw' atau 'XAUUSD_i')
        # Buat entri otomatis dengan menduplikasi template dari 'XAUUSD'
        template = cfg.get("symbols", {}).get("XAUUSD", {}).copy()
        template["enabled"] = True
        template["_auto_created"] = True
        cfg.setdefault("symbols", {})[primary] = template
        log.info(f"BROKER simbol utama '{primary}' didaftarkan otomatis ke bot dari {broker_info['name']}")

    # 5. Sinkronkan ke symbols_active.json agar status dashboard/konsol sinkron
    try:
        from .common import read_active, write_active
        act = read_active(cfg)
        changed = False
        for s in list(act.keys()):
            if s not in available_set and act[s]:
                act[s] = False
                changed = True
        if primary in act and not act[primary]:
            act[primary] = True
            changed = True
        if changed:
            write_active(act)
    except Exception:
        pass

    log.info(f"BROKER siap: {broker_info['name']} | Simbol aktif: "
             f"{[s for s, c in cfg['symbols'].items() if c.get('enabled')]}")

