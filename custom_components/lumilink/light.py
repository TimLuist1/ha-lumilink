"""LumiLink light entity.

Protocol faithfully ported from the reverse-engineered, hardware-verified
desktop controller (lumilink_ble.py) of the original SEAMAID LumiLink app
(com.alphadif.seamaid.lumilink). The transport is adapted to Home Assistant's
Bluetooth stack (bleak_retry_connector / habluetooth) for BlueZ + ESPHome
proxy reliability.
"""
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHAR_BUSY,
    CHAR_COMMAND,
    CHAR_ERROR,
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

# BUSY status byte bit flags (verified against the original app bytecode):
#   bit 0 (0x01) = isOutput1Busy  – module currently busy
#   bit 1 (0x02) = isLight1Available   – LIGHT toggle accepted
#   bit 2 (0x04) = isNext1Available    – NEXT colour accepted
#   bit 3 (0x08) = isReset1Available   – RESET accepted
# If the matching bit is NOT set, the lamp silently ignores the command.
BUSY_BIT_BUSY = 0x01
BUSY_BIT_LIGHT = 0x02
BUSY_BIT_NEXT = 0x04
BUSY_BIT_RESET = 0x08

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY = 15  # seconds between reconnect attempts
CONNECT_MAX_ATTEMPTS = 6
WRITE_TIMEOUT = 6.0  # seconds per GATT write
NUM_FIXED_COLORS = 11  # indices 0..10 are static colours; 11..15 are auto modes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    address = entry.data[CONF_ADDRESS]
    name = entry.data.get(CONF_NAME, f"LumiLink {address[-5:]}")
    async_add_entities([LumiLinkLight(hass, address, name, entry.entry_id)])


