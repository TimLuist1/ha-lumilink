"""Diagnostics support for LumiLink."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Any
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    light = getattr(entry.runtime_data, "light", None)

    light_info: dict[str, Any] = {}
    if light is not None:
        light_info = {
            "available": light.available,
            "is_on": light.is_on,
            "effect": light.effect,
            "assumed_state": light._attr_assumed_state,
            "busy_status": (
                f"0x{light._busy_status:02x}"
                if light._busy_status is not None
                else None
            ),
            "busy_update_count": light._busy_update_count,
            "notifications_active": light._notifications_active,
            "last_error": (
                light._last_error.hex() if light._last_error else None
            ),
            "write_with_response": dict(light._write_with_response),
            "sync_mode": light._sync_mode,
            "step_delay": light._step_delay,
        }

    return {
        "domain": DOMAIN,
        "entry_data": dict(entry.data),
        "entry_options": dict(entry.options),
        "light": light_info,
    }
