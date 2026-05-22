# LumiLink Pool Light — Home Assistant Integration

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/TimLuist1/ha-lumilink/actions/workflows/validate.yml/badge.svg)](https://github.com/TimLuist1/ha-lumilink/actions/workflows/validate.yml)

Control your **SEAMAID LumiLink** Bluetooth pool lights directly from Home Assistant.

## Features

- Turn pool lights on/off
- 16 colour effects (Warm White, Cold White, Red, Blue, Green, Cyan, Magenta, Yellow, Orange, Purple, Pink, RGB Cycle, Colour Cycle, Flash, Strobe, Fade)
- Auto-discovery via Bluetooth LE
- German + English UI

## Requirements

- Home Assistant ≥ 2023.9 with Bluetooth integration enabled
- SEAMAID LumiLink BLE module (`LUMILINK-*`)
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

After adding the integration a **light entity** is created. You can:

| Action | How |
|--------|-----|
| Turn on/off | Standard HA light toggle |
| Change colour | Set **Effect** in the light card |
| Reset sequence | Select effect **Reset / Warm White** |

## Troubleshooting

- **Not discovered?** Make sure lights are powered on and within ~5 m of your HA host.
- **Connection drops?** Power-cycle both lights simultaneously so they stay in sync.
- **Wrong colour after power-cycle?** Use the **Reset** effect to jump back to warm white.

---

## Protocol notes

The LumiLink module exposes a single BLE service `bc3b4e71-…`. Commands are written to characteristic `bc3b4e72-…`. Only **Output 1** (`0x01`) is valid — both lamps are wired in parallel on that single output.

## License

MIT — see [LICENSE](LICENSE)
