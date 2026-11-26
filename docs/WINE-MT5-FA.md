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

## خطای «A debugger has been found running in your system»

این پیام از محافظ ضد-دیباگ خود متاتریدر می‌آید و با Wine یک **false positive**
شناخته‌شده است (خصوصاً **Wine 11.0**؛ هم هنگام نصب `mt5setup.exe` و هم هنگام اجرا).

ترتیب کار (کم‌هزینه → پرهزینه):

```bash
./scripts/fix_mt5_debugger.sh          # AeDebug + WINEDEBUG + wineserver
```

اسکریپت این کارها را می‌کند:

1. `WINEDEBUG` را حذف و `wineserver -k` می‌کند (بعضی نصب‌کننده‌ها وجود این متغیر را
   «دیباگر» می‌بینند).
2. مقدار `Debugger`/`Auto` را از `HKLM\...\AeDebug` در هر دو نمای رجیستری پاک
   می‌کند — Wine خودش `winedbg` را آنجا ثبت می‌کند و MT5 همان را می‌بیند.
3. دیالوگ کرش Wine را خاموش می‌کند.

اگر باز هم تکرار شد، مسئله نسخهٔ Wine است:

```bash
apt policy winehq-stable | head -12          # چه نسخه‌هایی موجود است؟
```

* نسخه‌ی **10.0** در فهرست بود → به آن برگرد و hold کن:
  ```bash
  sudo apt install winehq-stable=10.0.0.0~noble-1 wine-stable=10.0.0.0~noble-1 \
       wine-stable-amd64=10.0.0.0~noble-1 wine-stable-i386=10.0.0.0~noble-1
  sudo apt-mark hold winehq-stable wine-stable wine-stable-amd64 wine-stable-i386
  ```
* یا `winehq-devel` / `winehq-staging` (نسخه‌های ۱۱.۶+ که این مشکل در آن‌ها رفع شده)
  را نصب کن و `wine --version` را چک کن.
* اگر هیچ‌کدام جواب نداد: **VM ویندوزی** (VirtualBox/KVM) یا VPS ویندوزی برای MT5.
  بقیهٔ زنجیره (پایتون، گزارش، ژورنال) روی لینوکس می‌ماند و پوشه‌ی `MQL5/Files`
  با share/rsync منتقل می‌شود.

### اگر رجیستری را پاک کردی و باز هم شکست خورد (تجربه‌ی واقعی)

اگر بعد از `fix_mt5_debugger.sh` خروجی این‌طور بود:

```
0124:fixme:ntdll:NtRaiseHardError 0x50000018 ... stub
```

یعنی نصب‌کننده به مسیر مرگ‌بار رفته و Wine نتوانسته پنجره‌ی خطا را رسم کند؛
پاک‌کردن `AeDebug` دردی را دوا نمی‌کند چون **خودِ Wine 11.0** با محافظ MT5
ناسازگار است. جمع‌بندی گزارش‌های انجمن MQL5:

| Wine | نتیجه با MT5 امروزی |
|------|---------------------|
| Ubuntu 9.0 | خطای دیباگر، حتی وسط نصب |
| **WineHQ 11.0** | خطای دیباگر / کرش بی‌صدا |
| WineHQ 11.1 و بالاتر (مثلاً 11.6/11.18) | درست کار می‌کند |
| Wine 10.x | درست کار می‌کند (ولی متاکوتس پشتیبانی نمی‌کند) |

پس راه درست، تغییر **شاخه** است نه پاک‌کردن رجیستری:

```bash
sudo apt-get install -y --install-recommends winehq-devel   # یا winehq-staging
wine --version                                              # باید 11.x با x>0 باشد
```

`winehq-devel` جای `winehq-stable` را می‌گیرد (تضاد دارند و apt خودش stable را
حذف می‌کند)؛ prefix موجود با اولین اجرا خودش به‌روز می‌شود و چیزی پاک نمی‌شود.

گزینه‌ی جایگزین، نصب‌کننده‌ی رسمی خود متاکوتس است که Wine و MT5 را با هم در
`~/.mt5` می‌سازد:

```bash
wget -O ~/Downloads/mt5linux.sh \
  https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5linux.sh
chmod +x ~/Downloads/mt5linux.sh
~/Downloads/mt5linux.sh          # با کاربر معمولی، نه sudo
```

**هشدار:** این اسکریپت فایل‌های `/etc/apt/sources.list.d/winehq*` را بازنویسی
می‌کند و `apt upgrade -y` می‌زند؛ اگر WineHQ را دستی تنظیم کرده‌ای، اول از
`sources.list.d` بکاپ بگیر. مسیر داده‌اش `~/.mt5` است نه `~/Wine`، پس بعد از
نصب باید `MT5_WINEPREFIX=$HOME/.mt5 ./scripts/connect_harvester.sh` بزنی.

منابع این تشخیص: گزارش‌های انجمن MQL5 دربارهٔ Wine 11 و MT5، و تجربهٔ پروژه‌های
مشابه که با حذف مقدار `Debugger` از `AeDebug` یا برگشت به Wine 10 حل شده است.

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
| `winetricks` هنگام دانلود خطا می‌دهد | Sourceforge/سایت فونت بلاک است | اول MT5 را بدون آن امتحان کن؛ بیشتر بیلدها بدون corefonts/vcrun هم بالا می‌آیند. اگر لازم شد، فایل‌ها را دستی دانلود و در `~/.cache/winetricks` بگذار |
| پنجره‌ی نصب MT5 سفید/خالی است | رندر Gecko/فونت | `WINEDLLOVERRIDES="mscoree=d;mshtml=d" wine ~/Downloads/mt5setup.exe` |
| MT5 بعد از لاگین کرش می‌کند | کتابخانه‌ی VC++ | `winetricks -q vcrun2019` (یا `vcrun2022`) و دوباره اجرا |
| Wine 11 نصب شده ولی `winetricks` قدیمی است | نسخه‌ی apt قدیمی | `winetricks --self-update` یا از مخزن apt همان نسخه را بپذیر؛ خطای «unknown wine version» معمولاً بی‌خطر است |

## چک نهایی

```bash
.venv/bin/tradeagent doctor
```

باید `منبع داده harvester: OK` با تعداد نماد و عمر ضربان ببینی. سپس:

```bash
.venv/bin/tradeagent cycle     # یک چرخه‌ی واقعی روی داده‌ی تیک
.venv/bin/tradeagent report --cat
```
