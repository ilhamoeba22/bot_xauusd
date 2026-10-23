//+------------------------------------------------------------------+
//| XAUUSD_Bridge_Draw.mq5                                           |
//| Menggambar objek analisis bot XAUUSD_EXNESS langsung di chart    |
//| MT5: pola candlestick, S/R, trendline, chart pattern, Order      |
//| Block, FVG, BOS/CHoCH, likuiditas, dan entry bot.                |
//|                                                                  |
//| Sumber data: file yang ditulis engine Python (python -m bot.bridge)|
//|   <Terminal>\Common\Files\XAUUSD_EXNESS\bridge_<SIMBOL>_<TF>.csv  |
//|   <Terminal>\Common\Files\XAUUSD_EXNESS\heartbeat.txt             |
//| Dibaca dengan FILE_COMMON -> TIDAK perlu DLL.                    |
//|                                                                  |
//| Ini INDIKATOR (bukan EA): tidak pernah mengirim order sendiri,   |
//| tidak butuh Algo Trading, dan bisa dipasang bersama EA lain.     |
//|                                                                  |
//| PANEL BUY / SELL (v1.10): tombol di chart -> pratinjau entry     |
//| (garis harga masuk, SL, label rencana) -> KONFIRMASI -> perintah |
//| ditulis ke Common\Files\<folder>\cmd_*.txt -> BOT Python yang    |
//| mengeksekusi (burst, SL x ATR, trailing, SEMUA rem risiko) lalu  |
//| menulis hasilnya ke cmdres_<SIMBOL>.txt yang tampil di panel.    |
//+------------------------------------------------------------------+
#property copyright   "XAUUSD_EXNESS"
#property version     "1.10"
#property description "Auto-draw analisis bot XAUUSD_EXNESS dari bridge Python (Common\\Files)."
#property indicator_chart_window
#property indicator_plots 0

input string InpFolder     = "XAUUSD_EXNESS"; // Folder di Common\Files (sama dengan config bridge.folder)
input int    InpTimerSec   = 2;               // Baca bridge tiap N detik
input int    InpStaleSec   = 15;              // Heartbeat lebih tua dari ini = Bridge disconnected
input string InpSymbolFile = "";              // Nama simbol di file bridge (kosong = simbol chart)
input string InpPrefix     = "XE_";           // Prefix nama objek (untuk cleanup)
input int    InpFontSize   = 8;               // Ukuran huruf label
input bool   InpShowStatus = true;            // Tampilkan status bridge di pojok chart
input bool   InpSkipPosIfMT5Levels = true;    // Jika opsi MT5 "Show trade levels" aktif, EA TIDAK
                                               // menggambar ulang entry/SL/TP posisi (AGG_/TRD_) —
                                               // hindari dobel garis; zona/plan/berita tetap digambar

string g_lastHdr = "";
int    g_count   = 0;
string g_label;

//+------------------------------------------------------------------+
string TfName(const ENUM_TIMEFRAMES p)
  {
   switch(p)
     {
      case PERIOD_M1:  return "M1";
      case PERIOD_M3:  return "M3";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
     }
   return "";
  }

string SymFile() { return (InpSymbolFile == "") ? _Symbol : InpSymbolFile; }

//+------------------------------------------------------------------+
void SetStatus(const string text, const color col)
  {
   if(!InpShowStatus)
      return;
   if(ObjectFind(0, g_label) < 0)
     {
      ObjectCreate(0, g_label, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, g_label, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, g_label, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, g_label, OBJPROP_YDISTANCE, 22);
      ObjectSetInteger(0, g_label, OBJPROP_FONTSIZE, 9);
      ObjectSetString(0, g_label, OBJPROP_FONT, "Arial Bold");
      ObjectSetInteger(0, g_label, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, g_label, OBJPROP_HIDDEN, true);
     }
   ObjectSetString(0, g_label, OBJPROP_TEXT, text);
   ObjectSetInteger(0, g_label, OBJPROP_COLOR, col);
  }

//+------------------------------------------------------------------+
//| Status indikator untuk dashboard web (ea_<SIMBOL>_<TF>.txt)      |
//+------------------------------------------------------------------+
void WriteStatus(const string st, const int n)
  {
   string f = InpFolder + "\\ea_" + SymFile() + "_" + TfName((ENUM_TIMEFRAMES)_Period) + ".txt";
   int h = FileOpen(f, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, StringFormat("%I64d;%s;%d;%s;%s\n", (long)TimeGMT(), st, n, _Symbol,
                                   TfName((ENUM_TIMEFRAMES)_Period)));
   FileClose(h);
  }

