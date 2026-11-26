//+------------------------------------------------------------------+
//|                                               TickHarvester.mq5  |
//|  ضبط پیوسته‌ی تیک همه‌ی نمادهای ۲۴ ساعته در MQL5\Files\harvester  |
//|  خروجی: CSV روزانه به‌ازای هر نماد + symbols.json + status.json   |
//|  بدون شبکه، بدون WebRequest، بدون DLL — فقط فایل.                 |
//+------------------------------------------------------------------+
#property copyright "tradeAgent"
#property link      "https://github.com/enigmafarsco-lang"
#property version   "1.00"
#property description "Tick harvester: continuous tick capture for 24h symbols (crypto + FX)."

//--- خروجی فایل
input group             "1) Output"
input string InpOutDir            = "harvester";   // پوشه‌ی خروجی داخل MQL5\Files
input int    InpPollMs            = 250;           // فاصله‌ی نظرسنجی تیک (میلی‌ثانیه)
input int    InpFlushEveryTicks   = 200;           // هر چند تیک فایل flush شود
input int    InpStatusEverySec    = 5;             // هر چند ثانیه status.json نوشته شود
input int    InpBackfillSec       = 900;           // در شروع، چند ثانیه عقب‌تر تیک بگیر
input int    InpMaxFileMB         = 512;           // سقف حجم فایل روزانه (MB)
input bool   InpVerbose           = true;          // لاگ در تب Experts

//--- انتخاب نمادها
input group             "2) Symbols"
input string InpSymbolsCsv        = "";            // فهرست دستی با کاما؛ خالی = خودکار
input bool   InpAutoDiscover      = true;          // کشف خودکار نمادهای ۲۴ ساعته
input double InpMinWeeklyHours    = 100.0;         // حداقل ساعت معاملات هفتگی (فارکس ۲۴/۵ ≈ ۱۲۰)
input int    InpMaxSymbols        = 40;            // سقف تعداد نماد
input bool   InpForceMarketWatch  = true;          // افزودن خودکار به Market Watch

//--- محدودیت‌های ایمنی
input group             "3) Safety"
input int    InpMaxTicksPerPoll   = 20000;         // سقف تیک هر نظرسنجی هر نماد
input bool   InpStopOnFileLimit   = true;          // توقف ضبط نماد در صورت پر شدن فایل

#define HARVESTER_VERSION "1.01"
#define MAX_SYMBOLS_HARD  120

//+------------------------------------------------------------------+
//| وضعیت هر نماد                                                     |
//+------------------------------------------------------------------+
struct SymState
{
   string   name;
   int      digits;
   ulong    last_msc;
   long     ticks_written;
   long     errors;
   int      handle;
   string   file_day;
   bool     active;
   bool     limited;
   datetime last_tick_time;
   double   weekly_hours;
};

SymState g_sym[];
int      g_count        = 0;
string   g_day          = "";
datetime g_last_status  = 0;
long     g_total_written= 0;
long     g_total_errors = 0;
datetime g_started      = 0;
int      g_poll_count   = 0;

//+------------------------------------------------------------------+
//| ابزارک‌ها                                                         |
//+------------------------------------------------------------------+
string JsonEscape(const string src)
{
   string s = src;
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   return s;
}

string DayStamp(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
}

void EnsureFolder(const string path)
{
   string parts[];
   int n = StringSplit(path, '/', parts);
   string cur = "";
   for(int i = 0; i < n; i++)
   {
      if(parts[i] == "")
         continue;
      cur = (cur == "") ? parts[i] : cur + "/" + parts[i];
      FolderCreate(cur, 0);       // اگر از قبل باشد، خطای بی‌خطر می‌دهد
      ResetLastError();
   }
}

void LogInfo(const string msg)
{
   if(InpVerbose)
      Print("[harvester] ", msg);
}

//+------------------------------------------------------------------+
//| ساعت معاملات هفتگی نماد (برای تشخیص نمادهای ۲۴ ساعته)              |
//+------------------------------------------------------------------+
double WeeklyTradeHours(const string symbol)
{
   double total = 0.0;
   for(int day = 0; day <= 6; day++)
   {
      datetime from = 0, to = 0;
      for(uint idx = 0; idx < 8; idx++)
      {
         if(!SymbolInfoSessionTrade(symbol, (ENUM_DAY_OF_WEEK)day, idx, from, to))
            break;
         double secs = (double)((long)to - (long)from);
         if(secs > 0)
            total += secs;
      }
   }
   return total / 3600.0;
}

