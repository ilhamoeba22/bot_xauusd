"""Skill perilaku: News Trading Engine (Bot.news_engine) memeriksa ON/OFF-nya.
ON = Engine menempatkan pending order (Buy/Sell Stop & Limit / Straddle & Limit Rebound)
menjelang rilis berita High-Impact dengan proteksi OCO, expiry dan filter spread.
OFF = Bot kembali ke mode pasif blackout saat berita High-Impact."""

SKILL = {
    "id": "news_trading",
    "order": 9,
    "kind": "behavior",
    "deps": [],
    "layers": ["news"],
    "name": "News Trading Engine",
    "desc": "Pending order (Buy/Sell Limit & Stop) jelang berita High-Impact: Straddle Breakout / Rebound S/R dengan proteksi OCO & expiry.",
    "warn_off": "News Trading Engine nonaktif — bot kembali ke blackout pasif (tanpa pending order) saat berita High-Impact.",
    "default": False
}
