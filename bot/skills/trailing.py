"""Skill perilaku (tidak memberi skor; engine memeriksa status ON/OFF-nya)."""

SKILL = {"id": "trailing", "order": 5, "kind": "behavior", "deps": [], "layers": [],
         "name": "Trailing Stop Adaptif (ATR)",
         "desc": "BE-lock, trailing ATR & ikut candle. OFF hanya untuk posisi BARU (posisi terbuka tetap di-trail).",
         "warn_off": "RISIKO: posisi baru hanya dilindungi SL awal (tanpa BE/trailing) sampai skill dinyalakan lagi."}
