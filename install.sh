#!/bin/bash
# mixer — stamped by the architect
# Install Shadow's mesh snapshot system
set -euo pipefail

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║  mixer — shadow's mesh snapshots      ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

INSTALL_DIR="/usr/local/lib/mixer"
CONFIG_DIR="/etc/mixer"
DATA_DIR="/srv/mixer"

# Install
sudo mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$DATA_DIR/snapshots" "$DATA_DIR/ring"
sudo cp mixer.py "$INSTALL_DIR/mixer.py"
sudo chmod +x "$INSTALL_DIR/mixer.py"
sudo ln -sf "$INSTALL_DIR/mixer.py" /usr/local/bin/mixer

# Config
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    sudo cp config.json "$CONFIG_DIR/config.json"
    echo "  Config installed to $CONFIG_DIR/config.json"
    echo "  Edit machine list to match your mesh."
else
    echo "  Config exists — keeping current."
fi

# Systemd
sudo cp mixer.service /etc/systemd/system/mixer.service
sudo systemctl daemon-reload
sudo systemctl enable mixer.service

echo ""
echo "  Installed. Commands:"
echo "    mixer status      — show mesh health"
echo "    mixer run         — snapshot + distribute now"
echo "    mixer daemon      — run every 6 hours"
echo ""
echo "  Start the daemon:"
echo "    sudo systemctl start mixer"
echo ""
echo "  Shadow moves in silence."
echo ""