def _pkt(cmd: int, param: int, value: int = 0) -> bytes:
    """Build a 6-byte command packet: [cmd, param, value, 0, 0, 0]."""
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
    # The module has no readable on/off state characteristic – state is tracked
    # optimistically from the commands we send, so it is an assumed state.
    _attr_assumed_state = True

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
            connections={(CONNECTION_BLUETOOTH, address)},
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
        self._busy_status: int | None = None
        self._notifications_active: bool = False
        self._last_error: bytes | None = None
        self._closing: bool = False
        # Cached write-response preference per characteristic (auto-detected once).
        self._write_with_response: dict[str, bool] = {}

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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._busy_status is not None:
            attrs["busy_status"] = f"0x{self._busy_status:02x}"
        if self._last_error is not None:
            attrs["last_error"] = self._last_error.hex()
        return attrs

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        self.hass.async_create_task(self._connect())

    async def async_will_remove_from_hass(self) -> None:
        self._closing = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        await self._disconnect()

    # ── BLE connection management ─────────────────────────────────────────────

    def _get_ble_device(self):
        """Return a fresh connectable BLEDevice from HA's Bluetooth manager."""
        from homeassistant.components.bluetooth import async_ble_device_from_address

        return async_ble_device_from_address(self.hass, self._address, connectable=True)

    async def _connect(self) -> None:
        if self._closing:
            return
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                return
            try:
                from bleak_retry_connector import (
                    BleakClient,
                    establish_connection,
                    get_device,
                )

                ble_device = self._get_ble_device()
                if ble_device is None:
                    # Fall back to the bleak_retry_connector scanner – finds the
                    # device via BlueZ directly when HA's scanner hasn't seen an
                    # advertisement recently.
                    try:
                        ble_device = await get_device(self._address)
                    except Exception:  # noqa: BLE001
                        ble_device = None
                if ble_device is None:
                    _LOGGER.warning(
                        "LumiLink %s not found by any Bluetooth adapter/proxy – "
                        "will retry (check range / ESPHome proxy / power-cycle the "
                        "module if it left its 3-minute pairing window)",
                        self._address,
                    )
                    self._schedule_reconnect()
                    return

                _LOGGER.info(
                    "LumiLink %s: connecting via bleak_retry_connector", self._address
                )
                # establish_connection handles weak-signal flapping with internal
                # back-off retries and resolves GATT services during connect (this
                # is what the raw BlueZ path fails to do in time under weak signal).
                self._client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self._address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=CONNECT_MAX_ATTEMPTS,
                    use_services_cache=True,
                    ble_device_callback=self._get_ble_device,
                )

                if not self._client.is_connected:
                    _LOGGER.warning(
                        "LumiLink %s: connect() returned False", self._address
                    )
                    self._schedule_reconnect()
                    return

                # Verify the command characteristic is actually present – if
                # service discovery silently failed we must not report available.
                if self._client.services.get_characteristic(CHAR_COMMAND) is None:
                    _LOGGER.warning(
                        "LumiLink %s: command characteristic not found after "
                        "connect – service discovery incomplete, retrying",
                        self._address,
                    )
                    await self._disconnect(schedule_reconnect=True)
                    return

                _LOGGER.info("LumiLink %s connected", self._address)

                # Subscribe to BUSY (and ERROR) notifications, exactly like the
                # original app. Without this the lamp silently ignores commands
                # that arrive while it is not ready – that was why toggling did
                # nothing. Best-effort: if it fails we fall back to blind writes.
                self._busy_status = None
                self._notifications_active = False
                try:
                    await self._client.start_notify(CHAR_BUSY, self._on_busy_notify)
                    self._notifications_active = True
                    _LOGGER.info(
                        "LumiLink %s: BUSY notifications subscribed", self._address
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "LumiLink %s: BUSY subscribe failed: %s – commands sent blind",
                        self._address,
                        exc,
                    )
                try:
                    await self._client.start_notify(CHAR_ERROR, self._on_error_notify)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug(
                        "LumiLink %s: ERROR subscribe failed: %s", self._address, exc
                    )

                self._available = True
                self.async_write_ha_state()

            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "LumiLink %s connection error: %s", self._address, exc
                )
                self._client = None
                self._available = False
                self.async_write_ha_state()
                self._schedule_reconnect()

    @callback
    def _on_busy_notify(self, _char: Any, data: bytearray) -> None:
        """BUSY notification handler – stores the latest availability bitmask."""
        if not data:
            return
        self._busy_status = data[0]
        _LOGGER.debug(
            "LumiLink %s BUSY=0x%02x (busy=%s light=%s next=%s reset=%s)",
            self._address,
            data[0],
            bool(data[0] & BUSY_BIT_BUSY),
            bool(data[0] & BUSY_BIT_LIGHT),
            bool(data[0] & BUSY_BIT_NEXT),
            bool(data[0] & BUSY_BIT_RESET),
        )

    @callback
    def _on_error_notify(self, _char: Any, data: bytearray) -> None:
        """ERROR notification handler – stored for diagnostics only."""
        if not data:
            return
        self._last_error = bytes(data)
        _LOGGER.warning("LumiLink %s ERROR notify: %s", self._address, data.hex())

    async def _wait_for_feature(self, bit: int, max_tries: int = 10) -> bool:
        """Block until the BUSY byte has *bit* set, or give up.

        Mirrors the original app: every command is gated on the matching
        availability bit, otherwise the lamp ignores it silently. If we have no
        working notification subscription we return True immediately and let the
        write go out blind (best-effort, matches the desktop fallback).
        """
        if (
            not self._notifications_active
            or not self._client
            or not self._client.is_connected
        ):
            return True

        for _attempt in range(max_tries):
            status = self._busy_status
            if status is None:
                try:
                    val = await self._client.read_gatt_char(CHAR_BUSY)
                    if val:
                        status = val[0]
                        self._busy_status = status
                except Exception:  # noqa: BLE001
                    pass
            if status is not None and (status & bit):
                return True
            await asyncio.sleep(1.0)

        _LOGGER.warning(
            "LumiLink %s: feature bit 0x%02x not available after %ds (last=%s)",
            self._address,
            bit,
            max_tries,
            None if self._busy_status is None else f"0x{self._busy_status:02x}",
        )
        return False

    def _on_disconnect(self, _client: Any) -> None:
        """Called by bleak from its internal thread when the BLE link drops."""
        _LOGGER.warning("LumiLink %s disconnected", self._address)
        self._available = False
        self._notifications_active = False
        self._busy_status = None
        self._client = None
        # Must marshal back onto HA's event loop.
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect)

    @callback
    def _handle_disconnect(self) -> None:
        self.async_write_ha_state()
        if not self._closing:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._closing:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def _delayed_connect() -> None:
            await asyncio.sleep(RECONNECT_DELAY)
            if not self._closing:
                await self._connect()

        self._reconnect_task = self.hass.async_create_task(_delayed_connect())

    async def _disconnect(self, schedule_reconnect: bool = False) -> None:
        if not schedule_reconnect:
            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()
                self._reconnect_task = None
        client = self._client
        self._client = None
        self._available = False
        self._notifications_active = False
        if client:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if schedule_reconnect:
            self._schedule_reconnect()

    async def _ensure_connected(self) -> bool:
        if self._client and self._client.is_connected:
            return True
        await self._connect()
        return bool(self._client and self._client.is_connected)

    # ── Raw BLE write (auto-detects write type, with fallback) ────────────────

    def _prefers_response(self, characteristic: str) -> bool:
        """Decide the write type from the characteristic's GATT properties.

        Prefers Write-With-Response (as the original app does), falling back to
        Write-Without-Response only when the characteristic doesn't support a
        confirmed write. Cached after the first lookup.
        """
        if characteristic in self._write_with_response:
            return self._write_with_response[characteristic]
        with_response = True
        try:
            char = self._client.services.get_characteristic(characteristic)
            if char is not None:
                props = char.properties
                if "write" in props:
                    with_response = True
                elif "write-without-response" in props:
                    with_response = False
        except Exception:  # noqa: BLE001
            pass
        self._write_with_response[characteristic] = with_response
        return with_response

    async def _write(self, data: bytes, characteristic: str = CHAR_COMMAND) -> bool:
        """Write raw bytes, retrying with the opposite write type on failure."""
        if not await self._ensure_connected():
            return False

        with_response = self._prefers_response(characteristic)
        for response in (with_response, not with_response):
            try:
                await asyncio.wait_for(
                    self._client.write_gatt_char(
                        characteristic, data, response=response
                    ),
                    timeout=WRITE_TIMEOUT,
                )
                # Remember what actually worked for next time.
                self._write_with_response[characteristic] = response
                return True
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                if not self._client or not self._client.is_connected:
                    _LOGGER.error(
                        "LumiLink %s: link lost during write: %s", self._address, exc
                    )
                    break
                _LOGGER.debug(
                    "LumiLink %s: write (response=%s) failed: %s – trying other type",
                    self._address,
                    response,
                    exc,
                )

        _LOGGER.error("LumiLink %s: BLE write failed", self._address)
        self._available = False
        self.async_write_ha_state()
        self._schedule_reconnect()
        return False

    # ── Colour navigation helpers ─────────────────────────────────────────────

    async def _goto_color(self, target: int) -> bool:
        """Navigate to *target* colour index using NEXT steps (reset shortcut).

        The module can only cycle to the *next* colour – there is no direct
        select – so we step forward, optionally resetting to white (index 0)
        first when that is the shorter path to a fixed colour.
        """
        target = target % len(COLOR_NAMES)
        steps_forward = (target - self._color_index) % len(COLOR_NAMES)
        steps_via_reset = target  # from white (0) → target

        # A reset shortcut only makes sense for fixed colours (0..10); it would
        # otherwise force the module through the visible auto modes.
        if target < NUM_FIXED_COLORS and steps_via_reset < steps_forward:
            await self._wait_for_feature(BUSY_BIT_RESET, max_tries=30)
            if not await self._write(_pkt(CMD_RESET_OUTPUT, PARAM_OUTPUT_1)):
                return False
            self._color_index = 0
            # Wait for the reset animation to finish (module reports NEXT ready).
            await self._wait_for_feature(BUSY_BIT_NEXT, max_tries=45)
            steps_forward = target

        for _ in range(steps_forward):
            await self._wait_for_feature(BUSY_BIT_NEXT, max_tries=45)
            if not await self._write(_pkt(CMD_NEXT_COLOR, PARAM_OUTPUT_1)):
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
                    await self._goto_color(target)

            await self._wait_for_feature(BUSY_BIT_LIGHT)
            if await self._write(_pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_ON)):
                self._is_on = True
                self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        async with self._cmd_lock:
            await self._wait_for_feature(BUSY_BIT_LIGHT)
            if await self._write(
                _pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_OFF)
            ):
                self._is_on = False
                self.async_write_ha_state()
