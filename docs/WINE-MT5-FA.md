# نصب MetaTrader 5 و راه‌اندازی اکسپرت

هدف: یک ترمینال MT5 که با حساب **دمو** بروکر وصل است و اکسپرت `TickHarvester`
روی یکی از چارت‌هایش اجرا می‌شود. بقیه‌ی سیستم به پایتون لینوکسی وصل است.

---

## پیش‌فرض این پروژه: همه‌چیز در `~/Wine`

```
/usr/bin/wine                     ← خود Wine (از apt، نیازمند sudo، سیستم‌wide)
~/Wine                            ← WINEPREFIX؛ «محیط ویندوزی» تو
~/Wine/drive_c/Program Files/MetaTrader 5/              ← ترمینال MT5
~/Wine/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/<HASH>/MQL5/Files/
                                                        ← داده‌ی تیک و اکسپرت
```

یعنی نصب Wine سیستم‌wide است، ولی **هیچ فایل شخصی/داده‌ای بیرون از `~/Wine` نوشته
نمی‌شود**. برای اینکه مسیر دیگری بخواهی، همه‌ی اسکریپت‌ها این متغیر را می‌خوانند:

```bash
export MT5_WINEPREFIX="$HOME/WineMT5"     # یا هر مسیر دیگر
```

فقط این را در `~/.bashrc` بگذار تا `install_mt5_wine.sh`، `compile_harvester.sh`،
`find_mt5_data.sh` و `connect_harvester.sh` هم همان را ببینند.

### اگر از قبل در مسیر دیگری نصب کرده‌ای

```bash
mv ~/.mt5-wine64 ~/Wine        # انتقال prefix؛ محتوای drive_c نسبی است و سالم می‌ماند
sed -i 's|\.mt5-wine64|Wine|g' ~/.bashrc
source ~/.bashrc
mt5                            # لانچر جدید (یا دوباره گام ۷ راهنما)
```

### «می‌خواهم خود Wine هم داخل ~/Wine باشد»

شدنی است ولی **توصیه نمی‌شود** و این پروژه تستش نکرده: Wine به کتابخانه‌های
سیستمی (freetype، fontconfig، libx11، Vulkan/OpenGL، و برای بخش ۳۲بیتی multiarch)
وابسته است که بدون root نصب نمی‌شوند. مسیرهای واقع‌بینانه:

| گزینه | نتیجه |
|---|---|
| `apt` + prefix در `~/Wine` (پیش‌فرض ما) | پایدارترین؛ تنها چیزی که تست شده |
| Bottles / Flatpak (`flatpak install flathub com.usebottles.bottles`) | Wine داخل سندباکس خودش، داده در `~/.var/app/...` |
| استخراج دستی بسته‌های WineHQ در `~/Wine/runtime` | شکننده؛ MT5 x64 معمولاً بالا می‌آید ولی آپدیت/وابستگی دستی است |

اگر گزینه‌ی سوم را واقعاً می‌خواهی، بگو تا اسکریپت اختصاصی‌اش را برای همان
نسخه‌ی اوبونتوت بنویسم؛ ولی برای این پروژه `apt` + `~/Wine` انتخاب درست است.

---

## مسیر A — Linux (Ubuntu 22.04/24.04، Debian 12) با Wine

```bash
cd tradeAgent
./scripts/install_mt5_wine.sh --dry-run      # نمایش قدم‌ها بدون تغییر سیستم
./scripts/install_mt5_wine.sh
```

اسکریپت این کارها را می‌کند:

1. `winehq-stable` + `winetricks` را از مخزن رسمی WineHQ نصب می‌کند
   (روی دبیان، `deb https://dl.winehq.org/wine-builds/debian/` — اگر آدرس توزیعت
   فرق داشت، `DISTRO_ID`/`DISTRO_CODENAME` را در فایل دستی اصلاح کن).
