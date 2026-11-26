#!/usr/bin/env bash
# وصل کردن ربات به داده‌ی اکسپرت: پیدا کردن MQL5/Files و نوشتن مسیر در configs/local.toml
#
# استفاده:
#   ./scripts/connect_harvester.sh                 # جست‌وجو در ~/Wine
#   MT5_WINEPREFIX=~/WineMT5 ./scripts/connect_harvester.sh
#   ./scripts/connect_harvester.sh /path/to/MQL5/Files   # مسیر دستی
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
CONFIG="$ROOT/configs/local.toml"

log()  { printf '\033[1;34m[connect]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[connect]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[connect]\033[0m %s\n' "$*" >&2; exit 1; }

# وقتی چند ترمینال در Wine هست (مثلاً MetaQuotes-Demo و Alpari)، درست‌ترین را
# انتخاب کن: آن‌که harvester/status.json دارد، یا در bases نماد BTC دارد،
# یا لاگ‌هایش تازه‌تر است. اگر هیچ‌کدام، جدیدترین MQL5.
pick_files_dir() {
  local best="" best_score=-1 dir mql score btc
  while IFS= read -r dir; do
    [[ -n "$dir" && -d "$dir" ]] || continue
    mql="$(dirname "$dir")"
    score=0
    [[ -f "$dir/harvester/status.json" ]] && score=$((score + 3))
    btc=$(find "$mql/../bases" -maxdepth 4 -type d -iname '*BTC*' 2>/dev/null | head -n1)
    [[ -n "$btc" ]] && score=$((score + 2))
    [[ -d "$mql/Logs" ]] && [[ -n "$(find "$mql/Logs" -maxdepth 1 -type f -newermt '-2 days' 2>/dev/null | head -n1)" ]] && score=$((score + 1))
    [[ "$score" -gt "$best_score" ]] && { best="$dir"; best_score="$score"; }
  done < <(find "$PREFIX/drive_c" -maxdepth 9 -type d -path '*MQL5/Files' 2>/dev/null)
  printf '%s' "$best"
}

FILES="${1:-}"
if [[ -z "$FILES" ]]; then
  log "جست‌وجوی MQL5/Files در $PREFIX ..."
  ALL="$(find "$PREFIX/drive_c" -maxdepth 9 -type d -path '*MQL5/Files' 2>/dev/null || true)"
  if [[ -n "$ALL" ]]; then
    while IFS= read -r d; do log "   نامزد: $d"; done <<< "$ALL"
  fi
  FILES="$(pick_files_dir)"
  [[ -n "$FILES" ]] && log "انتخاب شد: $FILES"
fi
[[ -n "$FILES" ]] || die "MQL5/Files پیدا نشد. یک بار MT5 را باز کن یا مسیر را دستی بده."
[[ -d "$FILES" ]] || die "مسیر وجود ندارد: $FILES"

HARVESTER="$FILES/harvester"
log "MQL5/Files : $FILES"
log "داده‌ی تیک : $HARVESTER"
if [[ ! -d "$HARVESTER" ]]; then
  warn "پوشه‌ی harvester ساخته نشده — یعنی اکسپرت هنوز اجرا نشده. پوشه را می‌سازم؛"
  warn "بعد از اجرای اکسپرت روی چارت، دوباره doctor را بزن."
  mkdir -p "$HARVESTER"
fi

if [[ ! -f "$CONFIG" ]]; then
  log "configs/local.toml نبود؛ از پیش‌فرض ساخته می‌شود."
  cp "$ROOT/configs/default.toml" "$CONFIG"
fi

python3 - "$CONFIG" "$HARVESTER" <<'PY'
import sys, pathlib, tomllib, re

config_path = pathlib.Path(sys.argv[1])
value = sys.argv[2]

lines = config_path.read_text(encoding="utf-8").splitlines()
out, in_data, done = [], False, False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        in_data = stripped == "[data]"
    if in_data and re.match(r"^\s*harvester_dir\s*=", line):
        out.append(f'harvester_dir = "{value}"')
        done = True
        continue
    out.append(line)

if not done:
    # بخش [data] نبود؟ بساز
    if not any(l.strip() == "[data]" for l in out):
        out = ["[data]", f'harvester_dir = "{value}"', ""] + out
    else:
        idx = next(i for i, l in enumerate(out) if l.strip() == "[data]")
        out.insert(idx + 1, f'harvester_dir = "{value}"')

config_path.write_text("\n".join(out) + "\n", encoding="utf-8")

# اعتبارسنجی: فایل باید TOML معتبر بماند و مقدار درست را برگرداند
data = tomllib.loads(config_path.read_text(encoding="utf-8"))
got = data.get("data", {}).get("harvester_dir")
if got != value:
    raise SystemExit(f"نوشتن مسیر ناموفق بود: {got!r} != {value!r}")
print(f'OK: data.harvester_dir = "{got}"')
PY

cat <<EOF

────────────────────────────────────────────────────────────
مسیر داده ثبت شد. قدم‌های بعدی:

  cd "$ROOT"
  .venv/bin/tradeagent doctor
  .venv/bin/tradeagent cycle --no-publish
  .venv/bin/tradeagent report --cat | head -60

اگر doctor گفت «status.json تازه پیدا نشد»: اکسپرت روی چارت اجرا نشده
یا دکمه‌ی AutoTrading سبز نیست.
────────────────────────────────────────────────────────────
EOF
