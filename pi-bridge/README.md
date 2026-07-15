# LumiLink Raspberry Pi Bridge

A tiny service for a **Raspberry Pi placed next to the pool module**. The Pi
talks to the SEAMAID LumiLink over its **native Bluetooth** (reliable, unlike a
virtualised/USB‑passthrough adapter on the HA host) and exposes the light to
Home Assistant over **MQTT auto‑discovery**.

**Why:** on this HA install the Bluetooth adapter is passed through to a VM and
every BLE connection is cancelled by BlueZ (`br-connection-canceled`). A phone
or Mac connects fine because it has a real native adapter — the Pi does too.

**Self‑healing:** the module only accepts new connections for ~3 minutes after
power‑on. The bridge power‑cycles the module through your Meross plug (via the
HA API) whenever it needs to (re)connect, then grabs the connection in that
window and holds it.

You get, in Home Assistant, on one device:
- a **light** (on/off + 16 effects), and
- a **“Sync lamps”** button.

---

## What you need
- Raspberry Pi 3B (built‑in Wi‑Fi + Bluetooth) + a microSD card + power supply.
- The Pi within a few metres of the pool module.
- Home Assistant with the **Mosquitto broker** add‑on (you already have it).

## 1) Create an MQTT user (Home Assistant)
Settings → People → **Users** → Add user, e.g. `mqtt_pi` / a password. (Any HA
user works with the Mosquitto add‑on’s default auth.) Note the user + password.

## 2) Create a long‑lived token (for the auto power‑cycle)
Your HA profile → **Security** → *Long‑lived access tokens* → create one, copy it.

## 3) Flash Raspberry Pi OS Lite
Use **Raspberry Pi Imager** → *Raspberry Pi OS Lite (64‑bit)*. Click the gear
(⚙, “Edit settings”) and set:
- **Hostname:** `lumilink-pi`
- **Enable SSH** (password auth)
- **Username / password** (remember them)
- **Wireless LAN:** your Wi‑Fi SSID + password + country
- **Locale / timezone**

Flash, put the card in the Pi, power it on **next to the module**.

## 4) Install the bridge
SSH in and run the one‑liner:
```bash
ssh <user>@lumilink-pi.local
curl -sSL https://raw.githubusercontent.com/TimLuist1/ha-lumilink/main/pi-bridge/install.sh | sudo bash
```

## 5) Configure
```bash
sudo nano /etc/lumilink-bridge/config.yaml
```
Fill in the MQTT `username`/`password` (step 1) and the HA `token` (step 2). The
Meross `switch_entity` is already set to your plug; the module is auto‑found by
name, so you can leave `address` empty.

## 6) Start it
```bash
sudo systemctl restart lumilink-bridge
journalctl -u lumilink-bridge -f
```
You should see it power‑cycle the module, connect, and go “online”. In Home
Assistant a **LumiLink Pool Light** device appears automatically (Settings →
Devices & Services → MQTT). Toggle it — the pool light responds.

---

## How it behaves
- On start it tries to connect; if the module isn’t in its pairing window it
  power‑cycles the plug (15 s off), waits, and connects in the fresh window.
- Once connected it **holds** the link (like your Mac). A command locks it in.
- If the link ever drops, it repeats the power‑cycle + reconnect automatically.
- **Sync mode** (default on): each colour change resets both lamps to white
  first, then steps slowly, so the two parallel lamps stay identical. Turn it
  off in the config for faster (but possibly drifting) changes.

## Troubleshooting
- `journalctl -u lumilink-bridge -f` shows everything.
- *Never connects:* make sure the Pi is close to the module and the Meross
  `switch_entity` / HA `token` are correct (so it can power‑cycle). Test the BLE
  manually: `bluetoothctl scan on` should list `LUMILINK-…`.
- *No device in HA:* check the Mosquitto add‑on is running and the MQTT
  `username`/`password` are right; `journalctl` will show MQTT errors.
- *Wrong colours / lamps drift:* press **Sync lamps**, or increase `step_delay`.

## Optional: auto‑install on first boot
Advanced users can append the install command to the `firstrun.sh` that
Raspberry Pi Imager writes to the boot partition, so the Pi self‑installs on
first boot — but the SSH one‑liner above is simpler and easier to verify.
