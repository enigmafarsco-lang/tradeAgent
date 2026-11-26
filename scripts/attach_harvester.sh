#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# آماده‌سازی ضبط تیک برای ترمینالِ حساب واقعیِ تو (مثلاً Alpari-Demo):
#   ۱) انتخاب درست‌ترین ترمینال (اگر چند ترمینال در Wine داری)
#   ۲) کپی TickHarvester.mq5 در MQL5/Experts
#   ۳) کامپایل بی‌صدا با MetaEditor (/compile) و بررسی ex5
#   ۴) ساخت Preset و startup.ini برای اجرای ترمینال با چارت + اکسپرت متصل
#
# استفاده:
#   ./scripts/attach_harvester.sh                # تشخیص خودکار
#   ./scripts/attach_harvester.sh --symbol BTCUSD
#   ./scripts/attach_harvester.sh --data-dir "<...>/MQL5" --dry-run
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
export WINEPREFIX="$PREFIX"
export WINEARCH=win64
EA_SRC="$ROOT/mt5/Experts/TickHarvester.mq5"
SYMBOL_ARG=""
DATA_ARG=""
DRY=0
QUIET=0
LIST_SYMBOLS=0
ALL_TERMINALS=0
OUT_DIR="$ROOT/var/mt5"

log()  { printf '\033[1;34m[attach]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[attach]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[attach]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[attach]\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --symbol)   SYMBOL_ARG="$2"; shift 2 ;;
    --data-dir) DATA_ARG="$2"; shift 2 ;;
    --dry-run)  DRY=1; shift ;;
    --quiet)    QUIET=1; shift ;;
    --symbols)  LIST_SYMBOLS=1; shift ;;
    --all)      ALL_TERMINALS=1; shift ;;
    -h|--help)  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -f "$EA_SRC" ]] || die "EA source not found: $EA_SRC"
[[ -d "$PREFIX/drive_c" ]] || die "wine prefix not found: $PREFIX (set MT5_WINEPREFIX)"

# ---------------------------------------------------------------------------
# ۱) انتخاب ترمینال
# پوشه‌ی داده‌ی MT5 = پوشه‌ای که config/ و bases/ دارد (مثلاً
# AppData/Roaming/MetaQuotes/Terminal/<HASH>) یا در حالت portable = پوشه‌ی نصب.
# MQL5 ممکن است هنوز ساخته نشده باشد؛ ما می‌سازیمش.
find_data_dirs() {
  local roots=("$PREFIX/drive_c" "${HOME}/.wine/drive_c" "/mnt/c/Users")
  local r d
  for r in "${roots[@]}"; do
    [[ -d "$r" ]] || continue
    while IFS= read -r -d '' d; do
      # فقط پوشه‌ی ترمینال (دارای config یا bases یا MQL5)
      if [[ -d "$d/config" || -d "$d/bases" || -d "$d/MQL5" ]]; then printf '%s\n' "$d"; fi
    done < <(find "$r" -maxdepth 12 -type d -path '*MetaQuotes/Terminal/*' -print0 2>/dev/null)
    # حالت portable: پوشه‌ی نصب کنار terminal64.exe
    while IFS= read -r -d '' d; do
      printf '%s\n' "$(dirname "$d")"
    done < <(find "$r" -maxdepth 8 -type f -iname 'terminal64.exe' -print0 2>/dev/null)
  done | sort -u
}

score_dir() {
  local d="$1" score=0 btc
  [[ -d "$d/bases" ]] && score=$((score + 3))
  [[ -d "$d/MQL5" ]] && score=$((score + 1))
  [[ -d "$d/MQL5/Files/harvester" ]] && score=$((score + 4))
  [[ -f "$d/MQL5/Files/harvester/status.json" ]] && score=$((score + 4))
  [[ -d "$d/config" ]] && score=$((score + 2))
  btc=$(find "$d/bases" -maxdepth 3 -type d -iname '*XAU*' -o -maxdepth 3 -type d -iname '*BTC*' 2>/dev/null | head -n1)
  [[ -n "$btc" ]] && score=$((score + 1))
  printf '%s' "$score"
}

pick_data_dir() {
  local best="" best_score=-1 d sc
  while IFS= read -r d; do
    [[ -n "$d" && -d "$d" ]] || continue
    sc="$(score_dir "$d")"
    if [[ "$sc" -gt "$best_score" ]]; then best="$d"; best_score="$sc"; fi
  done < <(find_data_dirs)
  printf '%s' "$best"
}