//+------------------------------------------------------------------+
long ReadHeartbeat()
  {
   int h = FileOpen(InpFolder + "\\heartbeat.txt",
                    FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return -1;
   string s = FileReadString(h);
   FileClose(h);
   return StringToInteger(s);
  }

//+------------------------------------------------------------------+
//| Buat / perbarui satu objek dari baris OBJ                        |
//| OBJ;id;type;t1;p1;t2;p2;R,G,B;style;width;flag;text;tooltip      |
//+------------------------------------------------------------------+
bool ApplyObject(const string name, string &f[])
  {
   string   typ   = f[2];
   datetime t1    = (datetime)StringToInteger(f[3]);
   double   p1    = StringToDouble(f[4]);
   long     t2raw = StringToInteger(f[5]);
   double   p2    = StringToDouble(f[6]);
   color    col   = StringToColor(f[7]);
   int      style = (int)StringToInteger(f[8]);
   int      width = (int)StringToInteger(f[9]);
   int      flag  = (int)StringToInteger(f[10]);

   ENUM_OBJECT ot;
   if(typ == "TREND")      ot = OBJ_TREND;
   else if(typ == "RECT")  ot = OBJ_RECTANGLE;
   else if(typ == "ARROW") ot = OBJ_ARROW;
   else if(typ == "TEXT")  ot = OBJ_TEXT;
   else
      return false;

   // jenis berubah dengan id yang sama -> buat ulang
   if(ObjectFind(0, name) >= 0 && (ENUM_OBJECT)ObjectGetInteger(0, name, OBJPROP_TYPE) != ot)
      ObjectDelete(0, name);

   bool two = (ot == OBJ_TREND || ot == OBJ_RECTANGLE);
   if(ObjectFind(0, name) < 0)
     {
      bool ok = two ? ObjectCreate(0, name, ot, 0, t1, p1, (datetime)t2raw, p2)
                    : ObjectCreate(0, name, ot, 0, t1, p1);
      if(!ok)
         return false;
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);      // tidak ikut daftar objek user
     }
   else
     {
      ObjectMove(0, name, 0, t1, p1);
      if(two)
         ObjectMove(0, name, 1, (datetime)t2raw, p2);
     }

   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, f[12]);

   ENUM_LINE_STYLE ls = (style == 1) ? STYLE_DASH : (style == 2) ? STYLE_DOT : (style == 3) ? STYLE_DASHDOT : STYLE_SOLID;
   if(ot == OBJ_TREND)
     {
      ObjectSetInteger(0, name, OBJPROP_STYLE, ls);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, flag == 1);
      ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
     }
   else if(ot == OBJ_RECTANGLE)
     {
      ObjectSetInteger(0, name, OBJPROP_STYLE, ls);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, name, OBJPROP_FILL, flag == 1);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);        // di belakang candle
     }
   else if(ot == OBJ_ARROW)
     {
      ObjectSetInteger(0, name, OBJPROP_ARROWCODE, (int)t2raw);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, flag == 1 ? ANCHOR_BOTTOM : ANCHOR_TOP);
     }
   else // OBJ_TEXT
     {
      ObjectSetString(0, name, OBJPROP_TEXT, f[11]);
      ObjectSetString(0, name, OBJPROP_FONT, "Arial");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, flag == 1 ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
     }
   return true;
  }

