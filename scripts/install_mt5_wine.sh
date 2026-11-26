#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# نصب MetaTrader 5 روی Linux با Wine + آماده‌سازی پوشه‌ی داده برای TickHarvester
#
# پشتیبانی: Ubuntu 22.04/24.04 و Debian 12 (x86_64). روی macOS از CrossOver یا
# PlayOnMac استفاده کن (راهنما: docs/WINE-MT5-FA.md).
#
# نمونه‌ها:
#   ./scripts/install_mt5_wine.sh --dry-run
#   ./scripts/install_mt5_wine.sh
#   ./scripts/install_mt5_wine.sh --installer ~/Downloads/YourBroker5Setup.exe
#   ./scripts/install_mt5_wine.sh --installer-url https://example.com/mt5setup.exe
#   ./scripts/install_mt5_wine.sh --wine-branch devel   # اگر stable = 11.0 بود
#
# هیچ رمز، حساب یا سفارشی در این اسکریپت نیست. فقط نصب و مسیرسازی.
# ---------------------------------------------------------------------------
set -euo pipefail

PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
INSTALLER_URL="${MT5_INSTALLER_URL:-https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe}"
INSTALLER_FILE=""
DRY_RUN=0
SKIP_WINE=0
WINE_PKG="${MT5_WINE_PKG:-winehq-stable}"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/tradeagent"
EA_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/mt5/Experts/TickHarvester.mq5"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

log()  { printf '\033[1;34m[mt5]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mt5]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[mt5]\033[0m %s\n' "$*" >&2; exit 1; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '   + %s\n' "$*"
  else
    "$@"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --installer) INSTALLER_FILE="$2"; shift 2 ;;
    --installer-url) INSTALLER_URL="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-wine-install) SKIP_WINE=1; shift ;;
    --wine-branch) WINE_PKG="winehq-$2"; shift 2 ;;
    -h|--help) usage ;;
    *) die "گزینه ناشناخته: $1" ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || die "این اسکریپت برای Linux است؛ برای macOS راهنمای docs/WINE-MT5-FA.md را ببین."
[[ "$(uname -m)" == "x86_64" ]] || warn "معماری $(uname -m) ممکن است پشتیبانی نشود؛ MT5 نسخه‌ی ۶۴ بیتی x86 لازم دارد."
command -v curl >/dev/null || die "curl لازم است: sudo apt install -y curl"

SUDO=""
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "sudo لازم است یا اسکریپت را با root اجرا کن."
  SUDO="sudo"
fi

# ---------------------------------------------------------------------------
log "۱/۶ بررسی پیش‌نیازها"
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  log "توزیع: ${PRETTY_NAME:-unknown}"
else
  warn "/etc/os-release پیدا نشد؛ ادامه می‌دهیم."
fi

if [[ $SKIP_WINE -eq 0 ]]; then
  log "۲/۶ نصب Wine ($WINE_PKG) و winetricks"
  run $SUDO dpkg --add-architecture i386
  run $SUDO mkdir -p /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/winehq-archive.key ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "   + curl -fsSL https://dl.winehq.org/wine-builds/winehq.key | sudo tee /etc/apt/keyrings/winehq-archive.key"
    else
      curl -fsSL https://dl.winehq.org/wine-builds/winehq.key | $SUDO tee /etc/apt/keyrings/winehq-archive.key >/dev/null
    fi
  fi
  DISTRO_ID="${ID:-ubuntu}"
  DISTRO_CODENAME="${VERSION_CODENAME:-jammy}"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "   + نوشتن /etc/apt/sources.list.d/winehq.sources برای ${DISTRO_ID} ${DISTRO_CODENAME}"
  else
    $SUDO tee /etc/apt/sources.list.d/winehq.sources >/dev/null <<EOF
