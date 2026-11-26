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
    -h|--help)  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "گزینه ناشناخته: $1" ;;
  esac
done

[[ -f "$EA_SRC" ]] || die "اکسپرت پیدا نشد: $EA_SRC"
[[ -d "$PREFIX/drive_c" ]] || die "prefix پیدا نشد: $PREFIX (با MT5_WINEPREFIX بده)"

# ---------------------------------------------------------------------------
# ۱) انتخاب ترمینال
# دو چیدمان ممکن است:
#   الف) AppData/Roaming/MetaQuotes/Terminal/<HASH>/MQL5   (حالت عادی)
#   ب) <نصب MT5>/MQL5                                      (حالت portable)
# همه‌ی پوشه‌های MQL5 را پیدا می‌کنیم و بهترین را برمی‌گزینیم.
find_mql5_dirs() {
  local roots=("$PREFIX/drive_c" "${HOME}/.wine/drive_c" "/mnt/c/Users")
  local r d
  for r in "${roots[@]}"; do
    [[ -d "$r" ]] || continue
    while IFS= read -r -d '' d; do printf '%s\n' "$d"; done \
      < <(find "$r" -maxdepth 12 -type d -name MQL5 -print0 2>/dev/null)
  done | sort -u
}

score_dir() {
  local d="$1" score=0 btc
  [[ -f "$d/Files/harvester/status.json" ]] && score=$((score + 4))
  [[ -d "$d/Files/ticks" ]] && score=$((score + 2))
  btc=$(find "$d/../bases" -maxdepth 4 -type d -iname '*BTC*' 2>/dev/null | head -n1)
  [[ -n "$btc" ]] && score=$((score + 2))
  [[ -d "$d/Logs" ]] && [[ -n "$(find "$d/Logs" -maxdepth 1 -type f -newermt '-2 days' 2>/dev/null | head -n1)" ]] && score=$((score + 1))
  # MQL5 کنار terminal64.exe = portable = داده‌ی همان ترمینال
  [[ -f "$d/../terminal64.exe" ]] && score=$((score + 1))
  printf '%s' "$score"
}

pick_data_dir() {
  local best="" best_score=-1 d sc
  while IFS= read -r d; do
    [[ -n "$d" && -d "$d" ]] || continue
    sc="$(score_dir "$d")"
    if [[ "$sc" -gt "$best_score" ]]; then best="$d"; best_score="$sc"; fi
  done < <(find_mql5_dirs)
  printf '%s' "$best"
}

if [[ -n "$DATA_ARG" ]]; then
  DATA_DIR="$DATA_ARG"
  [[ "${DATA_DIR##*/}" == "Files" ]] && DATA_DIR="$(dirname "$DATA_DIR")"
else
  log "جست‌وجوی ترمینال‌های MT5 (prefix، ~/.wine) ..."
  while IFS= read -r d; do log "   کاندید: $d  (امتیاز $(score_dir "$d"))"; done < <(find_mql5_dirs)
  DATA_DIR="$(pick_data_dir)"
fi

if [[ -z "$DATA_DIR" || ! -d "$DATA_DIR" ]]; then
  warn "هیچ MQL5 پیدا نشد."
  echo "  WINEPREFIX=$PREFIX"
  find "$HOME" -maxdepth 14 -type d -name MQL5 2>/dev/null | sed 's/^/    /'
  die "مسیر را دستی بده: --data-dir \"<...>/MQL5\""
fi
ok "داده‌ی ترمینال: $DATA_DIR"

[[ "${DATA_DIR##*/}" == "Files" ]] && DATA_DIR="$(dirname "$DATA_DIR")"
EXPERTS="$DATA_DIR/Experts"
FILES="$DATA_DIR/Files"
PRESETS="$DATA_DIR/Presets"
mkdir -p "$EXPERTS" "$FILES/harvester" "$PRESETS" "$OUT_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# ۲) کپی اکسپرت
if [[ $DRY -eq 1 ]]; then
  echo "   + cp $EA_SRC $EXPERTS/TickHarvester.mq5"
else
  cp -f "$EA_SRC" "$EXPERTS/TickHarvester.mq5" || die "کپی اکسپرت نشد"
  ok "اکسپرت کپی شد → $EXPERTS/TickHarvester.mq5"
fi

# ---------------------------------------------------------------------------
# ۳) کامپایل با MetaEditor
EDITOR="$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'metaeditor64.exe' 2>/dev/null | head -n1)"
TERMINAL="$(find "$PREFIX/drive_c" -maxdepth 8 -type f -iname 'terminal64.exe' 2>/dev/null | head -n1)"
[[ -n "$TERMINAL" ]] || die "terminal64.exe پیدا نشد."

