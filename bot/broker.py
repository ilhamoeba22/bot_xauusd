"""
Koneksi MT5 (dengan auto-reconnect) + dua pelaksana order dengan antarmuka sama:
  LiveExec  -> order sungguhan ke MT5 (mode demo / live)
  PaperExec -> posisi virtual, harga dari tick MT5 asli, TIDAK ada order terkirim
"""
import json
import logging
import time
from dataclasses import dataclass, asdict

import MetaTrader5 as mt5

from .common import PAPER_PATH

log = logging.getLogger("bot")

TF = {"M1": mt5.TIMEFRAME_M1, "M3": mt5.TIMEFRAME_M3, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15}
TF_SEC = {"M1": 60, "M3": 180, "M5": 300, "M15": 900}


# ------------------------------------------------------------ koneksi
class Connection:
    def __init__(self, mcfg):
        self.mcfg = mcfg
        self.ok = False
        self.reconnects = 0
        self.last_ok = 0.0
        self._backoff = 5
        self._next_try = 0.0

    def connect(self):
        kw = {}
        if self.mcfg.get("terminal_path"):
            kw["path"] = self.mcfg["terminal_path"]
        if self.mcfg.get("login"):
            kw["login"] = int(self.mcfg["login"])
            if self.mcfg.get("server"):
                kw["server"] = self.mcfg["server"]
        if not mt5.initialize(**kw):
            log.error(f"MT5    initialize gagal: {mt5.last_error()}")
            return False
        return self.check()

    def check(self):
        """Dipanggil tiap siklus. True kalau terminal hidup, terhubung ke server & akun terbaca."""
        ti = mt5.terminal_info()
        acc = mt5.account_info() if ti else None
        good = bool(ti and ti.connected and acc)
        if good:
            if not self.ok and self.reconnects:
                log.info(f"MT5    TERSAMBUNG KEMBALI (reconnect ke-{self.reconnects})")
            self.ok, self.last_ok, self._backoff = True, time.time(), 5
            return True
        if self.ok:
            log.warning(f"MT5    koneksi TERPUTUS (terminal={'ada' if ti else 'tidak ada'}, "
                        f"server={'ya' if ti and ti.connected else 'tidak'}) — mencoba menyambung ulang")
        self.ok = False
        if time.time() >= self._next_try:
            self.reconnects += 1
            log.warning(f"MT5    reconnect percobaan {self.reconnects} (jeda berikut {self._backoff}s)")
            mt5.shutdown()
            kw = {"path": self.mcfg["terminal_path"]} if self.mcfg.get("terminal_path") else {}
            if mt5.initialize(**kw):
                ti = mt5.terminal_info()
                if ti and ti.connected and mt5.account_info():
                    return self.check()
            self._next_try = time.time() + self._backoff
            self._backoff = min(self._backoff * 2, 60)
        return False


# ------------------------------------------------------------ util harga
def value_per_price_unit(sym):
    """Nilai 1,0 unit harga untuk 1,0 lot dalam MATA UANG AKUN (IDR/USD/USC).
    order_calc_profit dipakai karena otomatis benar untuk akun cent & non-USD."""
    t = mt5.symbol_info_tick(sym)
    px = t.ask if t and t.ask > 0 else None
    if px:
        v = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, 1.0, px, px + 1.0)
        if v and v > 0:
            return float(v)
    i = mt5.symbol_info(sym)
    return (i.trade_tick_value / i.trade_tick_size) if i and i.trade_tick_size else 0.0


def filling_mode(sym):
    i = mt5.symbol_info(sym)
    fm = getattr(i, "filling_mode", 0) if i else 0
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


@dataclass
class Pos:
    ticket: int
    symbol: str
    dir: int            # +1 buy, -1 sell
    volume: float
    price_open: float
    sl: float
    tp: float
    time: float         # epoch pembukaan
    profit: float = 0.0
    risk_dist: float = 0.0


@dataclass
class PendingOrder:
    ticket: int
    symbol: str
    order_type: int     # 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP
    volume: float
    price_open: float
    sl: float
    tp: float
    time: float
    comment: str = ""

    @property
    def type(self):
        return self.order_type


