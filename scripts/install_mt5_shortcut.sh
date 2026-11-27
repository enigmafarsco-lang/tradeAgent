#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# آیکون متاتریدر روی دسکتاپ (و در منوی برنامه‌ها).
#
#   ./scripts/install_mt5_shortcut.sh            # آیکون معمولی
#   ./scripts/install_mt5_shortcut.sh --watch     # آیکونی که خودش اگر بسته شد بازش می‌کند
# ---------------------------------------------------------------------------
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ok()  { printf '\033[1;32m[icon]\033[0m %s\n' "$*"; }
dim() { printf '\033[2m[icon] %s\033[0m\n' "$*"; }

MODE="run"
[[ "${1:-}" == "--watch" ]] && MODE="watch"

PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
MT5DIR="$PREFIX/drive_c/Program Files/MetaTrader 5"

# ۲) سیستم: آیکون خودِ متاتریدر (از .ico داخل پوشهٔ برنامه) → png
PREVIEW="${MT5_ICON:-}"
ICODIR="$HOME/.local/share/icons/hicolor/128x128/apps"
if [[ -z "$PREVIEW" ]] && command -v convert >/dev/null 2>&1; then
  ico="$(ls -1 "$MT5DIR"/*.ico 2>/dev/null | head -1 || true)"
  [[ -z "$ico" ]] && ico="$(ls -1 "$MT5DIR"/*/*.ico 2>/dev/null | head -1 || true)"
  if [[ -n "$ico" ]]; then
    mkdir -p "$ICODIR"
    if convert "$ico" -resize 128x128 -strip "$ICODIR/tradeagent-mt5.png" 2>/dev/null; then
      PREVIEW="$ICODIR/tradeagent-mt5.png"
    fi
  fi
fi
for cand in \
  "$ROOT/assets/mt5.png" \
  "$HOME/.local/share/icons/hicolor/48x48/apps/metatrader5.png" \
  "$HOME/.local/share/icons/hicolor/256x256/apps/metatrader5.png" \
  "/usr/share/icons/hicolor/48x48/apps/metatrader5.png"; do
  [[ -z "$PREVIEW" && -f "$cand" ]] && PREVIEW="$cand"
done
[[ -n "$PREVIEW" && ! -f "$PREVIEW" ]] && PREVIEW=""
[[ -z "$PREVIEW" ]] && PREVIEW="applications-internet"

mkdir -p "$HOME/.local/share/applications"
if [[ "$MODE" == watch ]]; then
  NAME="MetaTrader (نگهبان)"
  BASE="tradeagent-mt5-watch"
  EXEC="$ROOT/scripts/start_mt5.sh --watch"
  TERM=true
else
  NAME="MetaTrader"
  BASE="tradeagent-mt5"
  EXEC="$ROOT/scripts/start_mt5.sh"
  TERM=false
fi
FILE="$HOME/.local/share/applications/${BASE:-tradeagent-mt5}.desktop"

cat > "$FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$NAME
Comment=MetaTrader 5 زیر Wine — با setsid اجرا می‌شود و ترمینال آن را نمی‌کشد
Exec=$EXEC
Icon=$PREVIEW
Terminal=$TERM
Categories=Trading;Finance;
StartupNotify=false
EOF
chmod +x "$FILE"
ok "ساخته شد: $FILE"

placed=()
for dir in "$HOME/Desktop" "$HOME/دسکتاپ"; do
  [[ -d "$dir" ]] || continue
  cp "$FILE" "$dir/$(basename "$FILE")"
  chmod +x "$dir/$(basename "$FILE")"
  gio set "$dir/$(basename "$FILE")" metadata::trusted true 2>/dev/null || true
  placed+=("$dir/$(basename "$FILE")")
done
if [[ ${#placed[@]} -eq 0 ]]; then
  mkdir -p "$HOME/Desktop"
  cp "$FILE" "$HOME/Desktop/$(basename "$FILE")"
  chmod +x "$HOME/Desktop/$(basename "$FILE")"
  gio set "$HOME/Desktop/$(basename "$FILE")" metadata::trusted true 2>/dev/null || true
  placed+=("$HOME/Desktop/$(basename "$FILE")")
fi
for f in "${placed[@]}"; do ok "روی دسکتاپ: $f"; done

dim "اگر آیکون «untrusted» ماند: راست‌کلیک → Allow Launching"
dim "حالت نگهبان: ./scripts/install_mt5_shortcut.sh --watch  (هر ۱۰s؛ بسته شد، بازش می‌کند)"
