#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# مدل زبانی: Gemini رایگان یا Ollama محلی — یک دستور، بدون کلید در گیت
#
#   ./scripts/setup_llm.sh --status                 # چه چیزی الان فعال است؟
#   ./scripts/setup_llm.sh --gemini AIza...         # کلید Gemini را تست و ثبت کن
#   ./scripts/setup_llm.sh --ollama                 # ollama + مدل پیش‌فرض
#   ./scripts/setup_llm.sh --ollama qwen2.5:7b-instruct
#   ./scripts/setup_llm.sh --off                    # قطع کردن مدل (حالت قطعی)
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
OLLAMA_URL="${TRADEAGENT_OLLAMA_URL:-http://127.0.0.1:11434}"
DEFAULT_MODEL="${TRADEAGENT_OLLAMA_MODEL:-qwen2.5:7b-instruct}"

ok()   { printf '\033[1;32m[llm]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[llm]\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m[llm]\033[0m %s\n' "$*"; }

set_env() {  # set_env KEY VALUE
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"
  tmp="$(mktemp)"
  grep -v "^${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

gemini_ok() {  # gemini_ok KEY
  curl -s --max-time 20 "https://generativelanguage.googleapis.com/v1beta/models?key=$1" \
    | grep -q '"name"'
}

ollama_ok() {
  curl -s --max-time 5 "$OLLAMA_URL/api/tags" | grep -q '"models"'
}

status() {
  echo "--- current selection ---"
  [[ -f "$ENV_FILE" ]] && grep -E '^(GEMINI_API_KEY|TRADEAGENT_LLM_)' "$ENV_FILE" | sed 's/=.*/=<set>/' || echo "(no .env)"
  local key provider
  key="$(grep -s '^GEMINI_API_KEY=' "$ENV_FILE" | cut -d= -f2- | head -n1)"
  provider="$(grep -s '^TRADEAGENT_LLM_PROVIDER=' "$ENV_FILE" | cut -d= -f2- | head -n1)"
  echo "provider: ${provider:-gemini (default)}"
  if [[ -n "$key" ]]; then
    if gemini_ok "$key"; then ok "gemini: key works"; else warn "gemini: key set but the API did not answer"; fi
  else
    warn "gemini: no key"
  fi
  if ollama_ok; then
    ok "ollama: reachable at $OLLAMA_URL"
    curl -s "$OLLAMA_URL/api/tags" | tr ',' '\n' | grep -o '"name":"[^"]*"' | head -5 | sed 's/^/    /'
  else
    warn "ollama: not reachable at $OLLAMA_URL"
  fi
  echo
  echo "note: without either, cycles still run - decisions are deterministic."
}

case "${1:-}" in
  --status|"")
    status ;;
  --gemini)
    key="${2:-}"
    [[ -n "$key" ]] || { bad "usage: --gemini <API_KEY>"; exit 2; }
    if gemini_ok "$key"; then
      set_env GEMINI_API_KEY "$key"
      set_env TRADEAGENT_LLM_PROVIDER gemini
      ok "key verified and written to .env (provider=gemini)"
      ok "restart the service: systemctl --user restart tradeagent-loop.service"
    else
      bad "the key did not authenticate; nothing written"; exit 1
    fi
    ;;
  --ollama)
    model="${2:-$DEFAULT_MODEL}"
    if ! command -v ollama >/dev/null; then
      warn "ollama is not installed. Install it (needs sudo) then rerun:"
      echo "    curl -fsSL https://ollama.com/install.sh | sh"
      exit 1
    fi
    if ! ollama_ok; then
      warn "starting ollama service"
      (nohup ollama serve >/dev/null 2>&1 &) ; sleep 3
    fi
    if ! curl -s "$OLLAMA_URL/api/tags" | grep -q "\"$model\""; then
      warn "pulling $model (first time can take a while)"
      ollama pull "$model" || { bad "pull failed"; exit 1; }
    fi
    set_env TRADEAGENT_LLM_PROVIDER ollama
    set_env TRADEAGENT_OLLAMA_MODEL "$model"
    ok "provider=ollama model=$model"
    ok "restart the service: systemctl --user restart tradeagent-loop.service"
    ;;
  --off)
    set_env TRADEAGENT_LLM_PROVIDER none
    ok "provider=none (deterministic decisions only)"
    ;;
  -h|--help)
    sed -n '2,/^set -uo/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
    ;;
  *)
    bad "unknown option: $1"; exit 2 ;;
esac
