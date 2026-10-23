//+------------------------------------------------------------------+
//| XAUUSD_Tester_EA.mq5 — replika strategi bot untuk MT5 Strategy Tester   |
//|                                                                  |
//| Tujuan: menguji ulang strategi bot Python (bot/strategy.py + exit di config.json)  |
//| di Strategy Tester MT5 (data tick broker, spread & komisi ASLI) sebagai pembanding |
//| backtest Python. Nilai default = profil NORMAL emas M5 (config 29 Sep).       |
//|                                                                  |
//| Cara pakai: Strategy Tester -> Expert: XAUUSD_EXNESS\XAUUSD_Tester_EA          |
//|   Symbol XAUUSD (atau XAUUSD247/BTCUSD/ETHUSD), Period M5 (crypto M15),         |
//|   Model "Every tick based on real ticks", Deposit sesuai akun. Atur input       |
//|   ke nilai per simbol di README bagian 17 (mis. BTC: SL 4.0, trail 1.2, M15).   |
//| Catatan perbedaan dgn bot: tanpa learning/SMC/berita/dashboard/profil dinamis;   |
//|   ATR = Wilder (sama dgn Python); EMA dari iMA (seed sedikit beda).              |
//+------------------------------------------------------------------+
#property copyright "XAUUSD_EXNESS"
#property version   "1.00"
#property strict
#include <Trade\Trade.mqh>

//--- sinyal
input int    InpFast          = 8;      // EMA cepat (entry.fast)
input int    InpSlow          = 21;     // EMA lambat (entry.slow)
input int    InpTrendEma      = 200;    // filter tren: BUY di atas / SELL di bawah EMA ini (0 = mati)
input int    InpAtrPeriod     = 14;     // ATR period
input bool   InpUseAtrBand    = true;   // filter atr_band (persentil ATR bergulir)
input int    InpAtrWindow     = 288;    // window persentil (bar)
input double InpAtrMinPct     = 0.40;   // batas bawah persentil
input double InpAtrMaxPct     = 0.95;   // batas atas persentil
//--- sesi UTC (server Exness = GMT+0). Simbol 24/7: set semua jam
input bool   InpSessAsia      = true;   // 00-07
input bool   InpSessLondon    = true;   // 07-12
input bool   InpSessNewYork   = true;   // 12-20
input bool   InpSessLate      = false;  // 20-24 (emas: mati; crypto: nyalakan)
input int    InpMaxSpreadPts  = 80;     // spread maks (poin)
//--- risiko & burst
input double InpRiskPct       = 0.20;   // % equity per tiket (risk_per_trade_pct)
input int    InpBurst         = 10;     // tiket per sinyal (burst_size, mode multiply)
input double InpSlAtr         = 1.5;    // SL = N x ATR (exit.sl_atr)
input double InpSlMinPct      = 0.0;    // jepit SL min % harga (crypto: 0.15-0.2)
input double InpSlMaxPct      = 0.0;    // jepit SL maks % harga (crypto: 1.5-2.0)
input double InpMinSpacingAtr = 0.5;    // jarak minimum antar-entri searah (x ATR)
input double InpDailyLossPct  = 4.0;    // rem rugi harian % (dari equity awal hari)
input int    InpMaxConsecLoss = 4;      // kalah beruntun -> jeda
input int    InpPauseMin      = 60;     // lama jeda (menit)
//--- exit adaptif (exit.* di config)
input double InpBeTrigger     = 0.5;    // BE-lock aktif setelah profit >= N x ATR
input double InpBeLock        = 0.1;    // kunci profit N x ATR di atas entry
input double InpTrailStart    = 0.6;    // trailing ATR mulai setelah profit >= N x ATR
input double InpTrail         = 0.8;    // jarak trailing (x ATR)
input double InpTightAfter    = 1.5;    // trailing ketat setelah profit >= N x ATR
input double InpTightTrail    = 0.5;    // jarak trailing ketat (x ATR)
input bool   InpCandleTrail   = true;   // SL ikut low/high candle tertutup
input double InpCandleAfter   = 0.5;    // candle trail aktif setelah profit >= N x ATR
input double InpCandleBuffer  = 0.1;    // buffer candle trail (x ATR)
input bool   InpCloseOpposite = true;   // tutup posisi bila muncul sinyal berlawanan
input int    InpMaxHoldBars   = 36;     // batas waktu tahan (bar), 0 = tanpa batas
input long   InpMagic         = 260999; // magic number EA tester
input int    InpDeviation     = 50;     // deviasi slippage (poin)

CTrade   trade;
int      hFast, hSlow, hTrend;
datetime g_lastBar = 0, g_pauseUntil = 0, g_dayStamp = 0;
double   g_dayStartEquity = 0;
int      g_consec = 0;
bool     g_dayHalt = false;

