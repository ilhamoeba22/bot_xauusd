"""
Filter berita high-impact dari kalender ekonomi (feed JSON ForexFactory/FairEconomy).
Tanpa API key. Feed hanya berisi minggu berjalan, jadi di-cache ke logs/news_cache.json
dan diunduh ulang tiap `refresh_hours` (feed membatasi frekuensi unduh).
"""
import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from .common import NEWS_CACHE

log = logging.getLogger("bot")


class NewsFilter:
    def __init__(self):
        self.events = []        # list[(datetime_utc, currency, impact, title)]
        self.fetched_at = 0.0
        self.ok = False
        self._load_cache()

    def _parse(self, raw):
        ev = []
        for e in raw:
            try:
                dt = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
                ev.append((dt, e.get("country", ""), e.get("impact", ""), e.get("title", "")))
            except Exception:
                continue
        return ev

    def _load_cache(self):
        try:
            c = json.loads(NEWS_CACHE.read_text(encoding="utf-8"))
            self.events, self.fetched_at, self.ok = self._parse(c["events"]), c["fetched_at"], True
        except Exception:
            pass

    def refresh(self, cfg):
        if time.time() - self.fetched_at < cfg.get("refresh_hours", 6) * 3600 and self.ok:
            return
        try:
            req = urllib.request.Request(cfg["url"], headers={"User-Agent": "Mozilla/5.0 xauusd-bot"})
            raw = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
            self.events, self.fetched_at, self.ok = self._parse(raw), time.time(), True
            NEWS_CACHE.write_text(json.dumps({"fetched_at": self.fetched_at, "events": raw}), encoding="utf-8")
            n = sum(1 for e in self.events if e[1] in cfg["currencies"] and e[2] in cfg["impacts"])
            log.info(f"NEWS   kalender diperbarui: {n} berita {'/'.join(cfg['impacts'])} {'/'.join(cfg['currencies'])} minggu ini")
        except Exception as e:
            # jangan coba lagi tiap siklus — tunggu 15 menit
            self.fetched_at = time.time() - cfg.get("refresh_hours", 6) * 3600 + 900
            log.warning(f"NEWS   gagal unduh kalender ({e}); {'pakai cache' if self.ok else 'TIDAK ada data'}")

    def blocked(self, cfg, now=None):
        """-> (diblokir, keterangan)"""
        if not cfg.get("enabled"):
            return False, ""
        self.refresh(cfg)
        if not self.ok:
            return (not cfg.get("fail_open", True)), "kalender tidak tersedia"
        now = now or datetime.now(timezone.utc)
        before, after = timedelta(minutes=cfg["minutes_before"]), timedelta(minutes=cfg["minutes_after"])
        for dt, cur, imp, title in self.events:
            if cur in cfg["currencies"] and imp in cfg["impacts"] and dt - before <= now <= dt + after:
                return True, f"{cur} {title} {dt:%H:%M}Z"
        return False, ""

    def upcoming(self, cfg, n=3):
        now = datetime.now(timezone.utc)
        out = [(dt, cur, title) for dt, cur, imp, title in self.events
               if cur in cfg["currencies"] and imp in cfg["impacts"] and dt >= now - timedelta(minutes=cfg["minutes_after"])]
        return [f"{dt:%a %H:%M}Z {cur} {t}" for dt, cur, t in sorted(out)[:n]]
