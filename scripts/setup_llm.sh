#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Language model for the trading loop: free Gemini API key, or local Ollama.
# Nothing is ever written to .env unless a real request to the provider succeeds.
#
#   ./scripts/setup_llm.sh --status              what is active right now?
#   ./scripts/setup_llm.sh --gemini              ask for the key (hidden), verify, store
#   ./scripts/setup_llm.sh --diagnose            show the provider's own answer, key masked
#   ./scripts/setup_llm.sh --install-ollama      install ollama (sudo) + pull the model
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
      (nohup ollama serve >/tmp/ollama.log 2>&1 &) ; sleep 3
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

script_version() {
  git -C "$ROOT" log -1 --format=%h -- scripts/setup_llm.sh 2>/dev/null || echo unknown
}

ollama_up() { curl -s --max-time 5 "$OLLAMA_URL/api/tags" | grep -q '"models"'; }

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
    else warn "model $cur did not answer: ${OLLAMA_ERR:-unknown}"; fi
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

  --install-ollama)
    if ! command -v ollama >/dev/null; then
      warn "downloads ~1.6 GB and installs a system service (sudo password will be asked)"
      read -r -p "continue? [y/N] " ans
      [[ "${ans:-n}" =~ ^[Yy]$ ]] || { warn "aborted"; exit 1; }
      curl -fsSL https://ollama.com/install.sh | sh || { bad "install failed (network?)"; exit 1; }
      command -v ollama >/dev/null || { bad "installed but not on PATH"; exit 1; }
      ok "ollama installed"
    else ok "ollama already installed: $(command -v ollama)"; fi
    ollama_up || { warn "starting server"; (nohup ollama serve >/tmp/ollama.log 2>&1 &); sleep 3; }
    ollama_up || { bad "server still down; see /tmp/ollama.log"; exit 1; }
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
