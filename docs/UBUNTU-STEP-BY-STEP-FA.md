# اوبونتو، قدم‌به‌قدم — از صفر تا سیستم ۲۴/۷

این فایل **دستور به دستور** است. هر بلوک را کامل کپی کن، اجرا کن، و «باید ببینی» را
چک کن. اگر خطا دیدی، همان بخش «اگر خطا» را بخوان و جلوتر نرو.

فرض: Ubuntu **22.04** یا **24.04**، کاربر معمولی با دسترسی `sudo`، اینترنت دارد.
زمان تقریبی کل مسیر: ۳۵–۵۰ دقیقه (بیشترش دانلود و نصب Wine/MT5).

### «`cd ~/projects/tradeAgent` یعنی چه؟»

* `~/projects` فقط یک **پوشه‌ی معمولی** است که خودت می‌سازی (`mkdir -p ~/projects`).
  هیچ چیز جادویی ندارد؛ می‌توانی `~/code` یا `~/trade` هم بگذاری.
* `tradeAgent` زیرپوشه‌ی آن است که با `git clone` ساخته می‌شود و **کد پروژه** داخلش
  می‌آید (`README.md`, `src/`, `mt5/`, `scripts/`, …).
* `cd` = «برو داخل این پوشه». بعد از `cd`، دستورهای بعدی (venv، pytest، tradeagent)
  در همان پوشه اجرا می‌شوند.
* اگر با **GitHub Desktop** کلون کردی، مسیر معمولاً
  `~/Documents/GitHub/tradeAgent` است (نه `~/projects/tradeAgent`). هر جا در این
  سند `~/projects/tradeAgent` دیدی، مسیر خودت را بگذار؛ هیچ دستوری به مسیر
  خاصی وابسته نیست.
* تا وقتی `git clone` را اجرا نکرده‌ای، این پوشه **وجود ندارد** و `cd` خطای
  `No such file or directory` می‌دهد — طبیعی است.

`git` (خط فرمان) چیز اصلی است. GitHub Desktop فقط یک **رابط گرافیکی** روی همان
`git` است؛ داشتنش اختیاری است (گام ۳ب).

---

## گام ۱ — به‌روزرسانی سیستم و ابزارهای پایه

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ca-certificates build-essential python3 python3-venv python3-pip \
                    winbind cabextract unzip p7zip-full
```

**باید ببینی:** بدون خطای قرمز تمام شود.

```bash
git --version && python3 -V && uname -m
```

**باید ببینی:** `git version 2.x`، `Python 3.10.x` یا `3.12.x`، `x86_64`.

> اگر `x86_64` نبود (مثلاً ARM)، MT5 زیر Wine قابل اجرا نیست؛ باید VPS ویندوزی بگیری
> یا از کامپیوتر دیگری استفاده کنی.

**اگر خطا:** `sudo apt --fix-broken install` بعد دوباره `sudo apt update`.

---

## گام ۲ — پایتون: فقط چک، نه نصب اضافه

```bash
python3 -c 'import sys; print(sys.version_info[:2])'
```

| خروجی | معنی | کار لازم |
|---|---|---|
| `(3, 12)` | اوبونتو ۲۴.۰۴ | هیچ؛ برو گام ۳ |
| `(3, 10)` | اوبونتو ۲۲.۰۴ | پروژه از ۳.۱۰ هم پشتیبانی می‌کند (`tomli` خودکار نصب می‌شود) — هیچ کاری لازم نیست |
| `(3, 8)` یا کمتر | نسخه‌ی قدیمی | `sudo add-apt-repository ppa:deadsnakes/ppa -y && sudo apt update && sudo apt install -y python3.11 python3.11-venv` و بعد در همه‌ی دستورها `python3` را با `python3.11` عوض کن |

---

## گام ۳ — گیت: نصب، هویت، احراز هویت

نصب (در گام ۱ انجام شد، فقط چک):

```bash
git --version
```

هویت کامیت‌ها (نام و ایمیل خودت؛ ایمیل همان ایمیل حساب گیت‌هاب باشد):

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
git config --global init.defaultBranch main
git config --list | grep user
```

### احراز هویت — گزینه‌ی الف: کلید SSH (توصیه‌شده)

```bash
ssh-keygen -t ed25519 -C "tradeagent-ubuntu" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

کل خروجی (یک خط با `ssh-ed25519` شروع می‌شود) را کپی کن و در گیت‌هاب اضافه کن:
**GitHub → Settings → SSH and GPG keys → New SSH key**.

```bash
ssh -T git@github.com
```

**باید ببینی:** `Hi <username>! You've successfully authenticated...` (پیام
`does not provide shell access` طبیعی است).

