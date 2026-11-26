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

MQL5="$(find "$PREFIX/drive_c" "$HOME/.wine/drive_c" -maxdepth 12 -type d -name MQL5 2>/dev/null | head -n1)"
[[ -n "$MQL5" ]] || { warn "MQL5 پیدا نشد."; exit 1; }
FILES="$MQL5/Files"
H="$FILES/harvester"

log "MQL5        : $MQL5"
log "Files       : $FILES  ($([[ -d "$FILES" ]] && echo هست || echo نیست))"
log "harvester   : $H  ($([[ -d "$H" ]] && echo هست || echo نیست))"

if [[ ! -d "$H" ]]; then
  warn "پوشه‌ی harvester ساخته نشده = اکسپرت هرگز اجرا نشده."
else
  ls -la "$H" | sed 's/^/    /'
  for f in status.json symbols.json; do
    [[ -f "$H/$f" ]] && { echo "--- $f ---"; head -c 600 "$H/$f"; echo; }
  done
  [[ -d "$H/ticks" ]] && { echo "--- ticks ---"; find "$H/ticks" -maxdepth 2 -name '*.csv' -printf '%TY-%Tm-%Td %TH:%TM  %8s  %p\n' 2>/dev/null | sort | tail -10; }
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
  warn "پوشه‌ی Logs نیست → یعنی این MQL5 پوشه‌ی داده‌ی ترمینال نیست."
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
