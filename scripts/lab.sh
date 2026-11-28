#!/usr/bin/env bash
# آزمایشگاه مدل آفلاین: تیک‌های ثانیه‌ای → کندل M1 → ویژگی‌ها → سنجش مدل‌ها → داشبورد
#
#   ./scripts/lab.sh                 # افق ۳ کندل M1، گزارش در var/lab/index.html
#   ./scripts/lab.sh --serve         # همان + سرور روی http://127.0.0.1:8610
#   ./scripts/lab.sh --horizon 2 --flat-bp 2
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m tradeagent.cli lab "$@"