2. prefix تمیز `~/Wine` می‌سازد، `corefonts` و `vcrun2019` را نصب می‌کند.
3. نصب‌کننده‌ی MT5 را دانلود و با `/auto` نصب می‌کند.
4. اکسپرت را در `MQL5/Experts` کپی و پوشه‌ی `MQL5/Files/harvester` را می‌سازد.
5. لانچر `~/.local/bin/mt5` را می‌سازد.

**نصب‌کننده‌ی بروکر خودت بهتر است.** نصب‌کننده‌ی پیش‌فرض، ترمینال عمومی MetaQuotes
است (بدون سرور بروکر). فایل `.exe` بروکر را بگیر و بده:

```bash
./scripts/install_mt5_wine.sh --installer ~/Downloads/YourBroker5Setup.exe
# یا
./scripts/install_mt5_wine.sh --installer-url "https://broker.example/download/mt5setup.exe"
```

نکته‌ها:

* اولین اجرا ممکن است پنجره‌ی Mono/Gecko بدهد؛ «Cancel» کن (MT5 به آن نیاز ندارد).
* اگر ترمینال هنگام ورود به حساب کرش کرد: `winetricks -q vcrun2019 corefonts` و
  دوباره امتحان کن؛ بعضی بروکرها به `msvcp140` هم نیاز دارند (`vcrun2019` پوشش می‌دهد).
* فونت فارسی: `winetricks -q corefonts` + در MT5 فونت چارت را روی `Tahoma` بگذار.
* برای اجرای بی‌صدا روی سرور بدون مانیتور از `Xvfb` استفاده کن:
  `xvfb-run -a mt5` (نصب: `sudo apt install -y xvfb`). MT5 برای تیک‌گرفتن به پنجره
  نیاز ندارد، ولی **باید** یک چارت باز و اکسپرت روی آن فعال باشد.

## مسیر B — ویندوز واقعی یا VPS ویندوزی

1. نصب‌کننده‌ی بروکر را نصب کن.
2. در MT5: `File → Open Data Folder` → `MQL5\Experts` → کپی `TickHarvester.mq5`.
3. MetaEditor: فایل را باز کن، `F7`.
4. اگر پایتون روی لینوکس/ماشین دیگری است، پوشه‌ی `MQL5\Files` را share کن (SMB)
   و در لینوکس mount کن؛ پیکربندی `data.harvester_dir` را روی مسیر mount بگذار.
   نمونه با SSHFS:
   ```bash
   sshfs user@vps:/c/Users/you/AppData/Roaming/MetaQuotes/Terminal/<HASH>/MQL5/Files ~/mt5/MQL5/Files
   ```
   (برای اینکه mount خودکار بماند: `autossh` + `systemd` یا `/etc/fstab`.)

## مسیر C — macOS

Wine خالص روی macOS جدید دردناک است؛ بهترتیب اولویت:

1. **CrossOver** (پولی، پایدارترین): نصب MT5 → همان مسیر `MQL5/Experts`.
2. **Whisky / PlayOnMac**: نصب MT5 با bottle ویندوز ۱۰ ۶۴ بیتی.
3. **یک VPS ویندوزی ارزان** و mount پوشه در مک (مسیر B).

---

## نصب و اجرای اکسپرت (همه‌ی سیستم‌عامل‌ها)

1. **کامپایل**: در MetaEditor فایل `TickHarvester.mq5` را باز کن → `F7`.
   باید `0 errors, 0 warnings` بدهد. از خط فرمان (Linux/Wine):
   ```bash
   ./scripts/compile_harvester.sh
   ```
2. یک **چارت** باز کن (هر نمادی؛ مثلاً `BTCUSD` M1). یک چارت کافی است — اکسپرت
   خودش به همه‌ی نمادهای Market Watch دسترسی دارد.
