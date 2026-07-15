#!/usr/bin/env bash
# LumiLink Pi bridge installer. Run on a Raspberry Pi (Raspberry Pi OS Lite):
#   curl -sSL https://raw.githubusercontent.com/TimLuist1/ha-lumilink/main/pi-bridge/install.sh | sudo bash
# or, from a checkout:  sudo bash install.sh
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/TimLuist1/ha-lumilink/main/pi-bridge"
APP=/opt/lumilink-bridge
CFG=/etc/lumilink-bridge

echo "== LumiLink Pi bridge installer =="
if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo/root."; exit 1; fi

echo "-- installing system packages --"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip bluez curl ca-certificates

echo "-- making sure Bluetooth is up --"
systemctl enable bluetooth >/dev/null 2>&1 || true
systemctl start bluetooth >/dev/null 2>&1 || true
rfkill unblock bluetooth 2>/dev/null || true

echo "-- app dir $APP --"
mkdir -p "$APP" "$CFG"

# Fetch the bridge script: prefer a local copy, else download.
if [ -f "$(dirname "$0")/lumilink_bridge.py" ]; then
  cp "$(dirname "$0")/lumilink_bridge.py" "$APP/lumilink_bridge.py"
else
  curl -fsSL "$REPO_RAW/lumilink_bridge.py" -o "$APP/lumilink_bridge.py"
fi

echo "-- python venv + deps --"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet bleak aiomqtt aiohttp pyyaml

echo "-- config --"
if [ ! -f "$CFG/config.yaml" ]; then
  if [ -f "$(dirname "$0")/config.example.yaml" ]; then
    cp "$(dirname "$0")/config.example.yaml" "$CFG/config.yaml"
  else
    curl -fsSL "$REPO_RAW/config.example.yaml" -o "$CFG/config.yaml"
  fi
  chmod 600 "$CFG/config.yaml"
  echo "   >> EDIT $CFG/config.yaml (MQTT user/pass + HA token) <<"
else
  echo "   keeping existing $CFG/config.yaml"
fi

echo "-- systemd service --"
if [ -f "$(dirname "$0")/lumilink-bridge.service" ]; then
  cp "$(dirname "$0")/lumilink-bridge.service" /etc/systemd/system/lumilink-bridge.service
else
  curl -fsSL "$REPO_RAW/lumilink-bridge.service" -o /etc/systemd/system/lumilink-bridge.service
fi
systemctl daemon-reload
systemctl enable lumilink-bridge >/dev/null 2>&1 || true

echo ""
echo "== Done =="
echo "1) Edit config:   sudo nano $CFG/config.yaml"
echo "2) Start:         sudo systemctl restart lumilink-bridge"
echo "3) Logs:          journalctl -u lumilink-bridge -f"
