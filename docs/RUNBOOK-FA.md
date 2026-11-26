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
./scripts/setup_llm.sh --gemini AIza...  # کلید Gemini را تست و در .env ثبت می‌کند
./scripts/setup_llm.sh --ollama          # ollama + مدل پیش‌فرض را آماده می‌کند
./scripts/setup_llm.sh --off             # فقط تصمیم قطعی
```

هر سه حالت فقط روی `.env` (که در گیت نیست) می‌نویسند؛ هیچ کلیدی در ریپو نمی‌رود.
