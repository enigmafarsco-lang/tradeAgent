#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Language model for the trading loop: free Gemini API key, or local Ollama.
# Nothing is ever written to .env unless a real request to the provider succeeds.
#
#   ./scripts/setup_llm.sh --status              what is active right now?
#   ./scripts/setup_llm.sh --gemini              ask for the key (hidden), verify, store
#   ./scripts/setup_llm.sh --diagnose            show the provider's own answer, key masked
#   ./scripts/setup_llm.sh --install-ollama      install ollama (sudo) + pull the model
#   ./scripts/setup_llm.sh --repair-ollama       fix a half-finished install (llama-server missing)
#   ./scripts/setup_llm.sh --hardware            CPU/RAM/GPU and what ollama really uses
#   ./scripts/setup_llm.sh --gpu                 why the GPU is (not) being used
#   ./scripts/setup_llm.sh --gpu-vulkan          use the Intel/AMD GPU through Vulkan
#   ./scripts/setup_llm.sh --ollama [model]      use an ollama that is already installed
#   ./scripts/setup_llm.sh --off                 deterministic decisions only
#
# Free Gemini key: https://aistudio.google.com/apikey   (no card, free tier)
# Any printable key of 20+ chars is accepted and sent to Google as-is: AI Studio
# hands out both 'AIza...' (~39 chars) and the newer 'AQ.Ab8...' (~53) formats, so the
# provider - not this script - decides whether a key is valid.
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
OLLAMA_URL="${TRADEAGENT_OLLAMA_URL:-http://127.0.0.1:11434}"
DEFAULT_OLLAMA="${TRADEAGENT_OLLAMA_MODEL:-qwen2.5:7b-instruct}"
PREFERRED_GEMINI="${TRADEAGENT_LLM_MODEL:-gemini-3.8-flash}"
SLEEP_S="${LLM_SETUP_SLEEP:-3}"
API_BASE="https://generativelanguage.googleapis.com/v1beta"

ok()   { printf '\033[1;32m[llm]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[llm]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[llm]\033[0m %s\n' "$*"; }
dim()  { printf '\033[2m[llm]   %s\033[0m\n' "$*"; }

mask() {
  local k="$1"
  [[ ${#k} -le 12 ]] && { printf '<too short>'; return; }
  printf '%s…%s (%d chars)' "${k:0:6}" "${k: -4}" "${#k}"
}

set_env() {
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"; tmp="$(mktemp)"
  grep -v "^${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"
}
env_get() { grep -s "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | head -n1; }

# پاک‌سازی: CR، فاصله، کوتیشن و بک‌اسلشِ پایانی که از چسباندن می‌آید
sanitize() {
  local k="$1"
  k="${k//$'\r'/}"; k="${k//$'\n'/}"; k="${k// /}"; k="${k//$'\t'/}"
  k="${k%\\}"; k="${k%\"}"; k="${k#\"}"; k="${k%\'}"; k="${k#\'}"
  printf '%s' "$k"
}

not_a_key_reason() {   # چاپ دلیل یا رشته‌ی خالی اگر معتبر به نظر می‌رسد
  local k="$1"
  [[ -z "$k" ]] && { echo "empty"; return; }
  if printf '%s' "$k" | LC_ALL=C grep -qP '[^\x20-\x7e]'; then
    echo "non-ascii"; return; fi
  case "$k" in
    *"..."*|*"…"*|*YOUR*|*your*|*PASTE*|*PLACEHOLDER*|*placeholder*|*EXAMPLE*|*xxx*|*XXX*)
      echo "placeholder"; return ;;
  esac
  if [[ ${#k} -lt 20 ]]; then echo "shape"; return; fi
  echo ""
}

http_get() {   # http_get <url> ; sets CODE / BODY
  local url="$1" out
  out="$(curl -s --max-time 30 -w $'\n%{http_code}' "$url" 2>/dev/null)"
  CODE="${out##*$'\n'}"; BODY="${out%$'\n'*}"
}

http_post() {  # http_post <url> <json>
  local url="$1" data="$2" out
  out="$(curl -s --max-time 30 -w $'\n%{http_code}' -H 'Content-Type: application/json' -d "$data" "$url" 2>/dev/null)"
  CODE="${out##*$'\n'}"; BODY="${out%$'\n'*}"
}

json_field() {  # json_field <key>  → نخستین مقدار رشته‌ای با این نام (stdin = JSON)
  python3 -c '
import json,sys
try: d=json.loads(sys.stdin.read() or "{}")
except Exception: sys.exit()
key=sys.argv[1]
def walk(o):
    if isinstance(o,dict):
        for k,v in o.items():
            if k==key and isinstance(v,(str,int)): yield str(v)
            yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
for v in walk(d): print(v)
' "$1" 2>/dev/null
}

explain() {   # پیام انسانی از پاسخ گوگل
  local reason msg
  reason="$(printf '%s' "$BODY" | json_field reason | head -n1)"
  msg="$(printf '%s' "$BODY" | json_field message | head -n1)"
  case "$reason" in
    API_KEY_INVALID)      echo "the key is not valid (Google: API_KEY_INVALID) - generate a new one";;
    API_KEY_EXPIRED)      echo "the key has expired - generate a new one";;
    API_KEY_SERVICE_BLOCKED) echo "this key is restricted and does not allow the Generative Language API - edit the key in AI Studio and clear the API restrictions";;
    SERVICE_DISABLED)     echo "the Generative Language API is not enabled for that project - enable it, or create the key in AI Studio (not in Cloud Console)";;
    PERMISSION_DENIED)    echo "permission denied - the key belongs to a project without access";;
    "")                   ;;
    *)                    echo "Google reason: $reason";;
  esac
  [[ -n "$msg" ]] && dim "google said: $msg"
  return 0
}

