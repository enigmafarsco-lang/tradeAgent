#!/usr/bin/env bash
# پیدا کردن پوشه‌ی MQL5/Files ترمینال‌های MT5 (Wine، ویندوز با WSL، یا مسیر دستی)
set -uo pipefail

PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
echo "active prefix: $PREFIX"
echo "searching: $PREFIX, ~/.wine, ~/Wine*, /mnt/c/Users ..."
# همه‌ی prefixهای ممکن را هم ببین (اگر قبلاً جای دیگری نصب شده)
for extra in "$HOME"/Wine*/drive_c "$HOME"/.wine/drive_c; do
  [[ -d "$extra" ]] || continue
  echo "  prefix found: ${extra%/drive_c}"
done

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
    echo "status.json age  : $([[ -f "$dir/harvester/status.json" ]] && stat -c '%y' "$dir/harvester/status.json" || echo 'none')"
  done < <(find "$root" -maxdepth 9 -type d -path '*MQL5/Files' 2>/dev/null | head -n 20)
done

if [[ $found -eq 0 ]]; then
  echo "no MQL5/Files found."
  echo "if MT5 runs on another Windows machine, share or rsync the folder here."
  exit 1
fi

echo
echo "put this path in configs/local.toml:"
echo "  [data]"
echo "  harvester_dir = \"<MQL5/Files>/harvester\""
