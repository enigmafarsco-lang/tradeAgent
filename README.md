# tradeAgent — تیک‌برداری ۲۴/۷ + چرخه‌ی کاغذی هوش مصنوعی با گزارش در گیت

**مسیر تازه‌ی این پروژه:** به‌جای «ابزار مشورتی که منتظر سؤال می‌ماند»، یک سیستم
خودکار که (۱) تیک همه‌ی نمادهای ۲۴ ساعته را از متاتریدر ضبط می‌کند، (۲) هر ۵ دقیقه
با آن داده معامله‌ی **کاغذی** انجام می‌دهد، (۳) پس از چند ضرر پشت‌سرهم نحوه‌ی
باز و بستن معامله را **عوض می‌کند**، و (۴) گزارش هر چرخه را در گیت منتشر می‌کند تا
هم تو و هم ایجنت (من) ببینیم چه اتفاقی افتاده.

> این نسخه **هیچ سفارشی به بروکر نمی‌فرستد**. مسیر اجرای واقعی عمداً وجود ندارد.
> اول باید روی حساب دمو و همین موتور کاغذی رفتار سیستم ثابت شود.

---

## معماری

```
┌───────────────────────── این کامپیوتر ─────────────────────────┐
│                                                                │
│  MetaTrader 5 (Wine / ویندوز / VPS)                             │
│    └── mt5/Experts/TickHarvester.mq5      ← روی یک چارت         │
│          هر ۲۵۰ms تیک همه‌ی نمادهای ۲۴ ساعته را می‌خواند        │
│          ↳ MQL5/Files/harvester/ticks/<SYM>/<YYYYMMDD>.csv      │
│          ↳ MQL5/Files/harvester/status.json + symbols.json      │
│                            │                                    │
│                            ▼  (فایل مشترک، بدون پورت و بدون API) │
│  Python — چرخه‌ی هر ۵ دقیقه (systemd / cron)                    │
│    ۱) خواندن افزایشی تیک‌ها و ساخت کندل ۱ ثانیه‌ای               │
│    ۲) ویژگی‌ها (تکانه، نوسان، اسپرد، z، ATR چنددقیقه‌ای)         │
│    ۳) استراتژی فعال: momentum | meanrev | scalp                 │
│    ۴) موتور کاغذی: ورود/خروج، SL/TP، هزینه، افت روزانه          │
│    ۵) نردبان سازگاری: ۲ ضرر پشت‌سرهم → تغییر نحوه‌ی معامله        │
│    ۶) مشاور LLM: Gemini رایگان یا Ollama محلی (اختیاری)         │
│    ۷) گزارش Markdown + JSON → شاخه‌ی `reports` در گیت           │
│                                                                │
└────────────────────────────────────────────────────────────────┘
                     │ git push (شاخه‌ی جدا)
                     ▼
        reports/paper/latest.md  ← همین را من می‌خوانم
        reports/paper/cycles.jsonl · daily/ · policy.json · state.json
```

