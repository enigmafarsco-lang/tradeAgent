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
dir_ci() {
  find "$1" -maxdepth 1 -mindepth 1 -type d -iname "$2" 2>/dev/null | head -n1
}

score_files_dir() {
  local dir="$1" mql score=0 syms bases
  mql="$(dirname "$dir")"
  if [[ -f "$dir/harvester/status.json" ]]; then
    score=$((score + 3))
    [[ -n "$(find "$dir/harvester/status.json" -newermt '-10 minutes' 2>/dev/null)" ]] && score=$((score + 10))
  fi
  [[ -d "$dir/harvester/ticks" ]] && score=$((score + 2))
  bases="$(dir_ci "$mql/.." bases)"
  [[ -n "$bases" ]] && [[ -n "$(find "$bases" -maxdepth 4 -type d -ipath '*/history/*' 2>/dev/null | head -n1)" ]] && score=$((score + 2))
  [[ -f "$mql/../terminal64.exe" ]] && score=$((score + 1))
  [[ -n "$(find "$mql/../logs" -maxdepth 1 -type f -newermt '-30 minutes' 2>/dev/null | head -n1)" ]] && score=$((score + 2))
  printf '%s' "$score"
}

pick_files_dir() {
  local best="" best_score=-1 dir score
  while IFS= read -r dir; do
    [[ -n "$dir" && -d "$dir" ]] || continue
    score="$(score_files_dir "$dir")"
    if [[ "$score" -gt "$best_score" ]]; then best="$dir"; best_score="$score"; fi
  done < <(find "$PREFIX/drive_c" -maxdepth 10 -type d -path '*MQL5/Files' 2>/dev/null)
  printf '%s' "$best"
}

FILES="${1:-}"
if [[ -z "$FILES" ]]; then
  log "scanning for MQL5/Files in $PREFIX ..."
  ALL="$(find "$PREFIX/drive_c" -maxdepth 9 -type d -path '*MQL5/Files' 2>/dev/null || true)"
  if [[ -n "$ALL" ]]; then
    while IFS= read -r d; do log "   candidate: $d  (score $(score_files_dir "$d"))"; done <<< "$ALL"
  fi
  FILES="$(pick_files_dir)"
  [[ -n "$FILES" ]] && log "selected: $FILES"
fi
[[ -n "$FILES" ]] || die "MQL5/Files not found. Open MT5 once or pass the path manually."
[[ -d "$FILES" ]] || die "path does not exist: $FILES"

HARVESTER="$FILES/harvester"
log "MQL5/Files : $FILES"
log "tick data : $HARVESTER"
if [[ ! -d "$HARVESTER" ]]; then
  warn "harvester/ does not exist yet - the EA has not run. Creating it."
  warn "after the EA runs on a chart, run doctor again."
  mkdir -p "$HARVESTER"
fi

if [[ ! -f "$CONFIG" ]]; then
  log "configs/local.toml missing; creating it from defaults."
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
    raise SystemExit(f"failed to write the path: {got!r} != {value!r}")
print(f'OK: data.harvester_dir = "{got}"')
PY

cat <<EOF

------------------------------------------------------------
Data path saved. Next steps:

  cd "$ROOT"
  .venv/bin/tradeagent doctor
  .venv/bin/tradeagent cycle --no-publish
  .venv/bin/tradeagent report --cat | head -60

If doctor says "status.json is stale": the EA is not running on a chart
or the AutoTrading button is off.
------------------------------------------------------------
EOF
