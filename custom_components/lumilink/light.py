"""LumiLink light entity."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHAR_COMMAND,
    CMD_NEXT_COLOR,
    CMD_RESET_OUTPUT,
    CMD_TOGGLE_LIGHT,
    COLOR_NAMES,
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    PARAM_OUTPUT_1,
    VALUE_LIGHT_OFF,
    VALUE_LIGHT_ON,
)

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY = 15  # seconds between reconnect attempts


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    address = entry.data[CONF_ADDRESS]
    name = entry.data.get(CONF_NAME, f"LumiLink {address[-5:]}")
    async_add_entities([LumiLinkLight(hass, address, name, entry.entry_id)])


def _pkt(cmd: int, param: int, value: int = 0) -> bytes:
    return bytes([cmd, param, value, 0, 0, 0])


class LumiLinkLight(LightEntity):
    """Represents the SEAMAID LumiLink pool light."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = COLOR_NAMES
    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, address: str, name: str, entry_id: str
    ) -> None:
        self.hass = hass
        self._address = address
        self._entry_id = entry_id
        safe_addr = address.replace(":", "_").replace("-", "_")
        self._attr_unique_id = f"lumilink_{safe_addr}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="SEAMAID / Alphadif",
            model="LumiLink Bluetooth Module (504017)",
        )
        self._client = None
        self._is_on: bool = False
        self._color_index: int = 0
        self._available: bool = False
        self._cmd_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def effect(self) -> str | None:
        if 0 <= self._color_index < len(COLOR_NAMES):
            return COLOR_NAMES[self._color_index]
        return None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        self.hass.async_create_task(self._connect())

    async def async_will_remove_from_hass(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        await self._disconnect()

    # ── BLE connection management ─────────────────────────────────────────────

    async def _connect(self) -> None:
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                return
            try:
                from homeassistant.components.bluetooth import (
                    async_ble_device_from_address,
                )
                from bleak_retry_connector import establish_connection, BleakClient

                ble_device = async_ble_device_from_address(
                    self.hass, self._address, connectable=True
                )
                if ble_device is None:
                    _LOGGER.warning(
                        "LumiLink %s not found in BT scanner – will retry", self._address
                    )
                    self._schedule_reconnect()
                    return

                self._client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self._address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=3,
                )

                if not self._client.is_connected:
                    _LOGGER.warning("LumiLink %s: connect() returned False", self._address)
                    self._schedule_reconnect()
                    return

                _LOGGER.info("LumiLink %s connected", self._address)
                # Reset output so tracker and lamp are in sync
                await self._send_reset_internal()
                self._available = True
                self.async_write_ha_state()

            except Exception as exc:
                _LOGGER.error("LumiLink %s connection error: %s", self._address, exc)
                self._available = False
                self.async_write_ha_state()
                self._schedule_reconnect()

    def _on_disconnect(self, _client: Any) -> None:
        """Called by bleak from its internal thread when BLE drops."""
        _LOGGER.warning("LumiLink %s disconnected", self._address)
        self._available = False
        self._client = None
        # Must marshal back to HA event loop
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect)

    def _handle_disconnect(self) -> None:
        self.async_write_ha_state()
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def _delayed_connect():
            await asyncio.sleep(RECONNECT_DELAY)
            await self._connect()

        self._reconnect_task = self.hass.async_create_task(_delayed_connect())

    async def _disconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None
        client = self._client
        self._client = None
        self._available = False
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _ensure_connected(self) -> bool:
        if self._client and self._client.is_connected:
            return True
        await self._connect()
        return bool(self._client and self._client.is_connected)

    # ── Raw BLE write ─────────────────────────────────────────────────────────

    async def _write(self, data: bytes) -> bool:
        if not await self._ensure_connected():
            return False
        try:
            await self._client.write_gatt_char(CHAR_COMMAND, data, response=True)
            return True
        except Exception as exc:
            _LOGGER.error("LumiLink BLE write error: %s", exc)
            self._available = False
            self.async_write_ha_state()
            self._schedule_reconnect()
            return False

    # ── Color navigation helpers ──────────────────────────────────────────────

    async def _send_reset_internal(self) -> None:
        """Send RESET and wait for it to complete."""
        await self._write(_pkt(CMD_RESET_OUTPUT, PARAM_OUTPUT_1))
        self._color_index = 0
        await asyncio.sleep(2.5)  # wait for reset animation

    async def _goto_color(self, target: int) -> bool:
        """Navigate to target color index using NEXT steps (with reset shortcut)."""
        target = target % len(COLOR_NAMES)
        steps_forward = (target - self._color_index) % len(COLOR_NAMES)
        steps_via_reset = target  # from 0 → target (only valid for fixed colors 0–10)

        if target < 11 and steps_via_reset < steps_forward:
            ok = await self._write(_pkt(CMD_RESET_OUTPUT, PARAM_OUTPUT_1))
            if not ok:
                return False
            self._color_index = 0
            await asyncio.sleep(2.5)
            steps_forward = target

        for _ in range(steps_forward):
            ok = await self._write(_pkt(CMD_NEXT_COLOR, PARAM_OUTPUT_1))
            if not ok:
                return False
            self._color_index = (self._color_index + 1) % len(COLOR_NAMES)
            await asyncio.sleep(0.5)

        return True

    # ── HA service handlers ───────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        async with self._cmd_lock:
            effect = kwargs.get(ATTR_EFFECT)
            if effect and effect in COLOR_NAMES:
                target = COLOR_NAMES.index(effect)
                if target != self._color_index:
                    ok = await self._goto_color(target)
                    if ok:
                        self._color_index = target

            ok = await self._write(_pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_ON))
            if ok:
                self._is_on = True
                self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        async with self._cmd_lock:
            ok = await self._write(_pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_OFF))
            if ok:
                self._is_on = False
                self.async_write_ha_state()