# مدل‌های مجاز برای این کلید، بهترین اول: فلشِ جدیدترین نسخه، بعد بقیه
gemini_candidates() {
  printf '%s' "$BODY" | python3 -c '
import json,sys,re
try: d=json.load(sys.stdin)
except Exception: sys.exit()
want=sys.argv[1]
BANNED=("tts","image","imagen","veo","embed","audio","live","native","vision","aqa","learnlm")
def ok(n):
    return not any(b in n.lower() for b in BANNED)
def ver(n):
    m=re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    return float(m.group(1)) if m else 0.0
ms=[m["name"].split("/")[-1] for m in d.get("models",[])
    if "generateContent" in m.get("supportedGenerationMethods",[])]
ms=[n for n in ms if ok(n)]
def rank(n):
    return (-ver(n), 0 if "flash" in n else 1, n.count("-"), len(n))
ms.sort(key=rank)
out=[]
if want in ms: out.append(want)
for n in ms:
    if n not in out: out.append(n)
print("\n".join(out))
' "$PREFERRED_GEMINI" 2>/dev/null
}

# 0 = works (sets GEMINI_MODEL_OK), 1 = key rejected, 2 = network, 3 = other
gemini_probe() {
  local key="$1" seq=() i=0
  GEMINI_MODEL_OK=""
  if [[ -n "${GEMINI_GEN_SEQ:-}" ]]; then
    IFS=';' read -r -a seq <<< "${GEMINI_GEN_SEQ//;;/;}"
  fi

  if [[ -n "${GEMINI_PROBE_MOCK:-}" ]]; then
    CODE="${GEMINI_PROBE_MOCK%%|*}"; BODY="${GEMINI_PROBE_MOCK#*|}"
  else
    http_get "$API_BASE/models?key=$key"
  fi
  if [[ "${CODE:-000}" != "200" ]]; then
    [[ "$CODE" == "000" ]] && return 2
    [[ "$CODE" =~ ^4 ]] && return 1
    return 3
  fi

  local cands; cands="$(gemini_candidates)"
  [[ -z "$cands" ]] && { BODY='{"error":{"message":"no generateContent model for this key"}}'; return 3; }

  local queue model sug tried_n=0 fails=""
  queue="$(echo $cands)"
  while [[ -n "$queue" && $tried_n -lt 3 ]]; do
    model="${queue%% *}"
    queue="${queue#"$model"}"; queue="${queue# }"
    tried_n=$((tried_n + 1))
    if [[ ${#seq[@]} -gt 0 && $i -lt ${#seq[@]} ]]; then
      CODE="${seq[$i]%%|*}"; BODY="${seq[$i]#*|}"; i=$((i+1))
    elif [[ -n "${GEMINI_GEN_MOCK:-}" ]]; then
      CODE="${GEMINI_GEN_MOCK%%|*}"; BODY="${GEMINI_GEN_MOCK#*|}"
    else
      http_post "$API_BASE/models/$model:generateContent?key=$key" \
        '{"contents":[{"parts":[{"text":"ping"}]}],"generationConfig":{"maxOutputTokens":8}}'
    fi
    if [[ "${CODE:-000}" == "200" ]]; then GEMINI_MODEL_OK="$model"; return 0; fi
    [[ "${CODE:-000}" == "000" ]] && return 2
    dim "model $model -> http ${CODE:-000}"
    fails="$fails$model=${CODE:-000} "

    LAST_CODE="${CODE:-000}"
    case "${CODE:-}" in
      503)   # شلوغی موقت: فقط یک‌بار همان مدل
        dim "overloaded - one retry in ${SLEEP_S}s"
        sleep "$SLEEP_S"
        if [[ -n "${MOCK_RETRY_200:-}" ]]; then
          CODE="200"; BODY='{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'
        else
          http_post "$API_BASE/models/$model:generateContent?key=$key" \
            '{"contents":[{"parts":[{"text":"ping"}]}],"generationConfig":{"maxOutputTokens":8}}'
        fi
        if [[ "${CODE:-000}" == "200" ]]; then GEMINI_MODEL_OK="$model"; return 0; fi
        dim "model $model -> http ${CODE:-000} again" ;;
      429)   # سهمیه: تلاش دوباره بی‌فایده است، مدل بعدی
        dim "quota for $model - moving on" ;;
      400|403)
        return 1 ;;   # کلید مشکل دارد، بقیهٔ مدل‌ها را امتحان نکن
      404)
        sug="$(python3 -c '
import re,sys
t=sys.stdin.read()
if "no longer available" in t.lower() or "use models/" in t.lower():
    for n in re.findall(r"models/([A-Za-z0-9._-]{3,})", t):
        if not n.startswith("gemini-2.5"): print(n); break
' <<< "$BODY")"
        case "$sug" in *tts*|*image*|*audio*|*embed*) sug="" ;; esac
        [[ -n "$sug" ]] && queue="$sug $queue" ;;
    esac
  done

  # هیچ مدلی جواب نداد: دقیقاً بگو چرا
  bad "Google accepted the key but no text model answered: $fails"
  local quota
  quota="$(printf '%s' "$BODY" | json_field quotaId | head -n1)"
  case "${CODE:-}" in
    429) warn "this is a QUOTA answer, not a bad key: the free tier of this project has"
         warn "no room right now for these models${quota:+ ($quota)}"
         echo "    - wait a few minutes; free tier resets per minute and per day"
         echo "    - or check the quota page of that key's project (AI Studio -> Usage)"
         echo "    - or switch to the local model: ./scripts/setup_llm.sh --install-ollama" ;;
    503) warn "this is CAPACITY, not a bad key: Google says the models are in high demand."
         echo "    - retry in a few minutes: ./scripts/setup_llm.sh"
         echo "    - or use the local model meanwhile: ./scripts/setup_llm.sh --install-ollama" ;;
  esac
  echo "    meanwhile the loop keeps running with deterministic decisions."
  case "${LAST_CODE:-}" in
    429) return 4 ;;
    503) return 5 ;;
  esac
  return 3
}

