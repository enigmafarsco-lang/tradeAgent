#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# پل واقعی MT5 ↔ ایجنت: اکسپرت TradeMCPConnector را سر جایش بگذار و اتصال را بسنج.
#
#   ./scripts/connect_trade_mcp.sh              # نصب/به‌روزرسانی + سنجش
#   ./scripts/connect_trade_mcp.sh --check      # فقط بگو الان چه وضعی دارد
#   ./scripts/connect_trade_mcp.sh --symbol XAUUSD_i
#   ./scripts/connect_trade_mcp.sh --watch      # هر ۵ ثانیه نگاه می‌کند تا وصل شود
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
REPO="${TRADE_MCP_DIR:-$HOME/trade-mcp}"
PY="$ROOT/.venv/bin/python"
SYMBOL="${TRADEAGENT_GOLD_SYMBOL:-XAUUSD_i}"
MODE="apply"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --watch) MODE="watch"; shift ;;
    --symbol) SYMBOL="${2:-$SYMBOL}"; shift 2 ;;
    -h|--help) sed -n '3,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1"; exit 2 ;;
  esac
done

ok()   { printf '\033[1;32m[mcp]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mcp]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[mcp]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[mcp] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

if [[ "$MODE" == "watch" ]]; then
  step "انتظار برای وصل شدن اکسپرت (هر ۵ ثانیه)"
  printf '%s\n' "  در متاتریدر: Navigator → Expert Advisors → TradeMCPConnector را روی چارت $SYMBOL بکش"
  printf '%s\n' "  و دکمهٔ Algo Trading را روشن کن. اینجا خبر می‌دهم."
  i=0
  while true; do
    i=$((i+1))
    OUT="$("$PY" - <<PY
import sys
sys.path.insert(0, "$ROOT/src")
from tradeagent.mcp_client import MCPClient
c = MCPClient("trade-mcp serve", timeout_s=20)
if not c.start():
    print("NOT"); raise SystemExit
try:
    st = c.call_tool("mt5_status")
except Exception as exc:
    print("NOT", exc); raise SystemExit
if isinstance(st, dict) and st.get("ok"):
    t = st.get("terminal") or {}
    a = st.get("account") or {}
    print("YES", t.get("company"), a.get("login"), a.get("balance"))
else:
    print("NOT")
c.close()
PY
)"
    case "$OUT" in
      YES*)
        ok "وصل شد! $OUT"
        step "تحلیل واقعی طلا (از ابزار خود trade-mcp)"
        "$PY" - <<PY
import sys
sys.path.insert(0, "$ROOT/src")
from tradeagent.mcp_client import MCPClient
c = MCPClient("trade-mcp serve", timeout_s=60)
c.start()
a = c.call_tool("analyze_market", {"symbol": "$SYMBOL"})
import json
print(json.dumps(a, ensure_ascii=False, indent=2)[:1200])
c.close()
PY
        exit 0 ;;
      *)
        printf '  [%02d] هنوز وصل نیست...\r' "$i" ;;
    esac
    sleep 5
  done
fi

step "۱) ریپوی trade-mcp"
if [[ ! -d "$REPO/.git" ]]; then
  if [[ "$MODE" == "check" ]]; then bad "نصب نیست: $REPO (بدون --check دوباره بزن)"; exit 1; fi
  git clone -q "${TRADE_MCP_URL:-https://github.com/enigmafarsco-lang/trade-mcp.git}" "$REPO" \
    || { bad "clone failed"; exit 1; }
fi
if [[ "$MODE" == "apply" ]] && git -C "$REPO" pull -q --ff-only 2>/dev/null; then
  ok "به‌روز شد ($(git -C "$REPO" rev-parse --short HEAD))"
else
  dim "HEAD: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null)"
fi
"$PY" -c "import trade_mcp" 2>/dev/null || {
  [[ "$MODE" == "check" ]] && { bad "پکیج نصب نیست: $PY -m pip install -e $REPO"; exit 1; }
  "$PY" -m pip install -q -e "$REPO" && ok "پکیج نصب شد"
}

