"""Config flow for LumiLink."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    CONF_STEP_DELAY,
    CONF_SYNC_MODE,
    DEFAULT_STEP_DELAY,
    DEFAULT_SYNC_MODE,
    DOMAIN,
    SERVICE_UUID,
)


class LumiLinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for LumiLink."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, str] = {}  # address → name

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> LumiLinkOptionsFlow:
        return LumiLinkOptionsFlow()

    # ── Automatic Bluetooth discovery ────────────────────────────────────────

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Called automatically when HA discovers a LumiLink via service UUID."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or f"LumiLink {discovery_info.address[-5:]}"
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Confirm auto-discovered device."""
        assert self._discovery_info is not None
        info = self._discovery_info
        display_name = info.name or f"LumiLink {info.address[-5:]}"

        if user_input is not None:
            return self.async_create_entry(
                title=display_name,
                data={CONF_ADDRESS: info.address, CONF_NAME: display_name},
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": display_name},
        )

    # ── Manual setup ─────────────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Manual setup: scan for nearby LumiLink devices."""
        errors: dict[str, str] = {}

        # Gather already-discovered LumiLink devices from HA's BT scanner
        # Match by service UUID *or* by device name prefix (LUMILINK-...)
        current_addresses = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in current_addresses:
                continue
            uuids = [u.lower() for u in (info.service_uuids or [])]
            name_matches = (info.name or "").upper().startswith("LUMILINK")
            if SERVICE_UUID.lower() in uuids or name_matches:
                self._discovered_devices[info.address] = (
                    info.name or f"LumiLink {info.address[-5:]}"
                )

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = self._discovered_devices.get(address, f"LumiLink {address[-5:]}")
            return self.async_create_entry(
                title=name,
                data={CONF_ADDRESS: address, CONF_NAME: name},
            )

        if self._discovered_devices:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            addr: f"{name} ({addr})"
                            for addr, name in self._discovered_devices.items()
                        }
                    )
                }
            )
        else:
            # No devices found via scan → allow manual address entry
            schema = vol.Schema(
                {vol.Required(CONF_ADDRESS): str}
            )
            errors["base"] = "no_devices_found"

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )


class LumiLinkOptionsFlow(config_entries.OptionsFlow):
    """Options: lamp-sync behaviour and pulse timing."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SYNC_MODE,
                    default=options.get(CONF_SYNC_MODE, DEFAULT_SYNC_MODE),
                ): bool,
                vol.Optional(
                    CONF_STEP_DELAY,
                    default=options.get(CONF_STEP_DELAY, DEFAULT_STEP_DELAY),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.3, max=5.0)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
