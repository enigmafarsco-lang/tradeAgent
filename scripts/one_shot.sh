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
"$PY" -m tradeagent --config configs/gold.toml diag >"$TMP/diag.log" 2>&1 || true
VERIFY_QUIET=1 "$ROOT/scripts/run_gold.sh" --verify >"$TMP/verify.log" 2>&1 || true

"$PY" - "$TMP" <<'PY'
import pathlib, re, sys

tmp = pathlib.Path(sys.argv[1])
diag = (tmp / "diag.log").read_text(encoding="utf-8", errors="replace")
verify = (tmp / "verify.log").read_text(encoding="utf-8", errors="replace")


def grab(pattern: str, text: str, default: str = "?") -> str:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else default


mt5 = "باز" if "NOT running" not in diag else "بسته"
bridge = grab(r"ea_connected=(\w+)", diag)
ticks = grab(r"XAUUSD_i: (\d+) فایل", diag, "0")
age = grab(r"XAUUSD_i: \d+ فایل · \S+ · (\d+)s پیش", diag, "—")
cycle = grab(r"چرخه (\d+) \|", verify)
bars = grab(r"bars=(\d+)", verify)
llm = grab(r"llm=(\w+)", verify)
equity = grab(r"equity=([\d.]+)", verify)
tools = grab(r"ابزارها: (.+)", verify)
action = grab(r"اقدام: (.+)", verify)
mom = grab(r"حکم تکانه: (.+)", verify)
analysis = grab(r"تحلیل: (.{0,120})", verify)
error = grab(r"ایجنت: .*?error=(.+)", verify, "-")

print()
print("────────── خلاصه ──────────")
print(f"متاتریدر: {mt5} | پل: {bridge}")
print(f"تیک XAUUSD_i: {ticks} فایل، آخرین {age} ثانیه پیش")
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