Types: deb
URIs: https://dl.winehq.org/wine-builds/${DISTRO_ID}
Suites: ${DISTRO_CODENAME}
Components: main
Architectures: amd64 i386
Signed-By: /etc/apt/keyrings/winehq-archive.key
EOF
  fi
  run $SUDO apt-get update
  run $SUDO apt-get install -y --install-recommends "$WINE_PKG" winetricks cabextract
else
  log "۲/۶ نصب Wine رد شد (--skip-wine-install)"
fi

for bin in wine wineboot winetricks; do
  command -v "$bin" >/dev/null || warn "$bin در PATH نیست؛ اگر نصب Wine رد شده، دستی نصب کن."
done

# Wine 11.0 در برابر محافظ ضد-دیباگ MT5 باگ دارد: نصب/اجرا با
# «A debugger has been found running in your system» یا NtRaiseHardError شکست می‌خورد.
# ۱۰.x و ۱۱.x بالاتر از ۱۱.۰ سالم‌اند.
if command -v wine >/dev/null; then
  WINE_VER="$(wine --version 2>/dev/null | sed 's/^wine-//' || true)"
  log "نسخه‌ی Wine: ${WINE_VER:-نامشخص}"
  case "$WINE_VER" in
    11.0|11.0-*)
      warn "Wine 11.0 برای MT5 خراب است (خطای دیباگر/کرش نصب‌کننده)."
      if [[ "${MT5_ALLOW_WINE_11_0:-0}" != "1" ]]; then
        die "یکی از این دو کار را بکن و دوباره اجرا کن:\n  sudo apt-get install -y --install-recommends winehq-devel\n  ./scripts/install_mt5_wine.sh --wine-branch devel\n(یا: MT5_ALLOW_WINE_11_0=1 برای ادامه‌ی اجباری)"
      fi
      ;;
  esac
fi

# ---------------------------------------------------------------------------
log "۳/۶ ساخت prefix تمیز Wine در $PREFIX"
export WINEPREFIX="$PREFIX"
export WINEARCH=win64
run mkdir -p "$PREFIX"
if [[ -d "$PREFIX/drive_c" ]]; then
  log "prefix از قبل وجود دارد؛ wineboot فقط یکپارچه‌سازی می‌کند (چیزی پاک نمی‌شود)."
elif [[ -n "$(ls -A "$PREFIX" 2>/dev/null)" ]]; then
  die "$PREFIX خالی نیست ولی prefix واین هم نیست. یک پوشه‌ی خالی بده: --prefix ~/WineMT5"
fi
run wineboot --init
if [[ $DRY_RUN -eq 0 ]]; then
  sleep 3
  winetricks -q corefonts || warn "corefonts نصب نشد (برای فونت فارسی مهم است، ولی MT5 بدون آن هم بالا می‌آید)"
  winetricks -q vcrun2019 || warn "vcrun2019 نصب نشد؛ اگر MT5 خطای DLL داد این را دستی نصب کن"
fi

# ---------------------------------------------------------------------------
log "۴/۶ آماده‌سازی نصب‌کننده"
mkdir -p "$CACHE_DIR"
if [[ -z "$INSTALLER_FILE" ]]; then
  INSTALLER_FILE="$CACHE_DIR/mt5setup.exe"
  if [[ -f "$INSTALLER_FILE" ]]; then
    log "نصب‌کننده از قبل هست: $INSTALLER_FILE"
  else
    log "دانلود نصب‌کننده: $INSTALLER_URL"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "   + curl -L --fail -o $INSTALLER_FILE $INSTALLER_URL"
    else
      curl -L --fail --retry 3 -o "$INSTALLER_FILE" "$INSTALLER_URL" \
        || die "دانلود نصب‌کننده نشد. از سایت بروکر خودت فایل .exe را بگیر و با --installer بده."
    fi
  fi
elif [[ ! -f "$INSTALLER_FILE" ]]; then
  die "فایل نصب‌کننده پیدا نشد: $INSTALLER_FILE"