هیچ `DLL` ویندوزی، هیچ `MetaTrader5` pip، هیچ پورت باز، هیچ `OrderSend`.
مرز فقط **فایل** است — همان تصمیمی که در پروژه‌ی خواهر
[`trade-mcp`](https://github.com/enigmafarsco-lang/trade-mcp) هم جواب داد.

---

## شروع سریع (۵ دقیقه، بدون متاتریدر)

```bash
cd tradeAgent
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/tradeagent init          # configs/local.toml + پوشه‌ها
.venv/bin/tradeagent demo          # ۶ چرخه با داده‌ی ساختگی، بدون MT5
.venv/bin/tradeagent report --cat  # متن گزارش آخرین چرخه
```

`demo` یک سیستم کامل را در `var/demo/...` می‌سازد: ژورنال SQLite، سیاست، و
گزارشی که دقیقاً همان شکلی است که در گیت منتشر می‌شود.

## شروع واقعی (اوبونتو، دستور به دستور)

مسیر کامل و کامندبه‌کامند: [docs/UBUNTU-STEP-BY-STEP-FA.md](docs/UBUNTU-STEP-BY-STEP-FA.md)

```bash
# ۱) MT5 روی Wine (اوبونتو/دبیان) — راهنما: docs/WINE-MT5-FA.md
./scripts/install_mt5_wine.sh --dry-run     # اول ببین چه می‌کند
./scripts/install_mt5_wine.sh

# ۲) اکسپرت را در MT5 کامپایل و روی یک چارت اجرا کن (F7 + Allow Algo Trading)
./scripts/compile_harvester.sh

# ۳) مسیر داده را پیدا و در پیکربندی بگذار (پیش‌فرض prefix: ~/Wine)
./scripts/find_mt5_data.sh
./scripts/connect_harvester.sh
#    [data] harvester_dir = "/home/you/Wine/drive_c/.../MQL5/Files/harvester"

# ۴) سلامت
.venv/bin/tradeagent doctor

# ۵) چرخه‌ی ۵ دقیقه‌ای + انتشار گزارش
cp .env.example .env       # GEMINI_API_KEY را بگذار (یا provider=ollama)
.venv/bin/tradeagent loop  # یا: systemd با deploy/tradeagent-loop.service
```

---

## نردبان سازگاری — «اگر چند ضرر داد، روشش را عوض کن»

هر پله یک تبدیل **صریح و بازگشت‌پذیر** روی پارامترهاست؛ هیچ‌چیز تصادفی نیست:

| پله | محرک | چه چیزی عوض می‌شود | چرا |
|---|---|---|---|
| ۱ | ۲ ضرر پشت‌سرهم | `breakout_k` ↑ ۲۵٪، `min_edge_bps` +۱ | ورود فقط با تکانه‌ی قوی‌تر |
| ۲ | ضرر بعدی | `max_spread_bps` ×۰٫۷۵، `cooldown_s` ×۱٫۵ | فیلتر هزینه، کمتر معامله‌کردن |
| ۳ | ضرر بعدی | `sl_atr` ×۱٫۱۵، `rr` ×۰٫۹ | استاپ دورتر از نویز، هدف نزدیک‌تر |
| ۴ | ضرر بعدی | تغییر خانواده: momentum → meanrev → scalp | شکست ساختاری یک استراتژی |
| ۵ | ضرر بعدی | `risk_scale` = ۰٫۵، کول‌داون ×۲ | کاهش ریسک، نه جنگیدن با بازار |
| ↑ | ۲ برد پشت‌سرهم | یک پله عقب‌نشینی | بازگشت تدریجی به سیاست پایه |

* حداقل ۲ چرخه بین دو تغییر فاصله است (`policy.adapt_cooldown_cycles`).
* هر تغییر با قبل/بعد و دلیل در گزارش و ژورنال ثبت می‌شود.
* سقف افت روزانه (`paper.daily_loss_stop_pct`) مستقل از نردبان، ورود را قفل می‌کند.

## مدل زبانی (اختیاری، نه اجباری)

| provider | هزینه | پیش‌فرض مدل | نکته |
|---|---|---|---|
| `gemini` | رایگان (سهمیه‌ی روزانه) | `gemini-2.5-flash` | فقط `GEMINI_API_KEY` در محیط |
| `ollama` | صفر، محلی | `qwen2.5:7b-instruct` | `ollama pull ...` و `provider=ollama` |
| `none` | — | — | سیستم فقط با نردبان قطعی کار می‌کند |

مدل **فقط پیشنهاد** می‌دهد: یک JSON شامل `variant` و تغییر پارامترها. هر پیشنهاد با
`policy.validate_proposal` سنجیده می‌شود — variant باید از فهرست مجاز باشد، هر پارامتر
حداکثر ±۲۰٪ جابه‌جا شود، `risk_scale` و سقف‌های ریسک از این مسیر قابل تغییر نیستند،
و پیشنهاد بدون تغییر یا با اطمینان کمتر از ۰٫۴ رد می‌شود. اگر کلید/شبکه نبود، چرخه
بدون مدل ادامه می‌دهد و در گزارش می‌نویسد «بدون پاسخ».

## گزارش‌ها

شاخه‌ی `reports` (جدا از شاخه‌ی کد، در کلون مستقل `var/publish`):

| فایل | برای چه |
|---|---|
| `reports/paper/latest.md` | گزارش کامل آخرین چرخه — چیزی که من می‌خوانم |
| `reports/paper/cycles.jsonl` | یک خط JSON فشرده به ازای هر چرخه |
| `reports/paper/daily/YYYY-MM-DD.md` | جدول تجمعی روز |
| `reports/paper/policy.json` | سیاست فعال و سیاست چرخه‌ی بعد |
| `reports/paper/state.json` | بالانس/اکوییتی/استریک/آمار |

بخش «۸) برای ایجنت» در هر گزارش، خودکار لیست می‌کند چه چیزی را باید در چرخه‌ی بعد
بررسی کنم (داده‌ی کهنه، تغییر سیاست، افت، نبود معامله…).
اگر پوش ناموفق باشد، **کامیت محلی می‌ماند** و هشدار در گزارش ثبت می‌شود.

---

## نقشه‌ی ریپو

