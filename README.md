# ⚡ XAUUSD Trading Bot - Automated Algorithmic Trading System

Bot trading otomatis multi-strategi untuk **MetaTrader 5 (MT5)** yang dirancang khusus untuk instrumen **Gold (XAUUSD)** dengan arsitektur modular, manajemen risiko institusional, dan Web Dashboard interaktif real-time.

Repository: **[https://github.com/ilhamoeba22/bot_xauusd.git](https://github.com/ilhamoeba22/bot_xauusd.git)**

---

## 🌟 Fitur Utama

- **Multi-Strategy Architecture (Dapat Di-switch Real-Time)**:
  - **Pure Zone Scalper (Bollinger Bands M3)**: Scalping pembalikan arah saat harga menyentuh batas luar zona (Support / Resistance), dilengkapi **Take Profit otomatis di Garis Tengah SMA 20** (`close_on_bb_mid`) dan pengunci profit **BE-LOCK (SL+)**.
  - **Smart Rejection Mode**: Filter Price Action cerdas pencegah "pisau jatuh" (*falling knife*) dan lilin *Marubozu* solid.
  - **SMC Multi-Timeframe (M15 + M3)**: Deteksi Order Block M15 + konfirmasi CHoCH & FVG M3 dengan eksekusi Limit Order.
  - **Candle Break & EMA Momentum**: Scalping cepat mengikuti ledakan volatilitas terarah.
- **Manajemen Risiko & Pengamanan Modal Berlapis**:
  - **Stop Loss Otomatis Disiplin (1.5x ATR)**: Membatasi risiko per tiket (~0.3% - 0.5% ekuitas) tanpa sistem *martingale* berbahaya.
  - **BE-LOCK / SL+ (Breakeven Lock)**: Begitu harga terapung profit $\ge 1.0\times$ ATR, Stop Loss otomatis dimajukan melewati harga entri (+0.15 ATR) untuk mengunci profit hijau.
  - **Batas Rugi Harian (*Daily Loss Limit*) & Max Drawdown**: Sistem otomatis mematikan bot jika batas harian tersentuh untuk melindungi modal.
  - **Burst Scaling Execution**: Membagi order menjadi beberapa tiket terukur (misal 3 tiket x 0.02 lot) untuk manajemen parsial keluar posisi.
- **Web Dashboard Interaktif (Port 8765)**:
  - Monitoring grafik live, floating profit/loss, status eksekusi tiket, radar sinyal, riwayat transaksi, dan tombol kontrol instan (*Emergency Stop*, Ganti Strategi, Ganti Profil Risiko).
- **MQL5 MT5 Chart Bridge**:
  - Indikator MQL5 opsional untuk menggambar zona Support/Resistance, Order Block, dan rencana level transaksi langsung di layar MT5.

---

## 📋 Prasyarat Sistem (Prerequisites)

Sebelum memulai setup di PC baru, pastikan hal-hal berikut sudah terpasang:
1. **Sistem Operasi**: Windows 10 atau Windows 11 (64-bit).
2. **Python**: Versi **3.10, 3.11, atau 3.12** (Sangat disarankan Python 3.11 atau 3.12 64-bit).
3. **MetaTrader 5 (MT5)**: Terpasang dan sudah login ke akun broker pilihan Anda (Exness, HFM, IC Markets, dll).
4. **Git for Windows**: Terpasang untuk memudahkan proses clone dan update kode.

---

## 🚀 Panduan Setup Lengkap dari 0 di PC Baru (Step-by-Step)

Ikuti langkah-langkah presisi berikut saat meng-clone repository ini ke komputer baru yang belum terpasang environment bot:

### Langkah 1: Persiapan Python di PC Baru
1. Kunjungi situs resmi [python.org/downloads](https://www.python.org/downloads/).
2. Unduh installer **Python 3.11 atau 3.12 (Windows installer 64-bit)**.
3. Jalankan installer:
   > ⚠️ **SANGAT PENTING**: Pada layar pertama instalasi, centang kotak **"Add python.exe to PATH"** sebelum mengklik *Install Now*.
4. Buka **Command Prompt (CMD)** atau **PowerShell**, ketik:
   ```cmd
   python --version
   ```
   Pastikan versi Python muncul (misal: `Python 3.11.x` atau `3.12.x`).

---

### Langkah 2: Konfigurasi MetaTrader 5 (MT5)
1. Buka aplikasi **MetaTrader 5** Anda.
2. Login ke akun trading Anda (*File -> Login to Trade Account*).
3. Aktifkan izin Algo Trading:
   * Buka menu: **Tools** $\rightarrow$ **Options** (atau tekan `Ctrl + O`).
   * Pilih tab **Expert Advisors**.
   * Centang:
     - ✅ **Allow Algo Trading**
     - ✅ **Allow WebRequest for listed URL**
   * Klik **OK**.
4. Di jendela **Market Watch** (tekan `Ctrl + M`), pastikan simbol emas aktif terlihat (misalnya `XAUUSD` atau `XAUUSDm` sesuai jenis akun broker Anda).

---

### Langkah 3: Clone Repository dari GitHub
Buka **CMD** atau **PowerShell** di folder yang Anda inginkan (misalnya di drive `D:\Trading` atau `C:\Users\<User>\Documents`), lalu jalankan perintah:

```cmd
git clone https://github.com/ilhamoeba22/bot_xauusd.git
cd bot_xauusd
```

---

### Langkah 4: Membuat Virtual Environment & Install Dependencies
Di dalam folder project `bot_xauusd`, buat lingkungan virtual Python terisolasi dan pasang seluruh pustaka yang dibutuhkan:

```cmd
python -m venv .venv
```

Aktifkan virtual environment:
* **Pada Command Prompt (CMD)**:
  ```cmd
  .venv\Scripts\activate
  ```
* **Pada PowerShell**:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
  *(Jika muncul error script execution policy di PowerShell, jalankan dulu: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`)*.

Upgrade pip dan install semua dependensi:
```cmd
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> **Catatan TA-Lib (Opsional)**:
> Pustaka `TA-Lib` opsional untuk deteksi candlestick tambahan. Mesin trading utama bot menggunakan algoritma bawaan murni (*pure NumPy/Pandas*), sehingga jika TA-Lib gagal dikompilasi di Windows, bot **tetap dapat berjalan 100% normal**.

---

### Langkah 5: Verifikasi Koneksi ke MT5
Pastikan aplikasi MetaTrader 5 sedang terbuka dan login di layar Anda, lalu jalankan verifikasi:

```cmd
start_bot.cmd check
```
*Atau via perintah python langsung:*
```cmd
python -m bot.control --check
```

Jika output menampilkan informasi akun broker Anda (Login, Nama Server, Saldo, dan Ekuitas), artinya koneksi Python ke MT5 telah **berhasil 100%**.

---

### Langkah 6: Menjalankan Bot & Web Dashboard

Anda memiliki 2 opsi cara menjalankan bot:

#### Opsi A: Cara Otomatis (All-in-One Launcher)
Cukup jalankan script launcher bawaan:
```cmd
start_bot.cmd all
```
Perintah ini akan secara otomatis membuka dan menjalankan:
1. **Bridge MT5 Data Sync**
2. **Trading Bot Engine Utama**
3. **Web Server Dashboard**

#### Opsi B: Menjalankan per Komponen di Jendela Terpisah
Jika ingin memantau log per modul secara terpisah:
1. **Terminal 1 (Bot Trading Utama)**:
   ```cmd
   python -m bot.main
   ```
2. **Terminal 2 (Web Server Dashboard)**:
   ```cmd
   python dashboard/web_server.py --no-browser
   ```
3. **Terminal 3 (Bridge Data MQL5)**:
   ```cmd
   python -m bot.bridge
   ```

---

### Langkah 7: Membuka Web Dashboard
Buka browser favorit Anda (Chrome / Edge / Firefox) dan buka alamat:
👉 **[http://localhost:8765](http://localhost:8765)**

Anda akan melihat dashboard pemantauan live yang menampilkan:
- Status akun, saldo, ekuitas, dan margin bebas.
- Indikator real-time dan zona aktif XAUUSD.
- Posisi terbuka dan riwayat keuntungan (*closed trades*).
- Tombol kontrol strategi dan emergency stop.

---

## ⚙️ Panduan Konfigurasi (`config/config.json`)

File konfigurasi utama terletak di [`config/config.json`](config/config.json). Anda dapat menyesuaikannya sesuai kebutuhan:

### 1. Pilihan Strategi Aktif (`active_strategy`)
- `"bb_reversion"`: **Pure Zone Scalper (Bollinger Bands M3)** *(Default & Rekomendasi)*.
- `"smc_mtf"`: **Smart Money Concepts Multi-Timeframe** (Order Block M15 + CHoCH M3).

Cara ganti strategi secara instan tanpa mematikan bot:
```cmd
python -m bot.control --strategy bb_reversion
```
atau
```cmd
python -m bot.control --strategy smc_mtf
```

### 2. Mode Trading (`mode`)
- `"demo"`: Eksekusi order nyata, tetapi hanya diizinkan di akun Demo.
- `"paper"`: Simulasi virtual internal (tidak mengirimkan order ke broker).
- `"live"`: Mode uang riil (membutuhkan konfirmasi eksplisit `"live_trading_confirmed": true`).

### 3. Pengaturan Risiko (`risk`)
```json
"risk": {
  "risk_per_trade_pct": 0.12,     // Risiko per tiket (% dari ekuitas)
  "burst_size": 3,                // Jumlah tiket split per setup (scaling out)
  "daily_loss_limit_pct": 3.0,    // Batas cut loss harian (3% dari modal)
  "max_drawdown_pct": 15.0        // Batas maksimal drawdown akun
}
```

---

## 🛠️ Perintah Berguna (`start_bot.cmd`)

| Kebutuhan | Perintah |
| :--- | :--- |
| Jalankan Semua Modul | `start_bot.cmd all` |
| Jalankan Bot Saja | `start_bot.cmd bot` |
| Jalankan Dashboard Web Saja | `start_bot.cmd web` |
| Jalankan Dashboard Konsol | `start_bot.cmd dashboard` |
| Cek Koneksi Akun MT5 | `start_bot.cmd check` |
| Hentikan Bot Normal | `start_bot.cmd stop` |
| Emergency Stop (Tutup Semua Posisi) | `start_bot.cmd emergency` |
| Reset File Kunci / Flag | `start_bot.cmd reset` |

---

## 🎨 Menampilkan Analisis di Chart MT5 (MQL5 Indicator Opsional)

Jika Anda ingin melihat level zona, Order Block, dan rencana entry bot digambar langsung di layar grafik MetaTrader 5:
1. Buka folder `mql5\` di repository ini.
2. Salin file [`mql5/XAUUSD_Bridge_Draw.mq5`](mql5/XAUUSD_Bridge_Draw.mq5) ke folder indikator MT5 Anda:
   * Di MT5: Klik **File** $\rightarrow$ **Open Data Folder**.
   * Masuk ke folder `MQL5\Indicators\`.
   * Paste file `XAUUSD_Bridge_Draw.mq5` di sana.
3. Buka **MetaEditor** (tekan `F4`), buka file tersebut, lalu klik **Compile** (atau tekan `F7`).
4. Lampirkan indikator tersebut ke grafik XAUUSD di MT5.

---

## ❓ Troubleshooting & Tanya Jawab Umum

| Kendala / Error | Solusi |
| :--- | :--- |
| **`python` is not recognized** | Pastikan Anda mencentang *"Add python.exe to PATH"* saat instalasi Python, lalu restart jendela CMD/PowerShell. |
| **MT5 tidak terhubung / failed to initialize** | Pastikan aplikasi MT5 sudah terbuka, sudah login ke akun broker, dan opsi *Algo Trading* sudah dicentang. |
| **Sinyal tidak muncul / bot tidak open posisi** | Bot bekerja berbasis zona. Bot disiplin menunggu harga menyentuh pita luar Bollinger Bands atau level Order Block. Anda dapat memantau statusnya di dashboard web. |
| **Port 8765 Already in Use** | Port dashboard sedang dipakai proses lain. Buka Task Manager dan matikan proses Python lama, atau jalankan `start_bot.cmd stop`. |

---

## 📁 Struktur Direktori Repository

```text
bot_xauusd/
├── bot/                     # Core Trading Engine
│   ├── analysis/            # Analisis Teknikal & Candlestick (SMC, S/R, Trend)
│   ├── bridge.py            # Sinkronisasi Data ke MT5 Bridge
│   ├── broker.py            # Modul Eksekusi Order MT5
│   ├── control.py           # CLI Control & Hot-switching
│   ├── indicators.py        # Perhitungan Indikator (BB, RSI, ATR, EMA, SMA)
│   ├── main.py              # Daemon Loop Trading Utama
│   ├── risk.py              # Manajemen Risiko & Validasi Drawdown
│   └── strategy.py          # Modular Strategy Engine (BB Pure Zone, SMC, Break)
├── config/                  # Konfigurasi JSON
│   └── config.json          # Parameter Utama, Simbol, Strategi, & Risk Gate
├── dashboard/               # Web Dashboard Server & Frontend UI
│   ├── web/                 # Antarmuka Web (HTML/CSS/JS)
│   └── web_server.py        # Web Server HTTP/WebSocket Dashboard
├── docs/                    # Panduan & Dokumentasi Tambahan
├── mql5/                    # Script & Indikator MT5
│   └── XAUUSD_Bridge_Draw.mq5
├── logs/                    # Direktori Runtime Logs & State
├── requirements.txt         # Daftar Dependensi Python
├── start_bot.cmd            # Launcher Windows All-in-One
├── start_bot.ps1            # Launcher PowerShell
└── README.md                # Dokumentasi & Panduan Lengkap
```

---

## 🛡️ Catatan Keamanan & Penafian (Disclaimer)

* **Manajemen Risiko**: Trading instrumen komoditas emas (XAUUSD) memiliki tingkat risiko dan volatilitas yang tinggi. Selalu uji bot terlebih dahulu di akun **Demo** sebelum menggunakan akun modal riil.
* **Keamanan Kredensial**: Password akun broker **TIDAK PERNAH disimpan di file konfigurasi**. Bot otomatis menggunakan sesi terminal MT5 yang sudah login secara lokal di komputer Anda.
* **Koneksi Internet & Power**: Disarankan menggunakan VPS Windows atau komputer yang menyala stabil dengan koneksi internet yang andal saat menjalankan bot trading otomatis 24 jam.

---
*Dibuat & Dikembangkan dengan Presisi untuk Trader Algo XAUUSD.*
