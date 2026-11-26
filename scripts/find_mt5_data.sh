#!/usr/bin/env bash
# پیدا کردن پوشه‌ی MQL5/Files ترمینال‌های MT5 (Wine، ویندوز با WSL، یا مسیر دستی)
set -uo pipefail

PREFIX="${MT5_WINEPREFIX:-$HOME/.mt5-wine64}"
echo "جست‌وجو در: $PREFIX و مسیرهای رایج..."

search_roots=("$PREFIX" "$HOME/.wine" "$HOME" "/mnt/c/Users")
found=0
for root in "${search_roots[@]}"; do
  [[ -d "$root" ]] || continue
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    found=1
    echo "------------------------------------------------------------"
    echo "MQL5/Files : $dir"
    echo "Experts    : $(dirname "$dir")/Experts"
    echo "has harvester dir: $([[ -d "$dir/harvester" ]] && echo yes || echo no)"
    echo "tick symbols today: $(find "$dir/harvester/ticks" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
    echo "status.json age  : $([[ -f "$dir/harvester/status.json" ]] && stat -c '%y' "$dir/harvester/status.json" || echo 'ندارد')"
  done < <(find "$root" -maxdepth 9 -type d -path '*MQL5/Files' 2>/dev/null | head -n 20)
done

if [[ $found -eq 0 ]]; then
  echo "هیچ MQL5/Files پیدا نشد."
  echo "اگر MT5 را روی ویندوز دیگری داری، پوشه را share کن یا با rsync منتقل کن."
  exit 1
fi

echo
echo "این مسیر را در configs/local.toml بگذار:"
echo "  [data]"
echo "  harvester_dir = \"<MQL5/Files>/harvester\""
