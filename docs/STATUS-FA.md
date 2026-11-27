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

## نمادهای واقعی الپاری (از `Bases/*/history`)

| نماد | نوع |
|------|-----|
| `BITCOIN_i` | بیت‌کوین (پسوند `_i` مخصوص الپاری) |
| `XAUUSD_i` | طلا |
| `EURUSD`, `GBPUSD`, `USDCHF`, `USDJPY` | جفت‌ارز، **بدون** پسوند |
| `XPENG INC - ADR CFD_i` | سهام CFD (نام دارای فاصله — پوشه‌ی CSV هم فاصله دارد، مشکلی نیست) |

پس «BTCUSD» وجود ندارد؛ نام درست **`BITCOIN_i`** است. چارت و preset از همین
تاریخچه‌ی خود ترمینال انتخاب می‌شوند، نه از حدس.

## مدل زبانی فعال

مدل محلی **`qwen2.5:7b-instruct`** روی Ollama نصب شده و انتخاب شده است
(`provider=ollama` در `.env`). کلید Gemini ذخیره مانده ولی گوگل برای آن پروژه
سهمیه/ظرفیت نمی‌دهد (`429`/`503`)، پس مسیر اصلی محلی است و گوگل فقط بکاپ.

`./scripts/setup_llm.sh` ترتیب کارش: اول مدل محلی را با یک درخواست واقعی
`/api/chat` می‌سنجد (نه فقط فهرست مدل‌ها)، بعد گوگل، و اگر گوگل سهمیه نداشت
خودکار به مدل محلی سوییچ می‌کند.

## سخت‌افزار این ماشین (اندازه‌گیری‌شده)

| جزء | مقدار |
|---|---|
| CPU | Intel Core Ultra 7 155H, 22 رشته |
| RAM | 30.7 GB |
| iGPU | Intel Arc Graphics (Meteor Lake) |
| dGPU | NVIDIA RTX 4050 Max-Q — **درایور در دسترس نیست** (`nvidia-smi` خطا) |
| NPU | Meteor Lake — Ollama پشتیبانی نمی‌کند |
| اجرای فعلی مدل | **`100% GPU`** روی Intel Arc با Vulkan، ۶.۶GB در VRAM |

مسیر Arc با Vulkan انجام شد (`--gpu-vulkan`، mesa 25.2.8): `ollama ps` می‌گوید
`100% GPU`. مسیر NVIDIA هنوز بسته است چون **Secure Boot روشن** است و ماژول DKMS
بار نمی‌شود (`dkms status` صفر ورودی، دو درایور ۵۹۰ و ۵۹۵ نصب)؛ راه‌حل: ثبت کلید MOK
یا خاموش‌کردن Secure Boot، بعد purge درایور قدیمی و ریبوت. GPU لازم نیست.

## RTX 4050 چرا بیکار است (و راهش)

`nvidia-driver-590-open` و `-595-open` هر دو نصب‌اند، ماژول روی دیسک هست (595.84) ولی
بار نمی‌شود: **Secure Boot روشن** است و امضای DKMS قبول نمی‌شود؛ `dkms status` هم صفر
ورودی دارد. `scripts/fix_nvidia.sh` این را تشخیص می‌دهد و بستهٔ امضاشدهٔ Canonical را نصب می‌کند.
روی این ماشین `linux-modules-nvidia-595-open-generic` نصب شد و برای کرنل‌های
**6.8.0-151** و **7.0.0-44** ماژول امضاشده آمد، ولی کرنل در حال اجرا **7.0.0-31** بود؛
یعنی فقط یک ریبوت لازم است تا روی 7.0.0-44 بالا بیاید و ماژول امضاشده بار شود.
اسکریپت این حالت «ماژول امضاشده برای کرنل دیگر» را جدا تشخیص می‌دهد.
تا آن زمان Intel Arc با Vulkan کار می‌کند (`100% GPU`).

## مرحلهٔ فعلی RTX 4050

