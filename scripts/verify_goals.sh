#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# چک‌لیست خواسته‌های اصلی پروژه (هر بار یک نگاه، بدون تغییر چیزی):
#   1. ضبط تیک از متاتریدر زنده است (ضربان status.json تازه)
#   2. تیک امروز در حال نوشتن است (فایل CSV امروز)
#   3. گزارش هر ۵ دقیقه در گیت پوش می‌شود (شاخه‌ی reports روی remote)
#   4. فقط کاغذی (هیچ مسیر ارسال سفارش واقعی)
#   5. مدل زبانی: Gemini رایگان یا Ollama محلی
#
#   ./scripts/verify_goals.sh
#   ./scripts/verify_goals.sh --max-report-age 900
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
CONFIG="${VERIFY_CONFIG:-$ROOT/configs/local.toml}"
[[ -f "$CONFIG" ]] || CONFIG="$ROOT/configs/default.toml"
MAX_REPORT_AGE=900
[[ "${1:-}" == "--max-report-age" && -n "${2:-}" ]] && MAX_REPORT_AGE="$2"

PASS=0; FAIL=0; WARN=0
say()  { printf '%s\n' "$*"; }
pass() { printf '\033[1;32m[PASS]\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; WARN=$((WARN+1)); }

HARV="$(python3 - "$CONFIG" <<'PY'
import sys, tomllib, pathlib
try:
    data = tomllib.load(open(sys.argv[1], "rb"))
except Exception:
    print(""); raise SystemExit
print(pathlib.Path(data.get("data", {}).get("harvester_dir", "")).expanduser())
PY
)"

say "=== 1) harvester heartbeat ==="
if [[ -n "$HARV" && -f "$HARV/status.json" ]]; then
  RESULT="$(python3 - "$HARV/status.json" <<'PY'
import json, os, sys, time

path = sys.argv[1]
data = None
for _ in range(6):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        break
    except (json.JSONDecodeError, UnicodeDecodeError):
        time.sleep(0.25)          # the EA may be rewriting the file right now
if data is None:
    mtime_age = max(0.0, time.time() - os.stat(path).st_mtime)
    print(f"  path      : {path}")
    print("  note      : status.json unreadable (rewritten while reading) - using mtime")
    print(f"  heartbeat : {mtime_age:.0f}s ago")
    print("OK" if mtime_age < 180 else "STALE")
    raise SystemExit

ages = [s.get("age_s", -1) for s in data.get("symbols", []) if isinstance(s, dict)]
fresh = [a for a in ages if isinstance(a, (int, float)) and a >= 0]
mtime_age = max(0.0, time.time() - os.stat(path).st_mtime)
age = min(fresh) if fresh else mtime_age
print(f"  path      : {path}")
print(f"  symbols   : {len(data.get('symbols', []))} | total_ticks: {data.get('total_ticks', 0)} | version: {data.get('version', '?')}")
print(f"  heartbeat : {age:.0f}s ago")
print("OK" if age < 180 else "STALE")
PY
)"
  printf '%s\n' "$RESULT" | sed 's/^/  /'
  if printf '%s\n' "$RESULT" | grep -qx 'OK'; then
    pass "harvester is alive"
  else
    fail "harvester heartbeat is stale (>180s) - EA/AutoTrading?"
  fi
else
  fail "no status.json at ${HARV:-<harvester_dir not set>} - the EA never ran"
fi

say ""
say "=== 2) today's tick files ==="
TODAY="$(date -u +%Y%m%d)"
N="$(find "$HARV/ticks" -mindepth 2 -maxdepth 2 -name "$TODAY.csv" 2>/dev/null | wc -l)"
SZ="$(du -sh "$HARV/ticks" 2>/dev/null | cut -f1)"
if [[ "$N" -gt 0 ]]; then pass "$N symbols wrote ticks today (total $SZ)"; else fail "no tick file for $TODAY"; fi