report_failure() {
  bad "$1"
  explain
  dim "http ${CODE:-000}"
  [[ -n "${BODY:-}" ]] && dim "raw: $(printf '%s' "$BODY" | tr -d '\n' | cut -c1-300)"
  return 0
}

info_key_ok() {
  ok "the stored key itself is fine - Google's quota/capacity is the limit right now"
  echo "    the loop keeps running with deterministic decisions; retry later,"
  echo "    or switch to the local model: ./scripts/setup_llm.sh --install-ollama"
}

interactive() {
  local k provider om rc usable=0
  k="$(env_get GEMINI_API_KEY)"
  provider="$(env_get TRADEAGENT_LLM_PROVIDER)"
  om="$(env_get TRADEAGENT_OLLAMA_MODEL)"; om="${om:-$DEFAULT_OLLAMA}"
  [[ -n "$k" ]] && [[ -z "$(not_a_key_reason "$(sanitize "$k")")" ]] && usable=1

  # ۰) اگر انتخاب فعلی ollama است و جواب می‌دهد، همین کافی است
  if [[ "$provider" == "ollama" ]] && command -v ollama >/dev/null; then
    local prc; ollama_probe "$om"; prc=$?
    if (( prc == 0 )); then
      ok "local model works: $om  ->  $OLLAMA_ANS"
      echo "    restart the loop if you changed anything: systemctl --user restart tradeagent-loop.service"
      return 0
    fi
    warn "provider is ollama but $om did not answer: ${OLLAMA_ERR:-unknown}"
    hint_repair
  fi

  # ۱) گوگل
  local gemini_rc=9
  if (( usable )); then
    echo "stored key: $(mask "$(sanitize "$k")")"
    gemini_probe "$(sanitize "$k")"
    gemini_rc=$?
    case $gemini_rc in
      0) ok "works with $GEMINI_MODEL_OK - nothing to do"
         local stored; stored="$(env_get TRADEAGENT_LLM_MODEL)"
         if [[ -n "$stored" && "$stored" != "$GEMINI_MODEL_OK" ]]; then
           set_env TRADEAGENT_LLM_MODEL "$GEMINI_MODEL_OK"
           ok "model updated $stored -> $GEMINI_MODEL_OK"
         fi
         return 0 ;;
      2) warn "network: generativelanguage.googleapis.com is not reachable from here" ;;
      4|5) warn "Google has no quota/capacity for these models right now" ;;
      *) report_failure "the stored key was rejected" ;;
    esac
  fi

  # ۲) اگر گوگل به سهمیه خورد، مدل محلی آماده است؟ خودکار برو سراغش
  if command -v ollama >/dev/null; then
    if ! ollama_up; then
      warn "starting the local ollama server"
      (nohup ollama serve >/tmp/ollama.log 2>&1 &)
      wait_ollama 30 || warn "it did not open $OLLAMA_URL - see /tmp/ollama.log"
    fi
    if ollama_up; then
      # مدلی که در .env است، وگرنه هر مدلی که نصب است
      local try_models
      try_models="$(printf '%s\n' "$om" $(ollama_models) | awk 'NF && !seen[$0]++')"
      for cand in $try_models; do
        [[ -z "$cand" ]] && continue
        ollama_probe "$cand"
        if [[ $? -eq 0 ]]; then
          set_env TRADEAGENT_LLM_PROVIDER ollama
          set_env TRADEAGENT_OLLAMA_URL "$OLLAMA_URL"
          set_env TRADEAGENT_OLLAMA_MODEL "$cand"
          ok "switched to the local model: $cand  ->  $OLLAMA_ANS"
          ok "now: systemctl --user restart tradeagent-loop.service"
          return 0
        fi
      done
      if ollama_models | grep -qx "$om"; then
        set_env TRADEAGENT_LLM_PROVIDER ollama
        set_env TRADEAGENT_OLLAMA_URL "$OLLAMA_URL"
        set_env TRADEAGENT_OLLAMA_MODEL "$om"
        warn "$om is installed but the test call failed (${OLLAMA_ERR:-unknown}) - selecting it anyway"
        dim "the loop falls back to deterministic decisions whenever the model is slow or silent"
        ok "now: systemctl --user restart tradeagent-loop.service"
        return 0
      fi
      warn "no installed model answered"
      hint_repair
      dim "installed: $(ollama_models | tr '\n' ' ')"
    fi
  fi

  # ۳) هیچ‌کدام: بپرس
  echo
  printf '\033[1m[llm] paste your Gemini key and press Enter (hidden): \033[0m'
  local typed
  read -rs typed
  echo

  if [[ -z "$typed" ]]; then
    if command -v ollama >/dev/null; then
      printf '[llm] pull a local model now? [y/N] '
      local a; read -r a
      if [[ "${a:-n}" =~ ^[Yy]$ ]]; then exec "$0" --install-ollama; fi
    else
      printf '[llm] nothing typed. install the local model instead? [y/N] '
      local a; read -r a
      if [[ "${a:-n}" =~ ^[Yy]$ ]]; then exec "$0" --install-ollama; fi
    fi
    warn "left as is: cycles keep running with deterministic decisions"
    return 1
  fi
  do_gemini "$typed"
}