//+------------------------------------------------------------------+
//| Baca file bridge; gambar semua objek, lalu HAPUS objek prefix    |
//| yang tidak ada lagi di file (pola batal / keluar jendela).       |
//| Return: -1 file tidak ada, 0 tidak berubah, 1 digambar ulang.    |
//+------------------------------------------------------------------+
int DrawFile(const string fname, int &count)
  {
   int h = FileOpen(fname, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return -1;
   string hdr = FileReadString(h);
   if(hdr == g_lastHdr)
     {
      FileClose(h);
      return 0;                                            // belum ada perubahan
     }
   string keep[];
   int nk = 0;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      if(StringLen(line) < 5)
         continue;
      string f[];
      if(StringSplit(line, ';', f) < 13 || f[0] != "OBJ")
         continue;                                         // baris rusak diabaikan, tidak crash
      // MT5 sudah punya "Show trade levels" bawaan (garis entry/SL/TP posisi) -> jangan dobel
      if(InpSkipPosIfMT5Levels && (StringFind(f[1], "AGG_") == 0 || StringFind(f[1], "TRD_") == 0)
         && (bool)ChartGetInteger(0, CHART_SHOW_TRADE_LEVELS))
         continue;
      string name = InpPrefix + f[1];
      if(ApplyObject(name, f))
        {
         ArrayResize(keep, nk + 1, 256);
         keep[nk++] = name;
        }
     }
   FileClose(h);

   // cleanup berbasis nama unik: semua objek ber-prefix yang tidak ada di file dihapus
   for(int i = ObjectsTotal(0, 0, -1) - 1; i >= 0; i--)
     {
      string nm = ObjectName(0, i, 0, -1);
      if(StringFind(nm, InpPrefix) != 0 || nm == g_label)
         continue;
      bool found = false;
      for(int j = 0; j < nk; j++)
         if(keep[j] == nm)
           {
            found = true;
            break;
           }
      if(!found)
         ObjectDelete(0, nm);
     }
   g_lastHdr = hdr;
   count = nk;
   return 1;
  }


//+------------------------------------------------------------------+
//| PANEL ENTRY MANUAL (BUY / SELL)                                  |
//| Aturan MT5: indikator TIDAK boleh mengirim order. Tombol hanya   |
//| menulis perintah; bot Python yang membuka posisi — dengan burst, |
//| SL, trailing dan semua rem risiko yang sama dengan entry otomatis.|
//+------------------------------------------------------------------+
input bool            InpTradePanel  = true;       // Tampilkan panel BUY/SELL
input int             InpPanelX      = 10;         // Posisi panel dari kiri (px)
input int             InpPanelY      = 45;         // Posisi panel dari atas (px)
input ENUM_TIMEFRAMES InpExecTf      = PERIOD_M5;  // TF eksekusi bot (ATR untuk pratinjau SL)
input int             InpBotStaleSec = 15;         // Heartbeat bot lebih tua dari ini = bot mati
input int             InpPendingSec  = 60;         // Pratinjau tanpa konfirmasi dibatalkan setelah N detik

#define PP "XEP_"
string   g_pending   = "";      // "" / "BUY" / "SELL"
datetime g_pendingAt = 0;
long     g_lastRes   = 0;
long     g_sentAt    = 0;
int      g_atr       = INVALID_HANDLE;
double   g_slAtr     = 1.5;
int      g_burst     = 1;
string   g_botMode   = "?";
bool     g_botAlive  = false;
bool     g_symActive = false;
string   g_profile   = "";        // profil risiko aktif simbol ini (dari bot_heartbeat kolom 6)
string   g_skillsOff = "";        // skill yang OFF (kolom 7)

void MkButton(const string name, const int x, const int y, const int w, const int h, const string text,
              const color bg, const color fg)
  {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, fg);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_STATE, false);
  }

void MkLabel(const string name, const int x, const int y, const string text, const color c, const int fs = 9)
  {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fs);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void DelObj(const string name) { if(ObjectFind(0, name) >= 0) ObjectDelete(0, name); }

void PanelMsg(const string text, const color c) { if(InpTradePanel) MkLabel(PP + "msg", InpPanelX, InpPanelY + 84, text, c); }

