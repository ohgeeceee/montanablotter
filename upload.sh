#!/bin/bash
# MontanaBlotter.com - Local Upload Script
# Run this on YOUR LOCAL MACHINE to upload files to your VPS
# Usage: ./upload.sh user@your-vps-ip

set -e

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[UPLOAD]${NC} $1"; }
ok()  { echo -e "${GREEN}[OK]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# ── Check args ──
if [ $# -lt 1 ]; then
  echo "Usage: ./upload.sh user@your-vps-ip"
  echo ""
  echo "Examples:"
  echo "  ./upload.sh root@123.45.67.89"
  echo "  ./upload.sh ubuntu@montanablotter.com"
  echo ""
  exit 1
fi

VPS="$1"
APP_DIR="/var/www/montanablotter"

# ── Build locally first ──
log "Building locally..."
npm run build 2>&1 | tail -5
ok "Local build complete"

# ── Create temp archive ──
log "Packaging files..."
TMP_DIR=$(mktemp -d)
ARCHIVE="${TMP_DIR}/montanablotter.tar.gz"

tar -czf "$ARCHIVE" \
  --exclude='node_modules' \
  --exclude='.git' \
  --exclude='db/migrations' \
  --exclude='*.db' \
  \
  api/ \
  contracts/ \
  db/ \
  src/ \
  public/ \
  index.html \
  package.json \
  package-lock.json \
  tsconfig.json \
  tsconfig.app.json \
  tsconfig.server.json \
  vite.config.ts \
  tailwind.config.js \
  postcss.config.js \
  drizzle.config.ts \
  deploy.sh \
  .env

SIZE=$(du -h "$ARCHIVE" | cut -f1)
ok "Package created (${SIZE})"

# ── Upload to VPS ──
log "Uploading to ${VPS}..."
ssh -o StrictHostKeyChecking=no "$VPS" "mkdir -p ${APP_DIR}" || fail "SSH connection failed. Check your SSH key and VPS address."

scp -o StrictHostKeyChecking=no "$ARCHIVE" "${VPS}:${APP_DIR}/package.tar.gz" || fail "Upload failed"
ok "Files uploaded"

# ── Extract on VPS ──
log "Extracting on VPS..."
ssh -o StrictHostKeyChecking=no "$VPS" "cd ${APP_DIR} && tar -xzf package.tar.gz && rm package.tar.gz" || fail "Extraction failed"
ok "Files extracted to ${APP_DIR}"

# ── Cleanup ──
rm -rf "$TMP_DIR"

# ── Instructions ──
echo ""
echo "========================================"
echo "  UPLOAD COMPLETE!"
echo "========================================"
echo ""
echo "  Next steps on your VPS:"
echo ""
echo "  1. SSH into your server:"
echo "     ssh ${VPS}"
echo ""
echo "  2. Run the deploy script:"
echo "     cd ${APP_DIR}"
echo "     sudo ./deploy.sh"
echo ""
echo "  3. The script will:"
echo "     - Install Node.js, PM2, Nginx (if needed)"
echo "     - Install dependencies and build"
echo "     - Connect your blotter.db"
echo "     - Configure Nginx reverse proxy"
echo "     - Start the app with PM2"
echo ""
echo "  4. After deploy, access your site at:"
echo "     http://your-server-ip"
echo "     or"
echo "     http://your-domain.com"
echo ""
echo "  Admin panel: /admin"
echo "  Login page: /admin-login"
echo ""
ok "Ready to deploy!"
