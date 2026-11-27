# Runbook — اجرا، پایش، عیب‌یابی

## نصب اولیه

```bash
cd ~/tradeAgent
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/tradeagent init
cp .env.example .env        # کلید Gemini یا تنظیم Ollama
```

## اجرا

### گزینه ۱: systemd (توصیه‌شده)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/tradeagent-loop.service ~/.config/systemd/user/
# اگر پیکربندی محلی داری، ExecStart را به --config configs/local.toml تغییر بده
systemctl --user daemon-reload
systemctl --user enable --now tradeagent-loop.service
loginctl enable-linger "$USER"      # بعد از logout هم بماند
```

پایش:

```bash
systemctl --user status tradeagent-loop
journalctl --user -u tradeagent-loop -f
```

### گزینه ۲: اجرای دستی / tmux

```bash
./scripts/run_loop.sh 300
# یا
.venv/bin/tradeagent loop --interval 300
```

حلقه قفل `var/loop.lock` دارد؛ اجرای دوم بلافاصله با کد ۲ خارج می‌شود.
سیگنال `SIGTERM` چرخه‌ی جاری را تمام می‌کند و تمیز خارج می‌شود.

### گزینه ۳: cron

`deploy/tradeagent.cron` را ببین.

## پایش روزانه (۳ دقیقه)

```bash
tradeagent doctor                     # داده، ژورنال، انتشار، مدل
tradeagent status | jq '{balance,streak_losses,streak_wins,overall}'
tail -30 var/logs/tradeagent.log
ls -la var/reports/latest.md
```

گزارش گیت را هم می‌توانی مستقیم ببینی:
`reports/paper/latest.md` در شاخه‌ی `reports`.

## عیب‌یابی

| نشانه | بررسی | اقدام |
|---|---|---|
| `داده harvester کهنه` | `cat "<MQL5/Files>/harvester/status.json"` | ترمینال باز است؟ اکسپرت روی چارت است؟ AutoTrading سبز؟ |
| `هیچ کندل تازه‌ای نیامد` | `ls -la .../ticks/<SYM>/` | بازار بسته (فارکس آخر هفته) یا مسیر `data.harvester_dir` غلط |
| `پوش ناموفق` | `tradeagent publish-test --write` | توکن/کلید SSH، دسترسی write به ریپو |
| `مدل: HTTP 429` | سهمیه‌ی Gemini | `llm.every_cycles` را بالا ببر یا `provider=ollama` |
| `مدل: JSON نامعتبر` | گزارش بخش ۶ | مدل کوچک‌تر/بهتر؛ سیستم خودش رد کرده و ادامه داده |
| چرخه کند است | `bars_processed` در گزارش | `data.max_bars_per_cycle` را کم کن (مثلاً ۳۶۰۰) |
| `قفل کهنه` در لاگ | `cat var/loop.lock` | `rm var/loop.lock` و ری‌استارت سرویس |
| معامله‌ی عجیب (قیمت ۰) | لاگ ورود/خروج | فایل تیک ناقص یا نماد بدون فید — نماد را از `data.symbols` بردار |

### بازسازی از صفر (وقتی داده/سیاست خراب شده)

```bash
systemctl --user stop tradeagent-loop
mv var/paper.sqlite3 var/paper.sqlite3.bak-$(date +%s)
rm -f var/ingest_state.json
systemctl --user start tradeagent-loop
```

داده‌ی تیک دست نمی‌خورد؛ فقط وضعیت سیستم بازسازی می‌شود. برای برگشت، فایل
`.bak` را جای اصلی بگذار.

### پشتیبان‌گیری

```bash
sqlite3 var/paper.sqlite3 ".backup 'var/backup-$(date +%F).sqlite3'"
```

## چک‌لیست ایمنی (قبل از هر تغییر بزرگ)

- [ ] `paper.live_trading` همیشه `false` است؛ کد هیچ مسیر ارسال سفارش ندارد.
- [ ] `paper.daily_loss_stop_pct` روی مقدار قابل‌تحمل (مثلاً ۳٪) تنظیم است.
- [ ] `paper.max_open_positions` و `risk_per_trade_pct` با هم جمع نمی‌شوند به ریسک
      بی‌معنا (> ۲٪ کل).
- [ ] گزارش‌ها فقط روی شاخه‌ی `reports` می‌روند، نه شاخه‌ی کد.
- [ ] کلیدهای API فقط در `.env`/محیط، هرگز در `configs/*.toml` یا گیت.
- [ ] حساب MT5 **دمو** است (این نسخه سفارش نمی‌فرستد، ولی نسخه‌های بعدی همین
      ترمینال را استفاده می‌کنند).

## ارتقا

```bash
systemctl --user stop tradeagent-loop
git pull --ff-only
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # قبل از ری‌استارت، تست‌ها سبز باشند
systemctl --user start tradeagent-loop
```

مهاجرت دیتابیس: schema با `CREATE TABLE IF NOT EXISTS` ساخته می‌شود؛ تغییر ستون
در نسخه‌های بعدی نیاز به اسکریپت مهاجرت خواهد داشت (در `CHANGELOG` علامت می‌خورد).

## چه چیزی را کجا ببینم

| سؤال | فایل/دستور |
|---|---|
| آخرین وضعیت | `reports/paper/latest.md` (گیت) یا `var/reports/latest.md` |
| روند ساعتی/روزی | `reports/paper/cycles.jsonl` |
| چرا سیاست عوض شد | بخش ۴ گزارش + جدول `decisions` |
| چرا این معامله باز شد | لاگ (`var/logs/tradeagent.log`) + `last_intent:<SYM>` در جدول `meta` |
| آمار هر نماد | بخش خلاصه / `per_symbol_stats` در `tradeagent status` |
| سلامت داده | بخش ۵ گزارش + `status.json` اکسپرت |

## چک‌لیست خواسته‌های اصلی (هر بار یک نگاه)

```bash
./scripts/verify_goals.sh
```

پنج شرط را چک می‌کند و PASS/WARN/FAIL می‌دهد:

| # | شرط | معیار |
|---|-----|-------|
| ۱ | ضبط تیک زنده است | `status.json` با ضربان < ۱۸۰ ثانیه |
| ۲ | تیک امروز نوشته می‌شود | وجود `ticks/<SYMBOL>/<YYYYMMDD>.csv` |
| ۳ | گزارش هر ۵ دقیقه پوش می‌شود | آخرین کامیت شاخهٔ `reports` روی remote (< ۹۰۰ ثانیه) |
| ۴ | فقط کاغذی | `paper.live_trading=false` و نبود هیچ فراخوانی ارسال سفارش |
| ۵ | مدل زبانی | کلید Gemini یا Ollama محلی |

خروج غیرصفر یعنی دست‌کم یک شرط FAIL شده است.

## مدل زبانی: Gemini رایگان یا Ollama محلی

```bash
./scripts/setup_llm.sh --status          # الان چه چیزی فعال است؟
./scripts/setup_llm.sh            # کلید را می‌پرسد، تست می‌کند، در .env ثبت می‌کند
./scripts/setup_llm.sh --ollama          # ollama + مدل پیش‌فرض را آماده می‌کند
./scripts/setup_llm.sh --off             # فقط تصمیم قطعی
```

هر سه حالت فقط روی `.env` (که در گیت نیست) می‌نویسند؛ هیچ کلیدی در ریپو نمی‌رود.

### سخت‌افزار و اینکه مدل روی CPU است یا GPU

```bash
./scripts/setup_llm.sh --hardware
```

چه می‌گوید: مدل CPU و تعداد رشته‌ها و RAM از `/proc`، کارت گرافیک از `nvidia-smi`
یا `lspci`، و مهم‌تر از همه پاسخ خودِ Ollama از `/api/ps`:

* `0 GB on the GPU -> CPU only` یعنی مدل با پردازنده اجرا می‌شود؛ GPU لازم نیست.
* `5.0 GB on the GPU -> GPU` یعنی واقعاً روی کارت گرافیک است (`size_vram`).

### GPU: چرا مدل روی CPU است

```bash
./scripts/setup_llm.sh --gpu
```

سه چیز را جدا می‌گوید: وضعیت درایور NVIDIA (`nvidia-smi` + ماژول کرنل + بسته‌ها +
پیشنهاد `ubuntu-drivers`)، دستگاه‌های Vulkan (برای Intel Arc / Iris Xe)، و اینکه
آیا بستهٔ Ollama اصلاً runner ولکان دارد.

| وضعیت | راه |
|---|---|
| `nvidia-smi` خطای «couldn't communicate with the driver» | `sudo ubuntu-drivers install` و بعد `sudo reboot` |
| Intel Arc موجود و Vulkan نصب است | `sudo apt install mesa-vulkan-drivers vulkan-tools` سپس `./scripts/setup_llm.sh --gpu-vulkan` |
| NPU اینتل (Meteor Lake) | پشتیبانی نمی‌شود؛ دنبالش نرو |

`--gpu-vulkan` دو متغیر را در سرویس کاربری می‌گذارد (`OLLAMA_VULKAN=1`,
`OLLAMA_IGPU_ENABLE=1`)، ری‌استارت می‌کند و با `ollama ps` / `size_vram` نشان می‌دهد
که واقعاً روی GPU رفت یا نه.

### سرویس ماندگار Ollama

اگر `systemctl restart ollama` بگوید `Unit ollama.service not found`، یعنی سرویس
سیستمی ساخته نشده. `./scripts/setup_llm.sh --repair-ollama` یک **سرویس کاربری**
می‌سازد (`~/.config/systemd/user/ollama.service`، `Restart=always`) و روشنش می‌کند تا
بعد از ریبوت هم مدل بالا بیاید.

### اگر Ollama نصب است ولی جواب نمی‌دهد

نشانه: `error starting llama-server: llama-server binary not found`.

یعنی دانلود نصب نصفه مانده: فایل `ollama` هست ولی موتور استنتاجش
(`/usr/local/lib/ollama/llama-server`) نه. مدل هم بی‌فایده است تا آن فایل باشد.

```bash
./scripts/setup_llm.sh --repair-ollama
```

* حدود ۱.۶GB دانلود **قابل‌ادامه** (`curl -C -` با ۵ تلاش)؛ اگر قطع شد همان دستور را
  دوباره بزن، از همان‌جا ادامه می‌دهد.
* بعد از استخراج، سرویس را ری‌استارت می‌کند، مدل را تست می‌کند و `.env` را می‌نویسد.
* خودِ مدل (۴.۷GB) دوباره دانلود نمی‌شود.

### اگر گوگل ۴۲۹ یا ۵۰۳ داد (نه ۴۰۰/۴۰۳)

| کد | معنی | کار |
|---|---|---|
| `429` | سهمیهٔ رایگان آن پروژه در آن لحظه پر است | چند دقیقه صبر کن؛ سهمیهٔ رایگان هم دقیقه‌ای و هم روزانه سقف دارد |
| `503` | ظرفیت خود گوگل پر است | ۳ ثانیه بعد یک‌بار دوباره خودکار؛ اگر نشد چند دقیقه بعد |
| `404` با «use models/X» | مدل بازنشسته شده | خودکار همان X امتحان می‌شود |

در این حالت‌ها **کلید مشکلی ندارد**؛ `setup_llm.sh` هم همین را می‌گوید و کلید جدید
نمی‌خواهد. اگر می‌خواهی مستقل از سهمیه باشی: `./scripts/setup_llm.sh --install-ollama`.

### اگر کلید رد شد: اول تشخیص، بعد حدس

```bash
./scripts/setup_llm.sh --diagnose
```

این دستور پاسخ خام خود گوگل را (با کلید پوشیده) چاپ می‌کند. سه حالت رایج:

| پاسخ گوگل | معنی | کار |
|---|---|---|
| `API_KEY_INVALID` (400) | کلید غلط/ناقص | کلید را دوباره از AI Studio با آیکن کپی بردار (نه اسکرین‌شات) |
| `API_KEY_SERVICE_BLOCKED` (403) | کلید محدود شده | در AI Studio → کلید → API restrictions را بردار |
| `SERVICE_DISABLED` (403) | API روی آن پروژه روشن نیست | کلید را در AI Studio بساز (نه Cloud Console)؛ یا همان پروژه را فعال کن |
| `http 000` | شبکه بسته است | پروکسی/فیلتر؛ `export https_proxy=...` یا مسیر Ollama |

### گرفتن کلید Gemini رایگان (بدون کارت)

1. برو به <https://aistudio.google.com/apikey> با همان حساب گوگل.
2. `Create API key` → کلید با آیکن کپی بردار (فرمت‌ها: `AIza…` ~۳۹ کاراکتر یا `AQ.Ab8…` ~۵۳ کاراکتر).
3. همان را بده: `./scripts/setup_llm.sh` و در پیام مخفی بچسبان.
   اسکریپت اول با یک درخواست کوچک تست می‌کند، بعد ذخیره می‌کند. اگر شبکه بسته
   باشد پیام جدا می‌دهد (نه «کلید بد»).

### نصب Ollama محلی (بدون کلید)

```bash
./scripts/setup_llm.sh --install-ollama
```

* حدود ۱.۶ گیگابایت دانلود خودِ Ollama (نصب سیستمی، رمز sudo می‌خواهد).
* بعد مدل `qwen2.5:7b-instruct` حدود ۴.۷ گیگابایت — یک‌بار.
* حداقل ۸ گیگ رم توصیه می‌شود؛ با ۱۶ گیگ راحت کار می‌کند.
* اگر `ollama` از قبل هست: `./scripts/setup_llm.sh --ollama`.

هر دو مسیر اختیاری‌اند: بدون هیچ‌کدام، چرخه‌ها با تصمیم قطعی اجرا می‌شوند.

## هاروستر بعد از ریبوت

اگر MT5 خودکار بالا نیاید، ضربان قطع می‌شود (نشانگی: `heartbeat ... STALE`).

```bash
./scripts/watch_harvester.sh --auto --install-boot --install-timer
```

* `--install-boot` واحد `tradeagent-mt5.service` می‌سازد که در هر ورود ترمینال را با
  `startup.ini` و اکسپرت باز می‌کند؛
* `--install-timer` هر ۵ دقیقه بررسی می‌کند و در صورت قطع، خودش ترمینال را دوباره
  بالا می‌آورد و تا ۳۰۰ ثانیه (`WATCH_WAIT`) منتظر ضربان تازه می‌ماند؛
* اگر باز هم نیامد، **خودش** علت را از `var/logs/mt5.out` و جدیدترین لاگ ترمینال
  (خطوط Expert/error) چاپ می‌کند و می‌گوید مشکل Wine/نمایش است یا اکسپرت.

## ایجنت ابزاردار با trade-mcp

چرخه می‌تواند به‌جای «حدس از خلاصه»، خودش داده بگیرد. کلاینت MCP داخل پروژه است
(`src/tradeagent/mcp_client.py`) و ایجنت در `src/tradeagent/agent.py`.

```bash
# نصب یک‌بار
git clone https://github.com/enigmafarsco-lang/trade-mcp.git ~/trade-mcp
cd ~/trade-mcp && python3 -m venv .venv && .venv/bin/pip install -e .
# تست بدون متاتریدر (داده‌ی ساختگی):
trade-mcp --mock serve
```

در `configs/*.toml`:

```toml
[mcp]
server_cmd = "trade-mcp serve"     # یا "trade-mcp --mock serve" برای تست
enabled = true

[agent]
max_rounds = 3
max_calls = 6
skill_prompt = "gold_session_briefing"
skill_symbol = "XAUUSD"
```

قرارداد مدل: `{"analysis", "tool_calls", "proposal"}`. ابزارهای اجرای سفارش
(`propose_order`, `confirm_proposal`, …) **برای ایجنت مسدودند**؛ این سیستم کاغذی است.
ترتیب هر چرخه: مدل ابزار می‌خواهد → سرور MCP جواب می‌دهد → نتیجه به مدل → پیشنهاد →
`validate_proposal` → گزارش (بخش ۶ نشان می‌دهد چه ابزاری صدا خورد و چه شد).

پروفایل طلا: `configs/gold.toml` (یک نماد، هر چرخه مدل).

## پیش‌بینی روند از تیک ثانیه‌ای (nowcast)

`src/tradeagent/nowcast.py`: از حرکت ثانیه‌به‌ثانیه چهار ویژگی می‌سازد (سرعت ۵s،
شتاب، روند ۳۰s، عدم‌تعادل تیک) و با یک پرسپترون علامت بازده افق (پیش‌فرض ۶۰s) را
پیش‌بینی می‌کند. آموزش روی ۷۰٪ گذشته، آزمون روی ۳۰٪ ادامهٔ همان سری.

* در هر چرخه برای همهٔ نمادها محاسبه می‌شود و در گزارش (زیر جدول کیفیت داده) می‌آید؛
* به مدل هم داده می‌شود (`nowcast` در context) تا با آن آستانه‌ها را تنظیم کند؛
* ستون «دقت آزمون» صادق است: ۵۰٪ یعنی مدل چیزی یاد نگرفته و باید دستش را کوتاه کرد.

```toml
[nowcast]
enabled = true
horizon_s = 60
window_s = 900
```

## ایجنت روی طلا (سرویس جدا)

```bash
./scripts/run_gold.sh            # سرویس طلا را جای سرویس عمومی می‌نشاند
./scripts/run_gold.sh --status   # چه چیزی در حال اجراست + مالک قفل
./scripts/run_gold.sh --revert   # برگشت به سرویس عمومی
```

علت وجودش: اگر سرویس عمومی روشن باشد و دستی هم `loop` بزنی، دو نمونه روی یک قفل
می‌افتند («یک نمونه‌ی دیگر در حال اجراست»). این اسکریپت اول سرویس عمومی را می‌خواباند،
قفل مرده را پاک می‌کند، بعد سرویس طلا را بالا می‌آورد.

## قضاوت روند: کار ایجنت است، نه فرمول

`nowcast.py` فقط **عدد** می‌سازد: بازده ۵/۱۵/۳۰/۶۰/۱۲۰ ثانیه، تغییر سرعت، سهم تیک
صعودی، دامنه، اسپرد و ۱۰ قیمت آخر. این‌ها به مدل داده می‌شود و مدل باید در فیلد
`momentum` بگوید: جهت (`up/down/flat`)، فاز (`accelerating/steady/fading/reversing`)،
قدرت، افق و اطمینان.

قضاوت مدل در `state/forecasts.json` ثبت می‌شود و وقتی افقش سر رسید با قیمت واقعی نمره
می‌گیرد؛ گزارش هر چرخه «کارنامهٔ پیش‌بینی‌های ایجنت» را نشان می‌دهد (نرخ درست + وزنی
بر اساس اطمینان). این‌طوری ادعای مدل سنجیده می‌شود.

## خطای کامپایل اکسپرت trade-mcp (LongToString)

اگر MetaEditor این را داد:

```
undeclared identifier 'LongToString'    TradeMCPConnector.mq5  870 / 897
'+' - unexpected token
implicit conversion from 'unknown' to 'string'
```

این باگ خودِ trade-mcp است: تابع `LongToString` صدا زده شده ولی تعریف نشده و MQL5
چنین تابعی ندارد. اصلاح:

```bash
./scripts/patch_trade_mcp_connector.sh
```

* تابع کمکی `string LongToString(const long value) { return IntegerToString(value); }`
  را **بعد از آخرین `#include`** (نه بین `input`ها) درج می‌کند؛
* هر دو نسخه را اصلاح می‌کند: کلون ریپو (`~/trade-mcp`) و نسخهٔ نصب‌شده در
  `MQL5/Experts`؛ هر فایل نسخهٔ پشتیبان `.tradeagent-backup-<timestamp>` می‌گیرد؛
* اگر MetaEditor64.exe پیدا شود، خودش کامپایل می‌کند و لاگ را نشان می‌دهد؛
  اگر `.ex5` ساخته شد یعنی تمام.

`connect_trade_mcp.sh` هم همین اصلاح را خودکار انجام می‌دهد.

## سوکت ۸۷۸۷: کی گوش می‌دهد و چرا اکسپرت «وصل نیست»

معماری trade-mcp این است: **پایتون سرور است** و اکسپرت کلاینت. اکسپرت روی چارت،
هر چند ثانیه به `127.0.0.1:8787` وصل می‌شود؛ اگر در آن لحظه هیچ پروسه‌ای گوش ندهد،
در تب Experts این خط می‌آید:

```
[trade-mcp] connect to 127.0.0.1:8787 failed, err=4014 (is 'trade-mcp serve' running?)
```

`err=4014` یعنی «connection refused» — یعنی سوکت بسته بود، نه اینکه اکسپرت مجوز ندارد.

قاعده‌ها:

* **پورت ۸۷۸۷ فقط یک صاحب دارد.** یا سرویس حلقه (`tradeagent-gold`) آن را گرفته، یا
  `connect_trade_mcp.sh --watch`، یا یک `trade-mcp serve` دستی. دو نسخه با هم نمی‌شوند
  و نسخهٔ دوم `bind_failed` می‌گیرد.
* `--watch` دیگر هر ۵ ثانیه سرور نمی‌سازد و نمی‌کشد: یک سرور می‌سازد و **تا Ctrl-C
  باز نگه می‌دارد**. اگر پورت دست سرویس باشد، همان را می‌پرسد و وضعیت را از
  `var/state/mcp_status.json` می‌خواند (سرویس حلقه هر چرخه می‌نویسد).
* برای اینکه سوکت همیشه باز بماند (بدون ترمینال باز):

```bash
./scripts/run_gold.sh                 # سرویس tradeagent-gold را نصب و روشن می‌کند
./scripts/connect_trade_mcp.sh --watch
```

  سرویس حلقه هم تحلیل و حکم `momentum` می‌دهد، هم پل را زنده نگه می‌دارد.
* اگر `--watch` نوشت «8787 held by <پروسه>» یعنی یک صاحب جامانده هست؛ اول
  `./scripts/run_gold.sh --status` را ببین و در صورت لزوم سرویس را ری‌استارت کن.

## «سرویس بالا نیامد» بلافاصله بعد از نصب

اگر در لاگ این را دیدی:

```
Started tradeagent-gold.service …
tradeagent[…]: سیگنال 15 دریافت شد؛ پایان تمیز پس از چرخه‌ی جاری
```

یعنی سرویس تازه بالا آمده و همان لحظه SIGTERM گرفته — نه اینکه کرش کرده باشد. علتش
خودِ `run_gold.sh` بود: شمارهٔ pid را از `var/loop.lock` می‌خواند و همان را می‌کشت،
در حالی که آن pid ممکن بود نمونهٔ قدیمی باشد که هنوز چرخه‌اش را تمام نکرده بود؛ بعد
قفل را پاک می‌کرد و دو نمونه بر سر یک قفل و یک سوکت می‌جنگیدند.

حالا:

* نمونه‌های قبلی با الگوی **لنگرخورده** `^<repo>/.venv/bin/tradeagent` پیدا می‌شوند
  (شل‌های دیگر با همین مسیر در متن دستور گرفته نمی‌شوند)؛
* SIGTERM می‌رود و اسکریپت تا **خروج واقعی** (حداکثر ۲۰ ثانیه) صبر می‌کند؛ اگر تمام
  نشد، خودش می‌گوید «چند لحظه بعد دوباره بزن»؛
* قفلِ زنده پاک نمی‌شود؛
* آمادگی با `var/state/mcp_status.json` سنجیده می‌شود، پس «فعال است» به‌تنهایی کافی
  نیست — سوکت و حساب هم چک می‌شوند.

## دو کامپیوتر، یک اکانت متاتریدر

پورت ۸۷۸۷ روی `127.0.0.1` است، یعنی **لوکال هر کامپیوتر**. trade-mcp روی کامپیوتر دوم
هیچ ربطی به این یکی ندارد و نمی‌تواند پورت را بگیرد. اکانت دمو هم اجازهٔ لاگین هم‌زمان از
چند ترمینال را می‌دهد (لاگ خودت: `authorized on Alpari-MT5-Demo` و
`terminal synchronized … 200 symbols`). پس اگر اکسپرت وصل نمی‌شود، دلیل در همین
کامپیوتر است — خروجی `--watch` حالا می‌گوید MT5 اصلاً باز است یا نه.

## «پاسخ مدل JSON نبود»

مدل ۷B گاهی به‌جای JSON متن آزاد می‌نویسد. دو لایه اضافه شد:

* در حالت ollama، درخواست با `format: json` می‌رود (سرور خودش JSON را اجبار می‌کند)؛
* اگر باز هم JSON نبود، ایجنت **تا دو بار** پیام ترمیمی می‌فرستد و پاسخ را دوباره
  می‌خواهد؛ فقط اگر آن هم نشد، چرخه با `agent error` ثبت می‌شود (چرخه نمی‌شکند).

## «وضعیت پل MCP هنوز منتشر نشد» در حالی که ایجنت ابزار واقعی صدا می‌زند

اگر در لاگ سرویس این را دیدی:

```
ایجنت: tools=local_tick_digest, analyze_market, account_info, risk_report ...
```

یعنی اکسپرت **وصل است** (این ابزارها بدون اتصال جواب نمی‌دهند). پیام «منتشر نشد» فقط
باگ مسیر بود: حلقه فایل را در `state_dir` می‌نویسد (پیش‌فرض `var`)، یعنی
`var/mcp_status.json`، ولی اسکریپت فقط `var/state/…` را نگاه می‌کرد.

حالا `status_file()` هر دو مسیر را می‌بیند و `./scripts/run_gold.sh --status` این‌ها را
نشان می‌دهد:

```
=== ۳) پل MCP
  bridge=127.0.0.1:8787 ea_connected=True peer=127.0.0.1:5xxxx since=...s
  account=53290594 server=Alpari-MT5-Demo balance=... equity=...
```

و هر چرخه در ژورنال، حکم تکانهٔ ایجنت را هم می‌نویسد:

```
چرخه #111 تمام شد | equity=... | تکانه: up/accelerating قدرت=0.60 اطمینان=0.55 افق=90s | ...
```

## بارگذاری کد تازه بعد از `git pull`

سرویس با کدی که هنگام شروع خوانده اجرا می‌شود؛ `git pull` به‌تنهایی آن را عوض نمی‌کند
(به همین دلیل چرخه‌های بعد از pull هنوز خط `تکانه:` را نداشتند).

```bash
./scripts/run_gold.sh --restart
```

ری‌استارت، پل و اکسپرت را از نو وصل می‌کند (اکسپرت هر ۳ ثانیه دوباره تلاش می‌کند).

## حکم تکانه در قرارداد ایجنت

قرارداد JSON ایجنت تا قبل از این، بخش `momentum` را از مدل **نمی‌خواست**؛ فقط اگر مدل
خودش می‌نوشت ثبت می‌شد. حالا صریح خواسته می‌شود:

```json
"momentum": {"direction": "up|down|flat",
             "phase": "accelerating|steady|fading|reversing|unclear",
             "strength": 0.0-1.0, "horizon_s": 5-900, "confidence": 0.0-1.0,
             "reason": "..."}
```

* در هر پاسخ نهایی (حتی بدون پیشنهاد، حتی با دادهٔ کم → `unclear` و اطمینان پایین)؛
* تصمیم با مدل است، پایتون فقط اعداد خام می‌دهد و بعداً حدس‌ها را نمره می‌دهد؛
* همهٔ متن انسانی (`analysis`، `reason`) باید **فارسی** باشد (قبلاً مدل ۷B گاهی چینی
  جواب می‌داد و همان در گزارش می‌نشست)؛ نام نماد و اعداد دست‌نخورده می‌مانند.

## «ea_connected=False» — چه چیزی را چک کن

`./scripts/run_gold.sh --status` حالا بخش «۵) متاتریدر» هم دارد:

* `terminal64.exe: running` یا `NOT running` — اگر باز نیست، همین دلیل قطعی است؛
* `Experts log: <تاریخ>.log (آخرین نوشتن Ns پیش)` و دو خط آخر مربوط به trade-mcp —
  اگر خط `connect … failed, err=4014` هست، سوکت نبوده؛ اگر خط `connected` هست، وصل بوده؛
  اگر **هیچ** خط trade-mcp نیست، اکسپرت روی چارت اجرا نشده است.

## اکسپرت `err=4014` می‌دهد در حالی که سرویس «فعال» است

سه چیز متفاوت را از هم جدا کن (همه در `./scripts/run_gold.sh --status`):

| نشانه | معنا |
|---|---|
| `socket=CLOSED` | هیچ پروسه‌ای روی ۸۷۸۷ گوش نمی‌دهد → پل مرده است، نه اکسپرت |
| `socket=open` + `EA log: refused(4014)` + `same_netns=False` | متاتریدر در namespace شبکهٔ دیگری است؛ loopback مشترک نیست |
| `socket=open` + `refused(4014)` + `same_netns=True` | سوکت هست ولی اکسپرت تلاش نمی‌کند/اجرا نشده (Algo Trading، چارت) |

برای حالت دوم: در `configs/gold.toml`:

```toml
[mcp]
bridge_host = "0.0.0.0"
```

بعد `./scripts/run_gold.sh --restart`، و در تنظیمات اکسپرت `InpHost` را IP همین کامپیوتر
بگذار (`hostname -I`). پورت لوکال در دو namespace یکی نیست.

`socket_open` با یک connect واقعی سنجیده می‌شود، و `EA log` آخرین خط خودِ متاتریدر است —
یعنی برای تشخیص لازم نیست چیزی را کپی کنی.

## چرا «هیچ خط trade-mcp نیست» دروغ بود

متاتریدر فایل‌های لاگش را **UTF-16LE** می‌نویسد. خواندن با utf-8 باعث می‌شود هر حرف با یک
بایت صفر جدا شود و رشتهٔ `trade-mcp` هرگز پیدا نشود — نتیجه: ابزار می‌گفت اکسپرت اجرا نشده،
در حالی که خط `connect failed, err=4014` درست همان‌جا بود.

حالا هر سه جا (حلقه، `--status`، `--watch`) با BOM/بایت صفر تشخیص می‌دهند و UTF-16
می‌خوانند. علاوه بر این، فایلی انتخاب می‌شود که واقعاً خط trade-mcp دارد، نه صرفاً
تازه‌ترین فایل (Journal ترمینال همیشه تازه‌تر است و هرگز این خطوط را ندارد).

## یک جا برای همهٔ خواندن‌های متاتریدر: `tradeagent/diag.py`

قبلاً سه جا (حلقه، `--status`، `--watch`) هر کدام لاگ را جدا می‌خواندند و هر سه یک اشتباه
داشتند. حالا یک ماژول:

* UTF-16LE را می‌شناسد (BOM یا بایت صفر) و درست می‌خواند؛
* `MQL5/Logs` را به `logs` (Journal) ترجیح می‌دهد و فایلی را برمی‌دارد که واقعاً خط
  trade-mcp دارد؛
* اگر دادهٔ ترمینال جای غیرمنتظره باشد، جست‌وجوی بازگشتی محدود (≤ ۴۰۰ فایل، < ۸MB، ۳ روز)
  انجام می‌دهد؛
* مسیر `MQL5/Logs` را از پوشهٔ هاروستر (که خودِ حلقه پیدا کرده) هم می‌سازد.

خروجی `./scripts/run_gold.sh --status`:

```
=== ۵) متاتریدر
  terminal64.exe: running (pid 52397)
  Experts log: users/joshua/AppData/.../MQL5/Logs/20261127.log (آخرین نوشتن 12s پیش)
    2026.11.27 14:54:15  TradeMCPConnector  [trade-mcp] connected
```
