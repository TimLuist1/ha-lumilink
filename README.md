# LumiLink Pool Light — Home Assistant Integration

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/TimLuist1/ha-lumilink/actions/workflows/validate.yml/badge.svg)](https://github.com/TimLuist1/ha-lumilink/actions/workflows/validate.yml)

Control your **SEAMAID LumiLink** Bluetooth pool lights directly from Home Assistant.

## Features

- Turn pool lights on/off — with **real state read-back** from the module
- 16 colour effects / modes — 11 fixed colours (White, Blue, Cyan, Turquoise,
  Magenta, Green, Orange, Yellow, Amber, Red, Pink) + 5 automatic modes
  (Slow, Medium, Fast, Flash, Strobe)
- **Lamp sync**: a dedicated *Sync lamps* button (same as the remote's
  "Synchron" button) plus an optional sync mode that anchors every colour
  change with a reset, so lamps wired in parallel stay perfectly in sync
- Configurable pulse timing (Options), instant reconnect the moment the
  module is seen advertising, diagnostics download
- Auto-discovery via Bluetooth LE (service UUID + `LUMILINK-*` name)
- Works through an ESPHome Bluetooth proxy for long range
- German + English UI

## Requirements

- Home Assistant ≥ 2024.12 with the Bluetooth integration enabled
- SEAMAID LumiLink BLE module (`LUMILINK-*`, model 504017)
- A Bluetooth adapter on the HA host **or** an ESPHome Bluetooth proxy in range
  (see [`esphome/pool-bt-proxy.yaml`](esphome/pool-bt-proxy.yaml))
- Lights must be powered on during setup

---

## Installation via HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/TimLuist1/ha-lumilink` as **Integration**
4. Search for **LumiLink** and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration** and search for **LumiLink**

## Manual Installation

1. Copy the `custom_components/lumilink/` folder to your HA config directory under `custom_components/`
2. Restart Home Assistant
3. Add the integration via **Settings → Devices & Services**

---

## Configuration

The integration supports automatic Bluetooth discovery. When your LumiLink lights are powered on and in range, HA will suggest adding them automatically. You can also add them manually by entering the Bluetooth MAC address.

## Usage

After adding the integration you get, on one device:

- a **light entity** (on/off + 16 effects), and
- a **`Sync lamps` button** — the same function as the remote's *Synchro* key:
  resets both lamps so they line up again.

| Action | How |
|--------|-----|
| Turn on/off | Standard HA light toggle |
| Change colour | Set **Effect** in the light card (11 colours + 5 auto modes) |
| Re-sync two lamps | Press the **Sync lamps** button (or the card's Sync button) |
| Tune sync timing | Integration → **Configure** (sync mode + pulse delay) |

### LumiLink Card (bundled)

The integration ships a custom Lovelace card that shows the pool lamp
illuminating live in the selected colour, with on/off, all 16 colours/modes
and a sync button. It's **served automatically** — no manual resource setup.

1. Edit a dashboard → **Add card** → search **“LumiLink Card”** (it has a live
   preview), or add it in YAML:

```yaml
type: custom:lumilink-card
entity: light.lumilink_pool_light
sync_entity: button.lumilink_pool_light_sync_lamps   # optional
name: Pool
```

2. The card is fully **resizable** in the new sections view (drag in the
   Layout tab). After updating the integration, **hard-refresh** the browser
   (Ctrl/Cmd-Shift-R) once so the new card version loads.

> Why colours only cycle: the module has no "set colour X" command — the app
> and remote only step to the *next* colour. The integration steps for you and,
> in **sync mode**, resets first so both parallel-wired lamps stay identical.

## Troubleshooting

- **Shows up but stays "unavailable" / won't connect?** The module only accepts
  **new** connections during the **first 3 minutes after power-on** (a security
  feature of the SEAMAID firmware). If HA didn't connect in that window,
  **switch the module's power off for ~15 seconds and on again**, then let HA
  reconnect. Once connected, HA keeps the link open.
- **Not discovered at all?** Make sure the lights are powered on and within range
  of a Bluetooth adapter or ESPHome proxy. The ESP32 proxy near the pool is the
  most reliable option (the module is IP67 and often far from the HA host).
- **Toggle does nothing?** Enable debug logging (below) and check for `BUSY` /
  `ERROR` notifications — the module silently ignores commands until it reports
  the matching availability bit, which the integration now waits for.
- **Lamps out of sync (two lamps on one output)?** Power-cycle both lamps
  simultaneously, then select the **Weiß / White** effect (RESET) to realign.

### Debug logging

```yaml
logger:
  logs:
    custom_components.lumilink: debug
    bleak_retry_connector: debug
```

---

## Protocol notes

The LumiLink module exposes a single BLE service `bc3b4e71-…`. Commands are written to characteristic `bc3b4e72-…`. Only **Output 1** (`0x01`) is valid — both lamps are wired in parallel on that single output.

## License

MIT — see [LICENSE](LICENSE)
