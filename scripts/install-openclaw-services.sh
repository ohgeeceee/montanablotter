#!/bin/bash
# Install OpenClaw agent systemd services with auto-heartbeat
set -e

REPO=/root/montanablotter
SERVICE_SRC=$REPO/scripts/openclaw-agent@.service
SERVICE_DST=/etc/systemd/system/openclaw-agent@.service

echo "Installing template service..."
cp "$SERVICE_SRC" "$SERVICE_DST"

systemctl daemon-reload

for agent in main reporter scout clerk bailbot; do
    echo "Enabling openclaw-agent@${agent}..."
    systemctl enable "openclaw-agent@${agent}"
done

echo ""
echo "Services installed. Start them with:"
echo "  systemctl start openclaw-agent@reporter"
echo "  systemctl start openclaw-agent@scout"
echo "  systemctl start openclaw-agent@clerk"
echo "  systemctl start openclaw-agent@bailbot"
echo "  systemctl start openclaw-agent@main"
echo ""
echo "Check status:"
echo "  systemctl status openclaw-agent@reporter"
echo "  journalctl -u openclaw-agent@reporter -f"