int Discover24hSymbols(string &out[])
{
   int found = 0;
   int total = SymbolsTotal(false);
   for(int i = 0; i < total && found < InpMaxSymbols; i++)
   {
      string sym = SymbolName(i, false);
      if(sym == "")
         continue;
      long mode = SymbolInfoInteger(sym, SYMBOL_TRADE_MODE);
      if(mode == SYMBOL_TRADE_MODE_DISABLED)
         continue;
      double hours = WeeklyTradeHours(sym);
      if(hours >= InpMinWeeklyHours)
      {
         ArrayResize(out, found + 1);
         out[found] = sym;
         found++;
      }
   }
   return found;
}

bool AddSymbol(const string raw)
{
   string sym = raw;
   StringTrimLeft(sym);
   StringTrimRight(sym);
   if(sym == "")
      return false;
   if(g_count >= MAX_SYMBOLS_HARD || g_count >= InpMaxSymbols)
      return false;

   for(int i = 0; i < g_count; i++)
      if(g_sym[i].name == sym)
         return true;                     // تکرار

   if(!SymbolSelect(sym, InpForceMarketWatch))
   {
      Print("[harvester] نماد پیدا/انتخاب نشد: ", sym, " err=", GetLastError());
      g_total_errors++;
      return false;
   }

   ArrayResize(g_sym, g_count + 1);
   g_sym[g_count].name            = sym;
   g_sym[g_count].digits          = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   g_sym[g_count].last_msc        = 0;
   g_sym[g_count].ticks_written   = 0;
   g_sym[g_count].errors          = 0;
   g_sym[g_count].handle          = INVALID_HANDLE;
   g_sym[g_count].file_day        = "";
   g_sym[g_count].active          = true;
   g_sym[g_count].limited         = false;
   g_sym[g_count].last_tick_time  = 0;
   g_sym[g_count].weekly_hours    = WeeklyTradeHours(sym);
   g_count++;
   LogInfo("نماد اضافه شد: " + sym + " | ساعت هفتگی=" + DoubleToString(g_sym[g_count-1].weekly_hours, 1));
   return true;
}

//+------------------------------------------------------------------+
//| فایل تیک روزانه                                                   |
//+------------------------------------------------------------------+
int OpenTickFile(const int idx, const string day)
{
   string dir  = InpOutDir + "/ticks/" + g_sym[idx].name;
   EnsureFolder(InpOutDir);
   EnsureFolder(InpOutDir + "/ticks");
   EnsureFolder(dir);

   string path = dir + "/" + day + ".csv";
   int h = FileOpen(path, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI, ';');
   if(h == INVALID_HANDLE)
   {
      Print("[harvester] بازکردن فایل نشد: ", path, " err=", GetLastError());
      g_sym[idx].errors++;
      g_total_errors++;
      return INVALID_HANDLE;
   }
   FileSeek(h, 0, SEEK_END);
   if(FileTell(h) == 0)
      FileWriteString(h, "time_msc;time;bid;ask;last;volume;flags\r\n");
   g_sym[idx].handle   = h;
   g_sym[idx].file_day = day;
   return h;
}

void CloseSymbolFile(const int idx)
{
   if(g_sym[idx].handle != INVALID_HANDLE)
   {
      FileFlush(g_sym[idx].handle);
      FileClose(g_sym[idx].handle);
      g_sym[idx].handle = INVALID_HANDLE;
   }
}

//+------------------------------------------------------------------+
//| چرخش روز: فایل جدید برای روز جدید                                 |
//+------------------------------------------------------------------+
void RollDayIfNeeded()
{
   string today = DayStamp(TimeTradeServer());
   if(today == g_day)
      return;
   for(int i = 0; i < g_count; i++)
      CloseSymbolFile(i);
   g_day = today;
   LogInfo("روز جدید: " + g_day);
}

