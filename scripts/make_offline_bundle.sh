#!/usr/bin/env bash
# ساخت بستهٔ آفلاین برای انتقال به کامپیوتری که به گیت‌هاب/PyPI دسترسی ندارد
#
#   ./scripts/make_offline_bundle.sh                 # فقط سورس
#   ./scripts/make_offline_bundle.sh --with-wheels   # سورس + چرخ‌های pip (نیازمند شبکه)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/dist"
NAME="tradeagent-offline-$STAMP"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

WITH_WHEELS=0
[[ "${1:-}" == "--with-wheels" ]] && WITH_WHEELS=1

echo "بسته‌بندی سورس..."
mkdir -p "$OUT" "$TMP/$NAME"
tar -cf - \
  --exclude=.git --exclude=.venv --exclude=var --exclude=dist --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='.pytest_cache' . \
  | tar -xf - -C "$TMP/$NAME"

if [[ $WITH_WHEELS -eq 1 ]]; then
  echo "دانلود وابستگی‌ها برای نصب آفلاین..."
  python3 -m pip download -q -d "$TMP/$NAME/wheels" \
      'tomli>=2.0; python_version < "3.11"' 'pytest>=8' 'setuptools>=68' 'wheel' || {
      echo "هشدار: بعضی چرخ‌ها دانلود نشدند؛ بسته بدون آن‌ها ساخته می‌شود." >&2
  }
fi

cat > "$TMP/$NAME/INSTALL-OFFLINE.md" <<'MD'
# نصب آفلاین

```bash
cd <این پوشه>
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links wheels -e .      # اگر پوشه‌ی wheels هست
# اگر wheels نیست:
.venv/bin/pip install -e .
.venv/bin/tradeagent demo --cycles 2
```

اگر نصب پکیج ممکن نبود، بدون نصب هم اجرا می‌شود:

```bash
PYTHONPATH=src python3 -m tradeagent demo --cycles 2
```
MD

tar -czf "$OUT/$NAME.tar.gz" -C "$TMP" "$NAME"
echo "ساخته شد: $OUT/$NAME.tar.gz"
ls -lh "$OUT/$NAME.tar.gz"
echo
echo "انتقال:  scp \"$OUT/$NAME.tar.gz\" user@host:~/"
echo "بازکردن: tar -xzf $NAME.tar.gz -C ~/projects && cd ~/projects/$NAME"
