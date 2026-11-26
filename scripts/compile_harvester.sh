#!/usr/bin/env bash
# کامپایل اکسپرت با MetaEditor از خط فرمان (زیر Wine) و نمایش نتیجه
set -uo pipefail

PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
export WINEPREFIX="$PREFIX"
export WINEARCH=win64

EDITOR=$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'metaeditor64.exe' 2>/dev/null | head -n 1 || true)
[[ -n "$EDITOR" ]] || { echo "MetaEditor64.exe پیدا نشد. مسیر را با MT5_WINEPREFIX بده."; exit 1; }

# مسیر ویندوزی فایل داخل prefix
targets=$(find "$PREFIX/drive_c" -maxdepth 9 -type f -name 'TickHarvester.mq5' 2>/dev/null | head -n 3)
[[ -n "$targets" ]] || { echo "TickHarvester.mq5 داخل prefix پیدا نشد؛ اول scripts/install_mt5_wine.sh را اجرا کن."; exit 1; }

rc=0
while IFS= read -r mq5; do
  win_path="Z:${mq5//\//\\}"
  echo "کامپایل: $mq5"
  wine "$EDITOR" "/compile:${win_path}" /log || rc=1
  log_file="${mq5%.mq5}.log"
  if [[ -f "$log_file" ]]; then
    echo "--- گزارش کامپایلر ---"
    tail -n 15 "$log_file"
  fi
  if [[ -f "${mq5%.mq5}.ex5" ]]; then
    echo "OK: ${mq5%.mq5}.ex5 ساخته شد"
  else
    echo "FAIL: فایل ex5 ساخته نشد (لاگ بالا را ببین)"
    rc=1
  fi
done <<< "$targets"
exit $rc
