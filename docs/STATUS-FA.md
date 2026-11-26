# وضعیت پروژه — بدون نیاز به لاگ‌فرستادن

این فایل تنها مرجع «کجا هستیم» است. هر بار فقط یک دستور:

```bash
./scripts/verify_goals.sh        # پنج شرط اصلی: PASS/WARN/FAIL
./scripts/check_harvester.sh     # جزئیات ضبط: مسیر داده، ضربان، CSVها، ژورنال
```

## خواسته‌های اصلی و وضعیت

| # | خواسته | وضعیت | شاهد |
|---|--------|-------|------|
| ۱ | ضبط تیک از متاتریدر | ✅ | ۴۰ نماد، `status.json` زنده (نسخهٔ 1.01)، حجم ۱.۶MB |
| ۲ | داده روی دیسک (CSV روزانه) | ✅ | ۳۴ نماد با فایل امروز، حجم ~۱.۵MB در چند دقیقه |
| ۳ | گزارش هر ۵ دقیقه در گیت | ✅ | شاخهٔ `reports`: `cycle #2 … #6`، هر ~۵ دقیقه |
| ۴ | فقط کاغذی | ✅ | `paper.live_trading=false`، بدون هیچ فراخوانی ارسال سفارش |
| ۵ | Gemini رایگان + Ollama محلی | ⚠️ آماده، کلید/مدل تنظیم نشده | `./scripts/setup_llm.sh --status` |
| ۶ | کالیبراسیون هزینه‌ها از دادهٔ واقعی | ⏳ بعد از ~۱ ساعت داده | `slippage_bps` / `commission_bps` |

## مسیرهای واقعی این ماشین (portable MT5)

```
دادهٔ ترمینال : ~/Wine/drive_c/Program Files/MetaTrader 5
اکسپرت        : .../MQL5/Experts/TickHarvester.mq5 + .ex5
خروجی تیک     : .../MQL5/Files/harvester/{status.json,symbols.json,ticks/}
حساب          : 53290594 — Alpari-MT5-Demo
```

نکته: ترمینال در حالت **portable** است (`portable.txt`) و پوشه‌ها با حرف بزرگ ساخته
می‌شوند (`Bases`, `Config`, `Logs`, `MQL5`, `Profiles`). لینوکس حساس به حرف است، پس
همه‌ی اسکریپت‌ها با `-iname`/`dir_ci` جست‌وجو می‌کنند. هیچ‌چیز در
`AppData/Roaming/MetaQuotes` نوشته نمی‌شود؛ آن پوشه‌ها دست‌ساز و بی‌اثرند.

## کارهای انجام‌شده (خلاصهٔ فنی)

* Wine از مخزن WineHQ، نسخهٔ **11.22** (نسخهٔ 11.0 با محافظ ضد-دیباگ MT5 خراب است).
* MT5 build 6333، حساب دموی الپاری، AutoTrading روشن، اکسپرت روی `XAUUSD_i`.
* اکسپرت نسخهٔ **1.01**: نوشتن `status.json`/`symbols.json` در فایل موقت و جایگزینی با
  `FileDelete` + `FileMove(tmp,0,path,0)` (نسخهٔ ۵ آرگومانی در این بیلد وجود ندارد).
* `doctor` حالا ضربان را از `age_s` هر نماد (یا mtime) می‌خواند، نه فیلد سطح‌بالا.
* سرویس `tradeagent-loop.service` هر ۳۰۰ ثانیه: چرخه + گزارش + پوش به `reports`.
* اسکریپت‌ها: `setup_once`, `attach_harvester`, `check_harvester`, `connect_harvester`,
  `verify_goals`, `setup_llm`, `install_systemd_user`, `make_offline_bundle`,
  `fix_mt5_debugger`, `find_mt5_data`.

## چه چیزی باقی مانده

1. `./scripts/setup_llm.sh --gemini <key>` یا `--ollama` (و بعد restart سرویس).
2. بعد از ~۱ ساعت داده: کالیبراسیون `slippage_bps`/`commission_bps`.
3. بازبینی روزانه: `verify_goals.sh` (اگر FAIL شد، همان خط می‌گوید کجا را ببین).

## اگر کامپایل شکست خورد

`./scripts/attach_harvester.sh` خروجی کامپایلر را چاپ می‌کند. خطاهای رایج:

* `error 199: wrong parameters count` → امضای تابع با این بیلد MT5 نمی‌خواند؛
  `FileMove` اینجا ۴ آرگومان دارد (بدون `FILE_REWRITE`).
* `no ex5` بدون خطا → مسیر دارای فاصله؛ اسکریپت خودش در `/tmp/tradeagent-compile`
  کامپایل می‌کند و فایل را برمی‌گرداند.