void ReadBotHeartbeat()
  {
   g_botAlive = false;
   g_symActive = false;
   int h = FileOpen(InpFolder + "\\bot_heartbeat.txt",
                    FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return;
   string line = FileReadString(h);
   FileClose(h);
   string f[];
   if(StringSplit(line, ';', f) < 6)
      return;
   g_botMode   = f[1];
   g_slAtr     = StringToDouble(f[2]);
   g_burst     = (int)StringToInteger(f[3]);
   g_symActive = StringFind("," + f[5] + ",", "," + SymFile() + ",") >= 0;
   g_botAlive  = (long)TimeGMT() - StringToInteger(f[0]) <= InpBotStaleSec;
   g_profile   = "";
   g_skillsOff = ArraySize(f) > 7 ? f[7] : "";
   if(ArraySize(f) > 6)                                   // SYM=PROFIL:slATR:burst|...
     {
      string per[];
      int n = StringSplit(f[6], '|', per);
      for(int i = 0; i < n; i++)
        {
         string kv[], v[];
         if(StringSplit(per[i], '=', kv) == 2 && kv[0] == SymFile() && StringSplit(kv[1], ':', v) == 3)
           {
            g_profile = v[0];
            g_slAtr   = StringToDouble(v[1]);
            g_burst   = (int)StringToInteger(v[2]);
           }
        }
     }
  }

double AtrNow()
  {
   double b[];
   if(g_atr == INVALID_HANDLE || CopyBuffer(g_atr, 0, 1, 1, b) != 1)
      return 0;
   return b[0];
  }

void PvTrend(const string name, const datetime t0, const double p0, const datetime t1, const double p1,
             const color c, const ENUM_LINE_STYLE st, const int w)
  {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TREND, 0, t0, p0, t1, p1);
   else
     {
      ObjectMove(0, name, 0, t0, p0);
      ObjectMove(0, name, 1, t1, p1);
     }
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_STYLE, st);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, w);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void PvText(const string name, const datetime t, const double p, const string text, const color c)
  {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TEXT, 0, t, p);
   else
      ObjectMove(0, name, 0, t, p);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, name, OBJPROP_COLOR, c);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

//--- gambar RENCANA entry: panah + garis harga masuk + garis SL + label (diperbarui mengikuti harga)
void DrawPreview()
  {
   if(g_pending == "")
      return;
   bool buy = (g_pending == "BUY");
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t))
      return;
   double px  = buy ? t.ask : t.bid;
   double atr = AtrNow();
   double dist = g_slAtr * atr;
   double sl  = buy ? px - dist : px + dist;
   datetime t0 = iTime(_Symbol, _Period, 0);
   datetime t1 = t0 + PeriodSeconds() * 12;
   color c = buy ? clrLime : clrRed;
   int dg = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   PvTrend(PP + "pv_entry", t0, px, t1, px, c, STYLE_SOLID, 2);
   PvTrend(PP + "pv_sl", t0, sl, t1, sl, clrTomato, STYLE_DASH, 1);
   PvTrend(PP + "pv_link", t0, px, t0, sl, clrTomato, STYLE_DOT, 1);
   if(ObjectFind(0, PP + "pv_arrow") < 0)
      ObjectCreate(0, PP + "pv_arrow", OBJ_ARROW, 0, t0, px);
   else
      ObjectMove(0, PP + "pv_arrow", 0, t0, px);
   ObjectSetInteger(0, PP + "pv_arrow", OBJPROP_ARROWCODE, buy ? 233 : 234);
   ObjectSetInteger(0, PP + "pv_arrow", OBJPROP_COLOR, c);
   ObjectSetInteger(0, PP + "pv_arrow", OBJPROP_WIDTH, 3);
   ObjectSetInteger(0, PP + "pv_arrow", OBJPROP_ANCHOR, buy ? ANCHOR_TOP : ANCHOR_BOTTOM);
   ObjectSetInteger(0, PP + "pv_arrow", OBJPROP_HIDDEN, true);
   PvText(PP + "pv_txt", t1, px, StringFormat("RENCANA %s x%d @ %s  (TP: trailing)", g_pending, g_burst,
          DoubleToString(px, dg)), c);
   PvText(PP + "pv_sltxt", t1, sl, StringFormat("SL %s  (%.1f x ATR %s = %.2f)", DoubleToString(sl, dg), g_slAtr,
          EnumToString(InpExecTf), dist), clrTomato);
   // perkiraan risiko dengan lot minimum (lot sebenarnya dihitung bot dari % risiko)
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk = (ts > 0) ? dist / ts * tv * vmin * g_burst : 0;
   MkLabel(PP + "risk", InpPanelX, InpPanelY + 100,
           StringFormat("Perkiraan risiko %d tiket x %.2f lot: %s %s (%.2f%% equity) jika SL kena", g_burst, vmin,
                        DoubleToString(risk, 0), AccountInfoString(ACCOUNT_CURRENCY), eq > 0 ? risk / eq * 100 : 0),
           clrGold);
  }

