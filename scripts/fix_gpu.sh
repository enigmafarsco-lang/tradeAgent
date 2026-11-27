#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# یک دستور برای GPU: به ترتیب تلاش می‌کند و آخرش می‌گوید چه شد.
#
#   ./scripts/fix_gpu.sh              # NVIDIA را آزاد کن، وگرنه Vulkan؛ بعد اثبات
#   ./scripts/fix_gpu.sh --debug      # فقط بگو ollama چه دستگاه‌هایی می‌بیند
#   ./scripts/fix_gpu.sh --vulkan     # فقط مسیر Vulkan (Intel/AMD)
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODE="all"
for a in "$@"; do
  case "$a" in
    --debug) MODE="debug" ;;
    --vulkan) MODE="vulkan" ;;
    -h|--help) sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$a"; exit 2 ;;
  esac
done

ok()   { printf '\033[1;32m[gpu]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[gpu]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[gpu]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[gpu] %s\033[0m\n' "$*"; }
step() { printf '\n\033[1;35m=== %s\033[0m\n' "$*"; }

OURL="${TRADEAGENT_OLLAMA_URL:-http://127.0.0.1:11434}"
vram_now() {
  curl -s --max-time 5 "$OURL/api/ps" 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print(0); raise SystemExit
tot=0
for m in d.get("models",[]): tot += int(m.get("size_vram") or 0)
print(tot)
' 2>/dev/null | head -n1
}

ollama_ready() { curl -s --max-time 4 "$OURL/api/tags" 2>/dev/null | grep -q '"models"'; }

restart_ollama() {
  if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
    systemctl --user restart ollama.service 2>/dev/null && dim "ollama.service restarted"
  else
    pkill -f 'ollama serve' 2>/dev/null; sleep 1
    (nohup ollama serve >/tmp/ollama.log 2>&1 &)
    dim "started ollama serve in the background"
  fi
  for _ in $(seq 1 45); do ollama_ready && return 0; sleep 1; done
  return 1
}

load_model() {
  local m; m="$(grep -s '^TRADEAGENT_OLLAMA_MODEL=' "$ROOT/.env" 2>/dev/null | cut -d= -f2-)"
  m="${m:-qwen2.5:7b-instruct}"
  curl -s --max-time 240 -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"model": sys.argv[1], "stream": False, "keep_alive": "15m", "options": {"num_predict": 8}, "messages": [{"role": "user", "content": "say ok"}]}))' "$m")" \
    "$OURL/api/chat" >/dev/null 2>&1
  sleep 2
}

proof() {
  local v; v="$(vram_now)"
  if [[ "${v:-0}" -gt 0 ]]; then
    ok "روی GPU است: $(awk -v x="$v" 'BEGIN{printf "%.1f", x/1e9}') GB در VRAM"
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------------------
# تشخیص: ollama چه دستگاه‌هایی می‌بیند؟ (سرویس موقت روی پورت دیگر)
# ---------------------------------------------------------------------------
ollama_discovery() {
  step "ollama چه چیزی می‌بیند"
  if ! command -v ollama >/dev/null 2>&1; then
    bad "ollama روی PATH نیست"; return 1
  fi
  local log="/tmp/ollama-debug-$$.log" port=11577
  ( OLLAMA_DEBUG=1 OLLAMA_HOST="127.0.0.1:$port" ollama serve >"$log" 2>&1 & echo $! > /tmp/ollama-debug.pid )
  local i
  for i in $(seq 1 20); do
    grep -qi 'inference compute\|listening on' "$log" 2>/dev/null && break
    sleep 1
  done
  sleep 2
  kill "$(cat /tmp/ollama-debug.pid 2>/dev/null)" 2>/dev/null
  rm -f /tmp/ollama-debug.pid

  local lines
  lines="$(grep -iE 'inference compute|vulkan|cuda|rocm|no compatible|library=' "$log" 2>/dev/null | head -20)"
  if [[ -z "$lines" ]]; then
    warn "هیچ خط تشخیصی در لاگ نبود - خروجی خام:"
    tail -8 "$log" | sed 's/^/    /'
    return 1
  fi
  printf '%s\n' "$lines" | sed 's/^/    /'
  echo
  if printf '%s' "$lines" | grep -qi 'library=vulkan'; then
    ok "ollama دستگاه Vulkan را می‌بیند"
  elif printf '%s' "$lines" | grep -qi 'library=cuda'; then
    ok "ollama دستگاه CUDA را می‌بیند"
  else
    warn "ollama فقط CPU را می‌بیند (بالا را ببین) - این جملهٔ علت است"
  fi
  dim "لاگ کامل: $log"
}

# ---------------------------------------------------------------------------
# فاز ۱: NVIDIA
# ---------------------------------------------------------------------------
phase_nvidia() {
  step "۱) NVIDIA"
  if [[ -r /proc/driver/nvidia/version ]]; then
    ok "ماژول nvidia بار است: $(head -n1 /proc/driver/nvidia/version)"
    return 0
  fi
  local drv
  drv="$(lspci -nnk 2>/dev/null | awk '/NVIDIA/{f=1} f&&/Kernel driver in use/{print $NF; exit}')"
  echo "  driver on the card: ${drv:-<none>}"
  case "$drv" in
    vfio*)
      warn "vfio-pci کارت را گرفته - آزادش می‌کنم (پشتیبان می‌گیرم)"
      "$ROOT/scripts/fix_nvidia.sh" --free-vfio || true ;;
    nouveau)
      warn "nouveau کارت را گرفته - بلک‌لیست می‌کنم"
      "$ROOT/scripts/fix_nvidia.sh" --nouveau-off || true ;;
    *)
      sudo -v 2>/dev/null || true
      sudo modprobe nvidia 2>&1 | tail -2 ;;
  esac
  [[ -r /proc/driver/nvidia/version ]]
}