if [[ -n "$DATA_ARG" ]]; then
  DATA_ROOT="$DATA_ARG"
  # اگر کاربر مسیر MQL5 یا MQL5/Files داد، به پوشه‌ی داده برگردان
  [[ "${DATA_ROOT##*/}" == "Files" ]] && DATA_ROOT="$(dirname "$(dirname "$DATA_ROOT")")"
  [[ "${DATA_ROOT##*/}" == "MQL5" ]] && DATA_ROOT="$(dirname "$DATA_ROOT")"
else
  log "scanning for MT5 data dirs (config/bases/terminal64.exe) ..."
  while IFS= read -r d; do log "   candidate: $d  (score $(score_dir "$d"))"; done < <(find_data_dirs)
  DATA_ROOT="$(pick_data_dir)"
fi

if [[ -z "$DATA_ROOT" || ! -d "$DATA_ROOT" ]]; then
  warn "no MT5 data dir found."
  find "$HOME" -maxdepth 12 -type d -path '*MetaQuotes/Terminal/*' 2>/dev/null | sed 's/^/    /'
  die "pass it manually: --data-dir \"<...>/MetaQuotes/Terminal/<HASH>\""
fi
DATA_DIR="$DATA_ROOT/MQL5"
mkdir -p "$DATA_DIR" 2>/dev/null || true
if [[ $LIST_SYMBOLS -eq 1 ]]; then
  echo "data dir: $DATA_ROOT"
  echo "--- history dirs (symbols already opened) ---"
  find "$DATA_ROOT/bases" -maxdepth 3 -type d -path '*/history/*' -printf '%f\n' 2>/dev/null | sort -u | sed 's/^/  /'
  echo "--- Market Watch (bases/*/symbols.sel) ---"
  for sel in "$DATA_ROOT"/bases/*/symbols.sel; do
    [[ -f "$sel" ]] || continue
    echo "  [$sel]"
    strings "$sel" 2>/dev/null | grep -E '^[A-Za-z][A-Za-z0-9._#-]{2,19}$' | grep -v -iE '^(sel|symbols|version|MetaQuotes)$' | sort -u | head -60 | sed 's/^/    /'
  done
  exit 0
fi
ok "data dir   : $DATA_ROOT"
ok "MQL5       : $DATA_DIR"

EXPERTS="$DATA_DIR/Experts"
FILES="$DATA_DIR/Files"
PRESETS="$DATA_DIR/Presets"
mkdir -p "$EXPERTS" "$FILES/harvester" "$PRESETS" "$OUT_DIR" 2>/dev/null || true

# ۲) کپی اکسپرت
if [[ $DRY -eq 1 ]]; then
  echo "   + cp $EA_SRC $EXPERTS/TickHarvester.mq5"
else
  cp -f "$EA_SRC" "$EXPERTS/TickHarvester.mq5" || die "could not copy the EA"
  ok "EA copied -> $EXPERTS/TickHarvester.mq5"
fi

# ---------------------------------------------------------------------------
# ۳) کامپایل با MetaEditor
EDITOR="$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'metaeditor64.exe' 2>/dev/null | head -n1)"
TERMINAL="$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'terminal64.exe' 2>/dev/null | head -n1)"
[[ -n "$TERMINAL" ]] || die "terminal64.exe not found."

if [[ -z "$EDITOR" ]]; then
  warn "metaeditor64.exe not found; compile manually in MT5 with F7."
else
  # نکته‌ی مهم: MetaEditor مسیرِ دارای فاصله را در /compile: درست نمی‌گیرد
  # («Program Files»!). پس در پوشه‌ی بی‌فاصله کامپایل می‌کنیم و ex5 را برمی‌گردانیم.
  # اکسپرت هیچ #include ندارد، پس جابه‌جا کردنش بی‌خطر است.
  TMPC="${TMPDIR:-/tmp}/tradeagent-compile"
  if [[ $DRY -eq 1 ]]; then
    echo "   + cp \"$EXPERTS/TickHarvester.mq5\" $TMPC/ && (cd $TMPC && wine \"$EDITOR\" /compile:TickHarvester.mq5 /log)"
    echo "   + cp $TMPC/TickHarvester.ex5 \"$EXPERTS/\""
  else
    rm -rf "$TMPC"; mkdir -p "$TMPC"
    cp -f "$EXPERTS/TickHarvester.mq5" "$TMPC/" || warn "could not copy the file for compiling"
    log "compiling in a space-free dir: $TMPC"
    ( cd "$TMPC" && timeout 180 wine "$EDITOR" /compile:TickHarvester.mq5 /log >/dev/null 2>&1 ) || true
    sleep 1

    # اگر آنجا هم نشد، یک بار در محل خودش (مسیر نسبی)
    if [[ ! -f "$TMPC/TickHarvester.ex5" ]]; then
      log "second attempt: compile in place with a relative path"
      ( cd "$EXPERTS" && timeout 180 wine "$EDITOR" /compile:TickHarvester.mq5 /log >/dev/null 2>&1 ) || true
      sleep 1
      [[ -f "$EXPERTS/TickHarvester.ex5" ]] || cp -f "$TMPC/TickHarvester.ex5" "$EXPERTS/" 2>/dev/null || true
    fi

    if [[ -f "$TMPC/TickHarvester.ex5" ]]; then
      cp -f "$TMPC/TickHarvester.ex5" "$EXPERTS/TickHarvester.ex5"
      [[ -f "$TMPC/TickHarvester.log" ]] && cp -f "$TMPC/TickHarvester.log" "$EXPERTS/TickHarvester.log"
    fi

    for f in "$EXPERTS/TickHarvester.log" "$TMPC/TickHarvester.log"; do
      if [[ -f "$f" ]]; then
        echo "--- compiler log ($f) ---"
        grep -E "error|warning|Result:" "$f" | tail -n 12 || tail -n 8 "$f"
        break
      fi
    done

    if [[ -f "$EXPERTS/TickHarvester.ex5" ]]; then
      ok "compiled -> $EXPERTS/TickHarvester.ex5"
    else
      warn "automatic compile failed. manually: F4 in MT5 -> MQL5/Experts/TickHarvester.mq5 -> F7"
    fi
  fi
fi

# ۳ب) --all: اکسپرت را در Experts همه‌ی پروفایل‌های ترمینال بگذار
if [[ $ALL_TERMINALS -eq 1 && $DRY -eq 0 ]]; then
  log "installing the EA into every terminal profile:"
  for prof in "$PREFIX"/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/*/; do
    [[ -d "$prof" ]] || continue
    mkdir -p "$prof/MQL5/Experts" 2>/dev/null || continue
    cp -f "$EA_SRC" "$prof/MQL5/Experts/TickHarvester.mq5" 2>/dev/null || continue
    if [[ -f "$EXPERTS/TickHarvester.ex5" ]]; then
      cp -f "$EXPERTS/TickHarvester.ex5" "$prof/MQL5/Experts/TickHarvester.ex5" 2>/dev/null || true
    fi
    ok "  -> $prof/MQL5/Experts"
  done
