"""Registers the bundled LumiLink Lovelace card with the HA frontend."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.integration_platform import async_process_integration_platforms

from .const import CARD_FILENAME, CARD_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card JS and inject it into the frontend (once per HA run).

    Uses the current (2026) APIs:
      * http.async_register_static_paths(StaticPathConfig(...))
      * frontend.add_extra_js_url(...)  – works for storage- and YAML-mode
        dashboards alike, no Lovelace-resource bookkeeping required.
    """
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    # Version for cache-busting – read from the integration manifest.
    version = "1.4.0"
    try:
        from homeassistant.loader import async_get_integration

        integration = await async_get_integration(hass, DOMAIN)
        version = str(integration.version or version)
        card_path = str(integration.file_path / CARD_FILENAME)
    except Exception:  # noqa: BLE001
        card_path = hass.config.path(f"custom_components/{DOMAIN}/{CARD_FILENAME}")

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, card_path, cache_headers=False)]
        )
    except (RuntimeError, ValueError) as exc:
        # Path already registered (e.g. a second config entry) – harmless.
        _LOGGER.debug("LumiLink card static path already registered: %s", exc)

    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    _LOGGER.info("LumiLink Lovelace card registered at %s (v%s)", CARD_URL, version)
