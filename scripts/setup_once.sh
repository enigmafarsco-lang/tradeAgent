#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# «یک دستور تا ضبط»: مسیر داده را پیدا می‌کند، اکسپرت را کپی/کامپایل می‌کند،
# preset و startup.ini می‌سازد، ترمینال را با چارت+اکسپرت بالا می‌آورد،
# منتظر status.json می‌ماند، بعد connect + doctor + اولین چرخه را اجرا می‌کند.
#
#   ./scripts/setup_once.sh              # همه‌ی مراحل
#   ./scripts/setup_once.sh --no-restart # ترمینال را نکش (اگر باز است)
#   ./scripts/setup_once.sh --no-cycle   # فقط تا doctor
#   ./scripts/setup_once.sh --wait 240   # چند ثانیه منتظر status.json بماند
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
export WINEPREFIX="$PREFIX"
export WINEARCH=win64
RESTART=1
DO_CYCLE=1
WAIT=150

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-restart) RESTART=0; shift ;;
    --no-cycle)   DO_CYCLE=0; shift ;;
    --wait)       WAIT="$2"; shift 2 ;;
    -h|--help)    sed -n '2,/^set -uo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "گزینه ناشناخته: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;35m══ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m✘\033[0m %s\n' "$*"; }
hint() { printf '\033[1;33m→\033[0m %s\n' "$*"; }

mkdir -p "$ROOT/var/logs"

# ── ۱) مسیر + اکسپرت + کامپایل + preset + ini ───────────────────────────────
step "۱/۵ پیدا کردن مسیر داده، کپی و کامپایل اکسپرت"
if ! ./scripts/attach_harvester.sh; then
  bad "attach_harvester شکست خورد (خروجی بالا)."
  exit 1
fi
INI="$ROOT/var/mt5/startup.ini"
[[ -f "$INI" ]] || { bad "startup.ini ساخته نشد."; exit 1; }
ok "startup.ini آماده است"

MQL5_DIR="$(find "$PREFIX/drive_c" -maxdepth 13 -type d -path '*Terminal/*/MQL5' 2>/dev/null | head -n1)"
[[ -z "$MQL5_DIR" ]] && MQL5_DIR="$(find "$PREFIX/drive_c" -maxdepth 13 -type d -name MQL5 2>/dev/null | head -n1)"
DATA_ROOT="$([[ -n "$MQL5_DIR" ]] && dirname "$MQL5_DIR")"
H="${MQL5_DIR:+$MQL5_DIR/Files/harvester}"
ok "پوشه‌ی داده: ${DATA_ROOT:-?}"
ok "خروجی تیک: ${H:-?}"

# ── ۲) ترمینال ─────────────────────────────────────────────────────────────
step "۲/۵ اجرای ترمینال با چارت و اکسپرت"
TERM_EXE="$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'terminal64.exe' 2>/dev/null | head -n1)"
[[ -n "$TERM_EXE" ]] || { bad "terminal64.exe پیدا نشد."; exit 1; }

if pgrep -f 'terminal64\.exe' >/dev/null 2>&1; then
  if [[ $RESTART -eq 1 ]]; then
    pkill -f 'terminal64\.exe' 2>/dev/null || true
    sleep 3
    ok "ترمینال قبلی بسته شد"
  else
    hint "ترمینال باز است و --no-restart داده‌ای → /config اعمال نمی‌شود"
  fi
fi

WIN_INI="Z:${INI//\//\\}"
nohup wine "$TERM_EXE" "/config:$WIN_INI" >>"$ROOT/var/logs/mt5.out" 2>&1 &
sleep 5
pgrep -f 'terminal64\.exe' >/dev/null 2>&1 && ok "ترمینال بالا آمد" || bad "ترمینال بالا نیامد (var/logs/mt5.out را ببین)"

# ── ۳) انتظار برای status.json ─────────────────────────────────────────────
step "۳/۵ انتظار برای اولین status.json (حداکثر ${WAIT}s)"
FOUND=0
for i in $(seq 1 "$WAIT"); do
  [[ -n "$H" && -f "$H/status.json" ]] && { FOUND=1; break; }
  # هر ۲۵ ثانیه مسیر را دوباره پیدا کن (اگر ترمینال تازه MQL5 ساخته باشد)
  [[ $((i % 25)) -eq 0 ]] && H="$(find "$PREFIX/drive_c" -maxdepth 13 -type d -path '*MQL5/Files/harvester' 2>/dev/null | head -n1)"
  sleep 1
done

if [[ $FOUND -eq 1 ]]; then
  ok "ضبط شروع شد: $H/status.json"
  head -c 400 "$H/status.json"; echo
else
  bad "اکسپرت هنوز چیزی ننوشته."
  if [[ -d "$H" ]]; then
    echo "  پوشه هست ولی خالی: $(ls -A "$H" | tr '\n' ' ')"
  else
    echo "  پوشه‌ی harvester ساخته نشده."
  fi
  hint "در پنجره‌ی MT5: Ctrl+N → Expert Advisors → TickHarvester را روی چارت بکش → تیک Allow Algo Trading → OK"
  hint "بعد: ./scripts/setup_once.sh --no-restart --wait 120"
  exit 3
fi

# ── ۴) وصل کردن پایتون ─────────────────────────────────────────────────────
step "۴/۵ ثبت مسیر در configs/local.toml"
./scripts/connect_harvester.sh || bad "connect_harvester شکست خورد"
.venv/bin/tradeagent doctor

# ── ۵) اولین چرخه ──────────────────────────────────────────────────────────
if [[ $DO_CYCLE -eq 1 ]]; then
  step "۵/۵ اولین چرخه‌ی واقعی (بدون انتشار)"
  .venv/bin/tradeagent cycle --no-publish && ok "چرخه اجرا شد" || bad "چرخه خطا داد"
  .venv/bin/tradeagent report --cat 2>/dev/null | head -40
else
  step "۵/۵ رد شد (--no-cycle)"
fi

step "خلاصه"
ok "ضبط تیک فعال است: $H"
hint "دائمی‌سازی: ./scripts/install_systemd_user.sh"