### احراز هویت — گزینه‌ی ب: `gh` (ساده‌تر برای HTTPS و ساخت ریپو)

```bash
sudo apt install -y gh
gh auth login          # GitHub.com → HTTPS → Login with a web browser
gh auth status
```

**باید ببینی:** `✓ Logged in to github.com as <username>`.

> اگر این گام یا هر دانلود دیگری در شبکه‌ات شکست خورد، اول
> [NETWORK-FA.md](NETWORK-FA.md) را ببین (پاک‌سازی مخزن خراب، پروکسی، SSH روی ۴۴۳،
> بسته‌ی آفلاین). کل پروژه بدون GitHub Desktop هم کامل کار می‌کند.

### گام ۳ب (اختیاری) — GitHub Desktop برای لینوکس

GitHub برای لینوکس نسخه‌ی رسمی ندارد؛ نسخه‌ی شناخته‌شده فورک `shiftkey` است
(همان `.deb`/`.rpm`/AppImage). اگر روی آن اوبونتوی دیگر داری، همین است.

```bash
sudo apt install -y wget gpg
wget -qO- https://apt.packages.shiftkey.dev/gpg.key | gpg --dearmor | sudo tee /usr/share/keyrings/shiftkey-packages.gpg >/dev/null
sudo sh -c 'echo "deb [arch=amd64 signed-by=/usr/share/keyrings/shiftkey-packages.gpg] https://apt.packages.shiftkey.dev/ubuntu/ any main" > /etc/apt/sources.list.d/shiftkey-packages.list'
sudo apt update
sudo apt install -y github-desktop
github-desktop --version
```

**باید ببینی:** یک شماره‌نسخه (مثل `3.4.x`). آیکون برنامه در منوی Applications می‌آید.

**اگر خطا:**
- `gpg: no valid OpenPGP data found` → اینترنت/DNS یا پروکسی؛ دوباره `wget` را بزن.
- بسته پیدا نشد (مثلاً معماری ARM) → از صفحه‌ی Releases فایل `.deb` را بگیر:
  `https://github.com/shiftkey/desktop/releases` و `sudo apt install ./GitHubDesktop-linux-amd64-*.deb`.
- راه جایگزین (Flatpak): `flatpak install flathub io.github.shiftey.Desktop`.

**کلون با GitHub Desktop (به‌جای گام ۴):**

1. `File → Clone repository → URL`
2. آدرس: `https://github.com/enigmafarsco-lang/tradeAgent`
3. `Local path`: مثلاً `/home/<user>/projects` (پوشه‌ی `tradeAgent` خودش ساخته می‌شود)
4. `Clone` → بعد از پایان، از منوی `Current branch` شاخه‌ی
   **`arena/e32ad3ff-tradeagent`** را انتخاب کن (شاخه‌ی `main` فقط یک README دارد).
