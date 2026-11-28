#!/usr/bin/env bash
# استودیو ایجنت: روی دادهٔ آفلاین کندل‌به‌کندل جلو می‌رود و اپ زندهٔ چارت را بالا می‌آورد.
#
#   ./scripts/studio.sh --serve                 # طلا، گام ۱ دقیقه، افق ۳ کندل + اپ
#   ./scripts/studio.sh --serve --limit 60      # آزمون سریع ۶۰ تصمیم
#   ./scripts/studio.sh --serve --no-llm        # فقط قاعدهٔ تکانه (بدون ollama)
#   ./scripts/studio.sh --symbol GER40_i --serve
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m tradeagent.cli studio "$@"
