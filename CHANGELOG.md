# Changelog

## 1.4.0

**Lamp sync + control card.** This release fixes the "lamps drift out of sync"
problem and adds a bundled control card with a live-illuminating lamp.

### Added
- **Sync lamps button** — mirrors the physical remote's *Synchro* key. Sends a
  reset so both parallel-wired lamps land on white together, back in sync.
- **Sync mode** (default on) — anchors every colour change with a reset, then
  steps slowly to the target, so the two lamps stay identical. Toggle it and
  the step delay under the integration's **Configure** (options).
- **Real on/off read-back** — `CHAR_COMMAND` bit 4 reports the true light
  state; the entity drops "assumed state" once read-back is confirmed, and
  on/off commands are verified (and retried once) against it.
- **Bundled LumiLink Lovelace card** (`custom:lumilink-card`) — resizable, with
  a photorealistic SEAMAID flat floodlight that glows in the selected colour,
  on/off, 11 colours, 5 auto modes and a sync button. Served automatically; no
  manual resource setup. Includes a visual editor.
- **Diagnostics** download and a new brand icon/logo (the actual lamp).

### Changed
- Colour stepping is now slow and BUSY-gated (the module drops fast pulses,
  which caused the desync). Colour transitions run in the background so the
  service call returns immediately.
- Instant reconnect the moment the module is seen advertising (Bluetooth
  presence callback) instead of only on the periodic retry.
- Migrated to `entry.runtime_data`; `PARALLEL_UPDATES = 0`; diagnostic
  attributes excluded from the recorder.
- Minimum Home Assistant 2024.12.

### Notes
- The module has **one output**; both lamps are wired in parallel on it
  (output `0x02` does not exist — verified against the hardware). Independent
  per-lamp control over BLE is therefore not possible; sync keeps them aligned.

## 1.3.0

- Reliable BLE control: write-type auto-detect with fallback, connection
  verification, BUSY-bit gating before every command, assumed state.
- MIT licence, ESPHome Bluetooth-proxy config, documentation of the
  3-minute pairing window.

## 1.2.1 and earlier

- Initial HACS integration, Bluetooth discovery, light entity with effects.
