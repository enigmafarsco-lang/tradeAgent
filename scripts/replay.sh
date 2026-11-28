#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# چهار روز تیک طلا → پیش‌بینی هر ۵ دقیقه برای ۵ دقیقهٔ بعد → گزارش گرافیکی + پوش
#
#   ./scripts/replay.sh                 # خلاصهٔ تیک‌ها + بازپخش کامل + پوش گزارش
#   ./scripts/replay.sh --limit 24       # فقط ۲۴ پیش‌بینی (≈۲ ساعت) برای تست سریع
#   ./scripts/replay.sh --summary        # فقط خلاصهٔ تیک‌ها
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
CONFIG="${CONFIG:-configs/gold.toml}"
ok()  { printf '\033[1;32m[rp]\033[0m %s\n' "$*"; }
dim() { printf '\033[2m[rp] %s\033[0m\n' "$*"; }

LIMIT=0
SUMMARY_ONLY=0
PUBLISH=1
STEP=300
HORIZON=300
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    --summary) SUMMARY_ONLY=1; shift ;;
    --no-publish) PUBLISH=0; shift ;;
    --step) STEP="$2"; shift 2 ;;
    --horizon) HORIZON="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

echo "═══ تیک‌های موجود ═══"
"$PY" -m tradeagent --config "$CONFIG" ticks "${ARGS[@]+"${ARGS[@]}"}" || exit 1
[[ "$SUMMARY_ONLY" == "1" ]] && exit 0

echo
echo "═══ بازپخش: هر ${STEP}s یک پیش‌بینی، افق ${HORIZON}s ═══"
dim "مدل فقط عدد می‌بیند؛ جهت و قدرت را خودش می‌گوید. هزینهٔ فرضی هر معامله ۱bp."
PUB_FLAG=()
[[ "$PUBLISH" == "1" ]] && PUB_FLAG=(--publish)
"$PY" -m tradeagent --config "$CONFIG" replay \
  --step "$STEP" --horizon "$HORIZON" --limit "$LIMIT" "${PUB_FLAG[@]+"${PUB_FLAG[@]}"}" \
  "${ARGS[@]+"${ARGS[@]}"}" || exit 1

OUT="var/backtest"
echo
ok "گزارش گرافیکی: $OUT/index.html"
ok "لاگ پیش‌بینی‌ها: $OUT/predictions.jsonl"
dim "برای دیدن: xdg-open $OUT/index.html"
if [[ "$PUBLISH" == "1" ]]; then
  dim "اگر پوش موفق بود، گزارش روی شاخهٔ reports هم هست."
fi
