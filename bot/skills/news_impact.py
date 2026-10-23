"""Skill perilaku: engine (Bot.entry_blockers / execute_entry) memeriksa ON/OFF-nya.
ON = blackout berita PER SIMBOL (peta relevansi config news_impact) menggantikan filter berita global USD."""

SKILL = {"id": "news_impact", "order": 8, "kind": "behavior", "deps": [], "layers": ["news"],
         "name": "News Impact",
         "desc": "Kalender & headline (Fed/SEC) per simbol: blackout event Tinggi, reaksi harga terukur. Tidak pernah memicu entry.",
         "warn_off": "Blackout per simbol mati — kembali ke filter berita global (USD High) bila skill Sesi & Berita ON."}
