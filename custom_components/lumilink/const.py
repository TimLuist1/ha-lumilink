"""Constants for the LumiLink integration."""

DOMAIN = "lumilink"

CONF_ADDRESS = "address"
CONF_NAME = "name"

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
