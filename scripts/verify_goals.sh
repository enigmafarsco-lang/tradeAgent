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

# نسخه‌ی همین کپی از ریپو: اگر قدیمی بود، جواب‌های عجیب مالِ کد قدیم است
HEAD_SHA="$(git -C "$ROOT" log -1 --format='%h %s' 2>/dev/null | cut -c1-70)"
printf '\033[1mREPO\033[0m    %s @ %s\n' "$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)" "${HEAD_SHA:-unknown}"

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

# اجازهٔ override برای تست و برای وقتی مسیر در کانفیگ نیست
[[ -n "${TRADEAGENT_HARVESTER_DIR:-}" ]] && HARV="$TRADEAGENT_HARVESTER_DIR"

say "=== 1) harvester heartbeat ==="
# دو سنجهٔ مستقل: status.json (که EA ممکن است در حال بازنویسی باشد) و ماندگاری
# خود فایل تیک‌ها. اگر یکی تازه باشد، ضبط زنده است؛ سنجهٔ تیک قابل‌دستکاری نیست
# چون هر تیک یک خط به فایل اضافه می‌کند.
STATUS_AGE=""
STATUS_NOTE=""
if [[ -n "$HARV" && -f "$HARV/status.json" ]]; then
  STATUS_AGE="$(python3 - "$HARV/status.json" <<'PY'
import json, os, sys, time
path = sys.argv[1]
data = None
for _ in range(20):                     # تا ۵ ثانیه صبر می‌کنیم تا نوشتن تمام شود
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        break
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        time.sleep(0.25)
if data is None:
    print(f"{max(0.0, time.time() - os.stat(path).st_mtime):.0f}")
    print("unreadable")
    raise SystemExit
ages = [s.get("age_s") for s in data.get("symbols", [])
        if isinstance(s, dict) and isinstance(s.get("age_s"), (int, float)) and s["age_s"] >= 0]
mtime_age = max(0.0, time.time() - os.stat(path).st_mtime)
age = min(ages) if ages else mtime_age
print(f"{age:.0f}")
print(f"{len(data.get('symbols', []))}")
print(f"{data.get('total_ticks', 0)}")
PY
)" 2>/dev/null
  [[ -n "$STATUS_AGE" ]] || STATUS_NOTE="status.json unreadable while the EA rewrites it"
else
  STATUS_NOTE="no status.json"
fi

TICK_AGE=""
TICK_FILE=""
if [[ -n "$HARV" ]]; then
  TICK_FILE="$(find "$HARV/ticks" -type f -name '*.csv' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
  if [[ -n "$TICK_FILE" ]]; then
    TICK_AGE=$(( $(date +%s) - $(stat -c %Y "$TICK_FILE") ))
  fi
fi

STATUS_LINE="$(printf '%s' "$STATUS_AGE" | head -n1)"
NSYM="$(printf '%s' "$STATUS_AGE" | sed -n 2p)"
NTICKS="$(printf '%s' "$STATUS_AGE" | sed -n 3p)"
if [[ -n "$STATUS_LINE" ]]; then printf '  status     : %ss ago\n' "$STATUS_LINE"
else printf '  status     : n/a\n'; fi
if [[ "$NSYM" == "unreadable" ]]; then
  printf '  note       : status.json was mid-rewrite; the tick files are the real proof\n'
elif [[ -n "$NSYM" ]]; then
  printf '  ea reports : %s symbols | %s ticks\n' "$NSYM" "$NTICKS"
fi
[[ -n "$STATUS_NOTE" ]] && printf '  note       : %s\n' "$STATUS_NOTE"
if [[ -n "$TICK_AGE" ]]; then printf '  newest tick: %ss ago  %s\n' "$TICK_AGE" "${TICK_FILE##*/}"
else printf '  newest tick: n/a\n'; fi

LIVE_LIMIT="${LIVE_LIMIT:-300}"
live=999999
[[ "$STATUS_LINE" =~ ^[0-9]+$ ]] && (( STATUS_LINE < live )) && live="$STATUS_LINE"
[[ "$TICK_AGE" =~ ^[0-9]+$ ]] && (( TICK_AGE < live )) && live="$TICK_AGE"
if (( live <= LIVE_LIMIT )); then
  pass "harvester is alive (freshest signal ${live}s ago, limit ${LIVE_LIMIT}s)"
elif [[ -z "$HARV" ]]; then
  fail "no harvester directory found - set TRADEAGENT_DATA_DIR"
else
  fail "no ticks and no heartbeat for ${live}s - EA/AutoTrading?  (./scripts/watch_harvester.sh --auto)"
fi
say ""say "=== 2) today's tick files ==="
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
OMODEL="$(grep -s '^TRADEAGENT_OLLAMA_MODEL=' "$ENVF" 2>/dev/null | cut -d= -f2- | head -n1)"
OMODEL="${OMODEL:-qwen2.5:7b-instruct}"
if curl -s --max-time 4 "$OURL/api/tags" 2>/dev/null | grep -q '"models"'; then
  pass "ollama reachable at $OURL"
  # پاسخ واقعی: باید یک JSON برگرداند، نه فقط پورت باز باشد
  T0="$(date +%s)"
  PAYLOAD="$(python3 -c '
import json, sys
print(json.dumps({"model": sys.argv[1], "stream": False, "format": "json",
                  "keep_alive": "30m",
                  "options": {"temperature": 0, "num_predict": 24},
                  "messages": [{"role": "user", "content": "Reply with JSON {\"ok\": true} and nothing else."}]}))
' "$OMODEL" 2>/dev/null)"
  RESP="$(curl -s --max-time 180 -H 'Content-Type: application/json' -d "$PAYLOAD" "$OURL/api/chat" 2>/dev/null)"
  ELAPSED=$(( $(date +%s) - T0 ))
  if printf '%s' "$RESP" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
c = (d.get("message") or {}).get("content", "")
try:
    json.loads(c)
except Exception:
    sys.exit(1)
' 2>/dev/null; then
    pass "model $OMODEL answered with JSON in ${ELAPSED}s"
  else
    warn "ollama is up but $OMODEL gave no usable JSON in ${ELAPSED}s (first load can be slow)"
  fi
  VRAM="$(curl -s --max-time 4 "$OURL/api/ps" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit()
for m in d.get("models",[]): print(int(m.get("size_vram") or 0))
' 2>/dev/null | head -n1)"
  if [[ "${VRAM:-0}" -gt 0 ]]; then
    pass "running on a GPU ($(awk -v v="$VRAM" 'BEGIN{printf "%.1f", v/1e9}') GB in VRAM)"
  else
    say "  (CPU inference - no VRAM reported)"
  fi
else
  warn "ollama not reachable at $OURL"
fi
say "  provider in .env: ${PROV:-<default: gemini>} (without either, cycles stay deterministic)"

say ""
printf '\033[1mSUMMARY\033[0m  pass=%d  warn=%d  fail=%d\n' "$PASS" "$WARN" "$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