void ClearPending()
  {
   g_pending = "";
   DelObj(PP + "ok");
   DelObj(PP + "cancel");
   DelObj(PP + "risk");
   ObjectsDeleteAll(0, PP + "pv_");
  }

void SendCommand(const string act)
  {
   string f = InpFolder + "\\cmd_" + SymFile() + "_" + IntegerToString((long)GetTickCount64()) + ".txt";
   int h = FileOpen(f, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      PanelMsg("Gagal menulis perintah (" + IntegerToString(GetLastError()) + ")", clrTomato);
      return;
     }
   FileWriteString(h, StringFormat("%s;%s;%I64d;%s\n", act, SymFile(), (long)TimeGMT(), TfName((ENUM_TIMEFRAMES)_Period)));
   FileClose(h);
   g_sentAt = (long)TimeGMT();
   PanelMsg("Dikirim ke bot: " + act + " " + SymFile() + " - menunggu hasil...", clrSilver);
  }

long ReadResult(string &msg, bool &ok)
  {
   int h = FileOpen(InpFolder + "\\cmdres_" + SymFile() + ".txt",
                    FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return 0;
   string line = FileReadString(h);
   FileClose(h);
   string f[];
   if(StringSplit(line, ';', f) < 3)
      return 0;
   ok = (f[1] == "OK");
   msg = f[2];
   for(int i = 3; i < ArraySize(f); i++)
      msg += ";" + f[i];
   return StringToInteger(f[0]);
  }

void BuildPanel()
  {
   if(!InpTradePanel)
      return;
   MkLabel(PP + "title", InpPanelX, InpPanelY, "Entry manual XAUUSD_EXNESS (dieksekusi bot)", clrWhite, 9);
   MkButton(PP + "buy", InpPanelX, InpPanelY + 18, 96, 26, "BUY", C'20,120,90', clrWhite);
   MkButton(PP + "sell", InpPanelX + 102, InpPanelY + 18, 96, 26, "SELL", C'170,40,40', clrWhite);
  }

void PanelTimer()
  {
   if(!InpTradePanel)
      return;
   ReadBotHeartbeat();
   string st = g_botAlive ? ("bot " + g_botMode + (g_symActive ? " - siap" : " - simbol ini TIDAK aktif di bot"))
                          : "BOT TIDAK BERJALAN - tombol tidak akan diproses";
   MkLabel(PP + "bot", InpPanelX + 206, InpPanelY + 24, st, g_botAlive && g_symActive ? clrMediumSeaGreen : clrTomato);
   // profil aktif (sinkron dengan dashboard web) + skill yang OFF
   string pf = g_botAlive && g_profile != "" ? ("Profil: " + g_profile + "  |  burst " + IntegerToString(g_burst) +
               "  SL " + DoubleToString(g_slAtr, 1) + "xATR" + (g_skillsOff != "" && g_skillsOff != "-" ? "  |  skill OFF: " + g_skillsOff : ""))
               : "Profil: - (bot tidak berjalan)";
   MkLabel(PP + "prof", InpPanelX + 206, InpPanelY + 40, pf,
           g_profile == "AGGRESSIVE" ? clrOrange : g_profile == "CONSERVATIVE" ? clrDeepSkyBlue : clrWhite);
   if(g_pending != "")
     {
      if(TimeLocal() - g_pendingAt > InpPendingSec)
        {
         ClearPending();
         PanelMsg("Rencana dibatalkan otomatis (tidak dikonfirmasi)", clrSilver);
        }
      else
         DrawPreview();
     }
   string msg;
   bool ok = false;
   long ts = ReadResult(msg, ok);
   if(ts > g_lastRes)
     {
      g_lastRes = ts;
      if(g_sentAt > 0 && ts >= g_sentAt - 2)
        {
         PanelMsg((ok ? "OK: " : "DITOLAK: ") + msg, ok ? clrMediumSeaGreen : clrTomato);
         g_sentAt = 0;
        }
     }
   else if(g_sentAt > 0 && (long)TimeGMT() - g_sentAt > 20)
     {
      PanelMsg("Belum ada jawaban bot setelah 20 dtk - cek jendela bot / dashboard", clrOrange);
      g_sentAt = 0;
     }
  }

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   if(id != CHARTEVENT_OBJECT_CLICK || StringFind(sparam, PP) != 0)
      return;
   ObjectSetInteger(0, sparam, OBJPROP_STATE, false);
   if(sparam == PP + "buy" || sparam == PP + "sell")
     {
      ReadBotHeartbeat();
      if(!g_botAlive)
        {
         PanelMsg("BOT TIDAK BERJALAN - jalankan start_bot.cmd all", clrTomato);
         ChartRedraw();
         return;
        }
      if(!g_symActive)
        {
         PanelMsg("Simbol " + SymFile() + " tidak aktif di bot (cek config symbols)", clrOrange);
         ChartRedraw();
         return;
        }
      ClearPending();
      g_pending = (sparam == PP + "buy") ? "BUY" : "SELL";
      g_pendingAt = TimeLocal();
      MkButton(PP + "ok", InpPanelX, InpPanelY + 50, 130, 26, "KONFIRMASI " + g_pending, C'200,150,20', clrBlack);
      MkButton(PP + "cancel", InpPanelX + 136, InpPanelY + 50, 62, 26, "BATAL", C'70,75,85', clrWhite);
      DrawPreview();
      PanelMsg("Periksa garis RENCANA di chart, lalu KONFIRMASI (mode bot: " + g_botMode + ")", clrGold);
     }
   else if(sparam == PP + "ok")
     {
      string act = g_pending;
      ClearPending();
      if(act != "")
         SendCommand(act);
     }
   else if(sparam == PP + "cancel")
     {
      ClearPending();
      PanelMsg("Dibatalkan", clrSilver);
     }
   ChartRedraw();
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   g_label = InpPrefix + "__status";
   EventSetTimer(MathMax(1, InpTimerSec));
   g_atr = iATR(_Symbol, InpExecTf, 14);
   string m;
   bool k;
   g_lastRes = ReadResult(m, k);          // hasil lama tidak ditampilkan sebagai hasil baru
   BuildPanel();
   SetStatus("XAUUSD_EXNESS bridge: menunggu data...", clrSilver);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, InpPrefix);                         // bersihkan semua objek bridge
   ObjectsDeleteAll(0, PP);                                // dan panel entry manual
   if(g_atr != INVALID_HANDLE)
      IndicatorRelease(g_atr);
   ChartRedraw();
  }

