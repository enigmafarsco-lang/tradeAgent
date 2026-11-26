# شبکه، فیلترینگ، و وقتی یک مخزن/سایت باز نمی‌شود

این سند برای موقعیتی است که نصب یک ابزار (مثل GitHub Desktop) یا `git clone`
شکست می‌خورد. هدف: **بدون ترمز کردن کل پروژه**، به نزدیک‌ترین مسیر کارکردن برسیم.

## ۰) اول تشخیص بده مشکل کجاست

| خطا | معنی | کار |
|---|---|---|
| `gpg: no valid OpenPGP data found` | به‌جای کلید GPG، HTML/صفحهٔ خطا گرفتی | دامنه فیلتر/دستکاری شده؛ از آن مخزن استفاده نکن |
| `Certificate verification failed: name ... does not match` | گواهی TLS با دامنه نمی‌خواند (intercept) | همان دامنه بلاک است؛ فیلترشکن یا مسیر جایگزین |
| `Could not resolve host` | DNS | DNS عوض کن یا پروکسی |
| `Failed to connect ... port 443: Connection timed out` | IP بسته است | پروکسی/تونل یا انتقال آفلاین |
| `gnutls_handshake() failed` | بلاک TLS | همان |

تست سریع:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://github.com
curl -sSI https://github.com | head -1
git ls-remote https://github.com/enigmafarsco-lang/tradeAgent.git | head -3
```

اگر `curl` هم شکست، مشکل شبکه است، نه پروژه.

## ۱) پاک‌سازی مخزن خرابِ نیمه‌کاره

```bash
sudo rm -f /etc/apt/sources.list.d/shiftkey-packages.list
sudo rm -f /usr/share/keyrings/shiftkey-packages.gpg
sudo apt update          # باید بدون خطا/Warning تمام شود
```

## ۲) `git` واقعی (چیزی که لازم است)

`git` در مخزن رسمی اوبونتوست و به هیچ سرویس شخص ثالثی نیاز ندارد:

```bash
sudo apt install -y git
git --version
```

GitHub Desktop **لازم نیست**. گزارش‌ها هم بدون آن پوش می‌شوند (`git` + توکن/SSH).

## ۲ب) GitHub Desktop — وقتی مخزن apt کار نمی‌کند

اگر `apt.packages.shiftkey.dev` بلاک/بدگواهی است، سه راه مستقل از آن مخزن:

### راه ۱ — فید جایگزین (@mwt)

```bash
wget -qO- https://mirror.mwt.me/shiftkey-desktop/gpgkey | gpg --dearmor | sudo tee /usr/share/keyrings/mwt-desktop.gpg >/dev/null
sudo sh -c 'echo "deb [arch=amd64 signed-by=/usr/share/keyrings/mwt-desktop.gpg] https://mirror.mwt.me/shiftkey-desktop/deb/ any main" > /etc/apt/sources.list.d/mwt-desktop.list'
sudo apt update && sudo apt install -y github-desktop
```

### راه ۲ — فایل `.deb` مستقیم از Releases (بدون apt repo، بدون کلید)

نسخه‌ی پایدار فعلی فورک لینوکس `3.4.13-linux1` است:

```bash
cd ~/Downloads
curl -L --fail -O https://github.com/shiftkey/desktop/releases/download/release-3.4.13-linux1/GitHubDesktop-linux-amd64-3.4.13-linux1.deb
sudo apt install -y ./GitHubDesktop-linux-amd64-3.4.13-linux1.deb
github-desktop --version
```

روی اوبونتو ۲۴.۰۴ اگر `apt install ./...deb` به‌خاطر نام بسته‌های صوتی
(`libasound2` در برابر `libasound2t64`) خطا داد، از AppImage استفاده کن (راه ۴).

### راه ۳ — کپی `.deb` از اوبونتوی دیگری که نصبش دارد

```bash
# روی آن ماشین:
cd ~/Downloads && apt-get download github-desktop
ls -lh github-desktop_*.deb
# یا اگر در کش هست:
ls /var/cache/apt/archives/github-desktop_*.deb

# انتقال (USB یا scp) و نصب روی این ماشین:
sudo apt install -y ./github-desktop_*.deb
```

### راه ۴ — AppImage (بدون نصب، بدون وابستگی بسته)

```bash
sudo apt install -y libfuse2
cd ~/Downloads
curl -L --fail -O https://github.com/shiftkey/desktop/releases/download/release-3.4.13-linux1/GitHubDesktop-linux-x86_64-3.4.13-linux1.AppImage
chmod +x GitHubDesktop-linux-x86_64-3.4.13-linux1.AppImage
./GitHubDesktop-linux-x86_64-3.4.13-linux1.AppImage
```

### راه ۵ — Flatpak

```bash
sudo apt install -y flatpak
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub io.github.shiftey.Desktop
flatpak run io.github.shiftey.Desktop
```

> پیش‌نیاز مشترک همه‌ی راه‌ها: `git` سیستمی (`sudo apt install -y git`). GitHub Desktop
> روی لینوکس از همان `git` استفاده می‌کند و نسخه‌ی داخلی ندارد.

## ۳) رابط گرافیکی، اگر واقعاً می‌خواهی

به‌ترتیب کم‌دردسرترین گزینه‌ها:

```bash
# گزینه A — git-cola (سبک، در مخزن اوبونتو)
sudo apt install -y git-cola && git-cola