step "۲) پیدا کردن پوشهٔ متاتریدر"
EXPERTS="$(find "$PREFIX/drive_c" -maxdepth 9 -type d -name Experts -path '*MQL5*' 2>/dev/null | head -n1)"
if [[ -z "$EXPERTS" ]]; then
  bad "پوشهٔ MQL5/Experts زیر $PREFIX پیدا نشد"
  dim "اگر متاتریدر جای دیگری است: MT5_WINEPREFIX=/path ./scripts/connect_trade_mcp.sh"
  exit 1
fi
dim "experts: $EXPERTS"

step "۳) نصب اکسپرت"
SRC="$REPO/mt5-connector/TradeMCPConnector.mq5"
[[ -f "$SRC" ]] || { bad "فایل اکسپرت در ریپو نیست: $SRC"; exit 1; }
DST="$EXPERTS/TradeMCPConnector.mq5"
if [[ "$MODE" == "apply" ]]; then
  cp -f "$SRC" "$DST" && ok "کپی شد: $DST"
else
  if cmp -s "$SRC" "$DST"; then ok "نسخهٔ نصب‌شده با ریپو یکی است"; else warn "نسخهٔ نصب‌شده قدیمی است (بدون --check به‌روز می‌شود)"; fi
fi

# اکسپرتِ ریپوی trade-mcp با MQL5 کامپایل نمی‌شود (LongToString تعریف نشده)؛
# همین‌جا اصلاحش می‌کنیم تا کاربر با خطای کامپایل روبه‌رو نشود.
if [[ "$MODE" == "apply" ]]; then
  step "۳.۵) اصلاح و کامپایل اکسپرت"
  MT5_WINEPREFIX="$PREFIX" TRADE_MCP_DIR="$REPO" "$ROOT/scripts/patch_trade_mcp_connector.sh" \
    || warn "اصلاح/کامپایل کامل نشد - لاگ بالا را ببین"
fi

step "۴) سوکت بریج"
"$PY" - <<'PY'
import socket
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect(("127.0.0.1", 8787))
    print("  bridge: port 8787 is listening")
except OSError:
    print("  bridge: nothing listening on 8787 (expected before MT5 connects)")
finally:
    s.close()
PY

step "۵) سنجش از سمت MCP (متاتریدر)"
"$PY" - <<PY
import sys
sys.path.insert(0, "$ROOT/src")
from tradeagent.mcp_client import MCPClient
c = MCPClient("trade-mcp serve", timeout_s=30)
if not c.start():
    print("  MCP server did not start"); raise SystemExit(1)
st = c.call_tool("mt5_status")
if isinstance(st, dict) and st.get("ok"):
    term = st.get("terminal") or {}
    acc = st.get("account") or {}
    print(f"  connected: {term.get('company', '?')} | build {term.get('build', '?')} | trade_allowed={term.get('trade_allowed')}")
    print(f"  account  : {acc.get('login')} {acc.get('server')} balance={acc.get('balance')} {acc.get('currency')}")
    print(f"  backend  : {(st.get('config') or {}).get('backend')}")
    try:
        sym = c.call_tool("find_symbol", {"query": "$SYMBOL", "limit": 1})
        print("  symbol   :", str(sym)[:160])
        snap = c.call_tool("market_snapshot", {"symbol": "$SYMBOL"})
        q = (snap or {}).get("quote") or {}
        print(f"  quote    : bid={q.get('bid')} ask={q.get('ask')} spread={ (snap or {}).get('spread_points') }")
    except Exception as exc:
        print("  symbol check failed:", exc)
    print("RESULT=CONNECTED")
else:
    err = (st or {}).get("error") if isinstance(st, dict) else st
    print("  not connected:", err)
    print("  hint:", (st or {}).get("hint") if isinstance(st, dict) else "")
    print("RESULT=NOT_CONNECTED")
c.close()
PY
