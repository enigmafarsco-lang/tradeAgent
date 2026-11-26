#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# نصب حلقه‌ی ۵ دقیقه‌ای به‌عنوان سرویس systemd --user (بدون path هاردکد)
#
#   ./scripts/install_systemd_user.sh              # نصب + فعال‌سازی
#   ./scripts/install_systemd_user.sh --interval 300 --no-start
#   ./scripts/install_systemd_user.sh --uninstall
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/tradeagent-loop.service"
INTERVAL=300
DO_START=1
ACTION=install

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval) INTERVAL="$2"; shift 2 ;;
    --no-start) DO_START=0; shift ;;
    --uninstall) ACTION=uninstall; shift ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "گزینه ناشناخته: $1" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;34m[systemd]\033[0m %s\n' "$*"; }

if [[ "$ACTION" == uninstall ]]; then
  systemctl --user disable --now tradeagent-loop.service 2>/dev/null || true
  rm -f "$UNIT"
  systemctl --user daemon-reload 2>/dev/null || true
  log "سرویس حذف شد."
  exit 0
fi

[[ -x "$ROOT/.venv/bin/tradeagent" ]] || {
  echo "venv آماده نیست: $ROOT/.venv/bin/tradeagent" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\"" >&2
  exit 1
}

mkdir -p "$UNIT_DIR" "$ROOT/var/logs"
if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  log ".env از .env.example ساخته شد → کلید Gemini را پر کن (اختیاری؛ بدون آن هم کار می‌کند)"
fi

CONFIG_ARG=""
[[ -f "$ROOT/configs/local.toml" ]] && CONFIG_ARG="--config $ROOT/configs/local.toml"

cat > "$UNIT" <<EOF
[Unit]
Description=TradeAgent — حلقه‌ی کاغذی ${INTERVAL}s با گزارش گیت
Documentation=file://$ROOT/docs/RUNBOOK-FA.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/tradeagent $CONFIG_ARG loop --interval $INTERVAL
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$ROOT/.env
Restart=always
RestartSec=15
Nice=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF

log "unit نوشته شد: $UNIT"
systemctl --user daemon-reload
loginctl enable-linger "$USER" 2>/dev/null && log "linger فعال شد (سرویس بعد از logout هم می‌ماند)" || log "enable-linger نشد؛ سرویس فقط تا logout زنده است"

if [[ $DO_START -eq 1 ]]; then
  systemctl --user enable --now tradeagent-loop.service
  sleep 2
  systemctl --user --no-pager status tradeagent-loop.service | head -12 || true
  echo
  log "لاگ زنده: journalctl --user -u tradeagent-loop.service -f"
else
  log "فعال‌سازی رد شد (--no-start). بعداً: systemctl --user enable --now tradeagent-loop.service"
fi
