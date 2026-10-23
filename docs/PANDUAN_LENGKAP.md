# XAUUSD_EXNESS — Bot Scalping Emas (MT5 Exness)

Bot scalping otomatis untuk **XAUUSD**, **XAUUSDc** (akun cent) dan **XAUUSD247** (emas 24/7)
di MetaTrader 5 Exness, dijalankan dari CMD/PowerShell Windows. XAUUSD dan XAUUSD247 bisa
berjalan **bersamaan** dalam satu proses bot.

> **Default = PAPER** (simulasi, tidak ada order ke broker). Mode `live` terkunci
> sampai syarat di bagian [Sebelum mode live](#sebelum-mode-live) terpenuhi.

---

## 1. Struktur folder

```
D:\Trade\XAUUSD_EXNESS\
├── bot\                 mesin trading (Python)
│   ├── main.py              loop utama, rem risiko, burst, trailing adaptif, mode paper/demo/live
│   ├── strategy.py          sinyal MODULAR (entry + filter) — dipakai bot & backtest
│   ├── broker.py            koneksi MT5 + auto-reconnect, eksekusi live & paper
│   ├── news.py / sessions.py filter berita high-impact & sesi
│   ├── common.py            path, config, logging harian, sidik jari config
│   ├── bridge.py            ENGINE BRIDGE: tulis objek analisis ke file untuk indikator MT5
│   └── analysis\            MODUL ANALISIS (terpisah dari eksekusi)
│       ├── candles.py           pola candlestick (TA-Lib + Pin Bar) + penjelasan Bahasa Indonesia
│       ├── structure.py         swing, support/resistance, trendline, chart pattern
│       ├── smc.py               BOS/CHoCH, Order Block, FVG, likuiditas & sweep
│       ├── overlay.py           semua objek gambar (waktu+harga, ID unik) — dipakai web & MT5
│       ├── features.py          fitur kondisi pasar saat entry
│       └── learning.py          adaptive learning (win-rate per fitur + logistic regression)
├── dashboard\
│   ├── web_server.py + web\index.html   DASHBOARD WEB: chart + semua lapisan analisis
│   └── dashboard.py                     dashboard konsol (teks, gaya DASHBOARD.bat)
├── mql5\XAUUSD_Bridge_Draw.mq5 (+ .ex5)  INDIKATOR MT5: auto-draw objek dari bridge
├── config\config.json   SEMUA parameter
├── logs\                bot.log (harian, milidetik), error.log, trades.csv, entries.jsonl (alasan entry),
│                        burst_log.csv (tiap tiket burst, ms), bridge.log, bridge_status.json,
│                        learning_dataset.csv, learning_report.json, state.json, flag STOP/PAUSE/HALT
├── backtest\            backtest.py + data\ (cache) + results\ (hasil, seed & validasi learning)
├── start_bot.cmd / start_bot.ps1
└── requirements.txt
```

## 2. Instalasi

Butuh **Python 3.10+** (64-bit) dan **terminal MT5 Exness** yang sudah login.

```bat
cd /d D:\Trade\XAUUSD_EXNESS
start_bot.cmd setup
```

`setup` membuat `.venv` dan meng-install `requirements.txt` (MetaTrader5, pandas, numpy,
psutil, matplotlib, **TA-Lib** — wheel Windows sudah tersedia di PyPI, tidak perlu compile).
Launcher otomatis memakai `.venv` kalau ada. Manual:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**Persiapan MT5** (sekali): terminal login; tombol **Algo Trading** hijau (wajib untuk demo/live);
Tools → Options → Charts → *Max bars in chart* ≥ 100000; power setting **jangan sleep**.
Dashboard web butuh internet sekali saat halaman dibuka (library chart Plotly dari CDN jsDelivr).

Cek semuanya: `start_bot.cmd check`.

## 3. Cara menjalankan

| Perintah | Fungsi |
|---|---|
| `start_bot.cmd` | menu interaktif |
| `start_bot.cmd bot` | bot (mode sesuai config, default **paper**) |
| `start_bot.cmd web` | **dashboard web** → http://127.0.0.1:8765 (browser terbuka otomatis) |
| `start_bot.cmd all` | **semua**: bot + engine bridge MT5 (jendela terpisah) + dashboard web |
| `start_bot.cmd bridge` | engine bridge auto-draw MT5 (jendela minimized) |
| `start_bot.cmd indicator` | salin + compile indikator ke folder MT5 |
| `start_bot.cmd dashboard` / `both` | dashboard konsol / bot + dashboard konsol |
| `start_bot.cmd backtest` | backtest 12 bulan (semua simbol aktif **bersama**) + validasi learning |
| `start_bot.cmd check` | cek akun / simbol / lot / sinyal |
| `start_bot.cmd emergency` | tutup semua posisi bot + bot berhenti |
| `start_bot.cmd stop` | hentikan proses bot, posisi tetap (SL di server) |
| `start_bot.cmd reset` | hapus flag EMERGENCY_STOP / HALT |
| `start_bot.cmd task` | pasang Scheduled Task Windows |

PowerShell: `.\start_bot.ps1 -Action Bot|Web|All|Bridge|BridgeBg|StopBridge|InstallIndicator|Dashboard|Both|Backtest|Check|EmergencyStop|Stop|Reset|Setup|InstallTask|RemoveTask|TaskStatus`.

> **Setelah mengedit file `.py`, bot yang sedang jalan WAJIB di-restart** (Python tidak
> memuat ulang kode). Config (`config.json`) dibaca ulang tiap 10 detik tanpa restart,
> kecuali `mode`, `magic`, `mt5`, dan daftar simbol aktif.

### Auto-start (Scheduled Task)

`start_bot.cmd task` memasang task **XAUUSD_EXNESS_Bot**: jalan saat login + tiap hari 05:05
(emas buka Minggu ~22:00 UTC = Senin 05:00 WIB), restart otomatis 5x kalau crash; `logs\bot.lock`
mencegah dobel. Bukan Windows Service karena paket `MetaTrader5` harus berbicara dengan terminal
MT5 di sesi desktop user. Pastikan MT5 juga auto-start & login.

## 4. Mode

| `mode` | Order ke broker | Syarat |
|---|---|---|
| `paper` (default) | **tidak** — posisi virtual, harga tick asli | apa saja |
| `demo` | ya | akun yang login harus DEMO |
| `live` | ya, **uang sungguhan** | lihat [Sebelum mode live](#sebelum-mode-live) |

Pindah paper → demo tanpa memutus posisi paper yang masih terbuka: `python tools\switch_to_demo.py`
menunggu posisi paper tertutup, mengubah `mode` ke `demo`, lalu me-restart bot & server dashboard
(catatan di `logs\switch_demo.log`).

## 5. Strategi & burst

Timeframe **M5**, sinyal hanya dari bar **tertutup**, maksimal satu entry per bar per simbol.

- **Entry** `ema_cross`: EMA 8 memotong EMA 21 (alternatif `rsi_cross50`).
- **Filter** (on/off satu-satu): `rsi_momentum` (RSI 50–70 beli / 30–50 jual), `atr_band`
  (persentil ATR 40–95%), `trend_ema` (searah EMA200).
- **Burst `multiply`**: tiap sinyal membuka **3 tiket**, **tiap tiket risiko penuh 0,15%**
  (0,45% per sinyal). Hanya **satu burst aktif per simbol** (maks tiket = burst_size, tanpa buffer).
  Satu burst dihitung sebagai **satu** hasil untuk rem kalah beruntun & dataset pembelajaran.
  (`burst_mode: "split"` = versi lama: lot dibagi, total risiko tetap.)
- **Exit — trailing ADAPTIF berbasis ATR realtime** (dihitung ulang tiap 2 detik):

| tahap | kapan | SL dipindah ke |
|---|---|---|
| awal | entry | 1,5 × ATR dari harga masuk |
| BE-lock | profit ≥ 0,5 ATR | entry + 0,1 ATR (posisi tidak bisa rugi lagi, kecuali gap/slippage) |
| trail | profit ≥ 0,6 ATR | harga − 0,8 ATR |
| ketat | profit ≥ 1,5 ATR | harga − 0,5 ATR |
| **ikut candle** | profit ≥ 0,5 ATR | low (BUY) / high (SELL) **candle yang baru tertutup** − 0,1 ATR, digeser tiap candle |

SL hanya pernah maju, tidak pernah mundur; yang paling menguntungkan dari semua kandidat yang dipakai.
Diuji 24 Sep: trailing rapat + ikut candle vs trailing lama (burst 10, 12 bln): PF 1,38 vs 1,44,
drawdown **22% vs 26%**, rata-rata kalah −0,78R vs −0,90R, paruh 1/2 lebih seimbang (1,40/1,36);
3 bulan terakhir PF 1,30 vs 1,18. **Posisi yang belum sempat untung tetap bisa kena SL penuh**
(±23% trade di backtest) — trailing tidak bisa mencegah itu.

Entry lebih sering sudah diuji dan DITOLAK: filter RSI/ATR dimatikan (4,5 burst/hari) PF 1,30 DD 42%;
EMA 5/13 PF 1,07; entry `candle_break` (9–10/hari) PF 0,93 = rugi; timeframe M1 (8,6/hari, 3 bln)
PF 1,13, DD 35%. Keunggulan strategi ini datang justru dari filternya.

Keluar juga saat sinyal berlawanan dan setelah 36 bar (3 jam). Tanpa TP tetap.

## 6. Manajemen risiko (default)

| parameter | default | arti |
|---|---|---|
| `risk_per_trade_pct` | **0,15%** per tiket | × `burst_size` 3 = 0,45% per sinyal |
| `max_total_risk_pct` | **0,45%** | risiko TERBUKA gabungan semua simbol (posisi yang SL-nya sudah terkunci untung tidak dihitung). XAUUSD & XAUUSD247 bergerak identik → hanya satu burst berisiko pada satu waktu. Tanpa batas ini backtest DD 18,6%, dengan batas 8,7% |
| `max_risk_overshoot_mult` | 1,5 | lot minimum 0,01 boleh membuat risiko riil sampai 1,5× rencana (per tiket & gabungan); lebih dari itu entry dilewati |
| `daily_loss_limit_pct` | **3%** (tetap — lihat bagian 16) | P/L bot hari ini ≤ −3% → tutup semua, stop sampai hari UTC berikutnya |
| `daily_budget_gate` | ON | burst baru hanya dibuka kalau P/L hari ini − risiko terbuka − risiko burst baru masih di atas −3% (rem harian tidak bisa "terlewati" oleh beberapa burst yang kalah bersamaan) |
| `max_drawdown_pct` | **25%** (dulu 12%, diubah 25 Sep — lihat bagian 16) | dari puncak equity → tutup semua, buat `logs\HALT`, bot tidak mau start sampai di-reset manual |
| `max_consecutive_losses` / `loss_pause_minutes` | 4 / 60 | 4 burst kalah beruntun → jeda 60 menit |
| `max_positions_total` | 6 | 3 tiket × 2 simbol |
| sesi / berita / spread | Asia+London+NY; blok berita High USD ±30 mnt; spread ≤ 80 (XAUUSD) / 120 (247) | |

**Catatan lot minimum:** di equity ±82 juta dan ATR M5 hari-hari ini (5–9), 0,01 lot sudah
≈ 0,10–0,20% per tiket — jadi lot per tiket praktis selalu 0,01 dan risiko riil ikut naik-turun
dengan ATR. Log `ENTRY` selalu menulis risiko riil per tiket & total.

## 7. Tiga simbol emas

| simbol | default | catatan |
|---|---|---|
| `XAUUSD` | **ON** | akun Zero 279718110: komisi ~0,11 USD/oz, spread ~50 poin |
| `XAUUSD247` | **ON** | 24/7, ikut trading Sabtu–Minggu. Harga identik XAUUSD; eksposur ganda dicegah `max_total_risk_pct` |
| `XAUUSDc` | OFF | hanya ada di akun **Standard Cent** (saldo USC) — dilewati otomatis kalau tidak ada. Lot/risiko dihitung dengan `order_calc_profit`, jadi otomatis benar untuk USC |

## 7b. Profil akun: Zero (burst 3) vs Standard Cent (burst 10)

Profil = config lengkap siap pakai di `config\profiles\`. Ganti dengan
`python tools\use_profile.py <nama>` (config lama di-backup ke `config\config.backup-<waktu>.json`),
lalu **restart bot**. Tanpa argumen skrip itu menampilkan daftar profil.

| profil | akun | simbol | burst | risiko | magic |
|---|---|---|---|---|---|
| `zero_burst3` (aktif) | Zero 279718110 (IDR) | XAUUSD + XAUUSD247 | 3 tiket × 0,15% | 0,45% per sinyal | 260901 |
| `cent_burst10` | **Standard Cent** (USC) | XAUUSDc | **10 tiket × 0,045%** | 0,45% per sinyal | 260911 |

**Kenapa perlu akun Cent untuk 10 posisi:** lot minimum 0,01 di akun Zero sudah ≈ 0,19% equity per
tiket, jadi 10 tiket = ±1,9% per sinyal. Di akun Cent, 0,01 lot XAUUSDc = 1/100 lot standar
(±7 USC risiko per tiket pada ATR normal, ±17 USC saat ATR ekstrem), sehingga 10 tiket muat di
total 0,45%. **Modal minimum ±$102 (ATR normal) – $251 (ATR ekstrem); disarankan ≥ $300.** Kalau
equity kurang, bot melewati entry ("lot ditolak"), tidak memaksa lot lebih besar.

**Estimasi backtest** (data XAUUSD sebagai pengganti XAUUSDc karena akun Cent belum login; tanpa
komisi, spread diperlebar; 12 bulan, satu simbol):

| skenario | PF | max DD | hari terburuk | PF paruh 1 / 2 |
|---|---|---|---|---|
| Zero 3 × 0,15% (pembanding, XAUUSD saja) | 1,25 | 10,8% | −2,2% | 1,30 / 1,21 |
| Cent 10 × 0,045%, spread +10 sen | 1,25 | 9,7% | −2,2% | 1,36 / 1,14 |
| Cent 10 × 0,045%, spread +15 sen | 1,22 | 9,7% | −2,2% | 1,34 / 1,11 |
| Cent 10 × 0,045%, spread +25 sen | 1,18 | 10,2% | −2,2% | 1,28 / 1,08 |

10 tiket kecil memberi hasil hampir sama dengan 3 tiket besar pada risiko total yang sama —
keunggulannya tidak bertambah, tapi eksposur bisa diatur lebih halus. Akun Cent hanya punya
XAUUSDc (tanpa versi 247 yang di backtest paling kuat), dan paruh kedua backtest makin tipis
(PF 1,08–1,14): **uji demo Cent dulu**.

**Langkah memakai profil Cent:**
1. Buka akun **Standard Cent demo** di Exness, login di terminal MT5 (akun Zero boleh tetap ada).
2. `python tools\use_profile.py cent_burst10` → restart bot (`start_bot.cmd stop`, lalu `all`).
3. `start_bot.cmd check` — cek spread asli XAUUSDc, lot & risiko per tiket; sesuaikan
   `symbols.XAUUSDc.max_spread_points` kalau spread asli > 350.
4. `start_bot.cmd backtest` — sekarang memakai data XAUUSDc asli (menggantikan estimasi di atas).
5. Pasang indikator bridge di chart XAUUSDc. Kembali ke akun Zero: `python tools\use_profile.py zero_burst3`.

Coba config lain tanpa menimpa config aktif: set variabel lingkungan `XAUUSD_CONFIG` ke file profil.

## 8. Modul analisis (terpisah dari mesin eksekusi)

Semua modul di `bot\analysis\` adalah fungsi murni atas data OHLC. Bot memakainya saat ada
sinyal (untuk skor & penjelasan), backtest memakainya untuk uji, dan dashboard web memakainya
untuk menggambar — **kode yang sama** di ketiganya. Semua **kausal**: hanya memakai candle yang
sudah tertutup, swing baru dipakai setelah terkonfirmasi, jadi tidak ada "repaint" di chart.

1. **Pola candlestick** (`candles.py`) — TA-Lib: Doji, Hammer, Hanging Man, Inverted Hammer,
   Shooting Star, Engulfing, Harami, Piercing, Dark Cloud, Morning/Evening Star, 3 White
   Soldiers/3 Black Crows; + Pin Bar buatan sendiri. Tiap pola punya penjelasan Bahasa Indonesia.
2. **Struktur & chart pattern** (`structure.py`) — swing high/low (fractal 3 bar), support/
   resistance (klaster ≥ 2 sentuhan), trendline (dua swing terakhir, ditandai *patah* kalau
   ditembus), Double Top/Bottom, Head & Shoulders (+inverse), Segitiga simetris/naik/turun,
   Bull/Bear Flag. Status: aktif / terkonfirmasi / batal / breakout.
3. **Smart Money Concepts** (`smc.py`) — BOS & CHoCH, Order Block (candle berlawanan terakhir
   sebelum impuls pematah struktur; batal kalau ditembus), Fair Value Gap (terisi/terisi
   sebagian), equal highs/lows + liquidity sweep.
4. **Adaptive learning** (`learning.py`) — lihat bagian 9.

**Pengaruh SMC ke entry** — `smc.filter_mode`:

| mode | perilaku | backtest 12 bln (2 simbol, burst 3) |
|---|---|---|
| `off` | diabaikan | PF 1,37 |
| **`score`** (default) | dihitung, digambar, jadi fitur learning; **tidak mengubah entry** | PF **1,37**, DD 8,7% |
| `prefer` | entry melawan bias struktur tetap boleh, tapi burst dikecilkan jadi 1 tiket | PF 1,30, DD 10,3%, return turun ½ |
| `strict` | entry wajib searah bias struktur | PF 1,22, DD 12,5%, trade −41% |

Hasilnya jelas: untuk strategi momentum EMA ini, memaksakan konfluensi SMC **memperburuk**
hasil. Default dibiarkan `score` — SMC tetap tampil & dipelajari, tanpa mengorbankan entry.

## 9. Adaptive learning (explainable)

- **Dataset**: tiap burst yang selesai → satu baris di `logs\learning_dataset.csv`: 16 fitur
  biner kondisi saat entry (sesi, volatilitas, RSI kuat, pola candle searah/berlawanan, bias SMC,
  CHoCH, dekat OB, di FVG, sweep, chart pattern, S/R menghalangi, simbol), hasil (R & P/L), durasi.
  Seed awal = ±1.180 trade backtest (`backtest\results\learning_seed.csv`); trade nyata bot diberi bobot 3×.
- **Model**: (1) win rate & rata-rata R per fitur, dihaluskan ke win rate umum; (2) logistic
  regression kecil (L2, Newton/IRLS) → **bobot per fitur** yang bisa dibaca; skor keyakinan
  entry = sigmoid(jumlah bobot fitur yang aktif).
- **Retrain**: tiap 20 burst selesai + tiap ganti hari (UTC).
- **Pengaruh** (`learning.mode`): `observe` (default) = skor dihitung & ditampilkan, tidak menolak
  entry; `gate` = tolak entry dengan skor < `min_confidence`.
- **Validasi walk-forward** (`backtest --learning`): tiap trade diskor oleh model yang hanya
  dilatih trade *sebelumnya*. Hasil saat ini:

| | trade | PF | total R |
|---|---|---|---|
| semua | 979 | 1,39 | +129,8 |
| skor ≥ 0,45 | 928 | 1,43 | +134,0 |
| skor ≥ 0,50 | 865 | 1,40 | +118,0 |
| skor ≥ 0,55 | 715 | 1,39 | +91,5 |
| skor ≥ 0,60 | 515 | 1,27 | +44,7 |

  → gate **belum terbukti membantu**: di 0,45 selisihnya kecil (PF +3%, di bawah syarat +5%),
  ambang lebih tinggi membuang pemenang lebih banyak daripada pecundang. Jadi default
  `observe`. Mode `gate` di **live** otomatis dinonaktifkan kecuali validasi terbaru (≤ 30 hari)
  menyatakan `improves: true`.
- Yang menarik dari bobotnya: sesi London (−0,48) dan "dekat Order Block searah" (WR 53%) justru
  negatif untuk strategi ini; lihat panel learning di dashboard untuk angka terkini.

## 10. Dashboard web (belajar sambil jalan)

`start_bot.cmd web` → http://127.0.0.1:8765 (hanya dari komputer ini; `--port=NNNN` untuk port lain).

**Tata letak** (menyesuaikan lebar layar: 3 kolom → 2 kolom → 1 kolom di HP):

| panel | isi |
|---|---|
| **Header** (selalu terlihat) | mode (PAPER/DEMO/LIVE), **BURST MODE: ACTIVE/INACTIVE** (+ alasan kalau inactive: pause, rem harian, HALT, jeda kalah beruntun, bot mati), status Bot / MT5 / Bridge, tombol **Pause/Resume** & **EMERGENCY STOP** |
| **Chart utama** | tab simbol · **pemilih timeframe tampilan M1/M5/M15/M30/H1/H4** · toggle per lapisan (pola candle, +Doji, chart pattern, S/R, trendline, OB, FVG, BOS/CHoCH, likuiditas, EMA, entry bot, objek batal) |
| **Info pola + Kenapa bot entry di sini?** | hover/klik objek di chart → penjelasan Bahasa Indonesia; tiap entry: sinyal, pola candle, SMC, struktur, skor keyakinan & faktornya |
| **P/L & equity** | equity (virtual di paper) + grafik kecil sesi ini, P/L hari ini vs rem, drawdown vs batas, win rate, PF, risiko terbuka vs batas |
| **Posisi terbuka** | tiket, arah, lot, SL, umur, P/L; tabel per simbol (status gerbang, spread, floating, WR) |
| **Akun MT5 — data langsung dari terminal** | balance, equity, floating, margin, free margin, margin level (sama persis dengan tab Trade MT5); **semua** posisi di akun dengan label pemilik per magic (**XAUUSD_EXNESS** = bot ini, **Exness_Bot** = 234601, **Manual** = 0); posisi tertutup hari ini (UTC) per pemilik; cek **sinkron** posisi versi bot vs MT5 (di mode paper: keterangan bahwa posisi bot virtual) |
| **Burst mode** | status, ukuran × risiko, TF eksekusi, jumlah burst hari ini/total, rata-rata jeda antar burst, tabel tiket terakhir dengan **waktu UTC milidetik** & latensi dari penutupan candle sinyal |
| **Koneksi, bridge MT5 & pengaman** | umur update bot, MT5 + jumlah reconnect, Algo Trading, **Bridge engine** (heartbeat, lama loop) dan daftar chart MT5 yang indikatornya aktif, sesi/berita, pause, rem harian/HALT |
| **Adaptive learning** | bobot fitur + tabel membaik/memburuk minggu ini |
| **Cara membaca chart / Log** | legenda warna & 14 baris log terakhir |

**Pemilih timeframe — "Chart view" vs "Execution timeframe".** Tombol M1…H4 hanya mengganti
**tampilan**: candle + seluruh lapisan analisis dihitung ulang untuk timeframe itu (pilihan
tersimpan di browser). Strategi burst **tidak ikut berubah** — ia tetap mengambil keputusan di
timeframe eksekusinya, `config.json → symbols.<SIMBOL>.timeframe` (default **M5**, satu-satunya
yang sudah di-backtest; data M1 di terminal hanya ±3 bulan). Label kecil di sebelah tombol selalu
menulis keduanya, mis. *Chart view: M15 · Execution timeframe: M5*. Entry bot ditaruh di candle
timeframe tampilan yang memuat waktu entry.

**Realtime tanpa lag:** status tiap 1,5 dtk, chart tiap 3 dtk. Objek analisis (pola, struktur,
SMC) hanya dihitung ulang saat ada **candle tertutup baru** untuk simbol+TF itu; di antara itu
server & browser hanya memperbarui candle yang sedang berjalan (tanpa menggambar ulang semua
objek). Request tidak menumpuk, dan polling berhenti saat tab browser tidak terlihat. HTTP polling
dipilih ketimbang websocket karena datanya berubah per detik, bukan per milidetik — cukup ringan
dan tanpa dependensi tambahan.

Scroll di atas chart = zoom, geser = pan (sumbu harga menyesuaikan otomatis). Dashboard konsol
lama (`start_bot.cmd dashboard`) tetap ada untuk pemantauan ringan tanpa browser.

## 10b. Auto-draw di MT5 (bridge Python → indikator MQL5)

```
bot\bridge.py  (Python, proses kontinu)                    mql5\XAUUSD_Bridge_Draw (indikator MT5)
  tiap 2 dtk: heartbeat.txt                     ─────▶     tiap 2 dtk (OnTimer): baca heartbeat
  candle tertutup baru / entry baru:                         heartbeat segar? baca bridge_<SIMBOL>_<TF>.csv
  hitung objek (overlay.py — SAMA dengan web)                ObjectCreate / ObjectMove (TREND, RECTANGLE,
  → bridge_<SIMBOL>_<TF>.csv                                  ARROW, TEXT) + tooltip Bahasa Indonesia
      di <MT5 Common>\Files\XAUUSD_EXNESS\                   HAPUS objek ber-prefix XE_ yang tidak ada
                                                              lagi di file (pola batal / keluar jendela)
                                                ◀─────     tulis ea_<SIMBOL>_<TF>.txt (status) → dashboard web
```

- **Tanpa DLL**: file ditaruh di folder `Common\Files` MT5 yang memang bisa dibaca MQL5 dengan
  `FILE_COMMON`. Jadi **"Allow DLL imports" TIDAK perlu dicentang**.
- Dibuat sebagai **indikator**, bukan EA: tidak bisa mengirim order, **tidak butuh "Allow Algo
  Trading"**, dan bisa dipasang bersamaan dengan EA lain di chart yang sama.
- **ID unik per objek** (mis. `XE_OB_1_1790250900` = Order Block bullish yang candle-nya jam itu).
  Objek yang sama diperbarui di tempat; objek yang tidak ada lagi di file dihapus — chart tidak
  menumpuk objek basi. Melepas indikator dari chart menghapus semua objek `XE_`.
- **Kalau engine Python mati/crash**: heartbeat berhenti → setelah 15 dtk indikator menulis
  **"BRIDGE DISCONNECTED – objek dibekukan"** di pojok chart, berhenti mengubah objek, tapi
  indikatornya sendiri tetap hidup. Dashboard web menampilkan **Bridge disconnected** di header.
  Begitu engine jalan lagi, gambar otomatis tersambung kembali.
- Timeframe yang digambar: `config.json → bridge.timeframes` (default M1, M5, M15, M30, H1);
  lapisan yang digambar: `bridge.layers`. Tidak memengaruhi trading.

**Menjalankan kedua proses bersamaan:** `start_bot.cmd all` (atau `.\start_bot.ps1 -Action All`)
menjalankan bot + engine bridge di jendela terpisah lalu membuka dashboard web, dan menampilkan
pengingat untuk memasang indikator. Engine saja: `start_bot.cmd bridge` / `-Action BridgeBg`
(background) / `-Action StopBridge`.

### Panel BUY / SELL di chart MT5 (indikator v1.10)

Indikator menampilkan panel di pojok kiri atas chart: **BUY**, **SELL**, status bot, dan hasil perintah.

1. Klik **BUY** atau **SELL** → muncul **gambar RENCANA entry** di chart: panah arah, garis harga masuk
   (hijau/merah), garis **SL** putus-putus (SL x ATR TF eksekusi, sama dengan bot), label
   `RENCANA BUY x10 @ harga (TP: trailing)`, dan perkiraan risiko (tiket x lot minimum, % equity).
   Garis mengikuti harga selama menunggu konfirmasi; batal otomatis setelah 60 dtk.
2. Klik **KONFIRMASI BUY/SELL** (atau **BATAL**).
3. Indikator menulis perintah ke `<MT5 Common>\Files\XAUUSD_EXNESS\cmd_*.txt`. **Bot** mengeksekusinya
   dalam ±2 dtk lewat jalur yang sama dengan entry otomatis: burst, SL x ATR, trailing candle, jurnal,
   `burst_log.csv`, alasan entry (ditandai *ENTRY MANUAL*), marker entry/SL di web & MT5.
4. Hasil tampil di panel: `OK: ENTRY 10x (manual) @ harga` atau `DITOLAK: <alasan>`.

Kenapa lewat bot: aturan MT5 melarang indikator mengirim order; kalau order dibuka EA terpisah, posisinya
tidak dikelola trailing, rem risiko, maupun jurnal bot.

**Rem yang TETAP berlaku untuk entry manual:** HALT, rugi harian, anggaran rugi harian, jeda kalah
beruntun, posisi maksimal / burst masih aktif, risiko terbuka, spread, pasar tutup, Algo Trading
(demo/live), berita (`manual.respect_news`). **Yang diabaikan** (karena kamu yang memutuskan arah):
sinyal EMA, filter sesi, status Burst OFF / Pause. Juga: perintah > 30 dtk ditolak (kedaluwarsa), jeda
10 dtk antar perintah per simbol, simbol harus aktif di bot. Semua perintah tercatat di
`logs\control_audit.log` (sumber `mt5`). Atur di `config.json → manual`.

Panel perlu **bot versi 25 Sep ≥ 10:38** yang berjalan (bot menulis `bot_heartbeat.txt`); kalau bot mati,
panel menulis *BOT TIDAK BERJALAN* dan konfirmasi tidak dikirim. Panel tetap bekerja walau engine
bridge gambar sedang mati. Input indikator: `InpTradePanel`, `InpPanelX/Y`, `InpExecTf` (M5),
`InpBotStaleSec`, `InpPendingSec`.

### Langkah manual di MT5 (sekali saja)

1. **Pasang & compile** — `start_bot.cmd indicator` (atau `-Action InstallIndicator`) menyalin
   `mql5\XAUUSD_Bridge_Draw.mq5` ke `<Data MT5>\MQL5\Indicators\XAUUSD_EXNESS\` dan meng-compile-nya
   lewat MetaEditor (hasil: *0 errors, 0 warnings*). Manual: MT5 → File → Open Data Folder →
   `MQL5\Indicators` → salin file .mq5 → buka di MetaEditor (F4) → **Compile (F7)**.
2. Di MT5: panel **Navigator** → klik kanan → **Refresh** → *Indicators → XAUUSD_EXNESS →
   XAUUSD_Bridge_Draw*.
3. Buka chart **XAUUSD** (dan/atau XAUUSD247) di timeframe M1/M5/M15/M30/H1, **seret indikator ke
   chart** → tab *Common*: tidak ada yang perlu dicentang (DLL & Algo Trading tidak dipakai) →
   tab *Inputs*: biarkan default → **OK**.
4. Pastikan engine bridge berjalan (`start_bot.cmd bridge` atau `all`). Di pojok kiri atas chart
   muncul *"XAUUSD_EXNESS bridge: OK … N objek"*.
5. Supaya terpasang lagi setelah MT5 dibuka ulang: klik kanan chart → **Template → Save Template**
   (atau simpan Profile). Satu chart = satu simbol + satu timeframe; pasang di tiap chart yang ingin
   digambar. Untuk akun Cent, isi input *InpSymbolFile* kalau nama simbol chart berbeda.

## 11. Backtest

```bat
start_bot.cmd backtest                          :: = python backtest\backtest.py --learning
python backtest\backtest.py --symbol XAUUSD     :: satu simbol saja
python backtest\backtest.py --slippage 30       :: uji ketahanan biaya
python backtest\backtest.py --burst 3 --burst-mode multiply --risk 0.2 --max-risk 0.6   :: coba varian
python backtest\backtest.py --optimize          :: grid + walk-forward parameter strategi
```

Semua simbol aktif disimulasikan **di satu garis waktu dengan equity, rem harian, anggaran rugi
harian dan batas risiko terbuka yang sama** — persis seperti bot. Model eksekusi konservatif:
entry di open bar berikutnya, spread asli per bar + 5 poin slippage + komisi, SL dianggap kena
duluan kalau SL & TP di bar yang sama, trailing berlaku bar berikutnya. **Batasan:** filter
berita tidak disimulasikan; spread bar MT5 = spread minimum bar; lot minimum 0,01 tidak
dimodelkan (live bisa sedikit berbeda risikonya); data M1 terminal hanya ±3 bulan.

### Hasil default (24 Sep 2026, XAUUSD + XAUUSD247 bersamaan)

| | nilai |
|---|---|
| Periode | 24 Sep 2025 – 24 Sep 2026 (XAUUSD247 hanya punya data sejak 13 Des 2025) |
| Jumlah trade (burst) | 1.179 (~4,5/hari bursa) |
| Win rate | 61,6% |
| Profit factor | **1,37** (paruh 1 / 2: 1,36 / 1,38) |
| Expectancy | +0,127 R per burst (menang +0,76R, kalah −0,89R) |
| Max drawdown | **8,7%** |
| Return | 91% (compounding) |
| Kalah beruntun maks | 9 |
| Uji stres +30 poin biaya | PF 1,34, DD 10,0% |
| Per simbol | XAUUSD PF 1,23 · XAUUSD247 PF 1,57 |

**Membaca dengan jujur:** burst *multiply* **tidak menambah keunggulan** — PF sama dengan tanpa
burst; yang berubah hanya ukuran taruhan per sinyal. Jadi return & drawdown naik bersamaan:
3 × 0,3% per tiket memberi return 266% tapi DD 13,8% dan hari terburuk −4,4%. Default 0,15%/tiket
dipilih supaya DD tetap di bawah 10%. Parameter strategi (trail 1,2 ATR, ATR ≥ 40%) dipilih lewat
walk-forward; angka realistis ke depan lebih dekat ke PF out-of-sample **~1,2–1,3**.

Varian burst (backtest gabungan 2 simbol, semua dengan anggaran rugi harian):

| risiko / tiket | batas risiko terbuka | PF | DD | return | hari terburuk |
|---|---|---|---|---|---|
| **0,15%** (default) | 0,45% | 1,37 | **8,7%** | 91% | −2,9% |
| 0,20% | 0,60% | 1,39 | 11,1% | 140% | −2,9% |
| 0,30% | 0,90% | 1,41 | 13,8% | 266% | −4,4% |
| 0,15% | tanpa batas | 1,30 | 18,6% | 102% | −4,4% |

## 12. Cara berhenti dengan aman

| cara | posisi terbuka | bot |
|---|---|---|
| Ctrl+C / `start_bot.cmd stop` | tetap ada, SL di server (paper: tersimpan); trailing berhenti | berhenti |
| **Pause** (web/konsol [P]) / file `logs\PAUSE` | tetap dikelola | jalan, tidak entry baru |
| **EMERGENCY STOP** (web/konsol [S]) / `start_bot.cmd emergency` | **ditutup semua** (hanya magic 260901) | berhenti, menolak start sampai `reset` |

Posisi manual & bot lain (Exness_Bot magic 234601) tidak pernah disentuh.

## 13. Log

`bot.log` (harian, 90 hari, waktu sampai milidetik) · `error.log` · `trades.csv` (jurnal per tiket,
P/L riil termasuk komisi) · `entries.jsonl` (alasan tiap entry) · **`burst_log.csv`** (satu baris per
tiket burst: waktu UTC milidetik, simbol, id burst, tiket ke-k dari n, arah, lot, harga, SL, candle
sinyal, `ms_since_bar_close` = latensi keputusan, `ms_since_burst_start` = kecepatan eksekusi burst —
dipakai untuk mengevaluasi frekuensi) · `bridge.log` / `bridge_status.json` · `learning_dataset.csv` / `learning_model.json` /
`learning_report.json` · `state.json` (untuk dashboard) · `bot_state.json` (memori harian).

Uji coba tanpa mengganggu bot yang sedang jalan: set variabel lingkungan
`XAUUSD_LOG_DIR` ke folder lain sebelum menjalankan skrip.

## 14. Berdampingan dengan Exness_Bot

Akun demo 279718110 juga dipakai `D:\Trade\Exness_Bot` (magic 234601) yang ikut trading XAUUSD.
Magic berbeda sehingga posisi tidak saling dikelola, tapi di mode demo/live max drawdown diukur
dari equity akun (rugi bot lain ikut terhitung) dan eksposur emas bisa bertumpuk.

## 15. Tombol kontrol, marker posisi & peringatan (25 Sep)

**Tombol di header dashboard web** (masing-masing dengan dialog konfirmasi, dicatat di
`logs\control_audit.log` dengan waktu milidetik, dan terlihat di panel *Burst mode → Audit*):

| tombol | efek | tidak melakukan |
|---|---|---|
| **▶ Aktifkan Burst Sekarang** | hapus `logs\BURST_OFF` + buat `logs\BURST_NOW` → bot langsung mengevaluasi sinyal (tanpa menunggu siklus scan 10 dtk); hasilnya tampil di panel Burst | **tidak** melewati rem: rugi harian, drawdown, posisi maksimal, risiko terbuka, sesi, berita, spread tetap diperiksa. Tanpa sinyal valid, tidak ada entry |
| **■ Matikan Burst** | buat `logs\BURST_OFF` → tidak ada entry baru; badge langsung **INACTIVE — dimatikan manual** | **tidak** menutup posisi — posisi terbuka tetap dikelola SL/trailing |
| **EMERGENCY STOP** | tutup semua posisi bot + bot berhenti | – |

(Tombol "Pause" lama diganti tombol burst; file `logs\PAUSE` tetap dihormati bot & dashboard konsol.)

**Marker posisi (entry / SL / TP)** — satu sumber data: `logs\state.json` (sama dengan panel
"Posisi terbuka"), dibaca oleh `bot\analysis\trade_marks.py` untuk dashboard web **dan** bridge MT5:
- panah entry (hijau BUY / merah SELL), garis putus-putus **SL merah** dan **TP hijau** mulai dari
  candle entry, garis titik harga entry, label `SELL x10 0.1 lot @ harga | SL … | TP … | P/L …`;
  tiket satu burst digabung jadi satu marker; SL mengikuti trailing (diperbarui tiap detik);
- strategi ini tanpa TP tetap → label menulis `TP - (trailing)` dan garis TP tidak digambar;
- posisi ditutup (SL/trailing/manual): marker berubah abu-abu dengan garis entry → exit dan P/L,
  tampil `dashboard.closed_marker_minutes` (10) menit lalu hilang otomatis (cleanup berbasis ID).
  Daftar posisi tertutup disimpan di `bot_state.json`, jadi tetap ada walau bot restart.

**Peringatan eksplisit** (banner di bawah header): BOT TIDAK BERJALAN (state > 30 dtk basi),
EMERGENCY STOP, HALT, rem rugi harian, burst dimatikan, PAUSE, jeda kalah beruntun, MT5 putus,
Algo Trading OFF, filter berita, di luar sesi, bridge disconnected. Tabel **Per simbol** sekarang
selalu menampilkan status gerbang walau belum ada sinyal — `siap — menunggu sinyal` atau
`TERBLOKIR: <alasan>` — plus **kandidat terakhir** (cross EMA kapan, arah, lolos atau ditolak filter apa).

## 15b. Mode "risk taker" untuk entry otomatis (25 Sep)

Atas permintaan, filter `rsi_momentum` DIMATIKAN (profil `config\profiles\zero_burst10_risktaker.json`;
setelan sebelumnya = `zero_burst10`). Filter ATR, tren EMA200, dan semua rem risiko tetap aktif.

| varian (burst 10, 12 bln, 2 simbol) | burst/hari | PF | drawdown | hari terburuk | return | HALT 25%? |
|---|---|---|---|---|---|---|
| sebelumnya (semua filter) | 3,3 | 1,38 | 22,0% | −3,1% | +247% | tidak |
| **risk taker A1 (tanpa RSI) — AKTIF** | 3,4 | **1,43** | 23,4% | −4,1% | +337% | tidak |
| tanpa RSI & ATR | 4,9 | 1,30 | 45% | −3,9% | +431% | ya (22 Des 25) |
| taruhan x2 (0,3%/tiket, harian 6%) | 3,3 | 1,38 | 40% | −6,1% | +825% | ya (13 Nov 25) |
| A1 + taruhan x2 | 3,4 | 1,43 | 42% | −8,2% | +1.297% | ya (13 Nov 25) |

Catatan jujur: A1 dipilih dari data yang sama (belum walk-forward); paruh 1/2 PF 1,37/1,49 dan uji
biaya +30 poin PF 1,38 mendukung, tapi drawdown 23,4% sudah dekat batas HALT 25%.

## 15c. Eksperimen PAPER ">= 50 burst per hari" (25 Sep)

`tools\run_paper50.cmd` menjalankan bot KEDUA dalam mode paper (uang virtual, tanpa order ke broker),
terpisah total dari bot demo: config `config\profiles\paper_50burst.json`, log `logs_paper50\`,
magic 260950, dashboard **http://127.0.0.1:8766** (bot demo tetap di 8765). Entry `interval_trend`:
setiap candle M5 yang dibuka :00 dan :30, searah harga vs EMA21, tanpa menunggu sinyal. Rem harian,
anggaran harian, batas risiko terbuka & jeda kalah beruntun dimatikan; HALT di drawdown 90%.
Tombol BUY/SELL MT5 tetap hanya untuk bot demo. Dashboard 8766 menampilkan pill Bridge abu-abu
"tidak dipakai" (eksperimen tidak memakai bridge gambar) — bukan gangguan.

**Sejak 25 Sep 17:08 eksperimen berjalan mode DEMO** (atas permintaan: posisi harus terlihat di MT5).
Order sungguhan masuk ke akun demo yang SAMA dengan bot utama (magic 260950 vs 260901 — label pemilik
di panel "Akun MT5"). Rem rugi harian dihitung per bot, tetapi **drawdown memakai equity akun bersama**:
kerugian eksperimen bisa ikut mendorong bot utama ke HALT 25%. Karena itu HALT eksperimen diturunkan
90% → 20%. Restart bot eksperimen saja: `tools\restart_exp50_bot.cmd`; restart dashboard web (8765 &
8766): `tools\restart_web.cmd`. Hasil akhir fase paper disimpan di `logs_paper50\state_paper_final_20260925.json`
(180 trade, WR 94,4% — terlalu bagus dibanding backtest PF 0,95; fill paper lebih optimis dari broker).

Backtest 12 bln profil ini: rata-rata 70 burst/hari (median 66), WR 58,8%, **PF 0,95**, rata-rata
−0,02 R/burst; equity virtual −5% (1 minggu), −19% (1 bulan), −95% (3 bulan), HALT 90% pada 18 Des 2025.
Hentikan: tutup jendela "PAPER50 BOT" dan "PAPER50 DASHBOARD".

## 15d. Batas risiko dinaikkan (25 Sep, opsi D)

| | sebelum | sesudah |
|---|---|---|
| risiko per tiket | 0,15% | **0,20%** |
| batas risiko terbuka total | 1,5% | **2,0%** |
| rem rugi harian | 3% | **4%** |
| HALT drawdown | 25% | **35%** |

Backtest 12 bln: PF 1,44 (tidak berubah), DD −30,2% (dulu −23,4%), return +562% (dulu +336%), hari
terburuk −5,5%, bulan terburuk −22,2%. Lot dibulatkan KE BAWAH, jadi risiko riil tidak pernah lebih dari
0,20%/tiket: di equity ±83 jt lot 0,02 hanya dipakai bila SL ≤ ±5 poin (±45% burst menurut log),
selain itu tetap 0,01. Efek samping yang diinginkan: saat pasar volatil (SL lebar) lot minimum 0,01 kini
lolos sampai 0,30%/tiket (0,20 × overshoot 1,5) — sebelumnya entry & tombol BUY/SELL MT5 ditolak di 0,24%.
Backup config lama: `config\config.backup_20260925_2352.json`. Bot membaca config baru tanpa restart.

Dashboard: panel di bawah chart kini satu kartu bertab (Posisi, Log sinyal, Tiket burst, Akun MT5,
Learning, Log & koneksi, Legenda). Tombol **⚙ Atur** membuka laci pengaturan: mode ringkas, label chart,
update otomatis, grafik equity mini, tinggi chart, jumlah candle, lebar sidebar, serta tampil/sembunyi
dan urutan (↑↓) panel samping & tab. Semua tersimpan di browser; "Reset ke bawaan" mengembalikannya.

## 16. Diagnosis "tidak ada entry" 24–25 Sep & perubahan batas halt

**Temuan:** proses bot mati diam-diam antara 00:11 dan 00:31 WIB (tanpa error/traceback) saat laptop
berulang kali masuk Modern Standby; bot itu dijalankan dari sesi alat bantu (bukan terminal user) dan
tidak ada watchdog yang menyalakannya lagi. Satu sinyal valid terlewat (00:30 WIB, SELL). Selama bot
hidup (21:27–00:11) memang tidak ada sinyal valid — satu-satunya cross (23:15) benar ditolak filter
ATR. Koneksi MT5, rem risiko, filter SMC, sesi, dan data feed semuanya normal.
Log mentah: `reports\diagnosis_2026-09-25_log_mentah.txt`.

**Perbaikan:**
- `loop.keep_awake: "display"` — bot mencegah Windows idle-sleep/standby selama berjalan
  (menutup lid / Sleep manual tetap menidurkan laptop; atur juga Power & sleep = Never saat trading);
- **watchdog** di Scheduled Task (`start_bot.cmd task`): tiap 5 menit menjalankan
  `pythonw -m bot.main --watchdog` — keluar diam-diam kalau bot sudah jalan atau ada
  EMERGENCY_STOP/HALT, menyalakan bot kalau mati (log: `WATCHDOG bot dinyalakan otomatis`);
- **jalankan bot dari terminalmu sendiri** (`start_bot.cmd all`), bukan dari sesi alat lain;
- dashboard: banner **BOT TIDAK BERJALAN sejak …** + alasan non-entry per simbol (bagian 15).

**Batas halt — nilai lama vs baru** (semua di `config.json → risk`, tidak ada yang hardcode):

| parameter | lama | baru | alasan (backtest burst 10 + trailing candle, 12 bln) |
|---|---|---|---|
| `max_drawdown_pct` | 12% | **25%** | drawdown maksimum backtest 22%; batas 12% sudah tersentuh di drawdown normal (12 Nov 2025) → bot HALT padahal strategi masih sehat. 25% memberi ruang ±3 poin di atas DD terburuk historis |
| `daily_loss_limit_pct` | 3% | **3% (tetap)** | diuji 3/4/5/6%: dinaikkan justru memperburuk — DD 22% → 28–30%, hari terburuk −3,1% → −8,4%. Rem harian 3% tidak pernah menjadi penyebab tidak ada entry |
| `max_consecutive_losses` / jeda | 4 / 60 mnt | tetap | – |

Profil `config\profiles\zero_burst10.json` ikut diperbarui; backtest resmi dijalankan ulang
(sidik jari config baru) untuk gerbang live.

## 17. Multi-simbol: ETHUSD & BTCUSD (26 Sep)

Modul candlestick, chart pattern, SMC/Order Block, learning & overlay chart dipakai APA ADANYA (generik
OHLC). Yang terpisah per simbol ada di `config.json -> symbols.<SYM>`:

| kunci | arti |
|---|---|
| `exit`, `strategy`, `risk` | menimpa bagian global untuk simbol ini (mis. `exit.sl_atr`, `risk.burst_size`) |
| `sessions` + `trade_days` | jadwal SENDIRI (menggantikan jadwal global utuh); 0=Senin … 6=Minggu |
| `risk_group` | simbol satu grup berbagi `max_positions_total` & `max_total_risk_pct` (emas = `gold`; tiap crypto grup sendiri → tidak memakan slot emas). Jeda kalah beruntun juga per grup. Rem rugi harian & HALT = seluruh akun |
| `sl_min_pct` / `sl_max_pct`, `tp_min_pct` / `tp_max_pct` | jarak SL/TP dijepit ke % harga |
| `commission_pct` | biaya backtest dalam % harga (crypto); emas tetap `commission_price_units` |

| | XAUUSD / XAUUSD247 | ETHUSD | BTCUSD |
|---|---|---|---|
| kontrak / lot min | 100 oz / 0,01 | 1 ETH / **0,1** | 1 BTC / 0,01 |
| timeframe | M5 | **M15** | **M15** |
| SL | 1,5 × ATR | **3,0 × ATR** (0,2–2,0% harga) | **4,0 × ATR** (0,15–1,5% harga) |
| trailing | BE 0,5 · trail 0,8 · ketat 0,5 ATR | BE 1,0 · trail **1,6** ATR | BE 1,0 · trail **1,2** ATR |
| maks tahan | 36 bar (3 jam) | 144 bar (36 jam) | 144 bar (36 jam) |
| risiko × burst | 0,20% × 10 | 0,10% × **10** (26 Sep, dulu 5) | 0,20% × **10** (26 Sep, dulu 3; lot min 0,01 ≈ 0,22%) |
| grup: posisi / risiko terbuka | gold: 10 / 2,0% | ETHUSD: 10 / 1,0% | BTCUSD: 10 / 2,0% |
| jadwal | XAUUSD ikut jam bursa (tutup weekend); 247 = 7 hari | 24 jam × 7 hari | 24 jam × 7 hari |
| status | aktif | **enabled=false — tidak lolos** | **enabled=false — lolos tipis** |

**Backtest walk-forward (11 bln, Okt 2025 – Sep 2026, 60% awal pilih parameter / 40% akhir uji):**
parameter emas apa adanya rugi di kedua crypto (PF ETH 0,73, BTC 0,80). Dari 108 kombinasi (M5 & M15):
- ETHUSD M15: out-of-sample PF 0,86 (biaya 0,02%), bahkan tanpa biaya 0,94 → **tidak ada edge**, jangan live.
- BTCUSD M15: out-of-sample PF 1,07 (n 154), seluruh periode PF 1,15, DD −3,8%, +6,4%; impas pada biaya
  ±0,035–0,05%/transaksi. Biaya asli Exness untuk BTC di akun ini belum diketahui → forward-test demo dulu.
- Trade weekend tidak lebih buruk (BTC weekend +0,20R/trade vs hari kerja +0,01R).
Hasil: `backtest\results\crypto_walkforward_20260926.json`, `crypto_walkforward2_…`, `crypto_final_…`.
Gerbang mode live menolak simbol aktif yang tidak punya hasil backtest PF ≥ 1.

**26 Sep: 10 tiket per sinyal untuk crypto (permintaan user, varian A).** Backtest: BTC PF 1,05, DD −13%
(dulu −3,8%), +5,6%; ETH PF 0,93, DD −13%, −6,5%. Varian "agresif" lain DITOLAK karena merusak hasil:
tanpa filter ATR & EMA200 → BTC DD −63%, ETH −41%; ditambah M5 → keduanya DD −87…−88%.
Filter & timeframe tetap. Risiko terbuka maksimal bersamaan: emas 2% + BTC 2% + ETH 1%, tetapi
`daily_budget_gate` menolak burst baru bila P/L hari ini − semua risiko terbuka − burst baru < −4%.

**Menjalankan per simbol:**
```
start_bot.cmd bot XAUUSD,BTCUSD          (jendela ini)      start_bot.cmd all ETHUSD
.\start_bot.ps1 -Symbols XAUUSD,BTCUSD   .\start_bot.ps1 -Symbols ETHUSD   .\start_bot.ps1 -Action All -Symbols BTCUSD
python -m bot.main --symbols BTCUSD
```
Tanpa simbol = semua yang `enabled: true`. Simbol lain tetap DIMUAT (posisinya dikelola) dan bisa
dinyalakan/dimatikan kapan saja dari panel **Simbol** di dashboard (switch per simbol, dengan konfirmasi
& audit). Pilihan disimpan di `logs\symbols_active.json`; watchdog yang menghidupkan ulang bot memakai
pilihan terakhir. OFF = tidak ada entry baru; posisi yang terbuka tetap dikelola SL/trailing.
Dashboard: tab chart per simbol (lalu timeframe), tab **Performa per simbol** (WR & P/L per simbol +
TOTAL), filter simbol untuk tabel Posisi / Log sinyal / Tiket burst. `start_bot.cmd check` menampilkan
parameter, lot & jadwal per simbol.

## 18. Profil risiko & skill (28 Sep)

**Profil** (`config.json -> risk_profiles`) mengalikan nilai dasar per simbol (nilai dasar = NORMAL):

| parameter | CONSERVATIVE | NORMAL | AGGRESSIVE |
|---|---|---|---|
| skor sinyal minimum | 50% | 40% | 25% |
| SMC | wajib searah bias (strict) | pemberat skor | pemberat skor |
| cooldown antar-entry per simbol | 60 mnt | 10 mnt | 2 mnt |
| entry di candle berjalan | tidak | tidak | ya (sinyal bertahan 20 dtk) |
| burst aktif per simbol | 1 | 1 | 2 |
| slot grup (× nilai dasar) | ×0,5 | ×1 | ×2 |
| risk per tiket (× dasar, maks `risk_per_trade_cap_pct` 0,30%) | ×0,5 | ×1 | ×1,5 |
| ATR trailing (× dasar) | ×0,8 | ×1 | ×1,1 |

Tidak disentuh profil mana pun: rugi harian 4%, HALT DD 35%, `risk.max_account_open_risk_pct` 6%
(satu risk manager untuk total eksposur semua simbol). AGGRESSIVE ditolak di mode live kecuali
`risk_profiles.allow_aggressive_live: true`. Skor dicek pada 872 sinyal emas historis: ≥25 lolos 97,5%
(PF 1,40), ≥40 74% (PF 1,42), ≥50 58% (PF 1,48), ≥60 48% (PF 1,24 — karena itu CONSERVATIVE = 50).
Entry candle berjalan, cooldown & 2 burst per simbol TIDAK bisa di-backtest dengan data M5 — nilai dari demo.

**Skill** (`bot/skills/`, satu file = satu skill): candles, structure, smc, learning (butuh ketiganya),
trailing, session_news, burst. Skill OFF tidak dipanggil engine & tidak ikut skor (bobot sisanya
dinormalisasi); lapisannya hilang dari chart web & MT5. Trailing OFF hanya untuk posisi BARU.

**Ganti profil / skill** (selalu: perintah -> bot memvalidasi -> ACK; tanpa ACK 5 dtk = gagal, tampilan kembali):
- Dashboard: tombol CONSERVATIVE / NORMAL / AGGRESSIVE di atas (status MENUNGGU KONFIRMASI -> AKTIF),
  panel "Profil & skill" (switch skill, profil per simbol).
- CMD: `start_bot.cmd profile AGGRESSIVE` · `start_bot.cmd profile NORMAL BTCUSD` · `start_bot.cmd skill smc off`
  · start dengan profil: `start_bot.cmd bot AGGRESSIVE XAUUSD,BTCUSD`.
- PowerShell: `.\start_bot.ps1 -Profile Aggressive` (bot jalan = ganti langsung; belum jalan = start dengan profil).
Pilihan tersimpan di `logs\control_state.json`, perubahan dicatat di `logs\control_audit.log`
(lama -> baru, sumber, status ACK). Posisi terbuka tetap memakai parameter saat dibuka.
Evaluasi per profil & per kombinasi skill: tab "Evaluasi profil/skill" (`logs\trades_meta.csv`).

**Menambah skill baru:** buat `bot/skills/nama.py` berisi
```python
SKILL = {"id": "nama", "order": 8, "kind": "score", "weight": 1.0, "deps": [], "layers": [],
         "name": "Nama tampil", "desc": "satu baris"}
def evaluate(ctx):            # ctx: df, row, direction, atr, sym, swing_k, learner
    return {"score": 0.0, "lines": ["alasan"]}      # skor -1 (melawan) .. +1 (mendukung)
```
Registry menemukan file itu otomatis; dashboard, skor, ACK & state langsung mengenalnya.
`kind: "behavior"` = skill tanpa skor yang diperiksa engine lewat `Bot.skill("id")`.

## 19. Diagnosis lanjutan (28 Sep): rencana posisi, S/R MTF, berita, data 2 tahun, TETAP RUN

**P7 — mode per simbol.** Kartu simbol punya selector OTOMATIS / TETAP RUN / TETAP BLOKIR. TETAP RUN
hanya melewati `daily_budget_gate` (anggaran rugi harian) & cooldown profil — TIDAK melewati rem rugi
harian, HALT drawdown, margin, spread, atau batas posisi. Lot dikali `force_run.lot_mult` (default 0,5),
kedaluwarsa otomatis di akhir hari server atau `max_hours` (default 8), mana lebih dulu; kembali ke
OTOMATIS dicatat di audit dengan sumber "kedaluwarsa". Anggaran harian sendiri juga diperbaiki: dulu
memblokir SELURUH burst begitu 1 tiket saja tidak muat; sekarang burst DIKECILKAN agar muat sisa
anggaran (dicatat di `why.notes`), hanya diblokir total bila 1 tiket pun tidak muat. Perintah:
`start_bot.cmd bot`/dashboard → `POST /api/control {action:"override", symbol, mode}` →
`python -m bot.control --mode RUN --symbol BTCUSD [--hours N]` → ACK sama seperti profil/skill.
Diuji: 12 kasus (blokir anggaran, cooldown, HALT, rem harian, spread, kedaluwarsa 12 detik, TETAP
BLOKIR) — RUN terbukti melewati anggaran & cooldown, TETAP tunduk ke rem harian/HALT/spread/margin.

**Batasan baru pada entry manual** (post-mortem PF 0,76 sebelumnya, `manual.*`): tombol BUY/SELL MT5
ditolak bila melawan tren H1 (EMA50, 3 jam) atau sudah ada 1 burst manual terbuka di simbol itu.

**P3 — rencana posisi (trade plan).** `bot/analysis/plans.py`: SL = SL asli bot (dijepit per simbol),
TP1/TP2 dari S/R terdekat searah (fallback 1R/2R), invalidasi = swing/level terdekat di balik entry +
buffer ATR. Status MENUNGGU (garis putus-putus) → AKTIF (SL ikut trailing posisi nyata) → SELESAI
(memudar `closed_marker_minutes`) atau DITOLAK (abu-abu + alasan, toggle "Tampilkan sinyal ditolak").
Level BUKAN janji arah harga (ditulis di setiap tooltip). Sumber tunggal: `state.json["plans"]`, dibaca
dashboard web (`/api/chart`) dan bridge MT5 (`plan_lines()`, objek `PL_<key>_*`) — terverifikasi
identik karena keduanya membaca field yang sama.

**P4 — S/R multi-timeframe.** `bot/analysis/sr_mtf.py`: swing H1/H4/D1/W1 digabung jadi zona (lebar
0,35×ATR H1), kekuatan dari jumlah sentuhan×bobot TF + kedekatan waktu + rejection wick (jadi opasitas/
ketebalan garis di chart, gaya garis beda per TF asal). Level yang berganti peran (S↔R) diberi label
"flip". Referensi: PDH/PDL, PWH/PWL, high/low sesi Asia/London/NY hari ini, level bulat
(`symbols.<SYM>.round_step`). Dibatasi 6 zona + 8 referensi terdekat harga (`config.sr`, bisa diubah).

**P5 — News Impact** (skill baru, `bot/skills/news_impact.py` + `bot/analysis/newsimpact.py`).
Sumber: kalender FairEconomy (sudah dipakai `news_filter`, cache `logs/news_events.json` bertambah
kumulatif) + RSS resmi **Federal Reserve** dan **SEC** (domain pemerintah AS, publik, User-Agent
disertakan, refresh 30 menit — bukan scraping situs berbayar/berbatas ToS). Peta relevansi per simbol
di `config.news_impact.categories.*.tier` (bisa diedit): FOMC/inflasi/NFP/politik AS/geopolitik untuk
emas; regulasi kripto/kegagalan bursa untuk BTC & ETH; upgrade jaringan untuk ETH. Chart: garis vertikal
+ ikon per kategori, warna per tier (merah=High, kuning=Medium, abu=Low). Event lampau menampilkan
reaksi harga TERUKUR dalam ATR (+5/+15/+60 menit, dari `bot/analysis/newsimpact.reaction()`, bukan
tebakan arah); event mendatang menampilkan hitung mundur. Statistik reaksi historis (NFP dari aturan
kalender, FOMC dari `news_impact.fomc_dates`) ditandai "sampel sedikit" bila n < `min_samples` (8).
Blackout: entry baru diblokir (atau lot dikecilkan/SL dilebarkan bila `action: "reduce"`) di sekitar
event tier terkonfigurasi untuk simbol terdampak; posisi terbuka tetap dikelola. **Headline TIDAK
pernah memicu entry** — hanya dipetakan ke kategori untuk ditampilkan & dihitung blackout-nya.
Digambar juga di MT5 lewat bridge (`news_lines()`), sumber sama dengan dashboard.

**P6 — data 2 tahun & realtime.** `bot/datastore.py`: cache SQLite lokal, unduh bertahap per chunk
(mengatasi batas *Max bars in chart*), update inkremental (hanya bar setelah cache terakhir, thread
`_sync_loop` tiap 60 dtk). `GET /api/bars` mengambil rentang waktu & meresample di SERVER ke ≤2000 titik
(gabung k bar jadi 1 candle) — browser tidak pernah menerima lebih dari itu. Tombol "Histori 2 thn" di
chart: lazy-load saat geser ke kiri, "Muat lebih ke belakang". Candle berjalan diperbarui lewat
**SSE** (`/api/stream`, throttle 1 dtk); histori dimuat sekali saja. `/api/datastatus` menampilkan
rentang data & status sinkron. Bar tersedia per simbol/TF (26 Sep): lihat bagian 17 — M15/H1/H4
2 tahun penuh (XAUUSD247 sejak 11/12/25), M5 ±100 ribu bar, M1 ±100 ribu bar (±2 bulan, dibatasi
riwayat MT5). **Benchmark Plotly** (`window.benchChart(n)`): 10k bar 0,9 dtk render/0,8 dtk zoom —
lancar; 50k bar 2,7/4,0 dtk; **200k bar 7,3/9,3 dtk — TIDAK mulus**. Karena itu windowing di atas WAJIB
dipakai; migrasi ke library chart lain TIDAK dievaluasi karena windowing sudah cukup untuk kasus ini
(overlay SMC/pattern/S-R/plan/news semuanya hanya dihitung untuk jendela tampilan, tidak 2 tahun sekaligus).

## 20. Jarak minimum antar-entri searah (28 Sep, Prioritas 3)

Posisi searah pada simbol sama yang terlalu berdekatan = risiko berkorelasi tanpa diversifikasi.
`Bot.entry_blockers()` sekarang menolak entry (otomatis maupun manual dari tombol MT5) kalau jaraknya
ke posisi searah TERDEKAT < `min_spacing_atr × ATR` (config `risk.min_spacing_atr`, dioverride per
profil di `risk_profiles.profiles.<NAMA>.min_spacing_atr`):

| Profil | min_spacing_atr |
|---|---|
| CONSERVATIVE | 1.0 |
| NORMAL | 0.5 (nilai dasar) |
| AGGRESSIVE | 0.3 (tidak pernah 0 — aturan tidak bisa dihapus profil manapun) |

Sama persis diterapkan di backtest (`simulate_portfolio`) untuk pengujian yang konsisten dengan bot
live. Regresi 12 bulan emas: PF 1,434 (dari 1,441 sebelumnya) — dampaknya dapat diabaikan karena
mode burst `multiply` membuka semua tiket satu sinyal di harga yang sama; aturan ini menyaring
sinyal-sinyal TERPISAH yang kebetulan berdekatan. Diuji 6 skenario (tanpa posisi, dekat, jauh, arah
beda, per-profil) — semua sesuai ekspektasi.

Dashboard: kartu simbol menampilkan **risiko searah gabungan** per arah — jumlah posisi, total lot,
harga rata-rata, dan kerugian kalau SEMUA SL kena (mata uang akun & % equity), dihitung dari posisi
nyata MT5 (`write_state()` → `symbols.<SYM>.risk_same_dir`).

## 21. Diagnosis "kenapa market lain belum entry" (28 Sep #2)

`atr_band` sebenarnya SUDAH adaptif: persentil ATR bergulir (window 288 bar TF eksekusi ≈ 24 jam M5 /
72 jam M15), menolak kalau ATR sekarang di bawah persentil 40% (terlalu sepi) atau di atas 95% (spike).
Yang statis hanya angka cutoff-nya (0,4/0,95), bukan pengukurannya. Funnel 7 hari: 18–31% sinyal per
simbol lolos SEMUA filter termasuk `trend_ema` (searah tren kuat) — dugaan "hampir tidak ada setup
continuation" TIDAK terbukti. Heartbeat & candle semua real-time (<1 dtk), 0 kegagalan `order_send`.
Prioritas 2 (ambang atr_band adaptif berbasis rezim/kalender, atr_band jadi soft-filter untuk
AGGRESSIVE, setup continuation baru dengan backtest walk-forward) **BELUM dikerjakan** — perlu
perubahan arsitektur filter (hard vs soft) yang lebih besar; menunggu keputusan apakah diprioritaskan.

## 22. Rombak grafik (28 Sep #2, Prioritas 4)

Murni tampilan, TIDAK mengubah logika trading:
- **Agregasi posisi** (`bot/analysis/trade_marks.py`): posisi searah lintas-burst pada simbol sama
  digambar SATU garis + label ringkas ("SELL x7 · avg 82.985,49 · 0,07 lot · SL pita · floating ...")
  dengan pita SL/TP, bukan satu garis per posisi. Tiket individu = segitiga kecil tanpa teks. Identik
  di web (`state.json["trade_marks"]`, status `open_agg`) dan MT5 (`bot/bridge.py mark_lines()`,
  objek `AGG_<SYM>_<ARAH>_*`).
- **Preset Bersih / Trading (default) / Belajar** menggantikan 4 preset lama. Trading: posisi, titik
  entry, plan terbaik, S/R, berita saja — candlestick & SMC pindah ke Belajar. Terverifikasi: Bersih
  3–6 anotasi, Trading 25, Belajar 53–56 (dulu tidak terbatas).
- Layer `plan`/`entryline` sekarang hanya kandidat (MENUNGGU/DITOLAK) — AKTIF sudah diwakili `trades`
  (agregat), jadi tidak ada lagi duplikat TP1/TP2. DITOLAK disembunyikan default.
- Label pola candlestick dibatasi 60 bar terakhir & maksimum 25 label per layar. S/R lokal & S/R
  multi-TF dipisah 3 support + 3 resistance (bukan campur 4–6).
- MT5: indikator cek `ChartGetInteger(CHART_SHOW_TRADE_LEVELS)` — kalau opsi bawaan "Show trade
  levels" aktif, EA tidak menggambar ulang objek posisi (`InpSkipPosIfMT5Levels`, default true).
- Sinkron klik tabel Posisi → sorot garis chart (satu arah; chart → tabel belum ada).

## 23. Audit 29 Sep — temuan & perbaikan

| # | Temuan | Status |
|---|---|---|
| 1 | **Bot mati 749 mnt (12,5 jam) dari ±17,7 jam pada 29/09** (3 celah). Log daya Windows: Modern Standby karena **lid ditutup** (01:01, 14:45) dan **baterai** (10:35 "Sleep Reason: Battery"; 01:28 hibernate "Standby Battery Budget Exceeded"). `keep_awake` tidak bisa mencegah standby akibat lid/baterai. Tidak ada tiket tertutup selama celah (tidak ada kerugian langsung), tapi trailing/BE mati & posisi hanya dijaga SL server. | Bot kini mendeteksi & mencatat celah (`TIDUR ...` di bot.log, `state.gaps`, banner dashboard 1 jam). **Perlu tindakan Anda:** colok charger terus, atur "When I close the lid" = Do nothing (Control Panel → Power Options), atau jalankan bot di PC/VPS yang selalu hidup |
| 2 | **Celah keamanan web**: POST lintas-origin diterima (HTTP 200) & Host tidak divalidasi → halaman web mana pun di browser bisa mengirim EMERGENCY STOP / ganti profil / TETAP RUN, tercatat sbg "web" | Diperbaiki: validasi Host & Origin, POST wajib `application/json` (tanpa preflight), CORS `/api/ping` hanya origin lokal, id ACK divalidasi |
| 3 | Jalur STOP dari CMD/PowerShell/konsol tidak masuk `control_audit.log` (insiden STOP 28/09 21:20 tak bisa ditelusuri) | Diperbaiki: ketiganya kini mencatat; bot juga mencatat isi & waktu pembuatan flag saat menerima |
| 4 | Regresi P4: `entry_lines()`/`plan_lines()` di bridge MT5 masih menggambar plan AKTIF (dobel dgn agregat) + default `rejected` MT5 = tampil (web = sembunyi) | Diperbaiki: hanya kandidat MENUNGGU/DITOLAK; `bridge.layers` diperbarui |
| 5 | `trades_meta.csv` tak mencatat jenis entri → tab Evaluasi mencampur trade **manual** ke statistik profil; evaluasi intrabar mustahil | Diperbaiki: kolom `entry` (bar/intrabar/manual), baris lama di-backfill (manual / auto(?)), tab Evaluasi memisahkan "profil · jenis" |
| 6 | Log/dashboard menampilkan "skor 72%" = peluang menang model belajar, bukan skor gabungan yang dibandingkan dgn `min_score` | Diperbaiki: log "skor gabungan 38% (min 25%; model belajar 72%; SMC -1.0)", panel alasan entry menampilkan skor gabungan + komponen skill |
| 7 | **3 dari 3 burst otomatis di bawah AGGRESSIVE (BTCUSD SELL) rugi −2,85 jt**: ketiganya entri candle berjalan, SMC = −1,0 (melawan bias struktur), skor gabungan 26–38 (min AGGRESSIVE 25). Profil NORMAL (min 40) akan menolak ketiganya. n = 3 burst → belum signifikan | **Diterapkan 29 Sep (atas persetujuan):** entri candle berjalan butuh skor gabungan ≥ `intrabar_min_score` (40) DAN komponen SMC ≥ `intrabar_smc_min` (0) — config `risk_profiles.profiles.<NAMA>` (AGGRESSIVE & NORMAL). Entri bar-close tetap min_score biasa. Diuji 5 kasus (termasuk ketiga kasus rugi asli → ditolak). Evaluasi via tab "Evaluasi profil" (kolom jenis entri) |
| 8 | **Uji gerbang R:R minimum (walk-forward 60/40, 29 Sep) — DITOLAK, tidak diterapkan.** Emas M5 (872 trade): gerbang rr1≥1,0 menaikkan PF in-sample 1,43→1,76 tapi **out-of-sample turun 1,27→1,06** (bucket tidak monoton: rr 0–0,5 justru PF 1,45 OOS, rr 1–1,5 rugi di IS & OOS). BTC M15: gerbang menyisakan hanya 3–14% trade (tak bermakna). Sebab: exit bot = SL/trailing (tanpa TP broker), jadi jarak ke S/R bukan penentu hasil. Skrip: scratchpad `rr_gate.py` | Tidak ada perubahan |
| 9 | **Uji pelonggaran `atr_band` (walk-forward 60/40) — DITOLAK.** Emas: sekarang 0,40–0,95 → OOS PF 1,27 / DD −30%; min 0,25 → 1,20 / −45%; min 0,10 → 1,23 / −46%; dimatikan → 1,06 / −55%. BTC: sekarang OOS 1,16 / DD −13%; semua varian lebih buruk (PF 0,89–1,08, DD −17…−30%). Filter ini melindungi hasil; jangan dilonggarkan. Skrip: scratchpad `atr_wf.py` | Tidak ada perubahan |
| — | Diperiksa & aman: latensi endpoint (0,03–0,7 dtk), tidak ada exception di log, 0 kegagalan order_send, cache berita ringan |  |

**Belum dikerjakan:** collision-avoidance label penuh (gutter + garis penghubung), semantic zoom,
skenario uji stres 10 posisi searah.

## 24. Panel "Saran Level Trading" & EA Strategy Tester (29 Sep)

**Panel** (dashboard, kartu di bawah chart): satu baris per instrumen — Symbol | Arah | Entry (rentang entry–pemicu) | SL | TP1 | TP2 | R:R | Skor | Status — untuk XAUUSD, XAUUSDc, XAUUSD247, ETHUSD, BTCUSD serentak. XAUUSDc yang belum ada di akun menampilkan "Symbol tidak tersedia di akun saat ini" dan otomatis aktif begitu tersedia. Status: SIAP ENTRY / MENUNGGU KONFIRMASI (sinyal candle berjalan) / POSISI AKTIF / TIDAK ADA SETUP LAYAK (NETRAL; sinyal terakhir ditolak filter tetap disebut). Instrumen yang tertahan (anggaran/cooldown) tetap menampilkan level + catatan "entry otomatis tertahan: ...". Klik baris = detail (sumber SL/TP1/TP2, invalidasi, konfluensi per skill, waktu update) + pindah chart ke simbol itu; perubahan status di-highlight sesaat. **Sumber tunggal:** membaca `state.plans` (plan yang sama dgn chart & bot; diverifikasi identik), tanpa rumus baru. Field baru di plan: `tf`, `updated`, `sl_src`, `tp1_src`, `tp2_src`, `inv_src`, skor & `skill_scores` untuk kandidat (via `analyze_entry`, di-cache per sinyal).
Batasan jujur: modul "Zona Entry Optimal" dan aturan SL struktural belum pernah dibangun, jadi entry = rentang entry–pemicu plan dan SL = ATR (dijepit); R:R hanya informasi karena gerbang R:R minimum gagal walk-forward (§23 #8).

**EA Strategy Tester** — `mql5/XAUUSD_Tester_EA.mq5` (terpasang & compile 0 error di `MQL5\Experts\XAUUSD_EXNESS\`). Replika strategi bot: EMA 8/21 cross bar tertutup, filter EMA200 & `atr_band` (persentil ATR Wilder 288 bar), sesi UTC, spread maks, SL ATR (jepit % harga), lot dari risiko % equity (bulat ke bawah), burst N tiket, jarak minimum antar-entri, rem harian, jeda kalah beruntun, exit adaptif (BE-lock, trailing ATR, ketat, candle trail), tutup pada sinyal berlawanan, batas waktu tahan. Default = NORMAL emas M5; nilai per simbol (BTC/ETH M15, SL/trail, sesi `late` ON, jepit SL %) ada di §17. Pakai: Strategy Tester → Expert `XAUUSD_EXNESS\XAUUSD_Tester_EA`, Model "Every tick based on real ticks". Belum dijalankan di tester (hanya compile); tanpa learning/SMC/berita/profil dinamis.

## Sebelum mode live

Gerbang yang **dipaksakan kode** saat `mode: "live"`:
1. akun yang login harus REAL;
2. `live_trading_confirmed: true`;
3. `backtest\results\latest_metrics.json` berumur ≤ 30 hari, PF ≥ 1 untuk tiap simbol aktif, dan
   **sidik jari config sama** dengan config sekarang (strategi, exit, risiko, burst, SMC, sesi,
   simbol) — ubah satu parameter → wajib backtest ulang;
4. `learning.mode: "gate"` hanya aktif di live kalau validasi walk-forward membuktikan manfaatnya.

Urutan yang saya sarankan:
1. **Backtest** (`start_bot.cmd backtest`), baca `summary.txt` & validasi learning; jalankan juga `--slippage 30`.
2. **Paper** ≥ 1 minggu — cek log `ENTRY`/`WHY`/`BURST`, rem harian, dan dashboard web.
3. **Demo** 2–4 minggu / ≥ 100 burst — bandingkan WR, rata-rata menang/kalah & PF di
   `logs\trades.csv` dengan backtest. Selisih besar = biaya riil lebih buruk → jangan live.
4. **Live dengan risiko diturunkan** (mis. `burst_size` 1–2 atau `risk_per_trade_pct` lebih kecil,
   lalu backtest ulang config itu). Ingat batas lot minimum 0,01 di modal kecil; akun Cent
   (XAUUSDc) memberi granularitas 100× lebih halus.
5. Isi pernyataan di bawah, lalu set `"mode": "live"` dan `"live_trading_confirmed": true`.

```
Saya, ______________________, pada tanggal ____________ menyatakan bahwa:
[ ] sudah menjalankan backtest terbaru (tanggal: ________) dengan config yang akan dipakai
    (PF: _____  max DD: _____%  jumlah trade: _____)
[ ] sudah menjalankan mode demo selama ____ minggu / ____ burst, hasil: WR ____%  PF ____
[ ] memahami burst multiply MELIPATGANDAKAN risiko per sinyal, dan hasil masa lalu tidak
    menjamin hasil ke depan; saya bisa kehilangan seluruh dana di akun live
[ ] risk_per_trade_pct live ____% × burst ____ = ____% per sinyal; daily_loss_limit_pct ____%
```


## 25. Panel "Saran Level Trading" (sidebar, 4 kartu)
- Lokasi: sidebar, tepat di bawah RINGKASAN & di atas SIMBOL (panel `lv`). Versi awal ada di kolom utama di bawah chart, karena itu tidak terlihat di sidebar.
- Sumber angka: `Bot.level_card()` (bot/main.py) -> `plans.build()` (SAMA dengan plan burst/kandidat/chart/MT5), SL = `Bot.sl_distance()`, lot = `Bot.calc_lot()`, S/R = `sr_mtf.compute()`. Dikirim lewat `state.levels[sym]` (+ `updated`, `tf`, `sumber`).
- Kondisi: SETUP AKTIF (plan AKTIF), MENUNGGU KONFIRMASI, TAHAN (plan DITOLAK: alasan + nilai aktual vs ambang `filter_detail()` + ETA), SKENARIO PANTAUAN (bersyarat dari zona S/R terdekat, aturan SL/TP sama).
- Sisi BUY & SELL selalu ada; "utama" = searah bias (close vs EMA200, sama dgn filter trend_ema) atau arah plan.
- DATA BASI: `updated` > 2 siklus candle, koneksi MT5 putus, atau state > 20 dtk -> overlay, angka diredupkan, tombol salin disembunyikan.
- Cek SL/TP di MT5: `Bot.position_checks()` -> `positions[].sl_ok/tp_ok/sltp_warn`; tabel Posisi + kartu. SL kosong = peringatan merah + log `CEK-SLTP`. TP kosong hanya diperingatkan bila `risk.require_tp=true` (default false: TP=0 by design, exit = SL + trailing).
- `plans.rr_ref` (default 1,5) hanya penanda informasi R:R, BUKAN gerbang (gerbang R:R sudah diuji walk-forward & ditolak).
- Keterbatasan: modul "Zona Entry Optimal" & SL struktural belum ada; skenario bersyarat memakai zona S/R (belum OB/FVG). Level mengikuti bar tertutup (PROVISIONAL hanya utk sinyal candle berjalan), bukan tiap tick.
