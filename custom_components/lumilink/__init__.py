"""LumiLink Home Assistant integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .light import LumiLinkLight

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.BUTTON]


@dataclass
class LumiLinkRuntimeData:
    """Shared per-entry runtime objects (filled by the light platform)."""

    light: "LumiLinkLight | None" = field(default=None)


type LumiLinkConfigEntry = ConfigEntry[LumiLinkRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: LumiLinkConfigEntry) -> bool:
    entry.runtime_data = LumiLinkRuntimeData()
    # Serve & register the bundled Lovelace card (once per HA run, best-effort).
    try:
        from .frontend import async_register_card

        await async_register_card(hass)
    except Exception:  # noqa: BLE001  – card is a nice-to-have, never block setup
        pass
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # NOTE: no options-update reload listener on purpose – the light entity
    # reads options live, and a reload would drop the precious BLE connection
    # (the module only whitelists new centrals for 3 min after power-on).
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LumiLinkConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