//+------------------------------------------------------------------+
//| خواندن تیک تازه‌ی یک نماد و نوشتن آن                              |
//+------------------------------------------------------------------+
void HarvestSymbol(const int idx)
{
   if(!g_sym[idx].active || g_sym[idx].limited)
      return;
   if(g_sym[idx].handle == INVALID_HANDLE)
   {
      if(OpenTickFile(idx, g_day) == INVALID_HANDLE)
         return;
   }

   ulong from = 0;
   if(g_sym[idx].last_msc > 0)
      from = g_sym[idx].last_msc + 1;
   else
   {
      long start = (long)TimeTradeServer() - (long)InpBackfillSec;
      if(start < 1)
         start = 1;
      from = (ulong)start * 1000;
   }

   MqlTick ticks[];
   int n = CopyTicks(g_sym[idx].name, ticks, COPY_TICKS_ALL, from, (uint)InpMaxTicksPerPoll);
   if(n < 0)
   {
      g_sym[idx].errors++;
      g_total_errors++;
      if(g_sym[idx].errors % 50 == 1)
         Print("[harvester] CopyTicks خطا برای ", g_sym[idx].name, " err=", GetLastError(), " from=", from);
      return;
   }
   if(n == 0)
      return;

   int digits = g_sym[idx].digits;
   string buf = "";
   int appended = 0;
   ulong max_msc = g_sym[idx].last_msc;

   for(int i = 0; i < n; i++)
   {
      if(ticks[i].time_msc <= g_sym[idx].last_msc)
         continue;
      buf += (string)ticks[i].time_msc + ";"
           + TimeToString(ticks[i].time, TIME_DATE | TIME_SECONDS) + ";"
           + DoubleToString(ticks[i].bid, digits) + ";"
           + DoubleToString(ticks[i].ask, digits) + ";"
           + DoubleToString(ticks[i].last, digits) + ";"
           + DoubleToString((double)ticks[i].volume_real, 2) + ";"
           + (string)ticks[i].flags + "\r\n";
      if(ticks[i].time_msc > max_msc)
         max_msc = ticks[i].time_msc;
      if(ticks[i].time > g_sym[idx].last_tick_time)
         g_sym[idx].last_tick_time = ticks[i].time;
      appended++;
   }

   if(appended == 0)
      return;

   FileWriteString(g_sym[idx].handle, buf);
   g_sym[idx].last_msc = max_msc;
   g_sym[idx].ticks_written += appended;
   g_total_written += appended;

   if(appended >= InpFlushEveryTicks)
      FileFlush(g_sym[idx].handle);

   if(InpMaxFileMB > 0)
   {
      ulong size = FileSize(g_sym[idx].handle);
      if(size > (ulong)InpMaxFileMB * 1024 * 1024)
      {
         if(InpStopOnFileLimit)
         {
            g_sym[idx].limited = true;
            CloseSymbolFile(idx);
            Print("[harvester] سقف حجم فایل برای ", g_sym[idx].name, " پر شد؛ ضبط متوقف شد.");
         }
         else
            FileFlush(g_sym[idx].handle);
      }
   }
}

//+------------------------------------------------------------------+
//| status.json و symbols.json                                        |
//+------------------------------------------------------------------+
void WriteStatus()
{
   // نوشتن اتمیک: اول فایل موقت، بعد جایگزینی — تا خواننده فایل نیمه‌کاره نبیند
   string path = InpOutDir + "/status.json";
   string tmp  = InpOutDir + "/status.tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;

   string out = "{";
   out += "\"ea\":\"TickHarvester\",";
   out += "\"version\":\"" + HARVESTER_VERSION + "\",";
   out += "\"updated_ts\":\"" + TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS) + "\",";
   out += "\"terminal_connected\":" + (TerminalInfoInteger(TERMINAL_CONNECTED) ? "true" : "false") + ",";
   out += "\"trading_allowed\":" + (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? "true" : "false") + ",";
   out += "\"server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\",";
   out += "\"login\":" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + ",";
   out += "\"uptime_s\":" + (string)(TimeGMT() - g_started) + ",";
   out += "\"polls\":" + (string)g_poll_count + ",";
   out += "\"total_ticks\":" + (string)g_total_written + ",";
   out += "\"total_errors\":" + (string)g_total_errors + ",";
   out += "\"symbols\":[";
   for(int i = 0; i < g_count; i++)
   {
      int age = -1;
      if(g_sym[i].last_tick_time > 0)
         age = (int)(TimeGMT() - g_sym[i].last_tick_time);
      if(i > 0)
         out += ",";
      out += "{";
      out += "\"symbol\":\"" + JsonEscape(g_sym[i].name) + "\",";
      out += "\"digits\":" + (string)g_sym[i].digits + ",";
      out += "\"ticks\":" + (string)g_sym[i].ticks_written + ",";
      out += "\"errors\":" + (string)g_sym[i].errors + ",";
      out += "\"last_msc\":" + (string)g_sym[i].last_msc + ",";
      out += "\"age_s\":" + (string)age + ",";
      out += "\"active\":" + (g_sym[i].active ? "true" : "false") + ",";
      out += "\"limited\":" + (g_sym[i].limited ? "true" : "false") + ",";
      out += "\"file_day\":\"" + g_sym[i].file_day + "\"";
      out += "}";
   }
   out += "]}";
   FileWriteString(h, out);
   FileClose(h);
   // جابه‌جایی با ۴ آرگومان (سازگار با همه‌ی بیلدها)؛ پنجره‌ی نبودن فایل کوتاه است
   FileDelete(path);
   FileMove(tmp, 0, path, 0);
}

