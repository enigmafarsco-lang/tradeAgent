#!/usr/bin/env bash
# رفع خطای «A debugger has been found running in your system» در MT5 زیر Wine
#
# علت: متاتریدر دنبال یک دیباگر می‌گردد؛ Wine خودش را در
# HKLM\...\AeDebug به‌عنوان winedbg ثبت می‌کند و MT5 آن را دیباگر می‌بیند.
# (با Wine 11.0 شایع است؛ با 11.6+ یا 10.0 معمولاً رخ نمی‌دهد.)
#
#   ./scripts/fix_mt5_debugger.sh            # اصلاح + تست
#   ./scripts/fix_mt5_debugger.sh --dry-run
set -uo pipefail

PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

log()  { printf '\033[1;34m[fix]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[fix]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[fix]\033[0m %s\n' "$*"; }

[[ -d "$PREFIX/drive_c" ]] || { echo "prefix پیدا نشد: $PREFIX" >&2; exit 1; }
export WINEPREFIX="$PREFIX"
export WINEARCH=win64

run() {
  if [[ $DRY -eq 1 ]]; then printf '   + %s\n' "$*"; else "$@"; fi
}

log "prefix: $WINEPREFIX"
[[ -n "${WINEDEBUG:-}" ]] && warn "WINEDEBUG تنظیم شده بود (${WINEDEBUG}) — حذف می‌شود" 
unset WINEDEBUG

log "۱) بستن wineserver (تا متغیر محیطی پاک به همه برسد)"
run wineserver -k 2>/dev/null || true
sleep 1

log "۲) حذف مقدار Debugger از AeDebug (هر دو نمای رجیستری)"
for key in \
  'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' \
  'HKLM\Software\Wow6432Node\Microsoft\Windows NT\CurrentVersion\AeDebug'
do
  log "   $key"
  run wine reg delete "$key" /v Debugger /f 2>/dev/null || true
  run wine reg delete "$key" /v Auto /f 2>/dev/null || true
done

log "۳) خاموش‌کردن دیالوگ کرش واین"
run wine reg add 'HKCU\Software\Wine\WineDbg' /v ShowCrashDialog /t REG_DWORD /d 0 /f 2>/dev/null || true

log "۴) بررسی نتیجه"
if [[ $DRY -eq 0 ]]; then
  echo "--- AeDebug (باید خالی باشد) ---"
  wine reg query 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' 2>/dev/null || echo "(کلید نیست)"
  echo "--- نسخه واین ---"
  wine --version
fi

cat <<'EOF'

────────────────────────────────────────────────────────────
حالا نصب/اجرای متاتریدر را با محیط تمیز امتحان کن:

  unset WINEDEBUG
  wine "/path/to/mt5setup.exe"

اگر باز هم همان پیام آمد، مشکل نسخهٔ Wine است (۱۱.۰). دو راه:

  الف) نسخه‌های موجود را ببین:
       apt policy winehq-stable | head -12

  ب) اگر 10.0.0.0~noble-1 در فهرست بود، به آن برگرد:
       sudo apt install winehq-stable=10.0.0.0~noble-1 \
            wine-stable=10.0.0.0~noble-1 \
            wine-stable-amd64=10.0.0.0~noble-1 \
            wine-stable-i386=10.0.0.0~noble-1
       sudo apt-mark hold winehq-stable wine-stable wine-stable-amd64 wine-stable-i386

  ج) یا نسخهٔ جدیدتر را امتحان کن:
       sudo apt install winehq-devel        # یا winehq-staging
       wine --version

هیچ‌کدام جواب نداد؟ MT5 تنها قطعهٔ ویندوزی این پروژه است؛ یک VM ویندوزی
(VirtualBox/KVM) یا VPS ویندوزی هم مسیر معتبری است — داده‌ها با mount/rsync
به لینوکس می‌آید و بقیهٔ سیستم بدون تغییر کار می‌کند.
────────────────────────────────────────────────────────────
EOF
