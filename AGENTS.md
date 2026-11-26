# AGENTS.md — قواعد کار روی tradeAgent

این ریپو یک سامانه‌ی **تیک‌برداری ۲۴/۷ + معامله‌ی کاغذی خودکار** است. پیش از هر
تغییر این فایل و `README.md` را بخوان.

## اجرا و تست

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q            # کاملاً آفلاین، بدون MT5، بدون شبکه
.venv/bin/tradeagent demo --cycles 4     # اجرای انتها-به-انتها با داده‌ی ساختگی
.venv/bin/tradeagent doctor
.venv/bin/tradeagent cycle --no-publish  # یک چرخه‌ی واقعی روی داده‌ی تیک
```

## قواعد سخت (نقض نکن)

1. **هیچ مسیر ارسال سفارش واقعی نساز.** نه `OrderSend`، نه کلید API بروکر، نه
   اتصال به `trade_mcp.execution`. این نسخه فقط کاغذی است و `paper.live_trading`
   صرفاً یک علامت مستند است، نه کلید فعال‌سازی.
2. **هیچ نگاه به آینده در ویژگی/استراتژی.** هر محاسبه در بسته‌شدن کندل `i` فقط
   می‌تواند از `bars[0..i]` استفاده کند. تست `test_features_finite_and_no_lookahead`
   این را نگه می‌دارد.
3. **گاردهای ریسک را شل نکن.** `PARAM_BOUNDS`، `paper.daily_loss_stop_pct`،
   `max_open_positions`، `max_notional_multiple` و کف هزینه در `strategy._size_from_atr`
   قابل حذف/دور زدن نیستند.
4. **پیشنهاد LLM فقط از `policy.validate_proposal` عبور می‌کند.** هیچ مسیر دیگری
   نباید سیاست را از خروجی مدل تغییر دهد. پیشنهاد بدون اعتبارسنجی = باگ.
5. **ریپوی کد را با انتشار گزارش آلوده نکن.** همه‌ی نوشتن‌ها روی گیت باید از
   `publish.Publisher` و فقط داخل `report.dir` روی شاخه‌ی `reports` باشد.
6. **کلیدها فقط در محیط** (`GEMINI_API_KEY` …). هرگز در TOML، تست یا گیت.
7. **هیچ مسیر هاردکد `/home/user/...`**؛ همه چیز از `Config` و `cfg.path(...)`.
8. **لاگ روی stderr.** `stdout` فقط برای خروجی ساختاریافته‌ی CLI/JSON.
9. **پروتکل فایل اکسپرت قرارداد است.** تغییر ستون‌های CSV یا نام فایل‌ها باید
   هم‌زمان در `mt5/Experts/TickHarvester.mq5`، `ticks.py` و
   `docs/HARVESTER-FA.md` بیاید و تست داشته باشد.
10. **هر رفتار جدید تست دارد.** تست‌ها باید بدون MT5/شبکه اجرا شوند؛ برای گیت از
    یک bare repo محلی استفاده کن (نمونه در `tests/test_publish.py`).

## نقشه‌ی کد

| مسیر | نقش |
|---|---|
| `src/tradeagent/ticks.py` | پارس CSV اکسپرت، خواندن افزایشی با offset، تجمیع ۱ ثانیه، منبع ساختگی |
| `src/tradeagent/features.py` | ویژگی‌های عددی بدون look-ahead (ATR بلوکی، z، تکانه، اسپرد) |
| `src/tradeagent/strategy.py` | سه استراتژی `momentum` / `meanrev` / `scalp` و کف هزینه |
| `src/tradeagent/policy.py` | سیاست پایه، نردبان سازگاری، `validate_proposal` |
| `src/tradeagent/paper.py` | موتور کاغذی، پرکردن/خروج، SQLite (`Store`) |
| `src/tradeagent/llm.py` | Gemini/Ollama، محدودیت نرخ، `_strip_code_fence` |
| `src/tradeagent/report.py` | گزارش Markdown فارسی + JSON فشرده + چک‌لیست ایجنت |
| `src/tradeagent/publish.py` | کلون جدا، کامیت/پوش فقط گزارش‌ها، retry |
| `src/tradeagent/loop.py` | چرخه‌ی کامل + حلقه‌ی مقاوم + قفل |
| `src/tradeagent/cli.py` | `init / doctor / demo / cycle / loop / status / report / publish-test` |
| `mt5/Experts/TickHarvester.mq5` | اکسپرت ضبط تیک (بدون include، بدون DLL) |
| `scripts/` | نصب MT5 روی Wine، کامپایل اکسپرت، یافتن پوشه‌ی داده، اجرا |
| `deploy/` | systemd unit و نمونه‌ی cron |

## سبک

* پایتون ۳.۱۱+، stdlib-only در زمان اجرا، `tomllib` برای پیکربندی.
* type hint و docstring کوتاه فارسی؛ نام‌های کد انگلیسی.
* خطاها در حلقه گرفته می‌شوند: یک چرخه‌ی خراب نباید کل حلقه را بکشد.
* اعداد تحلیل فقط از `features.py`؛ در گزارش/سایر ماژول‌ها فرمول جدید نساز.

## کارهای بعدی (به‌ترتیب اولویت)

1. بک‌تست روی آرشیو تیک با همان موتور کاغذی (بدون تغییر مسیر تصمیم).
2. مقایسه‌ی آماری نردبان سازگاری با سیاست ثابت (train/test زمانی).
3. آستانه‌ی «چند ضرر» و کول‌داون را از داده کالیبره کن، نه از حس.
4. داشبورد خواندنی (بدون اجرا) برای اکوییتی/پوزیشن‌ها.
5. فقط بعد از ۲ و ۳: بحث اجرای واقعی با تأیید انسانی دو مرحله‌ای.
