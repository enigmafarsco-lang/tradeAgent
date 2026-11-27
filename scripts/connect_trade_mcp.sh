#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# پل واقعی MT5 ↔ ایجنت: اکسپرت TradeMCPConnector را سر جایش بگذار و اتصال را بسنج.
#
#   ./scripts/connect_trade_mcp.sh              # نصب/به‌روزرسانی + سنجش
#   ./scripts/connect_trade_mcp.sh --check      # فقط بگو الان چه وضعی دارد
#   ./scripts/connect_trade_mcp.sh --symbol XAUUSD_i
#   ./scripts/connect_trade_mcp.sh --watch      # هر ۵ ثانیه نگاه می‌کند تا وصل شود
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MT5_WINEPREFIX:-$HOME/Wine}"
REPO="${TRADE_MCP_DIR:-$HOME/trade-mcp}"
PY="$ROOT/.venv/bin/python"
SYMBOL="${TRADEAGENT_GOLD_SYMBOL:-XAUUSD_i}"
MODE="apply"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --watch) MODE="watch"; shift ;;
    --symbol) SYMBOL="${2:-$SYMBOL}"; shift 2 ;;
    -h|--help) sed -n '3,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1"; exit 2 ;;
  esac
done

ok()   { printf '\033[1;32m[mcp]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mcp]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[mcp]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[mcp] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

if [[ "$MODE" == "watch" ]]; then
  step "انتظار برای وصل شدن اکسپرت"
  printf '  اکسپرت باید روی چارت %s باشد و Algo Trading روشن.\n' "$SYMBOL"
  printf '  این حالت تا Ctrl-C روشن می‌ماند: سوکت ۸۷۸۷ باز می‌ماند تا اکسپرت هر وقت\n'
  printf '  تلاش کرد، جواب بگیرد (اگر ببندیم، متاتریدر «connection refused» می‌گیرد).\n'

  export ROOT SYMBOL
  "$PY" -u - <<'PY'
import json, os, pathlib, re, socket, subprocess, sys, time

ROOT = pathlib.Path(os.environ["ROOT"])
SYMBOL = os.environ.get("SYMBOL", "XAUUSD_i")
sys.path.insert(0, str(ROOT / "src"))

PREFIX = pathlib.Path(os.environ.get("MT5_WINEPREFIX") or (pathlib.Path.home() / "Wine"))
_C = PREFIX / "drive_c"
EXPERT_LOGS = sorted(
    {
        f
        for pat in (
            "Program Files/MetaTrader 5/MQL5/Logs/*.log",
            "Program Files/MetaTrader 5/logs/*.log",
            "users/*/AppData/Roaming/MetaQuotes/Terminal/*/MQL5/Logs/*.log",
            "users/*/AppData/Roaming/MetaQuotes/Terminal/*/logs/*.log",
        )
        for f in _C.glob(pat)
    },
    key=lambda f: f.stat().st_mtime,
)
STATUS_FILES = [p for p in [ROOT / "var" / "state" / "mcp_status.json", ROOT / "var" / "mcp_status.json"] if p.exists()]


def port_open(host="127.0.0.1", port=8787) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def read_log(path):
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace").splitlines()
    if b"\x00" in raw[:400]:
        return raw.decode("utf-16-le", errors="replace").splitlines()
    return raw.decode("utf-8", errors="replace").splitlines()


def ea_log_tail(n=2):
    """آخرین خطوط سمت متاتریدر؛ اول فایلی که واقعاً خط trade-mcp دارد (نه Journal)."""
    if not EXPERT_LOGS:
        return []
    best = None
    for f in EXPERT_LOGS:
        try:
            lines = read_log(f)
        except OSError:
            continue
        keep = [ln for ln in lines if "trade-mcp" in ln.lower() or "TradeMCPConnector" in ln]
        if keep:
            best = (f.stat().st_mtime, keep)
    if best:
        return best[1][-n:]
    try:
        lines = read_log(EXPERT_LOGS[-1])
    except OSError:
        return []
    return [f"(no trade-mcp line) {ln}" for ln in lines[-1:]]


def terminal_procs():
    try:
        out = subprocess.run(["pgrep", "-af", "terminal64.exe"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln for ln in out.splitlines() if "pgrep" not in ln]


def mt5_context():
    procs = terminal_procs()
    lines = [f"MT5 process: {procs[0][:90]}" if procs else
             "MT5 process: not running (terminal64.exe پیدا نشد)"]
    if EXPERT_LOGS:
        try:
            age = int(time.time() - EXPERT_LOGS[-1].stat().st_mtime)
        except OSError:
            age = -1
        lines.append(f"Experts log: {EXPERT_LOGS[-1].name} (last write {age}s ago)")
    else:
        lines.append(f"Experts log: not found under {PREFIX}/drive_c/.../MQL5/Logs")
    return lines


def port_owner():
    """چه پروسه‌ای پورت را گرفته؟ تا کاربر بداند نسخهٔ دوم چرا بالا نمی‌آید."""
    for cmd in (["ss", "-ltnp"], ["ss", "-ltn"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            if ":8787" in line:
                m = re.search(r"\(\(\"([^\"]+)\",pid=(\d+)", line)
                return f"{m.group(1)} pid {m.group(2)}" if m else line.split()[-1]
    return None


def service_status():
    newest, data = 0.0, None
    for path in STATUS_FILES:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            m = path.stat().st_mtime
        except Exception:
            continue
        if m > newest:
            newest, data = m, d
    return data if data and time.time() - newest < 900 else None


client = None
if port_open():
    print("  [watch] پورت ۸۷۸۷ باز است - صاحبش را (سرویس حلقه) می‌پرسم، نسخهٔ دوم نمی‌سازم")
else:
    from tradeagent.mcp_client import MCPClient
    client = MCPClient("trade-mcp serve", timeout_s=25)
    if not client.start():
        print("  [watch] سرور MCP بالا نیامد (نصب trade-mcp را چک کن)")
        sys.exit(1)
    print("  [watch] سرور MCP بالا آمد و تا Ctrl-C باز می‌ماند")

connected_reported = False
last_mt5 = None
try:
    for attempt in range(1, 721):
        status = None
        if client is not None:
            try:
                status = client.call_tool("mt5_status")
            except Exception as exc:
                status = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        else:
            status = service_status()
            if status is None:
                owner = port_owner()
                status = {
                    "ok": False,
                    "error": f"8787 held by {owner or 'unknown process'} but no status file "
                             f"(var/state/mcp_status.json) - that owner is the bridge; "
                             f"restart the gold service or stop the owner",
                }

        status = status or {}
        bridge = status.get("bridge") or {}
        ea = bool(bridge.get("ea_connected"))
        listen = bridge.get("listen") or "?"
        err = status.get("error")

        if ea and not connected_reported:
            term = status.get("terminal") or {}
            acc = status.get("account") or {}
            hello = bridge.get("ea_hello") or {}
            print(f"\n  ✓ وصل شد!  listen={listen}  peer={bridge.get('ea_peer')}")
            print(f"    {term.get('company', '?')} build {term.get('build', '?')} · account "
                  f"{acc.get('login')} ({acc.get('server')}) balance={acc.get('balance')} "
                  f"equity={acc.get('equity')}")
            if hello:
                print(f"    EA: {json.dumps(hello, ensure_ascii=False)[:200]}")
            if client is not None:
                try:
                    snap = client.call_tool("market_snapshot", {"symbol": SYMBOL})
                    q = (snap or {}).get("quote") or {}
                    print(f"    {SYMBOL}: bid={q.get('bid')} ask={q.get('ask')} "
                          f"spread={(snap or {}).get('spread_points')} points")
                    an = client.call_tool("analyze_market", {"symbol": SYMBOL})
                    print("    --- analyze_market ---")
                    print("    " + json.dumps(an, ensure_ascii=False)[:1200])
                except Exception as exc:
                    print("    تحلیل نشد:", exc)
            else:
                print("    (تحلیل را خودِ سرویس در چرخهٔ بعد با همین ابزارها انجام می‌دهد)")
            print("\n  سوکت باز می‌ماند؛ Ctrl-C برای توقف.")
            connected_reported = True
        elif connected_reported:
            if not ea:
                print(f"\n  ! اکسپرت قطع شد (peer={bridge.get('ea_peer')}) - منتظر وصل شدن دوباره…")
                connected_reported = False
            elif attempt % 6 == 0:
                print(f"  [ok] هنوز وصل است · peer={bridge.get('ea_peer')} · "
                      f"{bridge.get('connected_for_seconds')}s")
        else:
            socket_note = f"8787={'open' if port_open() else 'refused'}"
            print(f"  [{attempt:02d}] ea=no listen={listen} {socket_note} · {err}")
            if attempt == 1 or attempt % 12 == 0:
                for line in mt5_context():
                    print(f"      {line}")
            global_mt5 = ea_log_tail(1)
            if global_mt5 and global_mt5[0] != last_mt5:
                last_mt5 = global_mt5[0]
                print(f"      MT5: {last_mt5[:160]}")
        time.sleep(5)
finally:
    if client is not None:
        client.close()
PY
  exit 0
fi

step "۱) ریپوی trade-mcp"
if [[ ! -d "$REPO/.git" ]]; then
  if [[ "$MODE" == "check" ]]; then bad "نصب نیست: $REPO (بدون --check دوباره بزن)"; exit 1; fi
  git clone -q "${TRADE_MCP_URL:-https://github.com/enigmafarsco-lang/trade-mcp.git}" "$REPO" \
    || { bad "clone failed"; exit 1; }
fi
if [[ "$MODE" == "apply" ]] && git -C "$REPO" pull -q --ff-only 2>/dev/null; then
  ok "به‌روز شد ($(git -C "$REPO" rev-parse --short HEAD))"
else
  dim "HEAD: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null)"
fi
"$PY" -c "import trade_mcp" 2>/dev/null || {
  [[ "$MODE" == "check" ]] && { bad "پکیج نصب نیست: $PY -m pip install -e $REPO"; exit 1; }
  "$PY" -m pip install -q -e "$REPO" && ok "پکیج نصب شد"
}

step "۲) پیدا کردن پوشهٔ متاتریدر"
EXPERTS="$(find "$PREFIX/drive_c" -maxdepth 9 -type d -name Experts -path '*MQL5*' 2>/dev/null | head -n1)"
if [[ -z "$EXPERTS" ]]; then
  bad "پوشهٔ MQL5/Experts زیر $PREFIX پیدا نشد"
  dim "اگر متاتریدر جای دیگری است: MT5_WINEPREFIX=/path ./scripts/connect_trade_mcp.sh"
  exit 1
fi
dim "experts: $EXPERTS"

step "۳) نصب اکسپرت"
SRC="$REPO/mt5-connector/TradeMCPConnector.mq5"
[[ -f "$SRC" ]] || { bad "فایل اکسپرت در ریپو نیست: $SRC"; exit 1; }
DST="$EXPERTS/TradeMCPConnector.mq5"
if [[ "$MODE" == "apply" ]]; then
  cp -f "$SRC" "$DST" && ok "کپی شد: $DST"
else
  if cmp -s "$SRC" "$DST"; then ok "نسخهٔ نصب‌شده با ریپو یکی است"; else warn "نسخهٔ نصب‌شده قدیمی است (بدون --check به‌روز می‌شود)"; fi
fi

# اکسپرتِ ریپوی trade-mcp با MQL5 کامپایل نمی‌شود (LongToString تعریف نشده)؛
# همین‌جا اصلاحش می‌کنیم تا کاربر با خطای کامپایل روبه‌رو نشود.
if [[ "$MODE" == "apply" ]]; then
  step "۳.۵) اصلاح و کامپایل اکسپرت"
  MT5_WINEPREFIX="$PREFIX" TRADE_MCP_DIR="$REPO" "$ROOT/scripts/patch_trade_mcp_connector.sh" \
    || warn "اصلاح/کامپایل کامل نشد - لاگ بالا را ببین"
fi

step "۴) سوکت بریج"
"$PY" - <<'PY'
import socket
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect(("127.0.0.1", 8787))
    print("  bridge: port 8787 is listening")
except OSError:
    print("  bridge: nothing listening on 8787 (expected before MT5 connects)")
finally:
    s.close()
PY

step "۵) سنجش از سمت MCP (متاتریدر)"
"$PY" - <<PY
import sys
sys.path.insert(0, "$ROOT/src")
from tradeagent.mcp_client import MCPClient
c = MCPClient("trade-mcp serve", timeout_s=30)
if not c.start():
    print("  MCP server did not start"); raise SystemExit(1)
st = c.call_tool("mt5_status")
if isinstance(st, dict) and st.get("ok"):
    term = st.get("terminal") or {}
    acc = st.get("account") or {}
    print(f"  connected: {term.get('company', '?')} | build {term.get('build', '?')} | trade_allowed={term.get('trade_allowed')}")
    print(f"  account  : {acc.get('login')} {acc.get('server')} balance={acc.get('balance')} {acc.get('currency')}")
    print(f"  backend  : {(st.get('config') or {}).get('backend')}")
    try:
        sym = c.call_tool("find_symbol", {"query": "$SYMBOL", "limit": 1})
        print("  symbol   :", str(sym)[:160])
        snap = c.call_tool("market_snapshot", {"symbol": "$SYMBOL"})
        q = (snap or {}).get("quote") or {}
        print(f"  quote    : bid={q.get('bid')} ask={q.get('ask')} spread={ (snap or {}).get('spread_points') }")
    except Exception as exc:
        print("  symbol check failed:", exc)
    print("RESULT=CONNECTED")
else:
    err = (st or {}).get("error") if isinstance(st, dict) else st
    print("  not connected:", err)
    print("  hint:", (st or {}).get("hint") if isinstance(st, dict) else "")
    print("RESULT=NOT_CONNECTED")
c.close()
PY