fi

# ۴) انتخاب نماد
SYMBOL="$SYMBOL_ARG"
EXPLICIT=0
[[ -n "$SYMBOL_ARG" ]] && EXPLICIT=1

# نماد چارت: اول از تاریخچه‌ی خود ترمینال (bases/<server>/history/<SYMBOL>),
# چون نام‌گذاری بروکرها فرق دارد (BTCUSD / BTCUSD.a / XBTUSD ...).
if [[ -z "$SYMBOL" ]]; then
  BASES="$DATA_DIR/../bases"
  HIST="$(find "$BASES" -maxdepth 3 -type d -path '*/history/*' 2>/dev/null)"
  if [[ -n "$HIST" ]]; then
    # نمادهایی که کاربر واقعاً باز کرده (تازه‌ترین اول)
    SYMBOL="$(find "$BASES" -maxdepth 3 -type d -path '*/history/*' -printf '%T@ %f\n' 2>/dev/null \
              | sort -rn | awk '{print $2}' | head -n1)"
  fi
  # اگر چیزی نبود، طلا/بیت‌کوین/یورو را امتحان کن
  if [[ -z "$SYMBOL" ]] || [[ ! -d "$BASES" ]]; then
    for cand in XAUUSD XBTUSD BTCUSD EURUSD; do
      if find "$BASES" -maxdepth 3 -type d -name "$cand" 2>/dev/null | grep -q .; then SYMBOL="$cand"; break; fi
    done
  fi