بعد از ریبوت روی کرنل `7.0.0-44` ماژول امضاشده بارگذاری مجاز شد، ولی
`modprobe nvidia` می‌گوید **`No such device`** و `dmesg` هم `NVRM: No NVIDIA GPU found`
دارد. یعنی امضا حل شد و مسئله «چه کسی کارت را در اختیار دارد» است. حکم قطعی با `./scripts/fix_nvidia.sh --deep` آمد: **`vfio-pci` کارت را گرفته است**
(تنظیم پاس‌دادن GPU به ماشین مجازی). رفع: `./scripts/fix_nvidia.sh --free-vfio`
(جداکردن فوری + کامنت‌کردن `vfio` در `modprobe.d` + حذف `vfio-pci.ids` از grub،
با نسخهٔ پشتیبان) و سپس ریبوت.

## وضعیت فعلی GPU — حل شد

* NVIDIA: کرنل `7.0.0-44`، ماژول امضاشده بار شد (`NVRM 595.99.02`)، کارت از
  `vfio-pci` آزاد شد.
* Ollama: `size_vram ≈ 6.6 GB`، بک‌اند **NVIDIA (CUDA)** — یعنی مدل روی RTX 4050 است.
* `ExecStartPre` منتظر `/dev/dri` می‌ماند تا بعد از ریبوت سرویس قبل از دستگاه
  گرافیکی بالا نیاید؛ `OLLAMA_CONTEXT_LENGTH=8192` هم برای ۶GB VRAM ست می‌شود.
* سنجش: `./scripts/fix_gpu.sh --speed` → tokens/s واقعی.

## نگهبان ضبط (watchdog)

`scripts/watch_harvester.sh` ضربان `status.json` و تازه‌ترین فایل تیک را می‌سنجد:

| حالت | تشخیص | کار |
|---|---|---|
| ضربان < ۹۰۰s | سالم | خارج می‌شود (exit 0) |
| تیک تازه، status قدیمی | اکسپرت نسخهٔ قدیمی | `--auto` دوباره اتصال/کامپایل می‌کند |
| هر دو قدیمی | ترمینال خواب/بسته | `--auto` با `startup.ini` دوباره بالا می‌آورد |

```bash
./scripts/watch_harvester.sh --auto --install-timer   # هر ۵ دقیقه، بدون دخالت دستی
```

## Ollama نصفه‌نصب (و تعمیرش)

نصب رسمی Ollama در میانهٔ دانلود قطع شد (`curl: (56)`), پس `ollama` نصب شد ولی
`/usr/local/lib/ollama/llama-server` هرگز استخراج نشد. مدل `qwen2.5:7b-instruct`
دانلود شده است، ولی بدون آن فایل هر درخواست `http 500: llama-server binary not found`
می‌دهد.

تعمیر: `./scripts/setup_llm.sh --repair-ollama` (دانلود قابل‌ادامهٔ ۱.۶GB، استخراج در
`/usr/local`، ری‌استارت سرویس، تست واقعی مدل). مدل ۴.۷GB دوباره دانلود نمی‌شود.

## مدل زبانی: کدام مدل؟

مدل‌ها را گوگل بازنشسته می‌کند (۲۵ نوامبر ۲۰۲۶ پیام داد `gemini-2.5-flash` برای
کاربران جدید بسته شده و `gemini-3.8-flash` جایش است). بنابراین مدل **ثابت نیست**:

1. `setup_llm.sh` فهرست `/v1beta/models` همان کلید را می‌گیرد و از میان مدل‌هایی که
   `generateContent` دارند، جدیدترین «flash» را انتخاب می‌کند؛
2. اگر گوگل ۴۰۴ با جملهٔ «use models/X» بدهد، همان X اول صف می‌رود و دوباره تست می‌شود؛
3. مدلِ کارکرده در `.env` نوشته می‌شود (`TRADEAGENT_LLM_MODEL`)، پس چرخه همان را
   استفاده می‌کند؛
4. در زمان اجرا هم `LLMAdvisor` اگر ۴۰۴ با پیشنهاد بگیرد، خودش مدل را عوض می‌کند و
   یک‌بار همان چرخه را با مدل جدید تکرار می‌کند (تست: `test_gemini_switches_to_the_model_google_suggests`).

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
