"""Skill perilaku: engine (Bot.scan / entry_blockers) memeriksa ON/OFF-nya."""

SKILL = {"id": "session_news", "order": 6, "kind": "behavior", "deps": [], "layers": [],
         "name": "Filter Sesi & Berita",
         "desc": "Entry hanya di jadwal simbol (sesi/hari) & tidak saat berita High USD.",
         "warn_off": "Entry boleh di SEMUA jam termasuk saat rilis berita besar (spread & slippage melebar)."}