fi
# اگر تاریخچه‌ای نبود، از فهرست Market Watch ترمینال (symbols.sel) بخوان
if [[ -z "$SYMBOL" ]]; then
  for cand in XAUUSD BTCUSD XBTUSD ETHUSD XAGUSD US30 EURUSD; do
    if strings "$DATA_DIR/../bases"/*/symbols.sel 2>/dev/null | grep -qx -- "$cand"; then SYMBOL="$cand"; break; fi
  done
fi
LOGIN_NEEDED=0
if [[ -z "$SYMBOL" ]]; then
  warn "no symbol found in the terminal -> it is probably not logged in."
  warn "startup.ini will be written WITHOUT a chart/EA; log in and rerun."
  SYMBOL=""
  LOGIN_NEEDED=1
fi
ok "chart symbol: $SYMBOL"

# فهرست نمادهای موجود در ترمینال (برای اینکه ببینی چه داری)
log "symbols available in the terminal (history dirs):"
find "$DATA_DIR/../bases" -maxdepth 3 -type d -path '*/history/*' -printf '   %f\n' 2>/dev/null \
  | sort -u | head -30

# ۵) Preset + startup.ini
SET_FILE="$PRESETS/TickHarvester.set"
INI_FILE="$OUT_DIR/startup.ini"
if [[ $DRY -eq 1 ]]; then
  echo "   + writing $SET_FILE and $INI_FILE"
else
  if [[ $EXPLICIT -eq 1 ]]; then
    # نماد دستی: فقط همان
    cat > "$SET_FILE" <<EOF
InpOutDir=harvester
InpPollMs=250
InpFlushEveryTicks=200
InpStatusEverySec=5
InpSymbolsCsv=$SYMBOL
InpAutoDiscover=false
InpMaxSymbols=40
EOF
  else
    # پیش‌فرض: اکسپرت خودش همه‌ی نمادهای ۲۴ ساعته را پیدا می‌کند
    cat > "$SET_FILE" <<EOF
InpOutDir=harvester
InpPollMs=250
InpFlushEveryTicks=200
InpStatusEverySec=5
InpSymbolsCsv=
InpAutoDiscover=true
InpMaxSymbols=40
EOF
  fi
  mkdir -p "$OUT_DIR"
  {
    echo "; generated by scripts/attach_harvester.sh"
    if [[ -n "$SYMBOL" ]]; then
      echo "[StartUp]"
      echo "Symbol=$SYMBOL"
      echo "Period=M1"
      echo "Expert=TickHarvester"
      echo "ExpertParameters=TickHarvester.set"
    fi
    echo "[Experts]"
    echo "Enabled=1"
    # اختیاری: اگر اعتبارنامه در محیط/.env باشد، لاگین خودکار
    if [[ -n "${MT5_LOGIN:-}" && -n "${MT5_SERVER:-}" ]]; then
      echo "[Common]"
      echo "Login=${MT5_LOGIN}"
      [[ -n "${MT5_PASSWORD:-}" ]] && echo "Password=${MT5_PASSWORD}"
      echo "Server=${MT5_SERVER}"
    fi
  } > "$INI_FILE"
  ok "preset -> $SET_FILE"
  ok "startup.ini -> $INI_FILE"
  [[ $LOGIN_NEEDED -eq 1 ]] && warn "startup.ini has no chart: log in to the account in MT5, then rerun this script."
fi

if [[ $QUIET -eq 0 ]]; then
cat <<EOF

------------------------------------------------------------
Launch the terminal with the $SYMBOL (M1) chart and the EA attached:

  # close MT5 first if it is running (File -> Exit)
  wine "$TERMINAL" "/config:${WIN_INI}"

or through the launcher:

  mt5 "/config:${WIN_INI}"

After it opens, look at the top-right corner of the chart:
  :)  = EA is running      |   :(  = stopped (Algo Trading off)

If you see :(  -> click the "Algo Trading" button in the toolbar.

Verify recording (after 15s):

  ls "$FILES/harvester"; tail -3 "$FILES/harvester/status.json"

Then in the repo root:

  ./scripts/connect_harvester.sh
  .venv/bin/tradeagent doctor
------------------------------------------------------------
EOF
fi