# گزینه B — gitg (GNOME)
sudo apt install -y gitg && gitg

# گزینه C — VS Code (اگر نصب داری: Source Control → Clone Repository)
code .
```

GitHub Desktop فقط وقتی معنا دارد که `.deb` آن را از شبکه‌ای با دسترسی آزاد
(یا فیلترشکن) بگیری و دستی نصب کنی:

```bash
sudo apt install ./GitHubDesktop-linux-amd64-*.deb
```
(فایل `.deb` از صفحهٔ Releases فورک shiftkey؛ هیچ مخزن/کلیدی لازم نیست.)

## ۴) اگر GitHub بلاک است: پروکسی برای git و pip

اگر روی سیستم پروکسی محلی داری (v2ray/xray/clash روی مثلاً پورت 10809):

```bash
# موقت برای همین ترمینال
export https_proxy=http://127.0.0.1:10809 http_proxy=http://127.0.0.1:10809 all_proxy=socks5://127.0.0.1:10808

# دائمی برای git
git config --global http.proxy  http://127.0.0.1:10809
git config --global https.proxy http://127.0.0.1:10809

# برای pip
mkdir -p ~/.pip && printf '[global]\nproxy = http://127.0.0.1:10809\n' > ~/.pip/pip.conf
```

نکته: در برخی ابزارها `socks5h://` لازم است؛ اگر `http://` جواب نداد امتحان کن.

## ۵) SSH روی پورت ۴۴۳ (بدون پروکسی، برای عبور از بلاک پورت ۲۲)

```bash
mkdir -p ~/.ssh && cat >> ~/.ssh/config <<'EOF'
Host github.com
  HostName ssh.github.com
  Port 443
  User git
EOF
ssh -T git@github.com        # Hi <username>! ...
```

سپس کلون با SSH: `git clone -b arena/e32ad3ff-tradeagent git@github.com:enigmafarsco-lang/tradeAgent.git`

## ۶) مسیر آفلاین: از ماشینی که دسترسی دارد

روی ماشین/اوبونتوی دیگر که گیت‌هاب برایش باز است:

```bash
git clone -b arena/e32ad3ff-tradeagent https://github.com/enigmafarsco-lang/tradeAgent.git
cd tradeAgent && ./scripts/make_offline_bundle.sh
# خروجی: dist/tradeagent-offline-<تاریخ>.tar.gz
```

بعد فایل را با USB / `rsync` / `scp` به این کامپیوتر بیاور:

```bash
tar -xzf tradeagent-offline-*.tar.gz -C ~/projects
cd ~/projects/tradeAgent
```

## ۷) اگر حتی PyPI هم باز نیست

`pip install -e .` روی پایتون ۳.۱۲ فقط به `packaging`/`setuptools` نیاز دارد و
وابستگی اجرایی پروژه **صفر** است (`tomli` فقط برای پایتون ۳.۱۰). اگر نصب پکیج
گیر کرد:

```bash
sudo apt install -y python3-venv python3-pip
python3 -m venv .venv --system-site-packages
.venv/bin/pip install --no-index -e .        # بدون اینترنت، اگر setuptools هست
.venv/bin/pip install --no-index .           # یا نصب معمولی بدون -e
```

یا حتی بدون نصب، مستقیم اجرا کن:

```bash
PYTHONPATH=src python3 -c "from tradeagent.cli import main; raise SystemExit(main(['--root','.','demo']))"
```

## ۸) گزارش‌ها وقتی پوش ممکن نیست

پوش گیت **اختیاری** است؛ تمام سیستم بدون آن کار می‌کند:

```toml
[publish]
enabled = false
```

گزارش‌ها در `var/reports/latest.md` نوشته می‌شوند و بعداً از هر ماشینی می‌توانی
`var/reports` را کپی/کامیت کنی. هیچ داده‌ای از دست نمی‌رود و چرخه‌ی ۵ دقیقه‌ای
متوقف نمی‌شود.

## ۹) چک‌لیست «همه‌چیز باز است»

```bash
sudo apt update                                     # بدون خطا
git ls-remote https://github.com/... | head -1      # پاسخ دارد
curl -sS -o /dev/null -w '%{http_code}\n' https://pypi.org/simple/   # 200
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/tradeagent demo --cycles 2
```