ollama_lib_ok() {
  local bin d
  local candidates=("${OLLAMA_LIB_DIR:-}" /usr/local/lib/ollama /usr/lib/ollama /usr/local/ollama/lib)
  bin="$(command -v ollama 2>/dev/null || true)"
  [[ -n "$bin" ]] && candidates+=("$(cd "$(dirname "$bin")/.." 2>/dev/null && pwd)/lib/ollama")
  for d in "${candidates[@]}"; do
    [[ -n "$d" && -x "$d/llama-server" ]] && return 0
  done
  return 1
}

hint_repair() { case "${OLLAMA_ERR:-}" in *llama-server*binary*not\ found*) dim "this is a half-finished install, not a model problem: run ./scripts/setup_llm.sh --repair-ollama" ;; esac; }

repair_ollama() {
  local arch url tmp attempt
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) bad "unsupported architecture: $arch"; return 1 ;;
  esac
  url="https://github.com/ollama/ollama/releases/latest/download/ollama-linux-${arch}.tar.zst"
  tmp="/tmp/ollama-linux-${arch}.tar.zst"

  if ollama_lib_ok; then
    if ollama_up; then
      ok "runtime present and the server is answering - verifying the model"
    else
      warn "runtime is installed but the server is down - restarting and verifying"
    fi
    ollama_restart_and_verify "${1:-$DEFAULT_OLLAMA}"
    return $?
  fi
  warn "ollama is installed but its inference engine (llama-server) is missing."
  warn "This is what an interrupted download leaves behind. Downloading again (~1.6 GB,"
  warn "resumable: if the line drops, run this same command again)."

  if [[ -n "${REPAIR_DRY_RUN:-}" ]]; then
    dim "dry run: would download $url to $tmp, extract to /usr/local, restart, then test $DEFAULT_OLLAMA"
    return 2
  fi

  for attempt in 1 2 3 4 5; do
    curl -L --fail -C - --retry 5 --retry-all-errors --retry-delay 5 \
         --connect-timeout 20 -o "$tmp" "$url" && break
    warn "download interrupted (attempt $attempt) - resuming in 3s"
    sleep 3
    (( attempt == 5 )) && { bad "could not finish the download; run the command again later"; return 1; }
  done
  ok "downloaded $(du -h "$tmp" 2>/dev/null | cut -f1) to $tmp"

  if command -v zstd >/dev/null; then
    zstd -t "$tmp" >/dev/null 2>&1 || { bad "archive is incomplete - delete $tmp and retry"; return 1; }
    sudo tar --zstd -xf "$tmp" -C /usr/local || { bad "extraction failed"; return 1; }
  else
    warn "zstd is not installed; falling back to the official installer"
    curl -fsSL https://ollama.com/install.sh | sh || { bad "official installer failed"; return 1; }
  fi

  ollama_lib_ok || { bad "extraction finished but llama-server is still missing"; return 1; }
  ok "inference engine installed: $(ollama_lib_ok && echo ok)"

  ollama_restart_and_verify "$( [[ -n "${1:-}" ]] && echo "$1" || echo "$DEFAULT_OLLAMA" )"
  return $?
}

ensure_ollama_unit() {
  # ۰) سرویس سیستمی هست؟ کاری لازم نیست
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
    return 0
  fi
  # ۱) بدون systemd اصلاً نمی‌توان unit ساخت
  if ! command -v systemctl >/dev/null 2>&1; then
    dim "no systemctl here - ollama stays a background process"
    return 3
  fi
  local dir="$HOME/.config/systemd/user" bin
  bin="$(command -v ollama 2>/dev/null)"
  [[ -n "$bin" ]] || return 3
  mkdir -p "$dir"
  cat > "$dir/ollama.service" <<UNIT
[Unit]
Description=Ollama local model server (tradeAgent)

[Service]
ExecStart=$bin serve
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload 2>/dev/null
  if systemctl --user enable --now ollama.service 2>/dev/null; then
    ok "user service installed: ollama.service (survives logout/reboot: loginctl enable-linger $USER)"
    return 0
  fi
  return 2
}

ollama_restart_and_verify() {
  local model="${1:-$DEFAULT_OLLAMA}"

  local has_sys=0
  command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service' && has_sys=1
  local has_user=0
  command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files 2>/dev/null | grep -q '^ollama\.service' && has_user=1

  if (( has_sys )); then
    :   # سرویس سیستمی، پایین ری‌استارت می‌شود
  elif ollama_up && (( has_user == 0 )); then
    # سرور دستی بالا آمده و واحدی ندارد: به واحد کاربری منتقلش کن تا بعد از ریبوت هم بماند
    dim "moving the hand-started server into a user service"
    pkill -f 'ollama serve' 2>/dev/null
    sleep 2
  fi

  if (( has_sys == 0 )); then
    ensure_ollama_unit
    if (( has_user == 0 )); then
      wait_ollama 30 && ok "service started by systemd (user)" || true
    fi
  fi

  if ollama_up; then
    ok "server already answers at $OLLAMA_URL"
  elif command -v systemctl >/dev/null && (( has_sys )); then
    dim "restarting the system ollama service (sudo may ask for the password)"
    sudo systemctl restart ollama 2>&1 | tail -3
    if ! wait_ollama; then
      bad "the ollama service did not come back"
      sudo systemctl status ollama --no-pager -l 2>&1 | tail -12 | sed 's/^/    /'
      dim "details: journalctl -u ollama -n 40 --no-pager"
      return 1
    fi
  else
    warn "no systemd unit for ollama - starting 'ollama serve' myself"
    pkill -f 'ollama serve' 2>/dev/null
    sleep 1
    (nohup ollama serve >/tmp/ollama.log 2>&1 &)
    if ! wait_ollama; then
      bad "ollama serve did not open $OLLAMA_URL"
      tail -15 /tmp/ollama.log 2>/dev/null | sed 's/^/    /'
      return 1
    fi
  fi
  ok "server is up: $(ollama --version 2>/dev/null | head -n1)"

  ollama_models | grep -qx "$model" || ollama pull "$model" || return 1
  ollama_probe "$model"
  if [[ $? -ne 0 ]]; then
    bad "the runtime works but the model does not: ${OLLAMA_ERR:-unknown}"
    hint_repair
    return 1
  fi
  set_env TRADEAGENT_LLM_PROVIDER ollama
  set_env TRADEAGENT_OLLAMA_URL "$OLLAMA_URL"
  set_env TRADEAGENT_OLLAMA_MODEL "$model"
  ok "verified: $model -> $OLLAMA_ANS"
  ok "now: systemctl --user restart tradeagent-loop.service"
  return 0
}

