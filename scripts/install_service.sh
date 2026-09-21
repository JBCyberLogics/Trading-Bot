#!/bin/bash
# Install systemd service

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/smc-bot.service"

sudo cp "$SERVICE_FILE" /etc/systemd/system/smc-bot.service
sudo systemctl daemon-reload
sudo systemctl enable smc-bot
sudo systemctl start smc-bot

echo "Service installed and started"
echo "Check status: sudo systemctl status smc-bot"
echo "View logs: sudo journalctl -u smc-bot -f"
