#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Language model: free Gemini API or local Ollama. One command, key never in git.
#
#   ./scripts/setup_llm.sh --status                  what is active right now?
#   ./scripts/setup_llm.sh --gemini <REAL_KEY>       test the key, then store it
#   ./scripts/setup_llm.sh --install-ollama          install ollama (needs sudo) + pull model
#   ./scripts/setup_llm.sh --ollama [model]          use an already installed ollama
#   ./scripts/setup_llm.sh --off                     deterministic only
#
# Free Gemini key: https://aistudio.google.com/apikey  (no card, free tier)
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
OLLAMA_URL="${TRADEAGENT_OLLAMA_URL:-http://127.0.0.1:11434}"
DEFAULT_MODEL="${TRADEAGENT_OLLAMA_MODEL:-qwen2.5:7b-instruct}"
GEMINI_MODEL="${TRADEAGENT_LLM_MODEL:-gemini-2.5-flash}"

ok()   { printf '\033[1;32m[llm]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[llm]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[llm]\033[0m %s\n' "$*"; }

set_env() {
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"
  tmp="$(mktemp)"
  grep -v "^${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

env_get() { grep -s "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | head -n1; }

looks_like_placeholder() {
  local k="$1"
  [[ ${#k} -lt 25 ]] && return 0
  case "$k" in
    *"..."*|*YOUR*|*your*|*xxx*|*XXX*|*EXAMPLE*|*placeholder*) return 0 ;;
  esac
  return 1
}

# 0=ok 1=bad key 2=network 3=unknown
gemini_probe() {
  local key="$1" body code
  body="$(curl -s --max-time 25 -w '\n%{http_code}' \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"ping"}]}],"generationConfig":{"maxOutputTokens":8}}' \
    "https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent?key=${key}" 2>/dev/null)"
  code="$(printf '%s' "$body" | tail -n1)"
  case "$code" in
    200) return 0 ;;
    400|403) return 1 ;;
    000|"") return 2 ;;
    *) return 3 ;;
  esac
}

ollama_up() { curl -s --max-time 5 "$OLLAMA_URL/api/tags" | grep -q '"models"'; }

status() {
  echo "--- .env ---"
  local k p
  k="$(env_get GEMINI_API_KEY)"
  p="$(env_get TRADEAGENT_LLM_PROVIDER)"
  if [[ -z "$k" ]]; then
    echo "  GEMINI_API_KEY        : (empty)"
  elif looks_like_placeholder "$k"; then
    echo "  GEMINI_API_KEY        : looks like a placeholder (${#k} chars) - ignored"
  else
    echo "  GEMINI_API_KEY        : set (${#k} chars)"
  fi
  echo "  provider              : ${p:-gemini (default)}"
  echo "  ollama url            : $OLLAMA_URL"

  echo "--- gemini ---"
  if [[ -z "$k" ]] || looks_like_placeholder "$k"; then
    warn "no usable key. Free key: https://aistudio.google.com/apikey"
  else
    gemini_probe "$k"
    case $? in
      0) ok "key works against $GEMINI_MODEL" ;;
      1) bad "key rejected (400/403) - create a new one at aistudio.google.com/apikey" ;;
      2) warn "cannot reach generativelanguage.googleapis.com (network/proxy?)" ;;
      *) warn "unexpected answer from the Gemini API" ;;
    esac
  fi

  echo "--- ollama ---"
  if command -v ollama >/dev/null; then ok "binary: $(command -v ollama)"; else warn "binary: not installed"; fi
  if ollama_up; then
    ok "server: reachable at $OLLAMA_URL"
    curl -s "$OLLAMA_URL/api/tags" | tr ',' '\n' | grep -o '"name":"[^"]*"' | head -5 | sed 's/^/    /'
  else
    warn "server: not reachable at $OLLAMA_URL"
  fi
  echo
  echo "without either, cycles still run - decisions stay deterministic."
}

