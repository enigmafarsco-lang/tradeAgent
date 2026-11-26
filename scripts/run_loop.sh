#!/usr/bin/env bash
# اجرای حلقه‌ی ۵ دقیقه‌ای با venv پروژه (برای cron/systemd یا اجرای دستی)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/tradeagent ]]; then
  echo "venv آماده نیست. اجرا کن:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

INTERVAL="${1:-300}"
exec .venv/bin/tradeagent loop --interval "$INTERVAL"
