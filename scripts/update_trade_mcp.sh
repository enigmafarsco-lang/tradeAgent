#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# آخرین تغییرات trade-mcp را بگیر، سالم بودنش را ثابت کن، و ابزارهایش را فهرست کن.
#
#   ./scripts/update_trade_mcp.sh              # pull + تست + فهرست ابزارها
#   ./scripts/update_trade_mcp.sh --check      # فقط وضعیت فعلی (بدون تغییر)
#   ./scripts/update_trade_mcp.sh --mock       # بعد از آپدیت، با دادهٔ ساختگی تست کن
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${TRADE_MCP_DIR:-$HOME/trade-mcp}"
URL="${TRADE_MCP_URL:-https://github.com/enigmafarsco-lang/trade-mcp.git}"
PY="$ROOT/.venv/bin/python"
MODE="update"
MOCK=0
for a in "$@"; do
  case "$a" in
    --check) MODE="check" ;;
    --mock) MOCK=1 ;;
    -h|--help) sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$a"; exit 2 ;;
  esac
done

ok()   { printf '\033[1;32m[mcp]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mcp]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[mcp]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[mcp] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

list_tools() {
  "$PY" - <<'PY'
import os, sys
sys.path.insert(0, os.environ["ROOT"] + "/src")
from tradeagent.mcp_client import MCPClient
cmd = os.environ.get("MCP_CMD", "trade-mcp serve")
c = MCPClient(cmd, timeout_s=25)
if not c.start():
    print("  server did not start: " + cmd)
    raise SystemExit(1)
print(f"  server : {c.server_info.get('name')} {c.server_info.get('version')}")
print(f"  tools  : {len(c.tools)}")
for t in c.tools:
    print(f"    - {t['name']}")
print(f"  prompts: {[p.get('name') for p in c.prompts]}")
c.close()
PY
}

step "۱) ریپو"
if [[ ! -d "$REPO/.git" ]]; then
  if [[ "$MODE" == "check" ]]; then
    bad "not installed at $REPO (run without --check to clone)"; exit 1
  fi
  dim "cloning $URL -> $REPO"
  git clone -q "$URL" "$REPO" || { bad "clone failed"; exit 1; }
fi
BEFORE="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null)"
dim "path: $REPO"
dim "head: $BEFORE  ($(git -C "$REPO" log -1 --format='%cs %s' 2>/dev/null | cut -c1-60))"

if [[ "$MODE" == "update" ]]; then
  step "۲) آخرین تغییرات"
  if git -C "$REPO" pull -q --ff-only 2>/dev/null; then
    AFTER="$(git -C "$REPO" rev-parse --short HEAD)"
    if [[ "$BEFORE" == "$AFTER" ]]; then ok "از قبل به‌روز بود ($AFTER)"
    else ok "به‌روز شد: $BEFORE → $AFTER"; git -C "$REPO" log --oneline "$BEFORE..$AFTER" | head -10 | sed 's/^/    /'; fi
  else
    warn "pull نشد (تغییر محلی یا شبکه) — با نسخهٔ فعلی ادامه می‌دهم"
  fi
else
  step "۲) وضعیت (بدون تغییر)"
  git -C "$REPO" status --short | head -5 | sed 's/^/    /'
fi

step "۳) نصب در venv همین پروژه"
if ! "$PY" -c "import trade_mcp" 2>/dev/null; then
  dim "pip install -e $REPO"
  "$PY" -m pip install -q -e "$REPO" || { bad "نصب نشد"; exit 1; }
  ok "نصب شد"
else
  ok "قبلاً نصب بود ($("$PY" -c 'import trade_mcp; print(getattr(trade_mcp, "__version__", "?"))'))"
fi

if [[ "$MODE" == "update" && -d "$REPO/tests" ]]; then
  step "۴) تست‌های خودشان"
  if ( cd "$REPO" && "$PY" -m pytest -q 2>&1 | tail -3 ); then ok "تست‌ها سالم‌اند"
  else warn "تست‌های trade-mcp سبز نبودند — قبل از استفاده ببین چه تغییر کرده"; fi
fi

step "۵) ابزارهای در دسترس ایجنت"
export ROOT
MCP_CMD="trade-mcp serve"
if (( MOCK )); then MCP_CMD="trade-mcp --mock serve"; dim "حالت mock (بدون متاتریدر)"; fi
if ! MCP_CMD="$MCP_CMD" list_tools; then
  warn "اتصال واقعی برقرار نشد (اکسپرت MT5 وصل نیست؟). با mock:"
  MCP_CMD="trade-mcp --mock serve" list_tools | sed 's/^/    /' || true
  echo
  dim "برای اتصال واقعی: ./scripts/connect_trade_mcp.sh"
fi
