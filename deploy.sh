#!/bin/bash
# MontanaBlotter.com - VPS Deployment Script
# Usage: ./deploy.sh
# This script deploys the MontanaBlotter fullstack app to your VPS

set -euo pipefail

# ── Configuration ──
APP_NAME="montanablotter"
APP_DIR="/var/www/${APP_NAME}"
DB_PATH="${APP_DIR}/blotter.db"
PORT=3000
NODE_VERSION="20"

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"; }
ok()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# ── Header ──
echo "========================================"
echo "  MontanaBlotter.com Deploy Script"
echo "========================================"
echo ""

# ── 0. Must run as root ──
if [ "$EUID" -ne 0 ]; then
  fail "Please run as root: sudo ./deploy.sh"
fi

# ── 1. Check / Install Node.js ──
log "Checking Node.js..."
if command -v node &> /dev/null; then
  NODE_CURRENT=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
  if [ "$NODE_CURRENT" -ge "${NODE_VERSION}" ]; then
    ok "Node.js $(node -v) installed"
  else
    warn "Node.js too old ($(node -v)), upgrading..."
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash -
    apt-get install -y nodejs
    ok "Node.js $(node -v) installed"
  fi
else
  log "Installing Node.js ${NODE_VERSION}..."
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash -
  apt-get install -y nodejs
  ok "Node.js $(node -v) installed"
fi

# ── 2. Check / Install PM2 ──
log "Checking PM2..."
if ! command -v pm2 &> /dev/null; then
  log "Installing PM2..."
  npm install -g pm2
  pm2 startup systemd -u root --hp /root
  ok "PM2 installed"
else
  ok "PM2 already installed ($(pm2 -v))"
fi

# ── 3. Check / Install Nginx ──
log "Checking Nginx..."
if ! command -v nginx &> /dev/null; then
  log "Installing Nginx..."
  apt-get update
  apt-get install -y nginx
  systemctl enable nginx
  ok "Nginx installed"
else
  ok "Nginx already installed ($(nginx -v 2>&1 | head -1))"
fi

# ── 4. Create app directory ──
log "Setting up app directory at ${APP_DIR}..."
mkdir -p "$APP_DIR"

# ── 5. Install dependencies ──
log "Installing npm dependencies..."
cd "$APP_DIR"
if [ ! -f "package.json" ]; then
  fail "No package.json found in ${APP_DIR}. Please upload the project files first."
fi
npm install --production 2>&1 | tail -5
ok "Dependencies installed"

# ── 6. Build for production ──
log "Building for production..."
cd "$APP_DIR"
npm run build 2>&1 | tail -10
ok "Build complete"

# ── 7. Connect blotter.db ──
log "Connecting blotter.db..."

if [ -f "$DB_PATH" ]; then
  ok "blotter.db already exists at ${DB_PATH}"
else
  # Check if user has a blotter.db elsewhere
  echo ""
  echo "  blotter.db not found at ${DB_PATH}"
  echo "  Options:"
  echo "    1) Symlink existing blotter.db (keeps your scraper working)"
  echo "    2) Create a new empty database"
  echo "    3) Copy existing blotter.db to this location"
  echo ""
  read -p "  Enter choice (1/2/3): " db_choice

  case "$db_choice" in
    1)
      read -p "  Enter full path to your existing blotter.db: " existing_db
      if [ -f "$existing_db" ]; then
        ln -s "$existing_db" "$DB_PATH"
        ok "Symlinked ${existing_db} → ${DB_PATH}"
      else
        fail "File not found: ${existing_db}"
      fi
      ;;
    2)
      log "Creating new database with schema..."
      npx tsx db/seed.ts 2>/dev/null || true
      ok "New database created at ${DB_PATH}"
      ;;
    3)
      read -p "  Enter full path to your existing blotter.db: " existing_db
      if [ -f "$existing_db" ]; then
        cp "$existing_db" "$DB_PATH"
        ok "Copied to ${DB_PATH}"
      else
        fail "File not found: ${existing_db}"
      fi
      ;;
    *)
      fail "Invalid choice"
      ;;
  esac
fi

# ── 8. Push database schema ──
log "Syncing database schema..."
cd "$APP_DIR"
npm run db:push 2>&1 | tail -5 || warn "db:push returned non-zero (may be OK if tables already exist)"
ok "Database schema synced"

# ── 9. PM2 Setup ──
log "Configuring PM2..."

# Check if already running
if pm2 describe "$APP_NAME" &> /dev/null; then
  log "App already managed by PM2, restarting..."
  pm2 restart "$APP_NAME"
else
  log "Starting app with PM2..."
  cd "$APP_DIR"
  pm2 start npm --name "$APP_NAME" -- start
fi

pm2 save
ok "PM2 configured — app running on port ${PORT}"

# ── 10. Nginx Config ──
log "Configuring Nginx..."

cat > "/etc/nginx/sites-available/${APP_NAME}" << 'NGINX'
server {
    listen 80;
    server_name _;  # Change to your domain: montanablotter.com

    # Gzip
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
    gzip_min_length 1000;

    # API calls → Node.js backend
    location /api/ {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 60s;
    }

    # Static assets (with caching)
    location /assets/ {
        proxy_pass http://localhost:3000;
        proxy_cache_bypass $http_upgrade;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Fonts
    location /fonts/ {
        proxy_pass http://localhost:3000;
        proxy_cache_bypass $http_upgrade;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Everything else → React SPA
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX

# Enable site
ln -sf "/etc/nginx/sites-available/${APP_NAME}" "/etc/nginx/sites-enabled/${APP_NAME}"

# Remove default if it conflicts
if [ -f "/etc/nginx/sites-enabled/default" ]; then
  rm "/etc/nginx/sites-enabled/default"
  ok "Removed default nginx site"
fi

# Test and reload nginx
nginx -t && systemctl reload nginx
ok "Nginx configured and reloaded"

# ── 11. Firewall ──
log "Configuring firewall..."
if command -v ufw &> /dev/null; then
  ufw allow 'Nginx Full' &> /dev/null || true
  ufw allow OpenSSH &> /dev/null || true
  ok "UFW rules updated"
else
  warn "UFW not installed, skipping firewall config"
fi

# ── 12. Done ──
echo ""
echo "========================================"
echo -e "  ${GREEN}DEPLOYMENT COMPLETE!${NC}"
echo "========================================"
echo ""
echo "  App directory: ${APP_DIR}"
echo "  Database:      ${DB_PATH}"
echo "  Node port:     ${PORT}"
echo "  Nginx config:  /etc/nginx/sites-available/${APP_NAME}"
echo ""
echo "  Check status:  pm2 status"
echo "  View logs:     pm2 logs ${APP_NAME}"
echo "  Restart:       pm2 restart ${APP_NAME}"
echo "  Stop:          pm2 stop ${APP_NAME}"
echo ""
echo "  Your site should be accessible at:"
echo "    http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_SERVER_IP')"
echo ""

# ── 13. Optional: SSL ──
echo "  Want SSL? Run: certbot --nginx -d montanablotter.com -d www.montanablotter.com"
echo ""
ok "All done!"