```
mt5/Experts/TickHarvester.mq5   اکسپرت ضبط تیک (MQL5، بدون include)
src/tradeagent/
  ticks.py      خواندن افزایشی CSV + تجمیع به کندل ۱ ثانیه‌ای + منبع ساختگی
  features.py   ویژگی‌های بدون نگاه به آینده (ATR بلوکی، z، تکانه، اسپرد)
  strategy.py   سه استراتژی: momentum / meanrev / scalp
  policy.py     سیاست پایه، نردبان سازگاری، اعتبارسنجی پیشنهاد LLM
  paper.py      موتور کاغذی + ژورنال SQLite (bars، positions، trades…)
  llm.py        Gemini/Ollama با محدودیت نرخ و fallback قطعی
  report.py     گزارش Markdown فارسی + JSON فشرده
  publish.py    کلون جدا، کامیت و پوش فقط فایل‌های گزارش
  loop.py       چرخه‌ی ۵ دقیقه‌ای و حلقه‌ی مقاوم به خطا
  cli.py        init / doctor / demo / cycle / loop / status / report
scripts/        نصب MT5 روی Wine، کامپایل اکسپرت، پیدا کردن پوشه‌ی داده، بسته‌ی آفلاین
deploy/         systemd unit + نمونه‌ی cron
tests/          تست‌های کاملاً آفلاین (بدون MT5 و بدون اینترنت)
docs/           راهنمای فارسی نصب، جریان داده، سیاست، انتشار، اجرا
```

## مستندات

| فایل | محتوا |
|---|---|
| [docs/UBUNTU-STEP-BY-STEP-FA.md](docs/UBUNTU-STEP-BY-STEP-FA.md) | **دستور به دستور روی اوبونتو**: گیت → Wine → MT5 → اکسپرت → اجرا |
| [docs/WINE-MT5-FA.md](docs/WINE-MT5-FA.md) | نصب MT5 روی Wine/ویندوز، اکسپرت، عیب‌یابی |
| [docs/HARVESTER-FA.md](docs/HARVESTER-FA.md) | قرارداد فایل تیک، ورودی‌های اکسپرت، حجم دیسک |
| [docs/AI-LOOP-FA.md](docs/AI-LOOP-FA.md) | چرخه‌ی ۵ دقیقه، استراتژی‌ها، نردبان، مدل، محدودیت‌ها |
| [docs/PUBLISH-FA.md](docs/PUBLISH-FA.md) | راه‌اندازی شاخه‌ی گزارش، احراز هویت گیت، خواندن گزارش |
| [docs/RUNBOOK-FA.md](docs/RUNBOOK-FA.md) | اجرا، پایش، عیب‌یابی، چک‌لیست ایمنی |
| [docs/NETWORK-FA.md](docs/NETWORK-FA.md) | وقتی GitHub/PyPI/یک مخزن باز نمی‌شود: پروکسی، SSH روی ۴۴۳، نصب آفلاین |
| [docs/PLAN-FA.md](docs/PLAN-FA.md) | نقشه‌ی راه و نسبت این پروژه با `trade-mcp` |

## تست

```bash
.venv/bin/python -m pytest        # ۶۲ تست، بدون MT5، بدون شبکه
```

## وضعیت و قدم‌های بعد

- [x] اکسپرت ضبط تیک با کشف خودکار نمادهای ۲۴ ساعته (فارکس ۲۴/۵ + کریپتو ۲۴/۷)
- [x] خط لوله‌ی داده‌ی افزایشی + کندل ۱ ثانیه + ژورنال SQLite
- [x] سه استراتژی، نردبان سازگاری، مدل زبانی با گارد و fallback
- [x] چرخه‌ی ۵ دقیقه‌ای، گزارش فارسی، انتشار گیت با retry
- [x] نصب Wine، systemd، تست آفلاین، CI
- [ ] اجرای زنده‌ی واقعی (از مسیر `trade-mcp` با تأیید انسانی دو مرحله‌ای)
- [ ] بک‌تست روی آرشیو تیک‌های ذخیره‌شده و مقایسه‌ی پله‌های نردبان
- [ ] داشبورد وب سبک برای دیدن اکوییتی و پوزیشن‌ها

---

<details>
<summary><b>English summary</b></summary>

`tradeAgent` captures live ticks for every 24-hour symbol (crypto + FX) from
MetaTrader 5 through an MQL5 Expert Advisor that writes CSV files into
`MQL5/Files/harvester`. A stdlib-only Python loop runs every 5 minutes:
incremental tick ingest → 1-second bars → features → one of three paper
strategies → SQLite journal → a deterministic adaptation ladder that changes
how trades are opened and closed after consecutive losses (or recovers after
wins) → an optional Gemini/Ollama advisor that can only propose bounded,
validated parameter changes → a Persian Markdown report committed and pushed
to a dedicated `reports` branch in a separate clone.

Paper only. No broker credentials, no live orders, no Windows Python, no
`OrderSend`. Offline test suite (62 tests) covers ingest, features without
look-ahead, strategy gating, position sizing, SL/TP accounting, adaptive
ladder bounds, LLM validation/fallback, report rendering, and git publishing
against a local bare repo.

</details>