# ---------------------------------------------------------------------------
# فاز ۲: Vulkan (Intel/AMD)
# ---------------------------------------------------------------------------
phase_vulkan() {
  step "۲) Vulkan (Intel Arc / AMD)"
  if command -v vulkaninfo >/dev/null 2>&1; then
    local devs
    devs="$(vulkaninfo --summary 2>/dev/null | grep -i 'deviceName' | sed 's/.*= *//' | sort -u | tr '\n' ';')"
    echo "  devices: ${devs:-none}"
  else
    dim "vulkaninfo نیست (sudo apt install vulkan-tools)"
  fi
  local unit="$HOME/.config/systemd/user/ollama.service"
  if [[ -f "$unit" ]]; then
    grep -q '^Environment=OLLAMA_VULKAN=1' "$unit" || "$ROOT/scripts/setup_llm.sh" --gpu-vulkan >/dev/null 2>&1 || true
  else
    "$ROOT/scripts/setup_llm.sh" --gpu-vulkan >/dev/null 2>&1 || true
  fi
  restart_ollama || { bad "سرور ollama بالا نیامد"; return 1; }
  load_model
  if proof; then return 0; fi
  warn "با Vulkan هم روی GPU نرفت"
  return 1
}

# ---------------------------------------------------------------------------
if [[ "$MODE" == "debug" ]]; then
  ollama_discovery
  exit $?
fi

step "۰) وضعیت فعلی"
echo "  vram in use: $(vram_now) bytes"
ollama_ready && echo "  ollama api : $OURL پاسخ می‌دهد" || warn "ollama api پاسخ نمی‌دهد"

HAVE_NVIDIA=0
if [[ "$MODE" != "vulkan" ]] && phase_nvidia; then HAVE_NVIDIA=1; fi

if (( HAVE_NVIDIA )); then
  step "۳) اثبات با NVIDIA"
  restart_ollama && load_model
  if proof; then
    ok "تمام. کارت NVIDIA کار می‌کند."
    exit 0
  fi
  warn "ماژول بار است ولی ollama روی GPU نرفت - تشخیص:"
  ollama_discovery
  exit 2
fi

if phase_vulkan; then
  ok "تمام. روی GPU (Vulkan) کار می‌کند."
  exit 0
fi

step "نتیجه"
ollama_discovery
cat <<'EOF'

[gpu] هیچ‌کدام از دو مسیر بدون یک اقدام سیستمی جواب نداد. دلیلش بالاست:
      * اگر ollama می‌گوید فقط cpu → نه NVIDIA بار شده و نه Vulkan سالم است.
      * NVIDIA: ./scripts/fix_nvidia.sh --free-vfio سپس sudo reboot
      * Vulkan: sudo apt install --reinstall mesa-vulkan-drivers
      * در هر حال مدل روی CPU هم کار می‌کند (همان ۲۲ رشته) و سیستم متوقف نمی‌شود.
EOF
exit 1