hardware() {
  echo "--- machine ---"
  local cpu cores mem virt=""
  cpu="$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')"
  cores="$(nproc 2>/dev/null || echo '?')"
  mem="$(awk '/^MemTotal/{printf "%.1f GB", $2/1048576}' /proc/meminfo 2>/dev/null)"
  grep -qi 'hypervisor' /proc/cpuinfo 2>/dev/null && virt=", virtual machine"
  echo "  cpu  : ${cpu:-unknown}"
  echo "  cores: ${cores} threads${virt}"
  echo "  ram  : ${mem:-unknown}"

  echo "--- gpu ---"
  local gpu_found=0
  if command -v nvidia-smi >/dev/null 2>&1; then
    gpu_found=1
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | sed 's/^/  nvidia : /'
  fi
  if command -v rocminfo >/dev/null 2>&1; then
    gpu_found=1
    rocminfo 2>/dev/null | grep -m1 'Marketing Name' | sed 's/^/  amd    : /'
  fi
  if command -v lspci >/dev/null 2>&1; then
    lspci 2>/dev/null | grep -Ei 'vga|3d controller|display' | sed 's/^/  pci    : /'
  fi
  (( gpu_found )) || echo "  no nvidia/amd tool found on PATH"

  echo "--- what ollama is using right now ---"
  local ps
  ps="$(curl -s --max-time 5 "$OLLAMA_URL/api/ps" 2>/dev/null)"
  if [[ -z "$ps" ]]; then
    warn "ollama is not reachable at $OLLAMA_URL - start it, then run this again"
    return 1
  fi
  printf '%s' "$ps" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit()
ms = d.get("models") or d.get("model") or []
if isinstance(ms, dict):
    ms = [ms]
if not ms:
    print("  nothing is loaded in memory right now (the next call loads it)")
for m in ms:
    name = m.get("name") or m.get("model") or "?"
    size = m.get("size") or 0
    vram = m.get("size_vram") or 0
    gb = lambda b: b / 1e9
    if vram and size:
        pct = 100.0 * vram / size
        where = "GPU" if pct > 90 else ("GPU+CPU (split)" if pct > 5 else "CPU only")
        print(f"  {name}: {gb(size):.1f} GB in use, {gb(vram):.1f} GB on the GPU -> {where}")
    else:
        print(f"  {name}: {gb(size):.1f} GB in use, 0 GB on the GPU -> CPU only")
print()
print("  processor column (ollama CLI):")
' 2>/dev/null
  if command -v ollama >/dev/null 2>&1; then
    ollama ps 2>/dev/null | sed 's/^/    /'
  fi
  echo
  echo "  note: 'CPU only' above means the model is computed on your processor with all"
  echo "        threads and needs no GPU; if it feels slow, a smaller model is ~2x faster:"
  echo "        ./scripts/setup_llm.sh --ollama qwen2.5:3b"
  return 0
}

gpu_diag() {
  local lib="${OLLAMA_LIB_DIR:-/usr/local/lib/ollama}"
  echo "--- nvidia (CUDA) ---"
  if command -v nvidia-smi >/dev/null 2>&1; then
    if out="$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1)"; then
      echo "  nvidia-smi: $out"
    else
      warn "nvidia-smi cannot talk to the driver:"
      printf '%s\n' "$out" | head -3 | sed 's/^/    /'
    fi
  else
    echo "  nvidia-smi: not installed"
  fi
  if [[ -r /proc/driver/nvidia/version ]]; then
    echo "  kernel module: $(head -n1 /proc/driver/nvidia/version)"
  else
    echo "  kernel module: not loaded (/proc/driver/nvidia/version is missing)"
  fi
  if command -v lsmod >/dev/null 2>&1; then
    echo "  loaded modules: $(lsmod | grep -c '^nvidia') nvidia*"
  fi
  if command -v dpkg >/dev/null 2>&1; then
    local pkgs; pkgs="$(dpkg -l 2>/dev/null | awk '/nvidia-driver-[0-9]/{print $2}' | tr '\n' ' ')"
    echo "  installed driver packages: ${pkgs:-none}"
  fi
  if command -v ubuntu-drivers >/dev/null 2>&1; then
    dim "ubuntu-drivers recommends: $(ubuntu-drivers devices 2>/dev/null | grep -m1 recommended | sed 's/^ *//')"
  fi

  echo "--- intel / vulkan ---"
  if command -v vulkaninfo >/dev/null 2>&1; then
    local devs
    devs="$(vulkaninfo --summary 2>/dev/null | grep -i 'deviceName' | sed 's/.*= *//' | sort -u | tr '\n' ';')"
    echo "  vulkan devices: ${devs:-none}"
  else
    echo "  vulkaninfo: not installed (sudo apt install vulkan-tools)"
  fi
  if command -v dpkg >/dev/null 2>&1; then
    dpkg -l 2>/dev/null | awk '/mesa-vulkan-drivers/{print "  mesa-vulkan-drivers: " $3}' | head -n1
  fi
  echo "  ollama vulkan runner: $(ls "$lib" 2>/dev/null | grep -ci vulkan) file(s) in $lib"
  echo "  OLLAMA_VULKAN in environment: ${OLLAMA_VULKAN:-unset}"

  echo "--- verdict ---"
  local mode="CPU only"
  if [[ -r /proc/driver/nvidia/version ]] && nvidia-smi >/dev/null 2>&1; then
    mode="NVIDIA CUDA available"
  elif [[ "$(ls "$lib" 2>/dev/null | grep -ci vulkan)" -gt 0 ]]; then
    if command -v vulkaninfo >/dev/null 2>&1 && vulkaninfo --summary 2>/dev/null | grep -qi 'deviceName'; then
      mode="Intel/AMD via Vulkan available"
    else
      mode="ollama has Vulkan support but no working Vulkan device was found"
    fi
  fi
  echo "  $mode"
  case "$mode" in
    "CPU only")
      echo "  fix for the NVIDIA card (RTX 4050):"
      echo "    sudo ubuntu-drivers install      # installs the recommended driver"
      echo "    sudo reboot                      # the kernel module must load"
      echo "  fix for the Intel Arc iGPU (no reboot, no NVIDIA driver):"
      echo "    sudo apt install mesa-vulkan-drivers vulkan-tools"
      echo "    ./scripts/setup_llm.sh --gpu-vulkan"
      ;;
    *Vulkan*)
      echo "  try: ./scripts/setup_llm.sh --gpu-vulkan   # sets OLLAMA_VULKAN=1 and restarts"
      ;;
    *CUDA*)
      echo "  nothing to do: nvidia-smi talks to the driver, ollama will use CUDA on restart"
      ;;
  esac
  dim "note: the Meteor Lake NPU is not supported by Ollama - ignore it for now"
}

