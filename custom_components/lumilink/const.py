"""Constants for the LumiLink integration."""

DOMAIN = "lumilink"

# Bundled Lovelace card (served from custom_components/lumilink/www/)
CARD_URL = "/lumilink/lumilink-card.js"
CARD_FILENAME = "www/lumilink-card.js"

CONF_ADDRESS = "address"
CONF_NAME = "name"

# Options (Optionsfluss / entry.options)
CONF_STEP_DELAY = "step_delay"
CONF_SYNC_MODE = "sync_mode"
# Pause zwischen NEXT-Pulsen bei Mehrfach-Schritten. Die Original-App kennt
# nur einen manuellen "Nächste Farbe"-Knopf (menschliches Tempo) und desynct
# nie; schnelle Pulsfolgen verschluckt eine der parallel verdrahteten Lampen.
DEFAULT_STEP_DELAY = 1.5
# Sync-Modus: jede Farbwahl ankert per RESET (beide Lampen → Weiß, synchron)
# und steppt dann langsam zum Ziel.
DEFAULT_SYNC_MODE = True

# BLE UUIDs
SERVICE_UUID  = "bc3b4e71-ee54-4f09-8f28-e865150c20b0"
CHAR_COMMAND  = "bc3b4e72-ee54-4f09-8f28-e865150c20b0"
CHAR_TIMER    = "bc3b4e73-ee54-4f09-8f28-e865150c20b0"
CHAR_BUSY     = "bc3b4e74-ee54-4f09-8f28-e865150c20b0"
CHAR_ERROR    = "bc3b4e75-ee54-4f09-8f28-e865150c20b0"

# Command IDs
CMD_TOGGLE_LIGHT   = 0x01
CMD_NEXT_COLOR     = 0x03
CMD_RESET_OUTPUT   = 0x04

PARAM_OUTPUT_1     = 0x01
VALUE_LIGHT_ON     = 0x01
VALUE_LIGHT_OFF    = 0x00

# CHAR_COMMAND ist auch LESBAR (App-Bytecode: _readLightState):
# Bit 4 (0x10) des ersten Bytes = Licht an/aus
LIGHT_STATE_BIT    = 0x10

# 11 fixed colors + 5 auto modes
# Order calibrated empirically (Lamp 1 reference, 22.05.2026)
COLOR_NAMES: list[str] = [
    "Weiß",               # 0  – after RESET
    "Blau",               # 1
    "Cyan",               # 2
    "Türkis",             # 3
    "Magenta",            # 4
    "Grün",               # 5
    "Orange",             # 6
    "Gelb",               # 7
    "Bernstein",          # 8
    "Rot",                # 9
    "Rosa/Pink",          # 10
    "Auto: Langsam",      # 11
    "Auto: Mittel",       # 12
    "Auto: Schnell",      # 13
    "Auto: Flash",        # 14
    "Auto: Stroboskop",   # 15
]
