#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# یک دستور برای GPU: به ترتیب تلاش می‌کند و آخرش می‌گوید چه شد.
#
#   ./scripts/fix_gpu.sh              # NVIDIA را آزاد کن، وگرنه Vulkan؛ بعد اثبات
#   ./scripts/fix_gpu.sh --debug      # فقط بگو ollama چه دستگاه‌هایی می‌بیند
#   ./scripts/fix_gpu.sh --vulkan     # فقط مسیر Vulkan (Intel/AMD)
#   ./scripts/fix_gpu.sh --speed      # فقط سرعت فعلی (tokens/s)
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODE="all"
for a in "$@"; do
  case "$a" in
    --debug) MODE="debug" ;;
    --vulkan) MODE="vulkan" ;;
    --speed) MODE="speed" ;;
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

backend_name() {
  if [[ -r /proc/driver/nvidia/version ]]; then printf 'NVIDIA (CUDA)'
  else printf 'Vulkan (Intel/AMD)'; fi
}

proof() {
  local v; v="$(vram_now)"
  if [[ "${v:-0}" -gt 0 ]]; then
    ok "روی GPU است: $(awk -v x="$v" 'BEGIN{printf "%.1f", x/1e9}') GB در VRAM — بک‌اند: $(backend_name)"
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
# CUDA: تنظیم متناسب با حافظهٔ کارت + سنجش سرعت واقعی
# ---------------------------------------------------------------------------
tune_cuda() {
  step "۳) تنظیم CUDA و سنجش سرعت"
  local mib unit="$HOME/.config/systemd/user/ollama.service"
  if command -v nvidia-smi >/dev/null 2>&1; then
    mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')"
    command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/  card: /'
  fi
  mib="${mib:-0}"
  if [[ "$mib" =~ ^[0-9]+$ ]] && (( mib > 0 && mib < 8192 )); then
    if [[ -f "$unit" ]] && ! grep -q '^Environment=OLLAMA_CONTEXT_LENGTH=' "$unit"; then
      python3 - "$unit" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
out, done = [], False
for ln in p.read_text().splitlines():
    out.append(ln)
    if ln.startswith("ExecStart=") and not done:
        out.append("Environment=OLLAMA_CONTEXT_LENGTH=8192")
        done = True
p.write_text("\n".join(out) + "\n")
PY
      ok "کارت ${mib} MiB است؛ context روی 8192 گذاشته شد تا در VRAM جا شود"
      restart_ollama || true
    else
      dim "context setting: $(grep -s '^Environment=OLLAMA_CONTEXT_LENGTH=' "$unit" | tail -1)"
    fi
  fi
  load_model
  proof || { warn "بعد از ری‌استارت روی GPU نرفت"; return 1; }
  speed_probe
}

speed_probe() {
  local m; m="$(grep -s '^TRADEAGENT_OLLAMA_MODEL=' "$ROOT/.env" 2>/dev/null | cut -d= -f2-)"
  m="${m:-qwen2.5:7b-instruct}"
  printf '  speed: '
  curl -s --max-time 240 -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"model": sys.argv[1], "stream": False, "keep_alive": "15m", "options": {"num_predict": 96}, "prompt": "List three short facts about gold."}))' "$m")" \
    "$OURL/api/generate" 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("no answer"); raise SystemExit
ec, ed = int(d.get("eval_count") or 0), int(d.get("eval_duration") or 0)
if ec and ed: print(f"{ec/(ed/1e9):.1f} tokens/s ({ec} tokens)")
else: print("no timing in the answer")
'
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
  drv="$(lspci -nnk 2>/dev/null | awk 'BEGIN{RS="";FS="\n"}
    /NVIDIA/ && (/\[0300\]/ || /\[0301\]/ || /\[0302\]/ || /VGA compatible controller/ || /3D controller/) {
      for (i=1;i<=NF;i++) if ($i ~ /Kernel driver in use/) { sub(/.*: /,"",$i); print $i; exit }
      print "none"; exit
    }')"
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
if [[ "$MODE" == "speed" ]]; then
  speed_probe
  exit $?
fi
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
    tune_cuda
    ok "تمام. روی GPU است ($(backend_name))."
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
