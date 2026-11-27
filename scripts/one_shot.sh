#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# یک دستور، یک بلوک کوتاه.  هیچ چیز دیگری لازم نیست.
#
#   ./scripts/one_shot.sh
#
# خودش: متاتریدر را اگر بسته باشد باز می‌کند، سرویس را موقتاً می‌خواباند،
# یک چرخهٔ واقعی با مدل می‌زند، سرویس را برمی‌گرداند، و فقط ۱۰ خط چکیده چاپ می‌کند.
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"$ROOT/scripts/start_mt5.sh" >"$TMP/mt5.log" 2>&1 || true
MT5_WINEPREFIX="${MT5_WINEPREFIX:-$HOME/Wine}" "$PY" -c 'from tradeagent.diag import report; report()' >"$TMP/diag.log" 2>&1 || true
"$PY" -c 'from tradeagent.diag import last_tick_price, mt5_price; import json; print(json.dumps({"ours": last_tick_price(), "theirs": mt5_price()}))' >"$TMP/price.json" 2>&1 || true
VERIFY_QUIET=1 "$ROOT/scripts/run_gold.sh" --verify >"$TMP/verify.log" 2>&1 || true

"$PY" - "$TMP" <<'PY'
import os, pathlib, re, sys

tmp = pathlib.Path(sys.argv[1])
diag = (tmp / "diag.log").read_text(encoding="utf-8", errors="replace")
verify = (tmp / "verify.log").read_text(encoding="utf-8", errors="replace")
assert os.environ.get("VERIFY_QUIET") in (None, "1", "0", "")


def grab(pattern: str, text: str, default: str = "?") -> str:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else default


mt5 = "باز" if "NOT running" not in diag else "بسته"
bridge = grab(r"ea_connected=(\w+)", diag)
cycle = grab(r"چرخه (\d+) \|", verify)
bars = grab(r"bars=(\d+)", verify)
llm = grab(r"llm=(\w+)", verify)
equity = grab(r"equity=([\d.]+)", verify)
tools = grab(r"ابزارها: (.+)", verify, "—")
action = grab(r"اقدام: (.+)", verify, "—")
mom = grab(r"حکم تکانه: (.+)", verify)
analysis = grab(r"تحلیل: (.{0,120})", verify)
error = grab(r"ایجنت: .*?error=(.+)", verify, "-")

price_text = (tmp / "price.json").read_text(encoding="utf-8", errors="replace")
price: dict = {}
try:
    start = price_text.find("{")
    if start >= 0:
        import json as _json

        price = _json.loads(price_text[start:])
except ValueError:
    price = {}


def price_line() -> str:
    ours = price.get("ours") or {}
    theirs = price.get("theirs") or {}
    if not ours.get("ok"):
        return f"قیمت تیک ما: — ({ours.get('why')})"
    base = f"قیمت تیک ما: {ours.get('bid')}/{ours.get('ask')}"
    if not theirs.get("ok"):
        return base + f" | MT5: — ({theirs.get('why')})"
    diff_bp = abs(float(theirs["price"]) - float(ours["bid"])) / float(ours["bid"]) * 1e4
    flag = "✅" if diff_bp < 50 else "❌ دو منبع مختلف"
    return base + f" | MT5: {theirs['price']} ({diff_bp:.0f}bp اختلاف {flag})"


def tick_line(text: str) -> tuple[str, str]:
    m = re.search(r"XAUUSD_i: (\d+) فایل · \S+ · (\d+)s پیش", text)
    if m:
        return m.group(1), m.group(2)
    if "XAUUSD_i: هیچ فایلی نیست" in text:
        return "0", "—"
    return "?", "—"


ticks, age = tick_line(diag)

print()
print("────────── خلاصه ──────────")
if bridge == "?":
    hint = next((ln.strip() for ln in diag.splitlines() if "پل" in ln or "bridge" in ln), "")
    print(f"متاتریدر: {mt5} | پل: ؟ | {hint[:90]}")
else:
    print(f"متاتریدر: {mt5} | پل: {bridge}")
print(f"تیک XAUUSD_i: {ticks} فایل، آخرین {age} ثانیه پیش")
print(price_line())
print(f"چرخه {cycle} | bars={bars} | llm={llm} | equity={equity}")
print(f"ابزارها: {tools}")
print(f"اقدام: {action}")
print(f"حکم: {mom}")
if analysis != "?":
    print(f"تحلیل مدل: {analysis}")
if error not in ("-", "?"):
    print(f"خطای ایجنت: {error}")
bad = []
if llm != "True":
    bad.append("مدل جواب نداد")
if bars in ("0", "?"):
    bad.append("کندلی نیامد")
if ticks == "0":
    bad.append("تیک طلا نمی‌رسد")
print()
print("نتیجه: " + ("همه‌چیز سالم است ✅" if not bad else " | ".join(bad)))
PY