//+------------------------------------------------------------------+
int OnInit()
  {
   hFast  = iMA(_Symbol, _Period, InpFast, 0, MODE_EMA, PRICE_CLOSE);
   hSlow  = iMA(_Symbol, _Period, InpSlow, 0, MODE_EMA, PRICE_CLOSE);
   hTrend = InpTrendEma > 0 ? iMA(_Symbol, _Period, InpTrendEma, 0, MODE_EMA, PRICE_CLOSE) : INVALID_HANDLE;
   if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE)
      return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviation);
   trade.SetTypeFillingBySymbol(_Symbol);
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   IndicatorRelease(hFast);
   IndicatorRelease(hSlow);
   if(hTrend != INVALID_HANDLE)
      IndicatorRelease(hTrend);
  }

//--- ATR Wilder (alpha = 1/n) — sama dengan bot/indicators.py atr(); out[0] = bar tertutup terbaru
bool AtrSeries(const int count, double &out[])
  {
   const int warm = 300, n = count + warm;
   MqlRates r[];
   ArraySetAsSeries(r, false);
   if(CopyRates(_Symbol, _Period, 1, n + 1, r) < n + 1)
      return false;
   double a = r[1].high - r[1].low;
   double tmp[];
   ArrayResize(tmp, n);
   for(int i = 1; i <= n; i++)
     {
      double tr = MathMax(r[i].high - r[i].low, MathMax(MathAbs(r[i].high - r[i - 1].close), MathAbs(r[i].low - r[i - 1].close)));
      a = (i == 1) ? tr : a + (tr - a) / InpAtrPeriod;
      tmp[i - 1] = a;
     }
   ArrayResize(out, count);
   for(int k = 0; k < count; k++)
      out[k] = tmp[n - 1 - k];
   return true;
  }

bool SessionOk()
  {
   MqlDateTime t;
   TimeToStruct(TimeGMT(), t);                             // jam UTC (juga benar di tester)
   int h = t.hour;
   return (h < 7 && InpSessAsia) || (h >= 7 && h < 12 && InpSessLondon) ||
          (h >= 12 && h < 20 && InpSessNewYork) || (h >= 20 && InpSessLate);
  }

int CountOpen(const int dir, double &nearestPriceGap, const double refPrice)
  {
   int n = 0;
   nearestPriceGap = DBL_MAX;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(!PositionSelectByTicket(tk) || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      int d = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      if(dir != 0 && d != dir)
         continue;
      n++;
      nearestPriceGap = MathMin(nearestPriceGap, MathAbs(refPrice - PositionGetDouble(POSITION_PRICE_OPEN)));
     }
   return n;
  }

double CalcLot(const double slDist)
  {
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(tv <= 0 || ts <= 0 || slDist <= 0)
      return 0;
   double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPct / 100.0;
   double lot = riskMoney / (slDist / ts * tv);
   lot = MathFloor(lot / step + 1e-9) * step;                 // dibulatkan KE BAWAH (sama dgn bot)
   return MathMin(MathMax(lot, vmin), vmax);
  }

void CheckDay()
  {
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   datetime day = TimeCurrent() - (t.hour * 3600 + t.min * 60 + t.sec);
   if(day != g_dayStamp)
     {
      g_dayStamp = day;
      g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      g_dayHalt = false;
     }
   if(!g_dayHalt && InpDailyLossPct > 0 &&
      (AccountInfoDouble(ACCOUNT_EQUITY) - g_dayStartEquity) / g_dayStartEquity * 100.0 <= -InpDailyLossPct)
     {
      g_dayHalt = true;                                        // rem harian: tutup semua & berhenti entry sampai besok
      CloseAll(0);
     }
  }

void CloseAll(const int onlyDir)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(!PositionSelectByTicket(tk) || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      int d = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      if(onlyDir == 0 || d == onlyDir)         // onlyDir 0 = semua arah
         trade.PositionClose(tk);
     }
  }