fi

# ---------------------------------------------------------------------------
log "۵/۶ نصب MT5 (حالت /auto، بی‌صدا)"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "   + wine \"$INSTALLER_FILE\" /auto"
else
  wine "$INSTALLER_FILE" /auto || warn "نصب‌کننده با کد خطا برگشت؛ اگر ترمینال نصب شده، ادامه بده."
fi

# ---------------------------------------------------------------------------
log "۶/۶ پیدا کردن ترمینال و کپی اکسپرت"
DATA_DIRS=$(find "$PREFIX/drive_c" -maxdepth 8 -type d -path '*MQL5/Files' 2>/dev/null || true)
if [[ -z "$DATA_DIRS" ]]; then
  warn "پوشه‌ی MQL5/Files پیدا نشد. یک بار MT5 را باز کن و بعد این اسکریپت را با --skip-wine-install دوباره اجرا کن."
else
  while IFS= read -r files_dir; do
    [[ -n "$files_dir" ]] || continue
    experts_dir="$(dirname "$files_dir")/Experts"
    log "MQL5/Files: $files_dir"
    if [[ -f "$EA_SOURCE" ]]; then
      run mkdir -p "$experts_dir"
      run cp -f "$EA_SOURCE" "$experts_dir/TickHarvester.mq5"
      log "اکسپرت کپی شد → $experts_dir/TickHarvester.mq5"
      run mkdir -p "$files_dir/harvester"
    else
      warn "فایل اکسپرت پیدا نشد: $EA_SOURCE"
    fi
  done <<< "$DATA_DIRS"
fi

TERMINAL=$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'terminal64.exe' 2>/dev/null | head -n 1 || true)
if [[ -n "$TERMINAL" ]]; then
  LAUNCHER="$HOME/.local/bin/mt5"
  mkdir -p "$(dirname "$LAUNCHER")"
  if [[ $DRY_RUN -eq 0 ]]; then
    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# MT5 → Wine. اگر پنجره نیامد/جای عجیبی باز شد:
#   MT5_DESKTOP=1 mt5
export WINEPREFIX="$PREFIX"
export WINEARCH=win64
TERM_EXE="$TERMINAL"
if [[ "\${MT5_DESKTOP:-0}" == "1" ]]; then
  GEOM="\${MT5_DESKTOP_GEOM:-1400x900}"
  exec wine explorer "/desktop=mt5,\${GEOM}" "\$TERM_EXE" "\$@"
fi
exec wine "\$TERM_EXE" "\$@"
EOF
    chmod +x "$LAUNCHER"
    log "لانچر ساخته شد: $LAUNCHER"
  fi
else
  warn "terminal64.exe پیدا نشد؛ مسیر نصب را دستی چک کن."
fi

cat <<EOF

────────────────────────────────────────────────────────────
نصب تمام شد. قدم‌های بعدی:

۱) اجرای ترمینال:
     mt5                           # یا: WINEPREFIX=$PREFIX wine "<مسیر terminal64.exe>"

۲) در MT5: File → Open Data Folder → پوشه‌ی MQL5\\Experts
   فایل TickHarvester.mq5 را باز کن و F7 (Compile) بزن.

۳) یک چارت باز کن، اکسپرت را از Navigator روی چارت بکش،
   «Allow Algo Trading» را تیک بزن و دکمه‌ی AutoTrading را سبز کن.

۴) در تب Experts باید این را ببینی:
     [harvester] 1.00 شروع شد | N نماد | پوشه=harvester

۵) در پیکربندی ربات، مسیر داده را بگذار:
     data.harvester_dir = "$(echo "$DATA_DIRS" | head -n1)"

۶) بررسی:
     .venv/bin/tradeagent doctor

اگر MT5 بالا نیامد: docs/WINE-MT5-FA.md بخش عیب‌یابی.
────────────────────────────────────────────────────────────
EOF