# ------------------------------------------------------------ LIVE / DEMO
class LiveExec:
    paper = False

    def __init__(self, magic, tag):
        self.magic, self.tag = magic, tag
        self.known = {}     # ticket -> Pos (untuk mendeteksi posisi yang tertutup broker)

    def positions(self, sym=None):
        raw = mt5.positions_get(symbol=sym) if sym else mt5.positions_get()
        out = []
        for p in raw or []:
            if p.magic != self.magic:
                continue
            out.append(Pos(p.ticket, p.symbol, 1 if p.type == 0 else -1, p.volume, p.price_open,
                           p.sl, p.tp, p.time, p.profit + p.swap))
        return out

    def pending_orders(self, sym=None):
        raw = mt5.orders_get(symbol=sym) if sym else mt5.orders_get()
        out = []
        for o in raw or []:
            if o.magic != self.magic:
                continue
            out.append(PendingOrder(o.ticket, o.symbol, o.type, o.volume_current,
                                    o.price_open, o.sl, o.tp, o.time_setup, getattr(o, "comment", "")))
        return out

    def open(self, sym, direction, lot, sl, tp, deviation, comment):
        t = mt5.symbol_info_tick(sym)
        price = t.ask if direction == 1 else t.bid
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL,
            "price": price, "sl": sl, "tp": tp, "deviation": deviation,
            "magic": self.magic, "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_mode(sym)})
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"ORDER  {sym} GAGAL retcode={getattr(res, 'retcode', None)} "
                      f"{getattr(res, 'comment', '')} {mt5.last_error()}")
            return None
        return res.order, res.price

    def open_pending(self, sym, order_type, price, lot, sl, tp, deviation=30, comment="", expiry_sec=None, tag="", **kwargs):
        comm = (comment or tag or "")[:31]
        req = {
            "action": mt5.TRADE_ACTION_PENDING, "symbol": sym, "volume": lot,
            "type": order_type, "price": price, "sl": sl, "tp": tp,
            "deviation": deviation, "magic": self.magic, "comment": comm,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode(sym)}
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"PENDING {sym} GAGAL retcode={getattr(res, 'retcode', None)} "
                      f"{getattr(res, 'comment', '')} {mt5.last_error()}")
            return None
        return res.order, price

    def cancel_pending(self, ticket):
        r = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket, "magic": self.magic})
        ok = bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)
        if not ok:
            log.warning(f"CANCEL_PENDING #{ticket} GAGAL retcode={getattr(r, 'retcode', None)} {mt5.last_error()}")
        return ok

    def modify_sl(self, p, sl):
        r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol,
                            "position": p.ticket, "sl": sl, "tp": p.tp})
        return bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)

    def close(self, p, why, deviation=30):
        t = mt5.symbol_info_tick(p.symbol)
        r = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
            "type": mt5.ORDER_TYPE_SELL if p.dir == 1 else mt5.ORDER_TYPE_BUY,
            "position": p.ticket, "price": t.bid if p.dir == 1 else t.ask,
            "deviation": deviation, "magic": self.magic, "comment": f"{self.tag}-{why}"[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_mode(p.symbol)})
        ok = bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)
        if not ok:
            log.error(f"CLOSE  #{p.ticket} GAGAL retcode={getattr(r, 'retcode', None)} {mt5.last_error()}")
        return ok

    def update(self):
        pass    # SL/TP dieksekusi oleh server broker

    def poll_closed(self):
        """Posisi yang hilang sejak siklus lalu -> ambil P/L riil (termasuk komisi & swap) dari history."""
        now = {p.ticket: p for p in self.positions()}
        closed = []
        for tk, p in list(self.known.items()):
            if tk in now:
                continue
            deals = mt5.history_deals_get(position=tk) or []
            if not deals:
                continue     # history belum tersedia, coba siklus berikut
            pnl = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
            outs = [d for d in deals if d.entry in (1, 3)]
            exit_px = outs[-1].price if outs else 0.0
            why = outs[-1].comment if outs else ""
            closed.append({"ticket": tk, "symbol": p.symbol, "dir": p.dir, "volume": p.volume,
                           "open": p.price_open, "close": exit_px, "pnl": pnl,
                           "open_time": p.time, "close_time": time.time(), "why": why})
            del self.known[tk]
        for tk, p in now.items():
            self.known.setdefault(tk, p)
        return closed

    def equity(self, acc):
        return acc.equity, acc.balance