case "${1:-}" in
  --status|"")
    status ;;

  --gemini)
    rc=0
    key="${2:-}"
    [[ -n "$key" ]] || { bad "usage: --gemini <REAL_KEY>   (get one: https://aistudio.google.com/apikey)"; exit 2; }
    if looks_like_placeholder "$key"; then
      bad "that is a placeholder, not a key: '$key'"
      echo "    get a free key at https://aistudio.google.com/apikey (Google AI Studio, no card)"
      exit 2
    fi
    gemini_probe "$key"
    case $? in
      0) set_env GEMINI_API_KEY "$key"
         set_env TRADEAGENT_LLM_PROVIDER gemini
         ok "key verified against $GEMINI_MODEL and stored in .env (600)"
         ok "restart: systemctl --user restart tradeagent-loop.service" ;;
      1) bad "the API rejected this key (400/403). Nothing written."; rc=1
         echo "    - copy the key again without spaces; keys start with 'AIza' and are ~39 chars"
         echo "    - make sure the Gemini API is enabled for that project" ;;
      2) bad "cannot reach generativelanguage.googleapis.com from this machine."; rc=1
         echo "    - check: curl -sS -o /dev/null -w '%{http_code}\\n' https://generativelanguage.googleapis.com"
         echo "    - behind a proxy? export https_proxy=http://127.0.0.1:PORT and retry"
         echo "    - or use the local model instead: ./scripts/setup_llm.sh --install-ollama" ;;
      *) bad "unexpected answer from the API. Nothing written."; rc=1 ;;
    esac
    exit "${rc:-0}" ;;

  --install-ollama)
    if command -v ollama >/dev/null; then
      ok "ollama already installed: $(command -v ollama)"
    else
      warn "this downloads ~1.6 GB and installs a system service (sudo password will be asked)"
      read -r -p "continue? [y/N] " ans
      [[ "${ans:-n}" =~ ^[Yy]$ ]] || { warn "aborted"; exit 1; }
      curl -fsSL https://ollama.com/install.sh | sh || { bad "install failed (network?)"; exit 1; }
      command -v ollama >/dev/null || { bad "installed but 'ollama' is not on PATH"; exit 1; }
      ok "ollama installed"
    fi
    if ! ollama_up; then
      warn "server not up; starting it in the background"
      (nohup ollama serve >/tmp/ollama.log 2>&1 &) ; sleep 3
    fi
    ollama_up || { bad "server still down; see /tmp/ollama.log"; exit 1; }
    if ! curl -s "$OLLAMA_URL/api/tags" | grep -q "\"$DEFAULT_MODEL\""; then
      warn "pulling $DEFAULT_MODEL (~4.7 GB, once)"
      ollama pull "$DEFAULT_MODEL" || { bad "pull failed"; exit 1; }
    fi
    set_env TRADEAGENT_LLM_PROVIDER ollama
    set_env TRADEAGENT_OLLAMA_MODEL "$DEFAULT_MODEL"
    set_env TRADEAGENT_OLLAMA_URL "$OLLAMA_URL"
    ok "provider=ollama model=$DEFAULT_MODEL"
    ok "restart: systemctl --user restart tradeagent-loop.service" ;;

  --ollama)
    model="${2:-$DEFAULT_MODEL}"
    command -v ollama >/dev/null || { bad "ollama is not installed; run: ./scripts/setup_llm.sh --install-ollama"; exit 1; }
    ollama_up || { warn "starting ollama"; (nohup ollama serve >/tmp/ollama.log 2>&1 &) ; sleep 3; }
    ollama_up || { bad "server not reachable at $OLLAMA_URL"; exit 1; }
    curl -s "$OLLAMA_URL/api/tags" | grep -q "\"$model\"" || ollama pull "$model" || exit 1
    set_env TRADEAGENT_LLM_PROVIDER ollama
    set_env TRADEAGENT_OLLAMA_MODEL "$model"
    ok "provider=ollama model=$model" ;;

  --off)
    set_env TRADEAGENT_LLM_PROVIDER none
    ok "provider=none (deterministic decisions only)" ;;

  -h|--help)
    sed -n '2,/^set -uo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//' ;;

  *) bad "unknown option: $1"; exit 2 ;;
esac
