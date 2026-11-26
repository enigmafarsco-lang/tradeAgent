#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# تشخیص وضعیت ضبط تیک: پوشه‌ی داده، status.json، فایل‌های CSV و لاگ اکسپرت
#   ./scripts/check_harvester.sh
# ---------------------------------------------------------------------------
set -uo pipefail
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"

log()  { printf '\033[1;34m[check]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[check]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[check]\033[0m %s\n' "$*"; }

# پوشه‌ی داده = پوشه‌ای که config/ یا bases/ یا MQL5/ دارد
DATA_ROOT=""
BEST=-1
while IFS= read -r d; do
  [[ -d "$d" ]] || continue
  sc=0
  [[ -d "$d/bases" ]] && sc=$((sc + 3))
  [[ -d "$d/MQL5" ]] && sc=$((sc + 2))
  [[ -d "$d/config" ]] && sc=$((sc + 1))
  [[ -f "$d/MQL5/Files/harvester/status.json" ]] && sc=$((sc + 5))
  if [[ "$sc" -gt "$BEST" ]]; then BEST="$sc"; DATA_ROOT="$d"; fi
done < <({ find "$PREFIX/drive_c" -maxdepth 12 -type d -path '*MetaQuotes/Terminal/*' 2>/dev/null
           find "$HOME/.wine/drive_c" -maxdepth 12 -type d -path '*MetaQuotes/Terminal/*' 2>/dev/null
           find "$PREFIX/drive_c" -maxdepth 8 -type d -name 'MetaTrader*' 2>/dev/null
         } | sort -u)

[[ -n "$DATA_ROOT" ]] || { warn "پوشه‌ی داده پیدا نشد."; exit 1; }
MQL5="$DATA_ROOT/MQL5"
FILES="$MQL5/Files"
H="$FILES/harvester"

log "داده‌ی MT5  : $DATA_ROOT"
log "  محتوا     : $(ls -A "$DATA_ROOT" 2>/dev/null | tr '\n' ' ')"
log "MQL5        : $MQL5  ($([[ -d "$MQL5" ]] && echo هست || echo 'نیست — ترمینال کامل init نشده'))"
log "harvester   : $H  ($([[ -d "$H" ]] && echo هست || echo نیست))"

if [[ -d "$DATA_ROOT/logs" ]]; then
  echo "--- ژورنال ترمینال (logs/) ---"
  J="$(ls -t "$DATA_ROOT/logs" 2>/dev/null | head -n1)"
  log "آخرین: $J"
  grep -ai "error\|fail\|cannot\|denied" "$DATA_ROOT/logs/$J" 2>/dev/null | tail -n 10 | sed 's/^/    /'
  echo "    ۱۵ خط آخر:"
  tail -n 15 "$DATA_ROOT/logs/$J" 2>/dev/null | sed 's/^/    /'
else
  warn "پوشه‌ی logs/ نیست → ترمینال هرگز به‌طور کامل بالا نیامده."
fi

echo "--- لاگ‌های متاتریدر (MQL5/Logs) ---"
LOGS="$MQL5/Logs"
if [[ -d "$LOGS" ]]; then
  NEWEST="$(ls -t "$LOGS" 2>/dev/null | head -n1)"
  log "آخرین فایل لاگ: $NEWEST"
  grep -ai "harvester\|expert\|error\|cannot" "$LOGS/$NEWEST" 2>/dev/null | tail -n 20 | sed 's/^/    /'
  echo "--- ۱۵ خط آخر لاگ ---"
  tail -n 15 "$LOGS/$NEWEST" 2>/dev/null | sed 's/^/    /'
else
  warn "MQL5/Logs نیست — طبیعی است تا اولین اجرای اکسپرت/متاادیتور. (ژورنال ترمینال در logs/ بالاست)"
  echo "--- فرآیند ترمینال در حال اجرا ---"
  PID="$(pgrep -f 'terminal64\.exe' | head -n1)"
  if [[ -n "$PID" ]]; then
    log "pid=$PID"
    tr '\0' '\n' < "/proc/$PID/environ" 2>/dev/null | grep -E '^(WINEPREFIX|WINEARCH|DISPLAY|WAYLAND_DISPLAY|HOME)=' | sed 's/^/    /'
    log "فایل اجرایی: $(readlink -f "/proc/$PID/exe" 2>/dev/null)"
  else
    warn "ترمینال در حال اجرا نیست."
  fi
  echo "--- پوشه‌های MetaQuotes موجود ---"
  find "$PREFIX" "$HOME/.wine" -maxdepth 14 -type d -path '*MetaQuotes*' 2>/dev/null | sed 's/^/    /'
  echo "--- هر status.json در سیستم ---"
  find "$PREFIX" "$HOME/.wine" -maxdepth 16 -name status.json -path '*harvester*' 2>/dev/null | sed 's/^/    /'
  echo "--- هر Logs زیر MQL5 ---"
  find "$PREFIX" "$HOME/.wine" -maxdepth 16 -type d -path '*MQL5/Logs' 2>/dev/null | sed 's/^/    /'
fi