if [[ -z "$EDITOR" ]]; then
  warn "MetaEditor64.exe پیدا نشد؛ کامپایل را دستی در MT5 با F7 بزن."
else
  WIN_MQ5="Z:${EXPERTS//\//\\}\\TickHarvester.mq5"
  if [[ $DRY -eq 1 ]]; then
    echo "   + wine \"$EDITOR\" /compile:\"$WIN_MQ5\" /log"
  else
    log "کامپایل: $WIN_MQ5"
    timeout 180 wine "$EDITOR" "/compile:${WIN_MQ5}" /log >/dev/null 2>&1 || warn "MetaEditor با کد غیرصفر برگشت (اغلب بی‌خطر است)."
    sleep 1
    LOGF="$EXPERTS/TickHarvester.log"
    if [[ -f "$LOGF" ]]; then
      echo "--- گزارش کامپایلر ---"
      tail -n 12 "$LOGF"
    fi
    if [[ -f "$EXPERTS/TickHarvester.ex5" ]]; then
      ok "کامپایل شد → $EXPERTS/TickHarvester.ex5"
    else
      warn "TickHarvester.ex5 ساخته نشد. در MT5 → F4 → فایل را باز کن و F7 بزن."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# ۴) انتخاب نماد
SYMBOL="$SYMBOL_ARG"
if [[ -z "$SYMBOL" ]]; then
  # مسیرها فاصله دارند (Program Files) → خط‌به‌خط بخوان، xargs نزن
  BTC_PATH="$(find "$DATA_DIR/../bases" -maxdepth 4 -type d \( -iname '*BTC*' -o -iname '*XBT*' \) 2>/dev/null | head -n1)"
  [[ -n "$BTC_PATH" ]] && SYMBOL="$(basename "$BTC_PATH")"
fi
if [[ -z "$SYMBOL" ]]; then
  warn "نماد BTC در bases پیدا نشد؛ از BTCUSD استفاده می‌کنم (اگر بروکرت نام دیگری دارد --symbol بده)."
  SYMBOL="BTCUSD"
fi
ok "نماد ضبط: $SYMBOL"

# ---------------------------------------------------------------------------
# ۵) Preset + startup.ini
SET_FILE="$PRESETS/TickHarvester.set"
INI_FILE="$OUT_DIR/startup.ini"
if [[ $DRY -eq 1 ]]; then
  echo "   + نوشتن $SET_FILE و $INI_FILE"
else
  cat > "$SET_FILE" <<EOF
InpOutDir=harvester
InpPollMs=250
InpFlushEveryTicks=200
InpStatusEverySec=5
InpSymbolsCsv=$SYMBOL
InpAutoDiscover=false
InpMaxSymbols=40
EOF
  mkdir -p "$OUT_DIR"
  cat > "$INI_FILE" <<EOF
; تولیدشده توسط scripts/attach_harvester.sh
[StartUp]
Symbol=$SYMBOL
Period=M1
Expert=TickHarvester
ExpertParameters=TickHarvester.set
[Experts]
Enabled=1
EOF
  ok "Preset → $SET_FILE"
  ok "startup.ini → $INI_FILE"
fi

WIN_INI="Z:${INI_FILE//\//\\}"
WIN_TERM="Z:${TERMINAL//\//\\}"
cat <<EOF

────────────────────────────────────────────────────────────
اجرای ترمینال با چارت $SYMBOL (M1) و اکسپرت متصل:

  # اگر ترمینال باز است، اول ببندش (File → Exit)
  wine "$TERMINAL" "/config:${WIN_INI}"

یا معادلش با لانچر:

  mt5 "/config:${WIN_INI}"

بعد از باز شدن، روی چارت دنبال یک «:)» بالای سمت راست بگرد:
  :)  = اکسپرت اجرا می‌شود   |   :(  = متوقف (AutoTrading خاموش)

اگر :( بود: دکمه‌ی «Algo Trading» در نوار ابزار را روشن کن.

تأیید ضبط (۱۵ ثانیه بعد):

  ls "$FILES/harvester"; tail -3 "$FILES/harvester/status.json"

سپس در ریشه‌ی ریپو:

  ./scripts/connect_harvester.sh
  .venv/bin/tradeagent doctor
────────────────────────────────────────────────────────────
EOF