int OnCalculate(const int rates_total, const int prev_calculated, const int begin, const double &price[])
  {
   return rates_total;
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   PanelTimer();                          // panel BUY/SELL tetap jalan walau bridge gambar terputus
   string tf = TfName((ENUM_TIMEFRAMES)_Period);
   if(tf == "")
     {
      SetStatus("XAUUSD_EXNESS bridge: timeframe chart ini tidak didukung (pakai M1/M5/M15/M30/H1/H4)", clrOrange);
      WriteStatus("tf_unsupported", 0);
      ChartRedraw();
      return;
     }
   long hb  = ReadHeartbeat();
   long age = (hb > 0) ? (long)TimeGMT() - hb : -1;
   if(hb <= 0 || age > InpStaleSec)
     {
      // engine Python mati: objek terakhir DIBEKUKAN (tidak diubah / ditambah), indikator tetap hidup
      SetStatus(hb > 0 ? StringFormat("XAUUSD_EXNESS: BRIDGE DISCONNECTED (%I64d dtk) - objek dibekukan", age)
                       : "XAUUSD_EXNESS: BRIDGE DISCONNECTED - engine Python belum jalan", clrTomato);
      WriteStatus("disconnected", g_count);
      ChartRedraw();
      return;
     }
   string fname = InpFolder + "\\bridge_" + SymFile() + "_" + tf + ".csv";
   int n = 0;
   int r = DrawFile(fname, n);
   if(r < 0)
     {
      SetStatus("XAUUSD_EXNESS: bridge OK, tapi belum ada data " + SymFile() + " " + tf +
                " (cek config bridge.timeframes / symbols)", clrOrange);
      WriteStatus("no_file", 0);
      ChartRedraw();
      return;
     }
   if(r > 0)
      g_count = n;
   SetStatus(StringFormat("XAUUSD_EXNESS bridge: OK  %s %s  %d objek  (heartbeat %I64d dtk)", SymFile(), tf, g_count, age),
             clrMediumSeaGreen);
   WriteStatus("ok", g_count);
   ChartRedraw();
  }
//+------------------------------------------------------------------+
