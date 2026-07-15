"""LumiLink sync button.

Mirrors the "Synchron" button on the physical remote: sends RESET, after
which both lamps blink twice and land on white together – back in sync.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Push-based; the underlying light serializes all BLE writes itself.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    address = entry.data[CONF_ADDRESS]
    async_add_entities([LumiLinkSyncButton(hass, entry, address)])


class LumiLinkSyncButton(ButtonEntity):
    """Re-syncs both lamps (RESET → both land on white together)."""

    _attr_has_entity_name = True
    _attr_translation_key = "sync_lamps"
    _attr_icon = "mdi:sync"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
        self.hass = hass
        self._entry = entry
        safe_addr = address.replace(":", "_").replace("-", "_")
        self._attr_unique_id = f"lumilink_{safe_addr}_sync"
        # Attach to the same device as the light entity.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, address)})

    async def async_press(self) -> None:
        light = self._entry.runtime_data.light
        if light is None:
            raise HomeAssistantError("LumiLink light entity is not set up yet")
        if not light.available:
            raise HomeAssistantError(
                "LumiLink is not connected – cannot sync right now"
            )
        _LOGGER.info("LumiLink sync button pressed – resetting both lamps")
        await light.async_sync_lamps()
