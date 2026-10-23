"""News Trading Engine (NTE) untuk XAUUSD & FX.

Menangani rilis berita High-Impact dengan strategi:
1. STRADDLE (Breakout): Buy Stop & Sell Stop di luar rentang ATR menjelang rilis.
2. LIMIT REBOUND (S/R Reversal): Buy Limit & Sell Limit pada level kunci S/R atau ekstrem ATR.
3. SMART ADAPTIVE: Otomatis memilih Straddle (bila konsolidasi/squeeze) atau Limit Rebound (bila dekat S/R).

Fitur Keamanan:
- OCO (One Cancels Other): Begitu salah satu pending order tersentuh/terisi, order lawan langsung dibatalkan.
- Auto-Expiry: Pending order yang tidak tersentuh dalam N menit setelah berita dirilis otomatis dibatalkan.
- Max Spread Filter: Menolak atau membatalkan order jika spread melonjak saat detik-detik rilis berita.
- Full Manual Override: Tombol ON/OFF di dashboard & control_state.json untuk kendali penuh pengguna.
"""
import copy
import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from .common import LOG_DIR
from .analysis import newsimpact, sr_mtf

log = logging.getLogger("bot")

DEFAULT_CONFIG = {
    "enabled": True,
    "strategy": "smart_adaptive",    # "smart_adaptive", "straddle", "limit_rebound"
    "minutes_before": 2,             # pasang pending order 2 menit sebelum rilis
    "cancel_after_min": 5,           # batalkan order jika tidak terisi 5 menit setelah rilis
    "min_seconds_before": 15,        # jangan pasang jika waktu tinggal < 15 detik (menghindari slippage ekstrim awal)
    "straddle_distance_atr": 1.2,    # jarak Buy/Sell Stop dari harga (x ATR)
    "limit_rebound_atr": 2.0,        # jarak Buy/Sell Limit dari harga (x ATR)
    "sl_atr": 1.5,                   # Stop Loss (x ATR)
    "tp_atr": 3.0,                   # Take Profit (x ATR)
    "max_spread_points": 70,         # batas spread maksimal untuk XAUUSD
    "oco": True,                     # One-Cancels-Other
    "tiers": ["High"],               # dampak berita yang diproses
    "currencies": ["USD"]
}


