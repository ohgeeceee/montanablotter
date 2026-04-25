#!/bin/bash
# Montana Blotter - Fresh Server Deployment Script
set -e

echo "=== Montana Blotter Deployment ==="
echo ""

# 1. System dependencies
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip sqlite3

# 2. Clone repo (skip if already cloned)
if [ ! -f "/root/montanablotter/app.py" ]; then
    echo "[2/7] Cloning repository..."
    git clone https://github.com/ohjoncurrie-netizen/policedashboard.git /root/montanablotter
else
    echo "[2/7] Repo already present, pulling latest..."
    # git -C /root/montanablotter pull
fi

cd /root/montanablotter

# 3. Virtualenv + dependencies
echo "[3/7] Setting up Python environment..."
python3 -m venv venv
venv/bin/pip install -q -r requirements.txt

# 4. Environment config check
if [ ! -f ".env" ]; then
    echo ""
    echo "ERROR: .env not found."
    echo "Create it from .env.example before continuing:"
    echo ""
    echo "  cp .env.example .env"
    echo "  nano .env"
    echo ""
    echo "Then re-run: bash deploy.sh"
    exit 1
fi

# 5. Database init + migration
echo "[5/7] Initializing database..."
mkdir -p uploads records
venv/bin/python init_db.py

# 6. Seed admin user if no users exist
USER_COUNT=$(venv/bin/python -c "import sqlite3, config; conn = sqlite3.connect(config.DB_PATH); print(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0])")
if [ "$USER_COUNT" -eq "0" ]; then
    echo "[6/7] Seeding admin user..."
    echo "Set MB_ADMIN_BOOTSTRAP_PASSWORD, or run seed_admin.py interactively."
    venv/bin/python seed_admin.py admin
else
    echo "[6/7] Admin user already exists, skipping seed."
fi

# 7. Systemd service
echo "[7/7] Installing systemd service..."
cp montanablotter.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now montanablotter

# 8. Crontab
echo ""
echo "Installing crontab..."
crontab crontab.txt

echo ""
echo "=== Deployment complete ==="
echo ""
systemctl status montanablotter --no-pager
echo ""
echo "App running at http://$(hostname -I | awk '{print $1}'):5000"
