#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# نگهبان ضبط: اگر ضربان harvester برای مدتی قطع شد، خودش ترمینال را با
# startup.ini و اکسپرت دوباره بالا می‌آورد (بدون اینکه کاری از تو خواسته شود).
#
#   ./scripts/watch_harvester.sh                 # فقط بررسی و گزارش
#   ./scripts/watch_harvester.sh --auto           # اگر لازم شد، خودش تعمیر کند
#   ./scripts/watch_harvester.sh --auto --install-timer   # هر ۵ دقیقه در پس‌زمینه
#   ./scripts/watch_harvester.sh --max-silence 1200
#   ./scripts/watch_harvester.sh --dry-run        # بگو چه می‌کرد
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
MAX_SILENCE="${MAX_SILENCE:-900}"
AUTO=0
DRY=0
INSTALL_TIMER=0
LOG="$ROOT/var/logs/watch.log"
mkdir -p "$ROOT/var/logs"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto) AUTO=1 ;;
    --dry-run) DRY=1 ;;
    --install-timer) INSTALL_TIMER=1 ;;
    --max-silence) MAX_SILENCE="${2:-900}"; shift ;;
    -h|--help) sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1"; exit 2 ;;
  esac
  shift
done

install_timer() {
  local dir="$HOME/.config/systemd/user"
  mkdir -p "$dir"
  cat > "$dir/tradeagent-watch.service" <<UNIT
[Unit]
Description=tradeAgent harvester watchdog

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=$ROOT/scripts/watch_harvester.sh --auto
UNIT
  cat > "$dir/tradeagent-watch.timer" <<UNIT
[Unit]
Description=tradeAgent harvester watchdog (every 5 minutes)

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl --user daemon-reload || return 1
  systemctl --user enable --now tradeagent-watch.timer || return 1
  ok "timer installed: tradeagent-watch.timer (every 5 min, --auto)"
  dim "logs: journalctl --user -u tradeagent-watch.service -n 20"
  dim "stop: systemctl --user disable --now tradeagent-watch.timer"
}

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say()   { printf '%s %s\n' "$(stamp)" "$*" | tee -a "$LOG"; }
dim()   { printf '\033[2m[watch] %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m[watch]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[watch]\033[0m %s\n' "$*"; }
bad()   { printf '\033[1;31m[watch]\033[0m %s\n' "$*"; }

if (( INSTALL_TIMER == 1 )); then
  install_timer || { bad "could not install the timer"; exit 1; }
  [[ "${1:-}" == "--only-timer" ]] && exit 0
fi

HARV="$(find "$PREFIX/drive_c" -maxdepth 13 -type d -path '*MQL5/Files/harvester' 2>/dev/null | head -n1)"
if [[ -z "$HARV" ]]; then
  bad "no harvester output under $PREFIX"
  say "no harvester dir"
  exit 1
fi

age_of() {  # age_of <path> → ثانیه، یا 999999 اگر نبود
  local p="$1"
  [[ -e "$p" ]] || { echo 999999; return; }
  echo $(( $(date +%s) - $(stat -c %Y "$p") ))
}

HB_FILE="$HARV/status.json"
HB_AGE="$(age_of "$HB_FILE")"

# اگر status.json فقط نیمه‌نوشته باشد، از سن قدیمی‌ترین فایل تیک هم استفاده می‌کنیم
TICK_NEWEST="$(find "$HARV/ticks" -type f -name '*.csv' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
TICK_AGE="$(age_of "${TICK_NEWEST:-/nonexistent}")"

dim "dir      : $HARV"
dim "heartbeat: ${HB_AGE}s ago"
dim "newest tick: ${TICK_AGE}s ago (${TICK_NEWEST:-none})"

terminal_running() { pgrep -f 'terminal64\.exe' >/dev/null 2>&1; }

if (( HB_AGE <= MAX_SILENCE )); then
  ok "recording is alive (heartbeat ${HB_AGE}s < ${MAX_SILENCE}s)"
  exit 0
fi

if (( TICK_AGE <= 120 )) && (( HB_AGE > MAX_SILENCE )); then
  warn "ticks are being written but status.json is ${HB_AGE}s old - the running EA is an older build"
  warn "re-attaching the current EA (compile + install + chart) is the fix"
elif (( TICK_AGE > MAX_SILENCE )); then
  warn "nothing written for ${HB_AGE}s (ticks ${TICK_AGE}s ago)"
fi

if terminal_running; then
  dim "terminal64.exe is running"
else
  dim "terminal64.exe is NOT running"
fi

if (( DRY == 1 )); then
  say "dry-run: would relaunch the terminal with var/mt5/startup.ini"
  exit 2
fi

if (( AUTO == 0 )); then
  warn "not fixing anything (no --auto). To let it repair itself:"
  echo "    ./scripts/watch_harvester.sh --auto"
  exit 2
fi

say "repairing: attach + relaunch"
if [[ ! -f "$ROOT/var/mt5/startup.ini" ]]; then
  say "no startup.ini yet - building it with attach_harvester.sh"
  ./scripts/attach_harvester.sh >>"$LOG" 2>&1 || { bad "attach failed (see $LOG)"; exit 1; }
fi

# setup_once با --restart ترمینال را با /config و اکسپرت بالا می‌آورد
if ./scripts/setup_once.sh --restart >>"$LOG" 2>&1; then
  ok "relaunched - see $LOG"
else
  bad "relaunch failed - last lines of $LOG:"
  tail -12 "$LOG" | sed 's/^/    /'
  exit 1
fi

# انتظار تا ضربان تازه
for i in $(seq 1 60); do
  if (( $(age_of "$HB_FILE") <= 180 )); then
    ok "heartbeat is fresh again ($(age_of "$HB_FILE")s)"
    say "recovered"
    exit 0
  fi
  sleep 2
done
bad "still no fresh heartbeat after 120s - check var/logs/mt5.out"
exit 1