void WriteSymbolsJson()
{
   string path = InpOutDir + "/symbols.json";
   string tmp  = InpOutDir + "/symbols.tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;
   string out = "{";
   out += "\"ea\":\"TickHarvester\",";
   out += "\"version\":\"" + HARVESTER_VERSION + "\",";
   out += "\"updated_ts\":\"" + TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS) + "\",";
   out += "\"min_weekly_hours\":" + DoubleToString(InpMinWeeklyHours, 1) + ",";
   out += "\"count\":" + (string)g_count + ",";
   out += "\"symbols\":[";
   for(int i = 0; i < g_count; i++)
   {
      if(i > 0)
         out += ",";
      out += "{\"symbol\":\"" + JsonEscape(g_sym[i].name) + "\",";
      out += "\"digits\":" + (string)g_sym[i].digits + ",";
      out += "\"weekly_hours\":" + DoubleToString(g_sym[i].weekly_hours, 1) + "}";
   }
   out += "]}";
   FileWriteString(h, out);
   FileClose(h);
   // جابه‌جایی با ۴ آرگومان (سازگار با همه‌ی بیلدها)؛ پنجره‌ی نبودن فایل کوتاه است
   FileDelete(path);
   FileMove(tmp, 0, path, 0);
}

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpPollMs < 50)
   {
      Print("[harvester] InpPollMs خیلی کوچک است (حداقل 50).");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpMaxSymbols < 1 || InpMaxSymbols > MAX_SYMBOLS_HARD)
   {
      Print("[harvester] InpMaxSymbols باید بین 1 و ", MAX_SYMBOLS_HARD, " باشد.");
      return INIT_PARAMETERS_INCORRECT;
   }

   g_started = TimeGMT();
   EnsureFolder(InpOutDir);
   EnsureFolder(InpOutDir + "/ticks");

   //--- نمادهای دستی
   if(StringLen(InpSymbolsCsv) > 0)
   {
      string parts[];
      int n = StringSplit(InpSymbolsCsv, ',', parts);
      for(int i = 0; i < n; i++)
         AddSymbol(parts[i]);
   }

   //--- کشف خودکار
   if(g_count == 0 && InpAutoDiscover)
   {
      string discovered[];
      int n = Discover24hSymbols(discovered);
      for(int i = 0; i < n; i++)
         AddSymbol(discovered[i]);
   }

   //--- هیچ نمادی نبود: حداقل نماد چارت
   if(g_count == 0)
   {
      Print("[harvester] هیچ نماد ۲۴ ساعته‌ای پیدا نشد؛ از نماد چارت استفاده می‌شود: ", _Symbol);
      AddSymbol(_Symbol);
   }
   if(g_count == 0)
   {
      Print("[harvester] راه‌اندازی ناموفق: نمادی اضافه نشد.");
      return INIT_FAILED;
   }

   g_day = DayStamp(TimeTradeServer());
   WriteSymbolsJson();
   WriteStatus();

   EventSetMillisecondTimer(InpPollMs);
   Print("==================================================");
   Print("[harvester] ", HARVESTER_VERSION, " شروع شد | ", g_count, " نماد | پوشه=", InpOutDir,
         " | poll=", InpPollMs, "ms | روز=", g_day);
   Print("[harvester] مسیر فایل‌ها: MQL5\\Files\\", InpOutDir, "\\ticks\\<SYMBOL>\\<YYYYMMDD>.csv");
   Print("==================================================");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   for(int i = 0; i < g_count; i++)
      CloseSymbolFile(i);
   WriteStatus();
   Print("[harvester] متوقف شد | تیک‌های نوشته‌شده=", g_total_written,
         " | خطا=", g_total_errors, " | reason=", reason);
}

//+------------------------------------------------------------------+
//| OnTimer — قلب ضبط                                               |
//+------------------------------------------------------------------+
void OnTimer()
{
   RollDayIfNeeded();
   g_poll_count++;

   for(int i = 0; i < g_count; i++)
      HarvestSymbol(i);

   if(InpStatusEverySec > 0 && (TimeGMT() - g_last_status) >= InpStatusEverySec)
   {
      g_last_status = TimeGMT();
      WriteStatus();
   }
}

//+------------------------------------------------------------------+
//| OnTick لازم نیست؛ نظرسنجی تایمر همه‌ی نمادها را پوشش می‌دهد.      |
//+------------------------------------------------------------------+
void OnTick()
{
}
//+------------------------------------------------------------------+
