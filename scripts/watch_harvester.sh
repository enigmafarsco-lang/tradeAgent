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
#   ./scripts/watch_harvester.sh --install-boot   # ترمینال را در هر ورود خودکار باز کن
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
MAX_SILENCE="${MAX_SILENCE:-300}"
AUTO=0
DRY=0
INSTALL_TIMER=0
INSTALL_BOOT=0
WATCH_WAIT="${WATCH_WAIT:-300}"
LOG="$ROOT/var/logs/watch.log"
mkdir -p "$ROOT/var/logs"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --auto) AUTO=1 ;;
    --dry-run) DRY=1 ;;
    --install-timer) INSTALL_TIMER=1 ;;
    --install-boot) INSTALL_BOOT=1 ;;
    --max-silence) MAX_SILENCE="${2:-300}"; shift ;;
    -h|--help) sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1"; exit 2 ;;
  esac
  shift
done

install_boot() {
  local dir="$HOME/.config/systemd/user"
  mkdir -p "$dir"
  cat > "$dir/tradeagent-mt5.service" <<UNIT
[Unit]
Description=tradeAgent MT5 terminal (tick harvester)
After=graphical-session.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$ROOT/scripts/setup_once.sh
UNIT
  systemctl --user daemon-reload 2>/dev/null || true
  if systemctl --user enable --now tradeagent-mt5.service 2>/dev/null; then
    ok "MT5 will start at every login: tradeagent-mt5.service"
    dim "now: systemctl --user start tradeagent-mt5.service   |   off: systemctl --user disable --now tradeagent-mt5.service"
  else
    bad "could not enable tradeagent-mt5.service"
    return 1
  fi
}

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

if (( INSTALL_BOOT == 1 )); then
  install_boot || { bad "could not install the login unit"; exit 1; }
fi
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

# اگر status.json در حال بازنویسی باشد سنش بی‌معنی است؛ فایل تیک سنجهٔ واقعی است
LIVE_AGE="$HB_AGE"
(( TICK_AGE < LIVE_AGE )) && LIVE_AGE="$TICK_AGE"
dim "dir        : $HARV"
dim "status.json: ${HB_AGE}s ago"
dim "newest tick: ${TICK_AGE}s ago (${TICK_NEWEST:-none})"
dim "liveness   : ${LIVE_AGE}s ago (limit ${MAX_SILENCE}s)"

terminal_running() { pgrep -f 'terminal64\.exe' >/dev/null 2>&1; }

if (( LIVE_AGE <= MAX_SILENCE )); then
  ok "recording is alive (freshest signal ${LIVE_AGE}s < ${MAX_SILENCE}s)"
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

# انتظار تا ضربان تازه (راه‌اندازی MT5 زیر Wine چند دقیقه طول می‌کشد)
waited=0
while (( waited < WATCH_WAIT )); do
  hb_now="$(age_of "$HB_FILE")"
  tk_now="$(age_of "${TICK_NEWEST:-/nonexistent}")"
  now_age="$hb_now"; (( tk_now < now_age )) && now_age="$tk_now"
  if (( now_age <= 180 )); then
    ok "heartbeat is fresh again (freshest signal ${now_age}s)"
    say "recovered"
    exit 0
  fi
  sleep 5; waited=$((waited + 5))
  (( waited % 30 == 0 )) && dim "waiting for the terminal... ${waited}s / ${WATCH_WAIT}s"
done
bad "still no fresh heartbeat after ${WATCH_WAIT}s - why (read from the terminal's own log):"

# تشخیص خودکار: لاگ Wine و لاگ خود ترمینال
if [[ -f "$ROOT/var/logs/mt5.out" ]]; then
  echo "  --- var/logs/mt5.out (tail) ---"
  tail -n 12 "$ROOT/var/logs/mt5.out" | sed 's/^/    /'
fi
DATA_LOG="$(find "$PREFIX/drive_c" -type f -name '*.log' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
if [[ -n "$DATA_LOG" && -f "$DATA_LOG" ]]; then
  echo "  --- newest terminal log: $DATA_LOG ---"
  grep -iE 'expert|error|fail|harvester|autotrading|denied|not allowed' "$DATA_LOG" 2>/dev/null | tail -n 12 | sed 's/^/    /'
fi
if ! pgrep -f 'terminal64\.exe' >/dev/null 2>&1; then
  bad "the terminal process is not running at all: Wine/display problem, not an EA problem"
  echo "    ./scripts/setup_once.sh --preview   # shows the exact wine command"
  echo "    ./scripts/setup_once.sh --restart   # forces a fresh instance"
fi
exit 1