say ""
say "=== 3) report publishing (branch: reports) ==="
REMOTE="$(git -C "$ROOT" remote get-url origin 2>/dev/null)"
if [[ -z "$REMOTE" ]]; then
  fail "no git remote 'origin'"
else
  if git -C "$ROOT" fetch -q origin reports 2>/dev/null; then
    TS="$(git -C "$ROOT" log -1 --format=%ct FETCH_HEAD 2>/dev/null)"
    NOW="$(date +%s)"
    AGE=$(( NOW - ${TS:-0} ))
    LAST="$(git -C "$ROOT" log -1 --format='%h %s' FETCH_HEAD 2>/dev/null)"
    if [[ "$AGE" -le "$MAX_REPORT_AGE" ]]; then
      pass "last report commit ${AGE}s ago (limit ${MAX_REPORT_AGE}s): $LAST"
    else
      warn "last report commit ${AGE}s ago (limit ${MAX_REPORT_AGE}s): $LAST"
      warn "service running? systemctl --user status tradeagent-loop.service"
    fi
  else
    fail "branch 'reports' does not exist on $REMOTE yet"
  fi
fi

say ""
say "=== 4) paper-only invariant ==="
LIVE="$(python3 - "$CONFIG" <<'PY'
import sys, tomllib
try:
    d = tomllib.load(open(sys.argv[1], "rb"))
except Exception:
    print("unknown"); raise SystemExit
print(d.get("paper", {}).get("live_trading", "missing"))
PY
)"
if [[ "$LIVE" == "False" || "$LIVE" == "false" ]]; then
  pass "paper.live_trading = false"
else
  fail "paper.live_trading = $LIVE (must be false)"
fi
# فقط فراخوانی واقعی؛ کامنت/داکیومنت‌ها حساب نمی‌شوند
PY_HITS="$(grep -rIn --include='*.py' --exclude-dir='*.egg-info' \
  -E '(^|[^#[:alnum:]_])(OrderSend|order_send)[[:space:]]*\(|^[[:space:]]*(import|from)[[:space:]]+MetaTrader5' src/ 2>/dev/null || true)"
EA_HITS="$(grep -nE '\b(OrderSend|OrderSendAsync|CTrade|trade\.Buy|trade\.Sell)\b' mt5/Experts/TickHarvester.mq5 2>/dev/null || true)"
if [[ -n "$PY_HITS" ]]; then
  fail "python code calls an order-sending API:"; printf '%s\n' "$PY_HITS" | head -3 | sed 's/^/    /'
else
  pass "no order-sending call in src/*.py (comments excluded)"
fi
if [[ -n "$EA_HITS" ]]; then
  fail "the EA contains trading calls:"; printf '%s\n' "$EA_HITS" | head -3 | sed 's/^/    /'
else
  pass "TickHarvester.mq5 has no trading calls (recording only)"
fi

say ""
say "=== 5) language model: Gemini (free) or Ollama (local) ==="
ENVF="$ROOT/.env"
GKEY="$(grep -s '^GEMINI_API_KEY=' "$ENVF" 2>/dev/null | cut -d= -f2- | head -n1)"
PROV="$(grep -s '^TRADEAGENT_LLM_PROVIDER=' "$ENVF" 2>/dev/null | cut -d= -f2- | head -n1)"
OURL="$(grep -s '^TRADEAGENT_OLLAMA_URL=' "$ENVF" 2>/dev/null | cut -d= -f2- | head -n1)"
OURL="${OURL:-http://127.0.0.1:11434}"
if [[ -n "$GKEY" ]]; then pass "gemini key present (${#GKEY} chars)"; else warn "no gemini key in .env"; fi
if curl -s --max-time 4 "$OURL/api/tags" 2>/dev/null | grep -q '"models"'; then
  pass "ollama reachable at $OURL"
else
  warn "ollama not reachable at $OURL"
fi
say "  provider in .env: ${PROV:-<default: gemini>} (without either, cycles stay deterministic)"

say ""
printf '\033[1mSUMMARY\033[0m  pass=%d  warn=%d  fail=%d\n' "$PASS" "$WARN" "$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