enable_vulkan() {
  local dir="$HOME/.config/systemd/user" unit="$HOME/.config/systemd/user/ollama.service" bin
  bin="$(command -v ollama 2>/dev/null)"
  [[ -n "$bin" ]] || { bad "ollama is not on PATH"; return 1; }
  command -v systemctl >/dev/null 2>&1 || { bad "systemctl is missing - cannot manage a service"; return 1; }

  mkdir -p "$dir"
  if [[ ! -f "$unit" ]]; then
    warn "no user unit yet - creating one first"
    ensure_ollama_unit >/dev/null
  fi
  [[ -f "$unit" ]] || { bad "could not create $unit"; return 1; }

  if ! grep -q '^Environment=OLLAMA_VULKAN=1' "$unit"; then
    python3 - "$unit" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
lines = p.read_text().splitlines()
out, done = [], False
for ln in lines:
    out.append(ln)
    if ln.startswith("ExecStart=") and not done:
        out.append("Environment=OLLAMA_VULKAN=1")
        out.append("Environment=OLLAMA_IGPU_ENABLE=1")
        done = True
p.write_text("\n".join(out) + "\n")
PY
    ok "added OLLAMA_VULKAN=1 and OLLAMA_IGPU_ENABLE=1 to $(basename "$unit")"
  else
    dim "OLLAMA_VULKAN=1 is already in the unit"
  fi
  systemctl --user daemon-reload 2>/dev/null
  if ! systemctl --user restart ollama.service 2>/dev/null; then
    bad "could not restart the user unit"; return 1
  fi
  wait_ollama 60 || { bad "server did not come back"; return 1; }
  ok "restarted with Vulkan enabled"

  local model="${1:-$(env_get TRADEAGENT_OLLAMA_MODEL)}"
  model="${model:-$DEFAULT_OLLAMA}"
  ollama_models | grep -qx "$model" || ollama pull "$model" || true
  ollama_probe "$model" >/dev/null
  sleep 1
  echo "--- ollama ps ---"
  ollama ps 2>/dev/null | sed 's/^/  /'
  local vram
  vram="$(curl -s --max-time 5 "$OLLAMA_URL/api/ps" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit()
for m in d.get("models",[]):
    print(m.get("size_vram") or 0)
' 2>/dev/null | head -n1)"
  if [[ "${vram:-0}" -gt 0 ]]; then
    ok "the model is now on a GPU ($(awk -v v="$vram" 'BEGIN{printf "%.1f", v/1e9}') GB in VRAM)"
    return 0
  fi
  warn "still CPU only. Check that a Vulkan device exists: vulkaninfo --summary | head"
  warn "Vulkan device memory reporting on Linux may also need: sudo setcap cap_perfmon+ep $bin"
  return 2
}

script_version() {
  git -C "$ROOT" log -1 --format=%h -- scripts/setup_llm.sh 2>/dev/null || echo unknown
}

ollama_up() { curl -s --max-time 5 "$OLLAMA_URL/api/tags" | grep -q '"models"'; }

wait_ollama() {  # wait_ollama [seconds]
  local limit="${1:-${OLLAMA_WAIT_S:-60}}" n=0
  while (( n < limit )); do
    ollama_up && return 0
    sleep 1; n=$((n + 1))
  done
  return 1
}