//--- trailing adaptif: BE-lock -> trailing ATR -> ketat -> candle trail (SL hanya maju)
void ManagePositions(const double atr)
  {
   if(atr <= 0)
      return;
   MqlRates r[];
   ArraySetAsSeries(r, true);
   bool haveBar = CopyRates(_Symbol, _Period, 1, 1, r) == 1;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(!PositionSelectByTicket(tk) || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      bool buy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      int  dir = buy ? 1 : -1;
      double open = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL), cur = buy ? bid : ask;
      if(InpMaxHoldBars > 0 && TimeCurrent() - (datetime)PositionGetInteger(POSITION_TIME) > (long)InpMaxHoldBars * PeriodSeconds())
        {
         trade.PositionClose(tk);
         continue;
        }
      double fav = (cur - open) * dir, nw = sl;
      if(fav >= InpBeTrigger * atr)
        {
         double c = open + dir * InpBeLock * atr;
         if(sl == 0 || (c - nw) * dir > 0)
            nw = c;
        }
      if(fav >= InpTrailStart * atr)
        {
         double k = (fav >= InpTightAfter * atr) ? InpTightTrail : InpTrail;
         double c = cur - dir * k * atr;
         if((c - nw) * dir > 0)
            nw = c;
        }
      if(InpCandleTrail && haveBar && fav >= InpCandleAfter * atr)
        {
         double c = buy ? r[0].low - InpCandleBuffer * atr : r[0].high + (ask - bid) + InpCandleBuffer * atr;
         if((c - nw) * dir > 0)
            nw = c;
        }
      nw = NormalizeDouble(nw, digits);
      double minGap = MathMax((double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL), 1.0) * point;
      double step = MathMax(0.05 * atr, 10 * point);
      if(nw != sl && MathAbs(cur - nw) >= minGap && (sl == 0 || (nw - sl) * dir >= step))
         trade.PositionModify(tk, nw, PositionGetDouble(POSITION_TP));
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   CheckDay();
   double atrArr[];
   if(!AtrSeries(InpAtrWindow + 2, atrArr))
      return;
   ManagePositions(atrArr[0]);

   datetime bar = iTime(_Symbol, _Period, 0);
   if(bar == g_lastBar)
      return;                                                  // sinyal hanya di bar TERTUTUP baru
   g_lastBar = bar;

   double f[3], s[3], tr[1];
   if(CopyBuffer(hFast, 0, 1, 3, f) < 3 || CopyBuffer(hSlow, 0, 1, 3, s) < 3)
      return;
   // f[0]=bar t-2, f[1]=bar t-1, f[2]=bar tertutup terbaru
   int sig = 0;
   if(f[2] > s[2] && !(f[1] > s[1]))
      sig = 1;
   else if(!(f[2] > s[2]) && f[1] > s[1])
      sig = -1;
   if(sig == 0)
      return;
   double close1 = iClose(_Symbol, _Period, 1), atr = atrArr[0];

   if(InpCloseOpposite)
      CloseAll(-sig);                                          // sinyal berlawanan -> tutup posisi lawan
   // ---- filter
   if(InpTrendEma > 0 && CopyBuffer(hTrend, 0, 1, 1, tr) == 1 && ((sig > 0 && close1 <= tr[0]) || (sig < 0 && close1 >= tr[0])))
      return;
   if(InpUseAtrBand)
     {
      int le = 0;
      for(int i = 0; i < InpAtrWindow; i++)
         if(atrArr[i] <= atr)
            le++;
      double pr = (double)le / InpAtrWindow;
      if(pr < InpAtrMinPct || pr > InpAtrMaxPct)
         return;
     }
   if(!SessionOk() || g_dayHalt || TimeCurrent() < g_pauseUntil)
      return;
   if((int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > InpMaxSpreadPts)
      return;
   // ---- slot & jarak minimum antar-entri searah (satu burst aktif per simbol, mode multiply)
   double gap;
   if(CountOpen(0, gap, 0) + InpBurst > InpBurst)
      return;
   double px = sig > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   CountOpen(sig, gap, px);
   if(gap < InpMinSpacingAtr * atr)
      return;
   // ---- ukuran & order
   double slDist = InpSlAtr * atr;
   if(InpSlMinPct > 0) slDist = MathMax(slDist, px * InpSlMinPct / 100.0);
   if(InpSlMaxPct > 0) slDist = MathMin(slDist, px * InpSlMaxPct / 100.0);
   double lot = CalcLot(slDist);
   if(lot <= 0)
      return;
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   for(int k = 0; k < InpBurst; k++)
     {
      double p = sig > 0 ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(p - sig * slDist, digits);
      if(sig > 0)
         trade.Buy(lot, _Symbol, 0, sl, 0, "xau-tester");
      else
         trade.Sell(lot, _Symbol, 0, sl, 0, "xau-tester");
     }
  }

//--- kalah beruntun (satu burst = satu hasil) -> jeda
void OnTradeTransaction(const MqlTradeTransaction &t, const MqlTradeRequest &rq, const MqlTradeResult &rs)
  {
   if(t.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(t.deal))
      return;
   if(HistoryDealGetInteger(t.deal, DEAL_MAGIC) != InpMagic || HistoryDealGetInteger(t.deal, DEAL_ENTRY) != DEAL_ENTRY_OUT)
      return;
   double pl = HistoryDealGetDouble(t.deal, DEAL_PROFIT) + HistoryDealGetDouble(t.deal, DEAL_COMMISSION) + HistoryDealGetDouble(t.deal, DEAL_SWAP);
   if(PositionsTotal() > 0)
     {
      int mine = 0;
      for(int i = 0; i < PositionsTotal(); i++)
         if(PositionGetTicket(i) && PositionGetInteger(POSITION_MAGIC) == InpMagic && PositionGetString(POSITION_SYMBOL) == _Symbol)
            mine++;
      if(mine > 0)
         return;                                               // tunggu tiket terakhir burst tutup
     }
   g_consec = pl < 0 ? g_consec + 1 : 0;
   if(InpMaxConsecLoss > 0 && g_consec >= InpMaxConsecLoss)
     {
      g_pauseUntil = TimeCurrent() + InpPauseMin * 60;
      g_consec = 0;
     }
  }
//+------------------------------------------------------------------+
