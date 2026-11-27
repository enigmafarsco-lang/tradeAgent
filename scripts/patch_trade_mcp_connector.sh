#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# اکسپرت کانکتور trade-mcp با MQL5 کامپایل نمی‌شود:
#   undeclared identifier 'LongToString'   (خطوط ۸۷۰ و ۸۹۷)
# علت: نویسندهٔ trade-mcp تابع LongToString را صدا زده ولی هیچ‌جا تعریف نکرده؛
# MQL5 چنین تابعی ندارد. این اسکریپت تابع را اضافه می‌کند (کم‌ترین تغییر ممکن،
# بدون دست‌زدن به منطق) و بعد کامپایل می‌کند.
#
#   ./scripts/patch_trade_mcp_connector.sh           # اصلاح + کامپایل
#   ./scripts/patch_trade_mcp_connector.sh --dry-run # فقط بگو چه می‌کند
#   ./scripts/patch_trade_mcp_connector.sh --no-compile
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
REPO="${TRADE_MCP_DIR:-$HOME/trade-mcp}"
DRY=0
COMPILE=1
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-compile) COMPILE=0 ;;
    -h|--help) sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$a"; exit 2 ;;
  esac
done

ok()   { printf '\033[1;32m[patch]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[patch]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[patch]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[patch] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

HELPER='//--- tradeAgent patch: MQL5 has no LongToString() -------------------
string LongToString(const long value) { return IntegerToString(value); }
//--------------------------------------------------------------------------'

fix_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  if grep -q "string LongToString(" "$file"; then
    dim "قبلاً اصلاح شده: $file"; return 0
  fi
  if ! grep -q "LongToString(" "$file"; then
    dim "نیازی به اصلاح نیست (LongToString استفاده نشده): $file"; return 0
  fi
  if (( DRY )); then
    warn "dry-run: تابع LongToString به $file اضافه می‌شد ($(grep -c 'LongToString(' "$file") استفاده)"
    return 0
  fi
  cp -f "$file" "$file.tradeagent-backup-$(date +%Y%m%d-%H%M%S)"
  python3 - "$file" <<'PY'
import sys, pathlib, re
path = pathlib.Path(sys.argv[1])
lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
helper = [
    "//--- tradeAgent patch: MQL5 has no LongToString() -------------------",
    "string LongToString(const long value) { return IntegerToString(value); }",
    "//--------------------------------------------------------------------------",
]
# درست بعد از آخرین #include (و اگر نبود بعد از آخرین #property) درج می‌کنیم؛
# گذاشتن تابع بین input ها کامپایل را شکننده می‌کند.
insert_at = 0
for i, line in enumerate(lines[:200]):
    stripped = line.strip()
    if stripped.startswith("#include"):
        insert_at = i + 1
if insert_at == 0:
    for i, line in enumerate(lines[:200]):
        if line.strip().startswith("#property"):
            insert_at = i + 1
out = lines[:insert_at] + ["", *helper, ""] + lines[insert_at:]
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  ok "اصلاح شد: $file (تابع LongToString اضافه شد، $(grep -c 'LongToString(' "$file") استفاده)"
}

step "۱) فایل‌های کانکتور"
TARGETS=()
[[ -f "$REPO/mt5-connector/TradeMCPConnector.mq5" ]] && TARGETS+=("$REPO/mt5-connector/TradeMCPConnector.mq5")
while IFS= read -r f; do [[ -n "$f" ]] && TARGETS+=("$f"); done < <(
  find "$PREFIX/drive_c" -maxdepth 9 -type f -name 'TradeMCPConnector.mq5' 2>/dev/null | head -n 5
)
if (( ${#TARGETS[@]} == 0 )); then
  bad "TradeMCPConnector.mq5 پیدا نشد (نه در $REPO و نه در $PREFIX)"
  dim "اول: ./scripts/connect_trade_mcp.sh"
  exit 1
fi
for f in "${TARGETS[@]}"; do printf '  %s\n' "$f"; done
for f in "${TARGETS[@]}"; do fix_file "$f"; done

step "۲) کامپایل با MetaEditor"
if (( COMPILE == 0 )); then
  dim "با --no-compile رد شد"; exit 0
fi
EDITOR="$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'metaeditor64.exe' 2>/dev/null | head -n1)"
if [[ -z "$EDITOR" ]]; then
  warn "MetaEditor64.exe زیر $PREFIX نیست؛ کامپایل را در خود متاتریدر بزن (F7 روی فایل)"
  exit 0
fi
export WINEPREFIX="$PREFIX" WINEARCH=win64
rc=0
for mq5 in "${TARGETS[@]}"; do
  win_path="Z:${mq5//\//\\}"
  dim "compile: $mq5"
  wine "$EDITOR" "/compile:${win_path}" /log >/dev/null 2>&1 || rc=1
  log_file="${mq5%.mq5}.log"
  if [[ -f "$log_file" ]]; then
    ERRORS="$(grep -cE 'error|Error' "$log_file" 2>/dev/null || true)"
    sed -n '1,20p' "$log_file" | sed 's/^/    /'
    if [[ -f "${mq5%.mq5}.ex5" ]] && ! grep -qE '^(.*: )?[0-9]+ error' "$log_file"; then
      ok "کامپایل شد: ${mq5%.mq5}.ex5"
    else
      bad "هنوز خطا دارد — لاگ بالا ($log_file)"
      rc=1
    fi
  else
    [[ -f "${mq5%.mq5}.ex5" ]] && ok "ex5 ساخته شد" || { warn "لاگ کامپایلر ساخته نشد"; rc=1; }
  fi
done

step "۳) گام بعدی"
cat <<'EOF'
  در متاتریدر: Navigator → Expert Advisors → TradeMCPConnector را روی چارت XAUUSD_i بکش
  و Algo Trading را روشن کن. بعد:
    ./scripts/connect_trade_mcp.sh --watch
EOF
exit $rc