# ------------------------------------------------------------ PAPER
class PaperExec:
    """Posisi virtual. SL/TP diperiksa terhadap bid/ask asli tiap siklus manage.
    Saldo virtual dimulai dari equity akun saat pertama kali dijalankan."""
    paper = True

    def __init__(self, magic, tag, commission_of):
        self.magic, self.tag = magic, tag
        self.commission_of = commission_of      # fn(sym) -> biaya komisi dalam satuan harga
        self.pos, self.balance, self._next = [], None, 1
        self.pending = []
        self._closed = []
        try:
            d = json.loads(PAPER_PATH.read_text(encoding="utf-8"))
            self.pos = [Pos(**p) for p in d["positions"]]
            self.balance, self._next = d["balance"], d["next"]
            if "pending" in d:
                self.pending = [PendingOrder(**p) for p in d["pending"]]
            log.info(f"PAPER  dipulihkan: saldo {self.balance:,.2f}, {len(self.pos)} posisi virtual, {len(self.pending)} pending")
        except Exception:
            pass

    def _save(self):
        PAPER_PATH.write_text(json.dumps({"balance": self.balance, "next": self._next,
                                          "positions": [asdict(p) for p in self.pos],
                                          "pending": [asdict(p) for p in self.pending]}), encoding="utf-8")

    def init_balance(self, eq):
        if self.balance is None:
            self.balance = eq
            log.info(f"PAPER  saldo virtual awal = equity akun {eq:,.2f}")
            self._save()

    def _pnl(self, p, price):
        typ = mt5.ORDER_TYPE_BUY if p.dir == 1 else mt5.ORDER_TYPE_SELL
        gross = mt5.order_calc_profit(typ, p.symbol, p.volume, p.price_open, price) or 0.0
        comm = self.commission_of(p.symbol) * value_per_price_unit(p.symbol) * p.volume
        return gross - comm

    def positions(self, sym=None):
        out = []
        for p in self.pos:
            if sym and p.symbol != sym:
                continue
            t = mt5.symbol_info_tick(p.symbol)
            if t:
                p.profit = self._pnl(p, t.bid if p.dir == 1 else t.ask)
            out.append(p)
        return out

    def pending_orders(self, sym=None):
        if sym:
            return [p for p in self.pending if p.symbol == sym]
        return list(self.pending)

    def open(self, sym, direction, lot, sl, tp, deviation, comment):
        t = mt5.symbol_info_tick(sym)
        if not t:
            return None
        price = t.ask if direction == 1 else t.bid
        tk = self._next
        self._next += 1
        self.pos.append(Pos(tk, sym, direction, lot, price, sl, tp, time.time()))
        self._save()
        return tk, price

    def open_pending(self, sym, order_type, price, lot, sl, tp, deviation=0, comment="", expiry_sec=None, tag="", **kwargs):
        tk = self._next
        self._next += 1
        comm = (comment or tag or "")[:31]
        p = PendingOrder(tk, sym, order_type, lot, price, sl, tp, time.time(), comm)
        self.pending.append(p)
        self._save()
        return tk, price

    def cancel_pending(self, ticket):
        b4 = len(self.pending)
        self.pending = [p for p in self.pending if p.ticket != ticket]
        if len(self.pending) != b4:
            self._save()
            return True
        return False

    def modify_sl(self, p, sl):
        p.sl = sl
        self._save()
        return True

    def close(self, p, why, deviation=0, price=None):
        t = mt5.symbol_info_tick(p.symbol)
        if price is None:
            if not t:
                return False
            price = t.bid if p.dir == 1 else t.ask
        pnl = self._pnl(p, price)
        self.balance += pnl
        self.pos = [x for x in self.pos if x.ticket != p.ticket]
        self._closed.append({"ticket": p.ticket, "symbol": p.symbol, "dir": p.dir, "volume": p.volume,
                             "open": p.price_open, "close": price, "pnl": pnl,
                             "open_time": p.time, "close_time": time.time(), "why": why})
        self._save()
        return True

    def update(self):
        """Simulasikan eksekusi SL/TP dan pending orders oleh broker."""
        # Eksekusi pending orders
        for po in list(self.pending):
            t = mt5.symbol_info_tick(po.symbol)
            if not t or t.bid <= 0 or t.ask <= 0:
                continue
            triggered, direction = False, 1
            if po.order_type == mt5.ORDER_TYPE_BUY_LIMIT and t.ask <= po.price_open:
                triggered, direction = True, 1
            elif po.order_type == mt5.ORDER_TYPE_SELL_LIMIT and t.bid >= po.price_open:
                triggered, direction = True, -1
            elif po.order_type == mt5.ORDER_TYPE_BUY_STOP and t.ask >= po.price_open:
                triggered, direction = True, 1
            elif po.order_type == mt5.ORDER_TYPE_SELL_STOP and t.bid <= po.price_open:
                triggered, direction = True, -1

            if triggered:
                self.pending = [p for p in self.pending if p.ticket != po.ticket]
                self.pos.append(Pos(po.ticket, po.symbol, direction, po.volume, po.price_open, po.sl, po.tp, time.time()))
                self._save()
                log.info(f"PAPER  PENDING #{po.ticket} ({po.comment}) TERPICU @ {po.price_open}")

        # Eksekusi SL / TP posisi
        for p in list(self.pos):
            t = mt5.symbol_info_tick(p.symbol)
            if not t or t.bid <= 0:
                continue
            px = t.bid if p.dir == 1 else t.ask
            if p.sl and ((p.dir == 1 and px <= p.sl) or (p.dir == -1 and px >= p.sl)):
                self.close(p, "sl", price=px)
            elif p.tp and ((p.dir == 1 and px >= p.tp) or (p.dir == -1 and px <= p.tp)):
                self.close(p, "tp", price=px)

    def poll_closed(self):
        c, self._closed = self._closed, []
        return c

    def equity(self, acc):
        return self.balance + sum(p.profit for p in self.positions()), self.balance