class NewsTradingEngine:
    def __init__(self, cfg, ex):
        self.cfg = cfg
        self.ex = ex
        self.active_pendings = {}    # sym -> dict info order aktif
        self.traded_events = set()   # event_id yang sudah ditangani
        self.status = "IDLE"
        self.last_event_info = None

    def _nte_cfg(self):
        return dict(DEFAULT_CONFIG, **(self.cfg.get("news_trading") or {}))

    def get_upcoming_event(self, sym):
        """Cari berita kalender High-Impact terdekat untuk simbol ini."""
        c = self._nte_cfg()
        ni_cfg = dict(self.cfg.get("news_impact", {}), symbols=list(self.cfg.get("symbols", {}).keys()))
        now = time.time()
        # Ambil event kalender dari rentang 1 jam lalu s/d 4 jam ke depan
        evs = newsimpact.events_for(sym, ni_cfg, now - 3600, now + 14400, with_reaction=False)
        for e in evs:
            if e.get("kind") == "kalender" and e.get("tier") in c.get("tiers", ["High"]):
                return e
        return None

    def process(self, sym, C, eq, atr, ask, bid, spread, calc_lot_fn, skill_on=False):
        """Dipanggil tiap siklus scan bot."""
        c = self._nte_cfg()
        now = time.time()

        # 1. Jika skill OFF, batalkan semua pending order aktif NTE
        if not skill_on:
            if sym in self.active_pendings:
                self._cancel_all(sym, "News Trading Engine dimatikan oleh user")
            self.status = "OFF"
            return

        # 2. Kelola pending orders yang sedang berjalan (OCO, expiry, spread spike)
        if sym in self.active_pendings:
            self._manage_active(sym, spread, c)
            return

        # 3. Cari event berita yang akan datang
        e = self.get_upcoming_event(sym)
        if not e:
            self.status = "IDLE (menunggu jadwal berita)"
            return

        ev_id = f"{e['t']}_{e['title']}"
        self.last_event_info = {
            "title": e["title"],
            "tier": e["tier"],
            "time_utc": datetime.fromtimestamp(e["t"], timezone.utc).strftime("%H:%M:%S"),
            "seconds_left": int(e["t"] - now)
        }

        if ev_id in self.traded_events:
            self.status = f"SELESAI (berita {e['title']} sudah ditangani)"
            return

        dt = e["t"] - now
        window_sec = c.get("minutes_before", 2) * 60
        min_sec = c.get("min_seconds_before", 15)

        # Di luar jendela eksekusi
        if dt > window_sec:
            self.status = f"SIAP ({e['title']} dalam {int(dt//60)}m {int(dt%60)}s)"
            return
        elif dt < -c.get("cancel_after_min", 5) * 60:
            self.status = "IDLE (berita lewat)"
            return
        elif dt < min_sec and dt > 0:
            # Terlalu mepet (< 15 detik), hindari slippage spread meledak sebelum order terpasang
            self.status = f"STANDBY (terlalu dekat rilis: {int(dt)}s)"
            return
        elif dt <= 0:
            # Berita sudah rilis dan belum sempat pasang
            self.status = "LEWAT (berita sudah rilis)"
            self.traded_events.add(ev_id)
            return

        # 4. Dalam jendela eksekusi [T - window_sec s/d T - min_sec] -> Pasang pending order!
        if spread > c.get("max_spread_points", 70):
            log.warning(f"NEWS_ENG {sym} batal pasang pending order: spread {spread} > {c['max_spread_points']}")
            self.status = f"DITAHAN: spread {spread} terlalu lebar"
            return

        strat = c.get("strategy", "smart_adaptive")
        if strat == "smart_adaptive":
            # Periksa apakah harga sedang konsolidasi atau dekat level S/R
            strat = self._select_adaptive_strategy(sym, ask, bid, atr)

        self._arm_orders(sym, C, eq, atr, ask, bid, spread, calc_lot_fn, e, strat, c)

    def _select_adaptive_strategy(self, sym, ask, bid, atr):
        """Memilih antara Straddle (Breakout) dan Limit Rebound (S/R)."""
        px = (ask + bid) / 2.0
        try:
            srl = sr_mtf.compute(sym, self.cfg.get("sr"), px)
            zones = srl.get("zones", [])
            for z in zones:
                # Jika harga saat ini berada sangat dekat dengan batas zona S/R kuat (< 0.75 ATR)
                if abs(px - z.get("lo", 0)) < 0.75 * atr or abs(px - z.get("hi", 0)) < 0.75 * atr:
                    return "limit_rebound"
        except Exception:
            pass
        # Default: Straddle Breakout adalah strategi berita paling umum & terbukti
        return "straddle"

    def _arm_orders(self, sym, C, eq, atr, ask, bid, spread, calc_lot_fn, event, strat, c):
        """Memasang sepasang pending order dengan proteksi OCO."""
        info = mt5.symbol_info(sym)
        digits = getattr(info, "digits", 2) if info else 2

        sl_dist = c.get("sl_atr", 1.5) * atr
        tp_dist = c.get("tp_atr", 3.0) * atr

        lot = calc_lot_fn(sl_dist)
        if not lot or lot <= 0:
            log.warning(f"NEWS_ENG {sym} lot tidak valid ({lot})")
            return

        orders = []
        if strat == "straddle":
            # BUY STOP di atas pasar, SELL STOP di bawah pasar
            dist = c.get("straddle_distance_atr", 1.2) * atr
            p_buy = round(ask + dist, digits)
            sl_buy = round(p_buy - sl_dist, digits)
            tp_buy = round(p_buy + tp_dist, digits)

            p_sell = round(bid - dist, digits)
            sl_sell = round(p_sell + sl_dist, digits)
            tp_sell = round(p_sell - tp_dist, digits)

            res_b = self.ex.open_pending(sym, mt5.ORDER_TYPE_BUY_STOP, p_buy, lot, sl_buy, tp_buy,
                                        deviation=30, comment="NTE-STRAD-B")
            res_s = self.ex.open_pending(sym, mt5.ORDER_TYPE_SELL_STOP, p_sell, lot, sl_sell, tp_sell,
                                        deviation=30, comment="NTE-STRAD-S")

            if res_b:
                orders.append({"ticket": res_b[0], "type": "BUY_STOP", "type_int": mt5.ORDER_TYPE_BUY_STOP,
                               "price": p_buy, "sl": sl_buy, "tp": tp_buy, "lot": lot})
            if res_s:
                orders.append({"ticket": res_s[0], "type": "SELL_STOP", "type_int": mt5.ORDER_TYPE_SELL_STOP,
                               "price": p_sell, "sl": sl_sell, "tp": tp_sell, "lot": lot})

        else: # limit_rebound
            # BUY LIMIT di bawah pasar, SELL LIMIT di atas pasar
            dist = c.get("limit_rebound_atr", 2.0) * atr
            p_buy = round(bid - dist, digits)
            sl_buy = round(p_buy - sl_dist, digits)
            tp_buy = round(p_buy + tp_dist, digits)

            p_sell = round(ask + dist, digits)
            sl_sell = round(p_sell + sl_dist, digits)
            tp_sell = round(p_sell - tp_dist, digits)

            res_b = self.ex.open_pending(sym, mt5.ORDER_TYPE_BUY_LIMIT, p_buy, lot, sl_buy, tp_buy,
                                        deviation=30, comment="NTE-REBND-B")
            res_s = self.ex.open_pending(sym, mt5.ORDER_TYPE_SELL_LIMIT, p_sell, lot, sl_sell, tp_sell,
                                        deviation=30, comment="NTE-REBND-S")

            if res_b:
                orders.append({"ticket": res_b[0], "type": "BUY_LIMIT", "type_int": mt5.ORDER_TYPE_BUY_LIMIT,
                               "price": p_buy, "sl": sl_buy, "tp": tp_buy, "lot": lot})
            if res_s:
                orders.append({"ticket": res_s[0], "type": "SELL_LIMIT", "type_int": mt5.ORDER_TYPE_SELL_LIMIT,
                               "price": p_sell, "sl": sl_sell, "tp": tp_sell, "lot": lot})

        if len(orders) >= 1:
            ev_id = f"{event['t']}_{event['title']}"
            self.active_pendings[sym] = {
                "event_id": ev_id,
                "event_title": event["title"],
                "event_t": event["t"],
                "strategy": strat,
                "armed_at": time.time(),
                "orders": orders
            }
            self.traded_events.add(ev_id)
            self.status = f"ARMED ({strat.upper()}: {len(orders)} order aktif)"
            log.info(f"NEWS_ENG {sym} ARMED! Memasang {len(orders)} pending order ({strat.upper()}) "
                     f"untuk '{event['title']}' rilis jam {datetime.fromtimestamp(event['t'], timezone.utc):%H:%M} UTC")
        else:
            log.error(f"NEWS_ENG {sym} gagal memasang pending orders")
            self.status = "GAGAL pasang pending orders"

    def _manage_active(self, sym, spread, c):
        """Memantau order aktif: OCO cancellation, auto-expiry, & spread spike."""
        info = self.active_pendings[sym]
        orders = info.get("orders", [])
        now = time.time()

        # 1. Spread spike protection saat pending order aktif
        if spread > c.get("max_spread_points", 70):
            log.warning(f"NEWS_ENG {sym} SPREAD SPIKE ({spread} > {c['max_spread_points']})! Membatalkan pending order.")
            self._cancel_all(sym, "Spread melonjak ekstrim")
            return

        # 2. Expiry check (melewati N menit setelah rilis berita)
        if now > info["event_t"] + c.get("cancel_after_min", 5) * 60:
            log.info(f"NEWS_ENG {sym} EXPIRY: Berita '{info['event_title']}' telah lewat {c.get('cancel_after_min', 5)}m.")
            self._cancel_all(sym, "Kedaluwarsa (unfilled)")
            return

        # 3. OCO (One-Cancels-Other) check:
        # Periksa apakah salah satu order sudah terisi menjadi posisi terbuka
        curr_pendings = {p.ticket: p for p in self.ex.pending_orders(sym)}
        positions = self.ex.positions(sym)

        # Cari tiket yang terisi
        filled_order = None
        remaining_orders = []

        for o in orders:
            tk = o["ticket"]
            # Jika tiket tidak ada lagi di pending_orders
            if tk not in curr_pendings:
                # Cek apakah ada posisi terbuka terkait
                pos_match = [p for p in positions if getattr(p, "ticket", 0) == tk or "NTE-" in getattr(p, "comment", "")]
                if pos_match or not self.ex.paper:
                    filled_order = o
            else:
                remaining_orders.append(o)

        # Jika salah satu terisi dan OCO aktif -> batalkan sisa pending order lawan
        if filled_order and remaining_orders and c.get("oco", True):
            log.info(f"NEWS_ENG [OCO] Order #{filled_order['ticket']} ({filled_order['type']}) TERISI! "
                     f"Membatalkan {len(remaining_orders)} pending order lawan.")
            for ro in remaining_orders:
                self.ex.cancel_pending(ro["ticket"])
            self.active_pendings.pop(sym, None)
            self.status = f"TEREKSEKUSI ({filled_order['type']} @ {filled_order['price']})"
            return

        # Jika semua pending orders hilang (terisi atau dibatalkan dari MT5 langsung)
        if not remaining_orders and not filled_order:
            self.active_pendings.pop(sym, None)
            self.status = "SELESAI"
            return

        self.status = f"ARMED ({info['strategy'].upper()} - menunggu rilis / trigger)"

    def _cancel_all(self, sym, reason):
        """Batalkan semua pending orders untuk simbol ini."""
        info = self.active_pendings.pop(sym, None)
        if not info:
            return
        for o in info.get("orders", []):
            try:
                self.ex.cancel_pending(o["ticket"])
            except Exception as e:
                log.warning(f"NEWS_ENG gagal cancel #{o['ticket']}: {e}")
        log.info(f"NEWS_ENG {sym} SEMUA PENDING DIBATALKAN: {reason}")
        self.status = f"DIBATALKAN ({reason})"

    def state(self):
        """Data status untuk dikirim ke Web Dashboard & logs."""
        c = self._nte_cfg()
        pend_list = []
        for sym, dat in self.active_pendings.items():
            for o in dat.get("orders", []):
                pend_list.append({
                    "symbol": sym,
                    "ticket": o["ticket"],
                    "type": o["type"],
                    "price": o["price"],
                    "sl": o["sl"],
                    "tp": o["tp"],
                    "lot": o["lot"],
                    "strategy": dat.get("strategy")
                })
        return {
            "status": self.status,
            "strategy": c.get("strategy", "smart_adaptive"),
            "active_orders": pend_list,
            "upcoming": self.last_event_info,
            "config": {
                "minutes_before": c.get("minutes_before", 2),
                "cancel_after_min": c.get("cancel_after_min", 5),
                "max_spread": c.get("max_spread_points", 70),
                "straddle_dist_atr": c.get("straddle_distance_atr", 1.2),
                "limit_rebound_atr": c.get("limit_rebound_atr", 2.0)
            }
        }