3. از پنجره‌ی Navigator اکسپرت را روی چارت بکش.
4. در پنجره‌ی تنظیمات: **Allow Algo Trading** را تیک بزن.
5. دکمه‌ی **AutoTrading** در نوار ابزار باید سبز باشد. (اگر سبز نیست: Tools →
   Options → Expert Advisors → `Allow automated trading`.)
6. تب **Experts** باید این را نشان بدهد:
   ```
   [harvester] 1.00 شروع شد | 14 نماد | پوشه=harvester | poll=250ms | روز=20261126
   [harvester] مسیر فایل‌ها: MQL5\Files\harvester\ticks\<SYMBOL>\<YYYYMMDD>.csv
   ```
7. چند دقیقه بعد چک کن:
   ```bash
   ./scripts/find_mt5_data.sh
   ls -la "<MQL5/Files>/harvester/ticks/"
   ```

> **حساب:** نیازی به اجازه‌ی معامله نیست؛ اکسپرت هیچ سفارشی نمی‌فرستد. حساب دمو
> کافی است. اگر بروکرت لاگین ندارد، MT5 تیک هم نمی‌گیرد — پس اتصال به سرور
> بروکر شرط لازم است.

## انتخاب نمادهای ۲۴ ساعته

اکسپرت به‌صورت خودکار همه‌ی نمادهایی را انتخاب می‌کند که **ساعت معاملات هفتگی‌شان
≥ `InpMinWeeklyHours`** (پیش‌فرض ۱۰۰ ساعت) باشد:

| نمونه | ساعت هفتگی | انتخاب می‌شود |
|---|---|---|
| BTCUSD، ETHUSD، XRPUSD | ۱۶۸ | ✅ |
| EURUSD، GBPUSD، USDJPY | ~۱۲۰ | ✅ |
| XAUUSD | ~۱۲۰ | ✅ |
| سهام CFD (AAPL…) | ۴۰ | ❌ |
| شاخص‌ها با سشن محدود | ۴۰–۸۰ | ❌ |

اگر می‌خواهی فقط چند نماد خاص باشد:
```
InpSymbolsCsv = "BTCUSD,ETHUSD,SOLUSD,EURUSD,GBPUSD"
InpAutoDiscover = false
```
نام دقیق نماد بروکر مهم است (`BTCUSD.a`، `BTCUSDm`، `BTCUSD.r` …). با
`scripts/find_mt5_data.sh` یا پنجره‌ی Market Watch چک کن.

## اگر تیک نمی‌آید

| علامت | علت | راه‌حل |
|---|---|---|
| `پوشه‌ی harvester خالی است` | اکسپرت روی چارت نیست / AlgoTrading خاموش | اکسپرت را دوباره attach کن، دکمه‌ی AutoTrading را سبز کن |
| `0 نماد` در لاگ | Market Watch خالی یا نام‌ها فرق دارد | چند نماد را دستی به Market Watch اضافه کن، `InpSymbolsCsv` بده |
| `CopyTicks خطا err=4401` | تاریخچه‌ی تیک برای نماد نیست | در Market Watch آن نماد را باز کن و کمی صبر کن |
| فایل ساخته می‌شود ولی خالی | بازار بسته است (فارکس آخر هفته) | کریپتو ۲۴/۷ است؛ آخر هفته فقط آن‌ها تیک دارند |
| `status.json` قدیمی است | ترمینال قطع/کرش کرده | ترمینال را دوباره باز کن؛ سرویس را ری‌استارت کن |
| تیک‌ها با تأخیر می‌رسند | `InpPollMs` بزرگ یا سیستم کند | ۲۵۰ms بگذار؛ اگر CPU ضعیف است ۵۰۰ms |

## چک نهایی

```bash
.venv/bin/tradeagent doctor
```

باید `منبع داده harvester: OK` با تعداد نماد و عمر ضربان ببینی. سپس:

```bash
.venv/bin/tradeagent cycle     # یک چرخه‌ی واقعی روی داده‌ی تیک
.venv/bin/tradeagent report --cat
```
