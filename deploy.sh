#!/bin/bash
# MontanaBlotter.com - Flask/Systemd Deployment Script
# Usage: sudo ./deploy.sh

set -euo pipefail

APP_DIR="/root/montanablotter"
VENV_PY="$APP_DIR/venv/bin/python"
PIP_BIN="$APP_DIR/venv/bin/pip"
REQUIREMENTS="$APP_DIR/requirements.txt"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo "========================================"
echo "  MontanaBlotter Flask Deploy Script"
echo "========================================"
echo ""

if [ "${EUID}" -ne 0 ]; then
  fail "Please run as root: sudo ./deploy.sh"
fi

if [ ! -d "$APP_DIR" ]; then
  fail "App directory not found: $APP_DIR"
fi

cd "$APP_DIR"

if [ ! -x "$VENV_PY" ]; then
  fail "Virtualenv Python not found: $VENV_PY"
fi

if [ -f "$REQUIREMENTS" ]; then
  log "Installing Python dependencies from requirements.txt..."
  "$PIP_BIN" install -r "$REQUIREMENTS"
  ok "Dependencies installed"
else
  warn "requirements.txt not found; skipping dependency install"
fi

log "Running app syntax check..."
"$VENV_PY" -m py_compile app.py
ok "Syntax check passed"

log "Checking Nginx configuration..."
nginx -t
ok "Nginx config valid"

log "Restarting montanablotter.service..."
systemctl restart montanablotter.service
systemctl is-active --quiet montanablotter.service || fail "montanablotter.service failed to start"
ok "montanablotter.service is active"

log "Reloading Nginx..."
systemctl reload nginx
ok "Nginx reloaded"

if systemctl list-unit-files | rg -q '^openclaw-gateway\.service'; then
  log "Restarting openclaw-gateway.service..."
  systemctl restart openclaw-gateway.service
  if systemctl is-active --quiet openclaw-gateway.service; then
    ok "openclaw-gateway.service is active"
  else
    warn "openclaw-gateway.service is not active"
  fi
fi

echo ""
echo "========================================"
echo -e "  ${GREEN}DEPLOYMENT COMPLETE${NC}"
echo "========================================"
echo ""
echo "  App dir:           $APP_DIR"
echo "  Service status:    systemctl status montanablotter.service --no-pager"
echo "  App logs:          journalctl -u montanablotter.service -n 100 --no-pager"
echo "  Gateway logs:      journalctl -u openclaw-gateway.service -n 100 --no-pager"
echo ""