ollama_models() { curl -s --max-time 5 "$OLLAMA_URL/api/tags" \
  | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit()
for m in d.get("models",[]): print(m.get("name",""))' 2>/dev/null; }

# 0 = پاسخ JSON سالم (در OLLAMA_ANS)، 1 = سرور نیست، 2 = مدل نیست، 3 = خطای HTTP، 4 = پاسخ بی‌معنی
# این‌ها متغیر جهانی‌اند تا در $(...) گم نشوند
OLLAMA_ANS=""
OLLAMA_ERR=""
ollama_probe() {
  local model="$1" payload out code body
  OLLAMA_ANS=""; OLLAMA_ERR=""
  ollama_up || { OLLAMA_ERR="server is not reachable at $OLLAMA_URL"; return 1; }
  ollama_models | grep -qx "$model" || { OLLAMA_ERR="model '$model' is not installed"; return 2; }

  payload="$(python3 -c '
import json, sys
print(json.dumps({
    "model": sys.argv[1],
    "stream": False,
    "format": "json",
    "keep_alive": "30m",
    "options": {"temperature": 0, "num_predict": 16},
    "messages": [{"role": "user", "content": "Answer with a JSON object where ok is true."}],
}))
' "$model" 2>/dev/null)"
  [[ -n "$payload" ]] || { OLLAMA_ERR="could not build the request body"; return 4; }

  dim "asking $model - the first call loads the model into RAM, this can take a minute"
  out="$(curl -s --max-time 300 -w $'\n%{http_code}' -H 'Content-Type: application/json' \
         -d "$payload" "$OLLAMA_URL/api/chat" 2>/dev/null)"
  code="${out##*$'\n'}"; body="${out%$'\n'*}"
  if [[ "$code" != "200" ]]; then
    OLLAMA_ERR="http ${code:-000}: $(printf '%s' "$body" | tr -d '\n' | cut -c1-200)"
    return 3
  fi
  OLLAMA_ANS="$(printf '%s' "$body" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print("PARSE:" + str(e)[:120]); raise SystemExit(0)
c = (d.get("message") or {}).get("content", "")
try:
    json.loads(c)
    print(c[:80])
except Exception:
    print("NOTJSON:" + (c or "")[:120])
' 2>/dev/null)"
  case "$OLLAMA_ANS" in
    PARSE:*|NOTJSON:*) OLLAMA_ERR="${OLLAMA_ANS}"; OLLAMA_ANS=""; return 4 ;;
    "") OLLAMA_ERR="empty answer"; return 4 ;;
  esac
  return 0
}

status() {
  echo "--- script ---"
  echo "  version               : $(script_version)"
  echo "--- .env ---"
  local k p
  k="$(env_get GEMINI_API_KEY)"; p="$(env_get TRADEAGENT_LLM_PROVIDER)"
  if [[ -z "$k" ]]; then echo "  GEMINI_API_KEY        : (empty)"
  elif [[ -n "$(not_a_key_reason "$k")" ]]; then echo "  GEMINI_API_KEY        : not usable ($(not_a_key_reason "$k"), ${#k} chars)"
  else echo "  GEMINI_API_KEY        : $(mask "$k")"; fi
  echo "  provider              : ${p:-gemini (default)}"
  echo "  ollama url            : $OLLAMA_URL"

  echo "--- gemini ---"
  if [[ -z "$k" ]]; then
    warn "no key stored"
  elif [[ -n "$(not_a_key_reason "$k")" ]]; then
    warn "stored value is not a key ($(not_a_key_reason "$k"))"
  else
    gemini_probe "$k"
    case $? in
      0) ok "works: model $GEMINI_MODEL_OK";;
      1) report_failure "the API rejected this key";;
      2) warn "cannot reach generativelanguage.googleapis.com (network/proxy)";;
      *) report_failure "unexpected answer from the API";;
    esac
  fi

  echo "--- ollama ---"
  command -v ollama >/dev/null && ok "binary: $(command -v ollama)" || warn "binary: not installed"
  if ollama_up; then
    ok "server: reachable at $OLLAMA_URL"
    echo "    installed: $(ollama_models | tr '\n' ' ')"
    local cur; cur="$(env_get TRADEAGENT_OLLAMA_MODEL)"; cur="${cur:-$DEFAULT_OLLAMA}"
    ollama_probe "$cur"
    if [[ $? -eq 0 ]]; then ok "model $cur answers: $OLLAMA_ANS"
    else warn "model $cur did not answer: ${OLLAMA_ERR:-unknown}"; hint_repair; fi
  else warn "server: not reachable at $OLLAMA_URL"; fi
  echo
  echo "without either, cycles still run - decisions stay deterministic."
}

do_gemini() {
  local key
  key="$(env_get GEMINI_API_KEY)"
  if [[ $# -gt 0 && -n "${1:-}" ]]; then key="$1"
  else
    printf '[llm] paste the key (hidden; not in shell history): '
    read -rs key; echo
  fi
  local clean; clean="$(sanitize "$key")"
  [[ "$clean" != "$key" ]] && warn "removed spaces/quotes/CR from what was pasted"

  local why; why="$(not_a_key_reason "$clean")"
  case "$why" in
    empty) bad "nothing was pasted"; return 2;;
    non-ascii) bad "that text contains non-ASCII characters - it is a placeholder from the chat, not a key"
               dim "a key is only printable ASCII: A-Z a-z 0-9 . _ -"; return 2;;
    placeholder) bad "that is a placeholder, not a key: '$clean'"
               dim "free key: https://aistudio.google.com/apikey"; return 2;;
    shape) bad "this is not the shape of a Gemini key: $(mask "$clean")"
           dim "a key is at least 20 printable characters (AI Studio emits 'AIza…' or 'AQ.Ab8…')"
           dim "in AI Studio the key is shown once - press the copy icon, not a screenshot"
           return 2;;
  esac

  gemini_probe "$clean"
  case $? in
    0) set_env GEMINI_API_KEY "$clean"
       set_env TRADEAGENT_LLM_PROVIDER gemini
       set_env TRADEAGENT_LLM_MODEL "$GEMINI_MODEL_OK"
       ok "verified with model $GEMINI_MODEL_OK; stored in .env (600)"
       ok "now: systemctl --user restart tradeagent-loop.service" ;;
    1) report_failure "the API rejected this key"
       dim "nothing written to .env"; return 1;;
    3) report_failure "no usable answer from the API"; return 1;;
    4|5) dim "the key is fine - the model side is the problem (see above)"; return 1;;
    2) bad "cannot reach generativelanguage.googleapis.com from this machine"
       dim "test: curl -sS -o /dev/null -w '%{http_code}\\n' https://generativelanguage.googleapis.com"
       dim "behind a proxy? export https_proxy=http://127.0.0.1:PORT then retry"
       return 1;;
    *) report_failure "unexpected answer from the API"; return 1;;
  esac
}