5. `Repository → Open in Terminal` (یا `Ctrl+``) → از اینجا به‌بعد همان دستورهای
   ترمینالی گام ۴ را اجرا کن.

> با GitHub Desktop هم **باز هم باید `git` نصب باشد** (در گام ۱ نصب شد) و برای
> اجرای ربات هم ترمینال لازم داری. پس اگر رابط گرافیکی نمی‌خواهی، گام ۳ب را رد کن.

---

## گام ۴ — گرفتن کد و تست بدون متاتریدر

اگر با GitHub Desktop کلون کردی، فقط وارد پوشه شو (مرحله‌ی `git clone` را رد کن):

```bash
mkdir -p ~/projects            # اگر از قبل نیست
cd ~/projects/tradeAgent       # پوشه‌ای که در GitHub Desktop انتخاب کردی
ls
```

اگر ترمینالی کار می‌کنی:

```bash
mkdir -p ~/projects && cd ~/projects
git clone -b arena/e32ad3ff-tradeagent https://github.com/enigmafarsco-lang/tradeAgent.git
cd tradeAgent
ls
```

**باید ببینی:** `README.md  configs  docs  mt5  pyproject.toml  scripts  src  tests ...`

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip setuptools wheel
.venv/bin/pip install -e ".[dev]"
.venv/bin/tradeagent --version
```

**باید ببینی:** `tradeagent 0.1.0`

```bash
.venv/bin/python -m pytest -q
```

**باید ببینی:** ۶۴ تست سبز (نقطه‌ها) و بدون `failed`.

```bash
.venv/bin/tradeagent init
.venv/bin/tradeagent demo --cycles 3 --step 300
.venv/bin/tradeagent report --cat | head -40
```

**باید ببینی:** سه خط «چرخه …» با بالانس/اکوییتی + یک گزارش فارسی با بخش‌های
۱ تا ۸. **اگر اینجا کار کرد، ۸۰٪ مسیر تمام است** — بقیه فقط اتصال به متاتریدر است.

**اگر خطا:**
- `No module named tomllib` → پایتونت ۳.۱۰ است و `tomli` نصب نشده:
  `.venv/bin/pip install "tomli>=2"` (یا venv را با `python3.11` بساز).
- `command not found: tradeagent` → دوباره `.venv/bin/pip install -e ".[dev]"`.
- `Permission denied` → دستورها را داخل `~/projects` اجرا کن، نه `/opt`.

---

## گام ۵ — نصب Wine از مخزن رسمی

```bash
sudo dpkg --add-architecture i386
sudo mkdir -pm755 /etc/apt/keyrings
sudo curl -fsSL -o /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key
```

کدام مخزن؟ (خروجی این دستور را ببین)

```bash
. /etc/os-release && echo "$ID $VERSION_CODENAME"
```

**باید ببینی:** `ubuntu noble` (۲۴.۰۴) یا `ubuntu jammy` (۲۲.۰۴). سپس:

```bash
. /etc/os-release
sudo tee /etc/apt/sources.list.d/winehq.sources >/dev/null <<EOF
Types: deb
URIs: https://dl.winehq.org/wine-builds/ubuntu/
Suites: ${VERSION_CODENAME}
Components: main
Architectures: amd64 i386
Signed-By: /etc/apt/keyrings/winehq-archive.key
EOF

sudo apt update
sudo apt install -y --install-recommends winehq-stable winetricks
wine --version
```

**باید ببینی:** `wine-9.x` / `wine-10.x` / `wine-11.x` (نسخه‌ی ۱۱ هم تست‌شده است).

**اگر خطا:**
- `Unable to locate package winehq-stable` → مخزن برای نسخه‌ی تو نیست؛ جایگزین:
  ```bash
  sudo apt install -y wine64 wine32 winetricks
  wine --version      # احتمالاً 6.x/8.x — ممکن است MT5 کندتر/بی‌ثبات‌تر باشد
  ```
- خطای GPG/key → `sudo apt-key` قدیمی است؛ همان دستورهای `keyrings` بالا را
  دوباره کامل اجرا کن.

---

## گام ۶ — ساخت محیط ویندوزی (prefix) در `~/Wine`

```bash
export WINEPREFIX="$HOME/Wine"
export WINEARCH=win64

wineboot --init
```

**باید ببینی:** پنجره‌ی «Wine Mono Installer» و «Wine Gecko Installer» می‌آید →
هر دو را **Cancel** کن (MT5 لازمشان ندارد). بعد چند ثانیه صبر؛ پوشه ساخته می‌شود.

```bash
winetricks -q corefonts
winetricks -q vcrun2019
```

**باید ببینی:** چند ثانیه بدون خطای قرمز؛ فونت‌های ویندوزی (لازم برای متن فارسی)
و کتابخانه‌های VC++ نصب می‌شوند. اگر `vcrun2019` گیر کرد، `winetricks -q vcrun2019`
را دوباره بزن یا `winetricks vcrun2022` را امتحان کن.

**همه‌چیز اینجا مینشیند:** `~/Wine/drive_c/Program Files/MetaTrader 5` (خود ترمینال)،
`~/Wine/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/<HASH>/MQL5/Files`
(داده‌ی تیک). خودِ برنامه‌ی `wine` از `apt` در `/usr` است — این طبیعی است.

می‌خواهی مسیر دیگری باشد؟ قبل از این گام `export MT5_WINEPREFIX="$HOME/WineMT5"` بگذار
(و در `~/.bashrc` هم ذخیره کن)؛ همه‌ی اسکریپت‌ها همان را می‌بینند.

این دو خط را دائمی کن (تا هر ترمینال جدید همان prefix را ببیند):

```bash
cat >> ~/.bashrc <<'EOF'

# MetaTrader 5 (Wine)
export WINEPREFIX="$HOME/Wine"
export WINEARCH=win64
EOF
source ~/.bashrc
```

---

## گام ۷ — نصب MetaTrader 5

### الف) ترمینال عمومی MetaQuotes (سریع‌ترین راه برای تست)

```bash
mkdir -p ~/Downloads
curl -L --fail -o ~/Downloads/mt5setup.exe \
  https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
wine ~/Downloads/mt5setup.exe
```

پنجره‌ی نصب ویندوزی باز می‌شود → `Next → Next → Install → Finish`. (حالت
`/auto` هم کار می‌کند ولی بدون دیدن پنجره دیباگ سخت‌تر است.)

### ب) نصب‌کننده‌ی بروکر خودت (وقتی بروکر مشخص شد)

```bash
wine ~/Downloads/YourBroker5Setup.exe
```

### پیدا کردن و ساخت لانچر

```bash
find "$WINEPREFIX/drive_c" -iname 'terminal64.exe' 2>/dev/null
```

**باید ببینی:** مسیری مثل
`/home/you/Wine/drive_c/Program Files/MetaTrader 5/terminal64.exe`

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/mt5 <<'EOF'
#!/usr/bin/env bash
export WINEPREFIX="$HOME/Wine"
export WINEARCH=win64
exec wine "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" "$@"
EOF
chmod +x ~/.local/bin/mt5
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
mt5
```

**باید ببینی:** پنجره‌ی MT5 باز می‌شود و پنجره‌ی لاگین می‌آید.

---

## گام ۸ — حساب دمو

در پنجره‌ی لاگین:

1. دکمه‌ی **«Open an account»** / «حساب جدید».
2. سرور: `MetaQuotes Software Corp` → `MetaQuotes-Demo`.
3. فرم را پر کن (نام/ایمیل واقعی لازم نیست، ولی برای حساب بروکر واقعی باید واقعی
   باشد) → `Next → Done`.

**باید ببینی:** ترمینال وصل می‌شود، گوشه‌ی پایین-راست قیمت‌ها می‌آیند و
`AutoTrading` در نوار ابزار دیده می‌شود. در پنجره‌ی **Market Watch** روی
`BTCUSD` و `EURUSD` راست‌کلیک → **Show** کن تا مطمئن شوی فید می‌آید.

> اگر با بروکر خودت کار می‌کنی: `File → Login to Trade Account` و سرور/یوزر/پسورد
> دموی بروکر را بزن. نام دقیق نمادها را بعداً در گام ۱۱ می‌بینیم.

---

## گام ۹ — کپی اکسپرت و کامپایل

```bash
cd ~/projects/tradeAgent
FILES="$(find "$WINEPREFIX/drive_c" -type d -path '*MQL5/Files' | head -n1)"
echo "FILES=$FILES"
EXPERTS="$(dirname "$FILES")/Experts"
mkdir -p "$EXPERTS" "$FILES/harvester"
cp mt5/Experts/TickHarvester.mq5 "$EXPERTS/"
ls -l "$EXPERTS"
```

**باید ببینی:** `TickHarvester.mq5` داخل پوشه‌ی Experts.

**کامپایل — دو راه:**

*راه ۱ (پیشنهادی، داخل MT5):* در ترمینال `F4` (MetaEditor) → `File → Open` →
`MQL5\Experts\TickHarvester.mq5` → `F7` (Compile).

**باید ببینی:** پایین MetaEditor: `0 errors, 0 warnings` و فایل
`TickHarvester.ex5` کنار `.mq5` ساخته می‌شود.

*راه ۲ (خط فرمان):*

```bash
./scripts/compile_harvester.sh
```

**باید ببینی:** `OK: ...TickHarvester.ex5 ساخته شد`.

**اگر خطا:** لاگ `TickHarvester.log` کنار فایل را ببین؛ خطای `can't open file` یعنی
مسیر ویندوزی غلط است — با راه ۱ (F7) انجام بده.

---

## گام ۱۰ — اجرای اکسپرت روی چارت

1. در MT5 یک چارت باز کن (مثلاً `BTCUSD` تایم‌فریم `M1`).
2. پنل **Navigator** (Ctrl+N) → `Expert Advisors` → `TickHarvester` را روی چارت بکش.
3. پنجره‌ی تنظیمات: تب **Common** → تیک **Allow Algo Trading** → `OK`.
4. دکمه‌ی **AutoTrading** در نوار ابزار باید **سبز** باشد. اگر نیست:
   `Tools → Options → Expert Advisors → Allow automated trading`.
5. تب **Experts** (پایین) را باز کن.

**باید ببینی:**

```
[harvester] 1.00 شروع شد | 14 نماد | پوشه=harvester | poll=250ms | روز=20261126
[harvester] مسیر فایل‌ها: MQL5\Files\harvester\ticks\<SYMBOL>\<YYYYMMDD>.csv
```

اگر تعداد نماد `0` بود: چند نماد را در Market Watch اضافه کن یا در تنظیمات اکسپرت
`InpSymbolsCsv` را پر کن (مثلاً `BTCUSD,ETHUSD,EURUSD`).

بعد از ۲ دقیقه:

```bash
find "$FILES/harvester" -type f | head -20
du -sh "$FILES/harvester"
tail -3 "$FILES/harvester"/ticks/*/*.csv | head -12
```

**باید ببینی:** فایل‌های `ticks/BTCUSD/2026xxxx.csv` با اندازه‌ی چند صد کیلوبایت و
خطوطی مثل `1764151200123;2026.11.26 08:40:00;91234.12;91240.50;...`.

> آخر هفته فارکس بسته است؛ آن موقع فقط کریپتو تیک دارد — طبیعی است.

---

## گام ۱۱ — وصل کردن ربات به داده

```bash
cd ~/projects/tradeAgent
.venv/bin/tradeagent init
./scripts/connect_harvester.sh
```

اسکریپت خودش `MQL5/Files` را زیر `~/Wine` پیدا می‌کند، مسیر را در
`configs/local.toml` می‌نویسد و فایل را دوباره اعتبارسنجی می‌کند.

<details>
<summary>معادل دستی (اگر اسکریپت را نمی‌خواهی)</summary>

```bash
FILES="$(find "$WINEPREFIX/drive_c" -type d -path '*MQL5/Files' | head -n1)"
sed -i "s|^harvester_dir = .*|harvester_dir = \"$FILES/harvester\"|" configs/local.toml
grep -n 'harvester_dir' configs/local.toml
```
</details>

**باید ببینی:** یک خط با مسیر واقعی `.../MQL5/Files/harvester`.

```bash
.venv/bin/tradeagent doctor
```

**باید ببینی:**

```
[OK   ] منبع داده harvester: 14 نماد (BTCUSD, ETHUSD, ...) · آخرین ضربان 3s پیش
```

اگر `[خطا]` ماند، مسیر یا اکسپرت مشکل دارد — به گام ۱۰ برگرد.

سپس اولین چرخه‌ی واقعی (بدون انتشار در گیت):

```bash
.venv/bin/tradeagent cycle --no-publish
.venv/bin/tradeagent report --cat | head -60
```

**باید ببینی:** گزارش با «منبع داده: `harvester`» و جدول کیفیت داده با تأخیر چند
ثانیه. اگر «هیچ کندل تازه‌ای نیامد» دیدی، یعنی هنوز ۵–۶ دقیقه تیک جمع نشده
(برای گرم‌کردن ۲۴۰۰ کندل لازم است) یا مسیر اشتباه است.

> صبر کن تا حداقل ۳۰–۴۰ دقیقه تیک جمع شود؛ قبل از آن، سیگنال‌ها روی تاریخچه‌ی کم
> ساخته می‌شوند و آمار معنا ندارد.

---

## گام ۱۲ — مدل زبانی (اختیاری ولی توصیه‌شده)

```bash
cp .env.example .env
nano .env
```

`GEMINI_API_KEY=` را با کلید رایگان از <https://aistudio.google.com/apikey> پر کن،
ذخیره (`Ctrl+O`, `Enter`, `Ctrl+X`).

**باید ببینی:** بعد از این، `tradeagent doctor` می‌نویسد
`[OK] مدل زبانی: gemini/gemini-2.5-flash · آماده`.

جایگزین بدون کلید و بدون هزینه (Ollama):

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct
sed -i 's|^TRADEAGENT_LLM_PROVIDER=.*|TRADEAGENT_LLM_PROVIDER=ollama|' .env
```

**باید ببینی:** `ollama list` مدل را نشان می‌دهد. (حدود ۵GB دیسک و ۸GB RAM لازم است.)

---

## گام ۱۳ — انتشار گزارش‌ها در گیت

```bash
.venv/bin/tradeagent publish-test
```

**باید ببینی:** `کلون گزارش: OK — آماده است (... branch=reports)`.

```bash
.venv/bin/tradeagent publish-test --write
```

**باید ببینی:** `"action": "pushed"` و یک `hash`. سپس در گیت‌هاب شاخه‌ی
`reports` را ببین: فایل `reports/paper/README.md` آنجاست.

**اگر `committed-local` یا خطای auth دیدی:** گام ۳ (SSH یا `gh auth login`) را
دوباره انجام بده؛ سپس `publish-test --write` را تکرار کن. کامیت محلی از بین نمی‌رود.

---

## گام ۱۴ — اجرای دائمی هر ۵ دقیقه

```bash
cd ~/projects/tradeAgent
mkdir -p ~/.config/systemd/user
cp deploy/tradeagent-loop.service ~/.config/systemd/user/

sed -i "s|tradeagent loop --interval 300|tradeagent --config configs/local.toml loop --interval 300|" \
  ~/.config/systemd/user/tradeagent-loop.service
grep ExecStart ~/.config/systemd/user/tradeagent-loop.service

systemctl --user daemon-reload
systemctl --user enable --now tradeagent-loop
loginctl enable-linger "$USER"
```

**باید ببینی:**

```bash
systemctl --user status tradeagent-loop --no-pager
journalctl --user -u tradeagent-loop -n 20 --no-pager
```

وضعیت `active (running)` و بعد از ۵ دقیقه یک خط «چرخه #1 تمام شد».

پایش زنده:

```bash
journalctl --user -u tradeagent-loop -f
```

---

## گام ۱۵ — چک‌لیست فردا

```bash
cd ~/projects/tradeAgent
.venv/bin/tradeagent doctor
.venv/bin/tradeagent status | head -30
tail -5 var/logs/tradeagent.log
find "$(dirname "$(dirname "$(grep harvester_dir configs/local.toml | cut -d'"' -f2)")")" -name '*.csv' | wc -l
```

در گیت‌هاب هم `reports/paper/latest.md` را ببین: باید هر ۵ دقیقه یک کامیت تازه
داشته باشد.

---

## عیب‌یابی سریع

| نشانه | کار |
|---|---|
| MT5 باز نمی‌شود / کرش می‌کند | `winetricks -q vcrun2019 corefonts gdiplus` و دوباره `mt5` |
| پنجره‌ی MT5 سیاه/بدون متن | `winetricks -q corefonts`؛ فونت چارت را `Tahoma` بگذار |
| `wine: command not found` | `source ~/.bashrc`؛ یا Wine نصب نشده (گام ۵) |
| اکسپرت روی چارت `smiley` خاکستری است | دوباره `Allow Algo Trading` + AutoTrading سبز |
| `status.json` قدیمی / فایل تیک ساخته نمی‌شود | ترمینال قطع شده؛ `mt5` را دوباره باز کن و اکسپرت را attach کن |
| `doctor`: منبع داده harvester `[خطا]` | مسیر `harvester_dir` و وجود `symbols.json` را چک کن |
| گزارش‌ها پوش نمی‌شوند | گام ۱۳ و احراز هویت SSH/`gh` |
| `قفل کهنه` در لاگ | `rm var/loop.lock && systemctl --user restart tradeagent-loop` |
| چرخه کند است (CPU بالا) | در `configs/local.toml`: `max_bars_per_cycle = 3600` و `warmup_bars = 1200` |
| همه چیز خراب شد، می‌خواهم از صفر | `rm -rf var/ && .venv/bin/tradeagent init` (داده‌ی تیک دست نمی‌خورد) |

---

## خلاصه‌ی ترتیب کارها

```
۱ apt update + ابزار پایه
۲ چک پایتون (۳.۱۰ یا بالاتر)
۳ git + هویت + SSH/gh
۴ کلون + venv + pytest + demo        ← اینجا ثابت می‌شود سیستم سالم است
۵ WineHQ + winetricks
۶ wineboot + corefonts + vcrun2019 + .bashrc
۷ نصب MT5 + لانچر mt5
۸ حساب دمو (MetaQuotes-Demo)
۹ کپی اکسپرت + F7 (کامپایل)
۱۰ attach اکسپرت + AutoTrading + بررسی فایل تیک
۱۱ configs/local.toml + doctor + اولین cycle
۱۲ .env برای Gemini یا Ollama
۱۳ publish-test --write
۱۴ systemd --user + linger
۱۵ پایش روزانه (doctor / status / reports)
```

بعد از گام ۱۱ داده‌ی واقعی داری؛ از آن به بعد مهم‌ترین کار **جمع‌کردن ۲۴–۷۲ ساعت
داده** و بعد کالیبره‌کردن `paper.slippage_bps` / `commission_bps` با اسپرد واقعی
بروکر خودت است (نه تغییر استراتژی).
