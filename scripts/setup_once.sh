#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One command: from "MT5 installed" to "tick recording + first paper cycle".
#
#   1. locate the real MT5 data dir (config/ bases/ MQL5/), copy + compile the EA
#   2. write Preset + startup.ini, close any running terminal, relaunch with /config
#   3. wait for MQL5/Files/harvester/status.json
#   4. connect_harvester.sh + tradeagent doctor
#   5. first real cycle + report
#
#   ./scripts/setup_once.sh                 # never touches a running terminal
#   ./scripts/setup_once.sh --restart       # close and relaunch it (destroys the session)
#   ./scripts/setup_once.sh --no-cycle      # stop after doctor
#   ./scripts/setup_once.sh --wait 240      # seconds to wait for status.json
#   ./scripts/setup_once.sh --preview       # print the exact wine command, do nothing
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
export WINEPREFIX="$PREFIX"
export WINEARCH=win64
RESTART=0
DO_CYCLE=1
WAIT=150
PREVIEW=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-restart) RESTART=0; shift ;;
    --restart)    RESTART=1; shift ;;
    --no-cycle)   DO_CYCLE=0; shift ;;
    --wait)       WAIT="$2"; shift 2 ;;
    --preview)    PREVIEW=1; shift ;;
    -h|--help)    sed -n '2,/^set -uo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; }
hint() { printf '\033[1;33m[hint]\033[0m %s\n' "$*"; }

mkdir -p "$ROOT/var/logs"

step "1/5 locate data dir, copy + compile EA"
if [[ $PREVIEW -eq 1 ]]; then
  ./scripts/attach_harvester.sh --dry-run --quiet
  hint "preview only; nothing was changed"
  exit 0
fi
if ! ./scripts/attach_harvester.sh --quiet; then
  bad "attach_harvester.sh failed (see output above)"
  exit 1
fi
INI="$ROOT/var/mt5/startup.ini"
[[ -f "$INI" ]] || { bad "startup.ini was not created"; exit 1; }
ok "startup.ini ready: $INI"

MQL5_DIR="$(find "$PREFIX/drive_c" -maxdepth 13 -type d -path '*MetaQuotes/Terminal/*/MQL5' 2>/dev/null | head -n1)"
[[ -z "$MQL5_DIR" ]] && MQL5_DIR="$(find "$PREFIX/drive_c" -maxdepth 13 -type d -name MQL5 2>/dev/null | head -n1)"
H="${MQL5_DIR:+$MQL5_DIR/Files/harvester}"
ok "MQL5 dir : ${MQL5_DIR:-not found}"
ok "output   : ${H:-not found}"

step "2/5 start terminal"
TERM_EXE="$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'terminal64.exe' 2>/dev/null | head -n1)"
[[ -n "$TERM_EXE" ]] || { bad "terminal64.exe not found"; exit 1; }

bases_dir() {
  # عادی (AppData) یا portable با نام Bases
  { find "$PREFIX/drive_c" -maxdepth 12 -type d -ipath '*MetaQuotes/Terminal/*/bases' 2>/dev/null
    find "$PREFIX/drive_c" -maxdepth 8 -type d -ipath '*/MetaTrader */Bases' 2>/dev/null
  } | head -n1
}

logged_in() {
  local b; b="$(bases_dir)"
  [[ -n "$b" && -n "$(ls -A "$b" 2>/dev/null)" ]]
}

launch() {
  if pgrep -f 'terminal64\.exe' >/dev/null 2>&1; then
    if [[ $RESTART -eq 1 ]]; then
      pkill -f 'terminal64\.exe' 2>/dev/null || true
      sleep 3
      ok "closed the running terminal"
    else
      hint "terminal already running: leaving it alone (no kill, no /config). Add --restart to force."
      return 0
    fi
  fi
  local args=()
  [[ -f "$INI" ]] && args+=("/config:Z:${INI//\//\\}")
  nohup wine "$TERM_EXE" "${args[@]}" >>"$ROOT/var/logs/mt5.out" 2>&1 &
  sleep 5
  pgrep -f 'terminal64\.exe' >/dev/null 2>&1 && ok "terminal started (log: var/logs/mt5.out)" || bad "terminal did not start; see var/logs/mt5.out"
}

launch

step "3/5 waiting for status.json (max ${WAIT}s per attempt, 2 attempts)"
FOUND=0
REATTACHED=0
for attempt in 1 2; do
  for ((i = 1; i <= WAIT; i++)); do
    NEW="$(find "$PREFIX/drive_c" -maxdepth 13 -type d -path '*MQL5/Files/harvester' 2>/dev/null | head -n1)"
    [[ -n "$NEW" ]] && H="$NEW"
    [[ -n "$H" && -f "$H/status.json" ]] && { FOUND=1; break; }
    if [[ $REATTACHED -eq 0 ]] && logged_in; then break; fi
    sleep 1
  done
  [[ $FOUND -eq 1 ]] && break

  if [[ $REATTACHED -eq 0 ]] && logged_in; then
    ok "account is logged in now -> re-reading the real symbol list"
    ./scripts/attach_harvester.sh --quiet || true
    INITXT="$(grep -m1 '^Symbol=' "$INI" 2>/dev/null || true)"
    if [[ -n "$INITXT" ]]; then
      ok "chart symbol from terminal: ${INITXT#Symbol=}"
    else
      warn "still no symbol in the terminal"
    fi
    REATTACHED=1
    launch
    continue
  fi
  break
done

if [[ $FOUND -eq 1 ]]; then
  ok "recording started: $H/status.json"
  head -c 400 "$H/status.json"; echo
else
  if ! logged_in; then
    bad "the terminal is NOT logged in (bases/ is empty) -> no symbols, no chart, no ticks"
    hint "log in once in the MT5 window (File -> Login to Trade Account), then rerun:"
    hint "  ./scripts/setup_once.sh --wait 120"
    exit 4
  fi
  bad "logged in, but the EA has not written anything yet"
  [[ -n "$H" && -d "$H" ]] && echo "  harvester dir exists, contents: $(ls -A "$H" | tr '\n' ' ')"
  hint "MT5: Ctrl+N -> Expert Advisors -> TickHarvester -> drag onto a chart"
  hint "     -> check 'Allow Algo Trading' -> OK -> 'Algo Trading' button green"
  hint "then: ./scripts/setup_once.sh --wait 120"
  exit 3
fi

step "4/5 write data path + doctor"
./scripts/connect_harvester.sh || bad "connect_harvester.sh failed"
.venv/bin/tradeagent doctor

if [[ $DO_CYCLE -eq 1 ]]; then
  step "5/5 first real cycle (no publish)"
  if .venv/bin/tradeagent cycle --no-publish; then
    ok "cycle done"
  else
    bad "cycle failed"
  fi
  .venv/bin/tradeagent report --cat 2>/dev/null | head -40
else
  step "5/5 skipped (--no-cycle)"
fi

step "summary"
ok "tick recording: $H"
hint "make it permanent (every 5 min + report push): ./scripts/install_systemd_user.sh"