case "${1:-}" in
  --status) status ;;
  --hardware|--hw) hardware; exit $? ;;
  --gpu) gpu_diag; exit 0 ;;
  --gpu-vulkan) shift; enable_vulkan "${1:-}"; exit $? ;;
  "") interactive ;;

  --gemini) shift; do_gemini "$@" ;;

  --diagnose)
    shift
    if [[ -n "${1:-}" ]]; then k="$1"
    else k="$(env_get GEMINI_API_KEY)"; fi
    if [[ -z "$k" ]]; then
      printf '[llm] no key stored. paste it (hidden): '; read -rs k; echo
    fi
    [[ -n "$k" ]] || { bad "no key given"; exit 1; }
    clean="$(sanitize "$k")"
    echo "key         : $(mask "$clean")"
    echo "shape ok?   : $([[ -z "$(not_a_key_reason "$clean")" ]] && echo yes || echo "no ($(not_a_key_reason "$clean"))")"
    echo "model asked : $PREFERRED_GEMINI"
    if [[ -n "${GEMINI_PROBE_MOCK:-}" ]]; then
      CODE="${GEMINI_PROBE_MOCK%%|*}"; BODY="${GEMINI_PROBE_MOCK#*|}"
    else
      http_get "$API_BASE/models?key=$clean"
    fi
    echo "models GET  : http ${CODE:-000}"
    [[ "${CODE:-}" == "200" ]] && printf '%s' "$BODY" | python3 -c '
import json,sys
d=json.load(sys.stdin)
ms=[m["name"].split("/")[-1] for m in d.get("models",[]) if "generateContent" in m.get("supportedGenerationMethods",[])]
print("available   :", ", ".join(ms[:8]))
' || dim "raw: $(printf '%s' "$BODY" | tr -d '\n' | cut -c1-400)"
    if [[ "${CODE:-}" == "200" ]]; then
      ok "the key is accepted by Google"
    else
      explain
    fi
    ;;

  --repair-ollama)
    shift
    repair_ollama "${1:-}"
    exit $?
    ;;

  --install-ollama)
    if ! command -v ollama >/dev/null; then
      warn "downloads ~1.6 GB and installs a system service (sudo password will be asked)"
      read -r -p "continue? [y/N] " ans
      [[ "${ans:-n}" =~ ^[Yy]$ ]] || { warn "aborted"; exit 1; }
      curl -fsSL https://ollama.com/install.sh | sh || { bad "install failed (network?)"; exit 1; }
      command -v ollama >/dev/null || { bad "installed but not on PATH"; exit 1; }
      ok "ollama installed"
    elif ollama_lib_ok; then
      ok "ollama already installed: $(command -v ollama)"
    else
      warn "ollama binary exists but its runtime is missing (interrupted install) - repairing"
      repair_ollama "$DEFAULT_OLLAMA"
      exit $?
    fi
    if ! ollama_up; then
      warn "starting the server"
      if command -v systemctl >/dev/null && systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
        sudo systemctl start ollama 2>&1 | tail -2
      else
        (nohup ollama serve >/tmp/ollama.log 2>&1 &)
      fi
    fi
    wait_ollama || { bad "server did not come up; see journalctl -u ollama or /tmp/ollama.log"; exit 1; }
    curl -s "$OLLAMA_URL/api/tags" | grep -q "\"$DEFAULT_OLLAMA\"" || ollama pull "$DEFAULT_OLLAMA" || exit 1
    set_env TRADEAGENT_LLM_PROVIDER ollama
    set_env TRADEAGENT_OLLAMA_MODEL "$DEFAULT_OLLAMA"
    set_env TRADEAGENT_OLLAMA_URL "$OLLAMA_URL"
    ollama_probe "$DEFAULT_OLLAMA"
    if [[ $? -eq 0 ]]; then
      ok "local model answered with JSON: $OLLAMA_ANS"
    else
      warn "model is pulled but the test answer was unusable - the loop will fall back to deterministic"
    fi
    ok "now: systemctl --user restart tradeagent-loop.service" ;;

  --ollama)
    model="${2:-$DEFAULT_OLLAMA}"
    command -v ollama >/dev/null || { bad "not installed; run: ./scripts/setup_llm.sh --install-ollama"; exit 1; }
    ollama_up || { (nohup ollama serve >/tmp/ollama.log 2>&1 &); sleep 3; }
    ollama_up || { bad "cannot reach $OLLAMA_URL"; exit 1; }
    ollama_models | grep -qx "$model" || ollama pull "$model" || exit 1
    ollama_probe "$model"
    if [[ $? -eq 0 ]]; then
      set_env TRADEAGENT_LLM_PROVIDER ollama
      set_env TRADEAGENT_OLLAMA_URL "$OLLAMA_URL"
      set_env TRADEAGENT_OLLAMA_MODEL "$model"
      ok "provider=ollama model=$model"
      ok "answered with JSON: $OLLAMA_ANS"
      ok "now: systemctl --user restart tradeagent-loop.service"
    else
      bad "ollama has $model but it did not answer: ${OLLAMA_ERR:-unknown}"
      exit 1
    fi ;;

  --off) set_env TRADEAGENT_LLM_PROVIDER none; ok "provider=none (deterministic only)" ;;

  -h|--help) sed -n '2,/^set -uo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//' ;;

  *) bad "unknown option: $1"; exit 2 ;;
esac
