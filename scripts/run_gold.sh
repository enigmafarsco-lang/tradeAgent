#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# اجرای ایجنت روی طلا به‌صورت سرویس (تک‌نمادی، MCP، مدل محلی)
#
#   ./scripts/run_gold.sh            # سرویس طلا را نصب/فعال کن (جای سرویس عمومی)
#   ./scripts/run_gold.sh --status   # چه چیزی الان در حال اجراست
#   ./scripts/run_gold.sh --restart  # بعد از git pull: کد تازه را بارگذاری کن
#   ./scripts/run_gold.sh --revert   # برگرد به سرویس عمومی (configs/local.toml)
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"
INTERVAL="${INTERVAL:-300}"
MODE="enable"
case "${1:-}" in
  --status) MODE="status" ;;
  --restart) MODE="restart" ;;
  --revert) MODE="revert" ;;
  -h|--help) sed -n '3,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

ok()   { printf '\033[1;32m[gold]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[gold]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[gold]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[gold] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

# حلقه وضعیت پل را در state_dir می‌نویسد؛ state_dir پیش‌فرض «var» است، پس هر دو مسیر را ببین
status_file() {
  local best=0 f m newest=""
  for f in var/mcp_status.json var/state/mcp_status.json; do
    [[ -f "$f" ]] || continue
    m="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
    if (( m > best )); then best="$m"; newest="$f"; fi
  done
  printf '%s' "$newest"
}

mt5_report() {
  step "۵) متاتریدر"
  "$PY" -c 'from tradeagent.diag import report; report()' 2>/dev/null \
    || dim "خواندن وضعیت متاتریدر نشد (نصب editable؟)"
}

show_bridge() {
  local f; f="$(status_file)"
  if [[ -z "$f" ]]; then
    dim "وضعیت پل MCP هنوز منتشر نشده ($f)"
    return 0
  fi
  STATUS_FILE="$f" python3 - <<'PY' 2>/dev/null || dim "خواندن وضعیت پل نشد"
import json, os, pathlib, time
p = pathlib.Path(os.environ["STATUS_FILE"])
d = json.loads(p.read_text(encoding="utf-8"))
age = int(time.time() - p.stat().st_mtime)
print(f"  (وضعیت {age}s پیش نوشته شده)")
b = d.get("bridge") or {}
a = d.get("account") or {}
t = d.get("terminal") or {}
sock = d.get("socket_open")
sock_txt = {True: "open", False: "CLOSED", None: "?"}[sock]
if not b.get("listen") and d.get("error"):
    print(f"  ! mt5_status چیزی برنگرداند: {str(d.get('error'))[:120]}")
print(f"  bridge={b.get('listen')} socket={sock_txt} ea_connected={b.get('ea_connected')} "
      f"peer={b.get('ea_peer')} since={b.get('connected_for_seconds')}s")
if a:
    print(f"  account={a.get('login')} server={a.get('server')} "
          f"balance={a.get('balance')} equity={a.get('equity')}")
if t.get("company"):
    print(f"  terminal={t.get('company')} build={t.get('build')}")
# لاگ را همین‌جا و تازه بخوان؛ نسخهٔ داخل فایل وضعیت ممکن است ۵ دقیقه کهنه باشد
try:
    from tradeagent.diag import ea_log as _fresh_ea_log

    ea = _fresh_ea_log()
except Exception:
    ea = d.get("ea_log") or {}
if ea.get("found"):
    if ea.get("not_allowed"):
        tag = "blocked(4014)"
    elif ea.get("refused"):
        tag = "refused(5272)"
    elif ea.get("connected"):
        tag = "connected"
    else:
        tag = "no-line"
    print(f"  EA log: {tag} · {ea.get('age_s')}s پیش · {pathlib.Path(ea.get('file','')).name}")
    for line in (ea.get("last") or [])[-1:]:
        print(f"    {line[:150]}")
else:
    print("  EA log: پیدا نشد (MT5_WINEPREFIX را چک کن)")

mt5 = d.get("mt5") or {}
if sock is False:
    print("  ! هیچ پروسه‌ای روی این آدرس گوش نمی‌دهد - مشکل پل، نه اکسپرت")
elif sock is None:
    print("  وضعیت پل ناقص است (bridge در خروجی mt5_status نبود)؛ چند ثانیه بعد دوباره ببین")
elif ea.get("not_allowed"):
    print("  ! err=4014 یعنی «Function is not allowed for call» - متاتریدر آدرس سوکت را مجاز نمی‌داند")
    print("    Tools -> Options -> Expert Advisors -> «Allow WebRequest for listed URL»")
    print("    و اضافه کن: 127.0.0.1  (اگر نپذیرفت: http://127.0.0.1 )")
elif not b.get("ea_connected"):
    same = mt5.get("same_netns")
    if same is False:
        print("  ! متاتریدر در network namespace دیگری است؛ loopback مشترک نیست.")
        print("    راه‌حل: در configs/gold.toml زیر [mcp] این را اضافه کن:  bridge_host = \"0.0.0.0\"")
        print("    و در تنظیمات اکسپرت InpHost را IP همین کامپیوتر بگذار (hostname -I).")
    elif mt5.get("pids"):
        print("  سوکت باز است و متاتریدر هم همان namespace است، ولی اکسپرت جواب نمی‌دهد:")
        print("    در متاتریدر TradeMCPConnector را روی چارت XAUUSD_i بگذار و Algo Trading را روشن کن")
    else:
        print("  اکسپرت وصل نیست: TradeMCPConnector را روی چارت XAUUSD_i بگذار و Algo Trading را روشن کن")
PY
}

DIR="$HOME/.config/systemd/user"
GEN="$DIR/tradeagent-loop.service"
GOLD="$DIR/tradeagent-gold.service"

status() {
  step "۱) سرویس‌ها"
  for unit in tradeagent-loop tradeagent-gold; do
    if systemctl --user list-unit-files 2>/dev/null | grep -q "^$unit\.service"; then
      printf '  %-22s %s\n' "$unit" "$(systemctl --user is-active "$unit".service 2>/dev/null)/$(systemctl --user is-enabled "$unit".service 2>/dev/null)"
    else
      printf '  %-22s not installed\n' "$unit"
    fi
  done
  step "۲) قفل چرخه"
  if [[ -f var/loop.lock ]]; then
    dim "var/loop.lock → $(cat var/loop.lock)"
    PID="$(awk '{print $1}' var/loop.lock)"
    if kill -0 "$PID" 2>/dev/null; then ok "صاحب قفل (pid $PID) زنده است"
    else warn "صاحب قفل مرده است (pid $PID) - اجرای بعدی خودش پاک می‌کند"; fi
  else
    ok "قفلی نیست (چیزی در حال اجرا نیست)"
  fi
  step "۳) پل MCP"
  show_bridge
  mt5_report
  step "۶) آخرین چرخه"
  if [[ -f var/reports/latest.md ]]; then
    grep -m 6 -E "momentum|جهت|فاز|شتاب|confidence|اطمینان" var/reports/latest.md \
      | sed 's/^/  /' || head -3 var/reports/latest.md | sed 's/^/  /'
  else
    dim "گزارشی نیست"
  fi
}

ensure_env() {
  [[ -f .env ]] || touch .env
  grep -q '^TRADEAGENT_LLM_PROVIDER=' .env || echo 'TRADEAGENT_LLM_PROVIDER=ollama' >> .env
  grep -q '^TRADEAGENT_OLLAMA_MODEL=' .env || echo 'TRADEAGENT_OLLAMA_MODEL=qwen2.5:7b-instruct' >> .env
  grep -q '^TRADEAGENT_OLLAMA_URL=' .env || echo 'TRADEAGENT_OLLAMA_URL=http://127.0.0.1:11434' >> .env
  dim "provider=ollama (روی GPU محلی)"
}

if [[ "$MODE" == "restart" ]]; then
  step "بارگذاری کد تازه"
  systemctl --user restart tradeagent-gold.service 2>/dev/null || {
    bad "سرویس نصب/فعال نیست؛ اول ./scripts/run_gold.sh را بزن"
    exit 1
  }
  ok "tradeagent-gold.service ری‌استارت شد (پل و اکسپرت از نو وصل می‌شوند)"
  START_STAMP="$(date +%s)"
  for _ in $(seq 1 12); do
    sleep 5
    systemctl --user is-active tradeagent-gold.service >/dev/null 2>&1 || { bad "سرویس بالا نیامد"; exit 1; }
    F="$(status_file)"
    if [[ -n "$F" ]] && (( $(stat -c %Y "$F" 2>/dev/null || echo 0) >= START_STAMP - 5 )); then
      ok "کد تازه در حال اجراست"
      show_bridge
      exit 0
    fi
  done
  warn "ری‌استارت شد ولی وضعیت پل هنوز تازه نشده (چرخهٔ اول شاید مشغول مدل است)"
  exit 0
fi

if [[ "$MODE" == "status" ]]; then
  status
  exit 0
fi

if [[ "$MODE" == "revert" ]]; then
  step "برگشت به سرویس عمومی"
  systemctl --user disable --now tradeagent-gold.service 2>/dev/null || true
  rm -f "$GOLD"
  systemctl --user daemon-reload 2>/dev/null || true
  if systemctl --user enable --now tradeagent-loop.service 2>/dev/null; then
    ok "tradeagent-loop.service دوباره فعال شد"
  else
    warn "سرویس عمومی نصب نیست؛ با ./scripts/install_systemd_user.sh بسازش"
  fi
  exit 0
fi

step "۱) آماده‌سازی"
ensure_env
[[ -f configs/gold.toml ]] || { bad "configs/gold.toml نیست"; exit 1; }
ok "پروفایل: configs/gold.toml (XAUUSD_i، MCP، هر چرخه مدل)"

command -v systemctl >/dev/null 2>&1 || { bad "systemctl نیست"; exit 1; }
if ! systemctl --user show-environment >/dev/null 2>&1; then
  bad "این شل به systemd کاربری وصل نیست؛ در ترمینال دسکتاپ اجرا کن"
  exit 1
fi

step "۲) قطع نمونه‌های قبلی (تا دو نمونه هم‌زمان نباشند)"
systemctl --user is-active tradeagent-loop.service >/dev/null 2>&1 \
  && systemctl --user disable --now tradeagent-loop.service 2>/dev/null \
  && ok "tradeagent-loop.service خوابید" \
  || dim "سرویس عمومی فعال نبود"

# نمونه‌های دستی را با الگوی خودِ پروسه پیدا کن، نه فقط با فایل قفل.
# الگو لنگر دارد (^) تا شل خودمان یا دستورهای دیگر که این مسیر را در متن دارند نگیرد.
mapfile -t strays < <(
  {
    pgrep -f "^$ROOT/.venv/bin/tradeagent" 2>/dev/null || true
    pgrep -f "^$ROOT/.venv/bin/python.*-m tradeagent" 2>/dev/null || true
  } | grep -v -x -e "$$" -e "$PPID" | sort -u
)
if (( ${#strays[@]} )); then
  warn "نمونهٔ در حال اجرا: ${strays[*]} - SIGTERM می‌فرستم (تا پایان چرخهٔ جاری)"
  kill "${strays[@]}" 2>/dev/null || true
  for _ in $(seq 1 20); do              # حداکثر ۲۰ ثانیه
    alive=()
    for pid in "${strays[@]}"; do kill -0 "$pid" 2>/dev/null && alive+=("$pid"); done
    (( ${#alive[@]} )) || break
    sleep 1
  done
  if (( ${#alive[@]} )); then
    bad "این نمونه‌ها هنوز زنده‌اند: ${alive[*]}"
    bad "یک چرخهٔ مدل ممکن است طول بکشد؛ چند لحظه بعد همین دستور را دوباره بزن."
    exit 1
  fi
  ok "همه بسته شدند"
else
  dim "نمونهٔ دیگری در حال اجرا نبود"
fi

# فایل قفل فقط وقتی پاک شود که صاحبش واقعاً مرده باشد (وگرنه دو نمونه هم‌زمان می‌شوند)
if [[ -f var/loop.lock ]]; then
  PID="$(awk '{print $1}' var/loop.lock 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    warn "قفل دست pid $PID است و زنده است؛ صبر می‌کنم تا خودش برود"
    exit 1
  fi
  rm -f var/loop.lock
fi

step "۳) نصب سرویس طلا"
mkdir -p "$DIR"
cat > "$GOLD" <<UNIT
[Unit]
Description=tradeAgent gold agent (XAUUSD, MCP tools)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/tradeagent --config $ROOT/configs/gold.toml loop --interval $INTERVAL
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload 2>/dev/null
systemctl --user enable --now tradeagent-gold.service 2>/dev/null || { bad "فعال نشد"; exit 1; }
ok "tradeagent-gold.service فعال شد (هر ${INTERVAL}s)"

step "۴) بررسی آمادگی (تا ۶۰ ثانیه)"
START_STAMP="$(date +%s)"
READY=0
for _ in $(seq 1 12); do
  sleep 5
  systemctl --user is-active tradeagent-gold.service >/dev/null 2>&1 || { READY=-1; break; }
  F="$(status_file)"
  if [[ -n "$F" ]]; then
    MTIME="$(stat -c %Y "$F" 2>/dev/null || echo 0)"
    (( MTIME >= START_STAMP - 5 )) && { READY=1; break; }
  fi
done

case "$READY" in
  1)
    ok "سرویس در حال اجراست و وضعیت پل MCP را منتشر کرد"
    show_bridge
    ;;
  0)
    warn "سرویس فعال است ولی وضعیت پل MCP هنوز منتشر نشد (شاید چرخهٔ اول مشغول مدل است)"
    ;;
  *)
    bad "سرویس بالا نیامد - دلیل در لاگ زیر است"
    ;;
esac
journalctl --user -u tradeagent-gold.service -n 12 --no-pager 2>/dev/null | sed 's/^/  /'

step "گام بعدی"
cat <<'EOF'
  * لاگ زنده:    journalctl --user -u tradeagent-gold.service -f
  * گزارش‌ها:    git -C ~/Documents/GitHub/tradeAgent log origin/reports --oneline | head -3
  * وضعیت:       ./scripts/run_gold.sh --status
  * برگشت:       ./scripts/run_gold.sh --revert
  * اتصال MT5:   ./scripts/connect_trade_mcp.sh        (کشیدن اکسپرت روی چارت طلا)
EOF
