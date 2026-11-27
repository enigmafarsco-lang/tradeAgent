#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# متاتریدر را مستقل از ترمینال اجرا کن (بسته شدن کنسول آن را نکشد)
#
#   ./scripts/start_mt5.sh          # اگر باز نباشد، بازش کن و مطمئن شو بالا آمد
#   ./scripts/start_mt5.sh --check  # فقط بگو باز است یا نه
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ok()   { printf '\033[1;32m[mt5]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mt5]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[mt5]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[mt5] %s\033[0m\n' "$*"; }

PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
EXE="$PREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"

running() {
  [[ "$(MT5_WINEPREFIX="$PREFIX" "$PY" -c 'from tradeagent.diag import mt5_pids; print(len(mt5_pids()))' 2>/dev/null || echo 0)" != "0" ]]
}
first_pid() {
  MT5_WINEPREFIX="$PREFIX" "$PY" -c 'from tradeagent.diag import mt5_pids; print((mt5_pids() or ["?"])[0])' 2>/dev/null || echo "?"
}

if [[ "${1:-}" == "--check" ]]; then
  if running; then ok "terminal64.exe در حال اجراست (pid $(first_pid))"; exit 0; fi
  bad "terminal64.exe اجرا نمی‌شود"; exit 1
fi

if running; then
  ok "از قبل باز است (pid $(first_pid))"
  MT5_WINEPREFIX="$PREFIX" "$PY" -c 'from tradeagent.diag import harvester; h=harvester(); print("  هاروستر:", h.get("newest"), "·", h.get("age_s"), "s پیش")' 2>/dev/null || true
  exit 0
fi

[[ -f "$EXE" ]] || { bad "پیدا نشد: $EXE"; bad "با MT5_WINEPREFIX مسیر prefix را بده."; exit 1; }
command -v wine >/dev/null 2>&1 || { bad "wine روی PATH نیست"; exit 1; }

dim "اجرا با setsid (مستقل از این ترمینال)"
setsid env WINEPREFIX="$PREFIX" wine "$EXE" >/dev/null 2>&1 < /dev/null &
disown 2>/dev/null || true

for i in $(seq 1 30); do
  sleep 1
  if running; then
    ok "بالا آمد (pid $(first_pid)) بعد از ${i}s"
    dim "اکسپرت TradeMCPConnector باید روی چارت XAUUSD_i باشد؛ تا ۳ ثانیه بعد وصل می‌شود"
    MT5_WINEPREFIX="$PREFIX" "$PY" -c 'from tradeagent.diag import harvester; h=harvester(); print("  هاروستر:", h.get("newest"), "·", h.get("age_s"), "s پیش")' 2>/dev/null || true
    exit 0
  fi
done

bad "بعد از ۳۰ ثانیه بالا نیامد"
dim "خروجی wine را ببین: WINEPREFIX=$PREFIX wine \"$EXE\"  (بدون /dev/null)"
exit 1
