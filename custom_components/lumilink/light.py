"""LumiLink light entity.

Protocol faithfully ported from the reverse-engineered, hardware-verified
desktop controller (lumilink_ble.py) of the original SEAMAID LumiLink app
(com.alphadif.seamaid.lumilink). The transport is adapted to Home Assistant's
Bluetooth stack (bleak_retry_connector / habluetooth) for BlueZ + ESPHome
proxy reliability.

Lamp-sync design (hardware-verified findings):
- Both lamps are wired in parallel on OUTPUT 1 of one module; param 0x02 was
  live-tested and rejected by the firmware (ERROR NOT_FOUND) — there is no
  second addressable output.
- After RESET both lamps land on white (index 0) IN SYNC.
- Desync happens when NEXT pulses are sent too fast: one lamp occasionally
  misses a pulse. The original app only has a manual next-colour button
  (human pace) and never desyncs. Hence: slow, gated stepping + RESET as the
  sync anchor before every colour selection (sync mode, default on).
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
    CONF_STEP_DELAY,
    CONF_SYNC_MODE,
    DEFAULT_STEP_DELAY,
    DEFAULT_SYNC_MODE,
    DOMAIN,
    LIGHT_STATE_BIT,
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

# Entities are push-based and all writes are serialized by an internal lock.
PARALLEL_UPDATES = 0

RECONNECT_DELAY = 15  # seconds between reconnect attempts
CONNECT_MAX_ATTEMPTS = 6
WRITE_TIMEOUT = 6.0  # seconds per GATT write
NUM_FIXED_COLORS = 11  # indices 0..10 are static colours; 11..15 are auto modes
# The reset animation (~4 s double-blink) is worth roughly this many NEXT
# steps of time; in fast mode only take the reset shortcut when it saves more.
RESET_STEP_COST = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    address = entry.data[CONF_ADDRESS]
    name = entry.data.get(CONF_NAME, f"LumiLink {address[-5:]}")
    entity = LumiLinkLight(hass, entry, address, name)
    # Make the entity reachable for sibling platforms (button: "sync lamps").
    entry.runtime_data.light = entity
    async_add_entities([entity])


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
    # Until the first successful state read-back proves CHAR_COMMAND is
    # readable on this firmware, the on/off state is assumed from commands.
    _attr_assumed_state = True
    # Diagnostic attributes churn on every BUSY notification – keep them out
    # of the recorder database.
    _unrecorded_attributes = frozenset(
        {"busy_status", "last_error", "sync_mode", "step_delay"}
    )

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._address = address
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
        self._transition_task: asyncio.Task | None = None
        self._connect_tasks: set[asyncio.Task] = set()
        self._busy_status: int | None = None
        self._busy_update_count: int = 0
        self._notifications_active: bool = False
        self._last_error: bytes | None = None
        self._closing: bool = False
        # Cancellation token: bumped whenever a new command supersedes a
        # running colour transition (same pattern as the desktop controller).
        self._cmd_token: int = 0
        # Cached write-response preference per characteristic (auto-detected).
        self._write_with_response: dict[str, bool] = {}

    # ── Options ──────────────────────────────────────────────────────────────

    @property
    def _step_delay(self) -> float:
        """Pause between NEXT pulses so both lamps register every pulse."""
        try:
            return float(
                self._entry.options.get(CONF_STEP_DELAY, DEFAULT_STEP_DELAY)
            )
        except (TypeError, ValueError):
            return DEFAULT_STEP_DELAY

    @property
    def _sync_mode(self) -> bool:
        """Anchor every colour selection with a RESET (keeps lamps in sync)."""
        return bool(self._entry.options.get(CONF_SYNC_MODE, DEFAULT_SYNC_MODE))

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
        attrs: dict[str, Any] = {
            "sync_mode": self._sync_mode,
            "step_delay": self._step_delay,
        }
        if self._busy_status is not None:
            attrs["busy_status"] = f"0x{self._busy_status:02x}"
        if self._last_error is not None:
            attrs["last_error"] = self._last_error.hex()
        return attrs

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        # Presence-triggered connect: the module only accepts NEW centrals for
        # 3 minutes after power-on, so reconnect the instant HA sees an
        # advertisement instead of waiting for the periodic retry.
        from homeassistant.components.bluetooth import (
            BluetoothCallbackMatcher,
            BluetoothScanningMode,
            async_register_callback,
        )

        self.async_on_remove(
            async_register_callback(
                self.hass,
                self._on_bluetooth_advertisement,
                BluetoothCallbackMatcher(address=self._address, connectable=True),
                BluetoothScanningMode.ACTIVE,
            )
        )
        self._spawn_connect()

    def _spawn_connect(self) -> None:
        """Start a tracked _connect() background task (cancellable on removal)."""
        if self._closing:
            return
        task = self.hass.async_create_background_task(
            self._connect(), name=f"lumilink_connect_{self._address}"
        )
        self._connect_tasks.add(task)
        task.add_done_callback(self._connect_tasks.discard)

    @callback
    def _on_bluetooth_advertisement(self, _service_info: Any, _change: Any) -> None:
        """Advertisement seen – connect right away if we aren't connected."""
        if self._closing or (self._client and self._client.is_connected):
            return
        if self._connect_lock.locked():
            return  # a connect attempt is already in flight
        _LOGGER.debug(
            "LumiLink %s: advertisement seen – connecting now", self._address
        )
        self._spawn_connect()

    async def async_will_remove_from_hass(self) -> None:
        self._closing = True
        self._cmd_token += 1  # abort any running colour transition
        for task in (
            self._reconnect_task,
            self._transition_task,
            *list(self._connect_tasks),
        ):
            if task and not task.done():
                task.cancel()
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

                # The entity may have been removed while establish_connection
                # was awaiting (it can take several seconds). Don't touch state
                # or keep the link – tear it down so we don't orphan a BLE
                # connection that would occupy the module's 3-minute slot.
                if self._closing:
                    await self._disconnect()
                    return

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
                # Sync the real on/off state from the module (CHAR_COMMAND is
                # readable: bit 4 = light on). Best-effort.
                await self._refresh_light_state()
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
        self._busy_update_count += 1
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

    async def _wait_for_new_busy_notification(self, max_wait: float = 2.0) -> None:
        """Wait until a fresh BUSY notification arrives (post-command settle).

        Prevents racing on a stale cached status right after a write.
        """
        if not self._notifications_active:
            return
        start_count = self._busy_update_count
        deadline = self.hass.loop.time() + max_wait
        while self._busy_update_count == start_count:
            remaining = deadline - self.hass.loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.1, remaining))

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

    def _on_disconnect(self, client: Any) -> None:
        """Called by bleak from its internal thread when the BLE link drops."""
        # Ignore a stale callback for a client we've already replaced – it must
        # not clobber a newer connection.
        if self._client is not None and client is not self._client:
            return
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

        self._reconnect_task = self.hass.async_create_background_task(
            _delayed_connect(), name=f"lumilink_reconnect_{self._address}"
        )

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

    # ── Raw BLE I/O (auto-detects write type, with fallback) ─────────────────

    def _prefers_response(self, characteristic: str) -> bool:
        """Decide the write type from the characteristic's GATT properties.

        Prefers Write-With-Response (the app's _sendCommandWithResponse does
        the same), falling back to Write-Without-Response only when the
        characteristic doesn't support a confirmed write. Cached per char.
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

        # Both write types failed. Tear the link down for real so the scheduled
        # reconnect actually reconnects – otherwise, if BlueZ still reports the
        # link "connected" (e.g. a GATT write timeout on a live link), _connect
        # would early-return and the entity would stay unavailable forever.
        _LOGGER.error("LumiLink %s: BLE write failed – forcing reconnect", self._address)
        await self._disconnect(schedule_reconnect=True)
        self.async_write_ha_state()
        return False

    async def _refresh_light_state(self) -> bool | None:
        """Read the real on/off state from CHAR_COMMAND (bit 4 = on).

        Extracted from the app bytecode (_readLightState). Returns the state,
        or None when the characteristic isn't readable on this firmware.
        On the first successful read the entity stops being an assumed-state
        light – HA then shows the true device state.
        """
        if not self._client or not self._client.is_connected:
            return None
        try:
            val = await self._client.read_gatt_char(CHAR_COMMAND)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "LumiLink %s: light-state read not supported: %s",
                self._address,
                exc,
            )
            return None
        if not val:
            return None
        state = bool(val[0] & LIGHT_STATE_BIT)
        if self._attr_assumed_state:
            self._attr_assumed_state = False
            _LOGGER.info(
                "LumiLink %s: state read-back works – tracking real on/off state",
                self._address,
            )
        if state != self._is_on:
            _LOGGER.debug(
                "LumiLink %s: read-back corrected state to %s",
                self._address,
                "on" if state else "off",
            )
        self._is_on = state
        return state

    # ── Colour navigation (sync-anchored) ─────────────────────────────────────

    async def _reset_and_wait(self) -> bool:
        """Send RESET and wait for completion.

        After a reset BOTH lamps land on white (index 0) in sync – this is the
        hardware-verified sync anchor. The reset animation takes ~4 s (lamps
        blink twice); the module then reports NEXT available again.
        """
        await self._wait_for_feature(BUSY_BIT_RESET, max_tries=30)
        if not await self._write(_pkt(CMD_RESET_OUTPUT, PARAM_OUTPUT_1)):
            return False
        self._color_index = 0
        self.async_write_ha_state()
        await self._wait_for_new_busy_notification(max_wait=3.0)
        await self._wait_for_feature(BUSY_BIT_NEXT, max_tries=45)
        return True

    async def _step_colors(self, steps: int, token: int) -> bool:
        """Send *steps* NEXT pulses slowly enough for both lamps to count them."""
        for _ in range(steps):
            if token != self._cmd_token or self._closing:
                _LOGGER.debug("LumiLink %s: colour transition superseded", self._address)
                return False
            await self._wait_for_feature(BUSY_BIT_NEXT, max_tries=45)
            if not await self._write(_pkt(CMD_NEXT_COLOR, PARAM_OUTPUT_1)):
                return False
            self._color_index = (self._color_index + 1) % len(COLOR_NAMES)
            self.async_write_ha_state()
            # Wait for the module to confirm it processed the pulse …
            await self._wait_for_new_busy_notification(max_wait=2.0)
            # … then give the LAMPS time to count it. Pulses sent faster than
            # this get occasionally missed by one lamp → colour drift.
            await asyncio.sleep(self._step_delay)
        return True

    async def _run_color_transition(self, target: int, token: int) -> None:
        """Navigate to *target* colour index (runs as a background task)."""
        async with self._cmd_lock:
            if token != self._cmd_token or self._closing:
                return
            target = target % len(COLOR_NAMES)

            if self._sync_mode:
                # Sync anchor: RESET puts both lamps on white, then step slowly.
                _LOGGER.info(
                    "LumiLink %s: sync transition – reset, then %d steps to '%s'",
                    self._address,
                    target,
                    COLOR_NAMES[target],
                )
                if not await self._reset_and_wait():
                    return
                if token != self._cmd_token or self._closing:
                    return
                await self._step_colors(target, token)
                return

            # Fast mode: step forward from the tracked index. A reset shortcut
            # only pays off for a fixed colour when it saves enough steps to
            # justify the visible ~4 s double-blink of the reset animation
            # (worth roughly RESET_STEP_COST steps of time).
            steps_forward = (target - self._color_index) % len(COLOR_NAMES)
            if (
                target < NUM_FIXED_COLORS
                and target + RESET_STEP_COST < steps_forward
            ):
                if not await self._reset_and_wait():
                    return
                steps_forward = target
            await self._step_colors(steps_forward, token)

    def _start_color_transition(self, target: int) -> None:
        """Kick off a colour transition without blocking the service call."""
        self._cmd_token += 1
        token = self._cmd_token
        self._transition_task = self.hass.async_create_background_task(
            self._run_color_transition(target, token),
            name=f"lumilink_transition_{self._address}",
        )

    async def _cancel_transition(self) -> None:
        """Cancel a running colour transition and wait for it to release the lock.

        Bumping _cmd_token alone is not enough: the transition can be parked
        inside a long _wait_for_feature (up to ~75 s) while holding _cmd_lock,
        which would make a following turn_off block that entire time. Cancelling
        the task interrupts its awaits and exits its `async with` promptly.
        """
        task = self._transition_task
        self._transition_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def async_sync_lamps(self) -> None:
        """Public: re-sync both lamps via RESET (used by the sync button)."""
        self._cmd_token += 1
        await self._cancel_transition()
        token = self._cmd_token
        async with self._cmd_lock:
            if token != self._cmd_token:
                return
            _LOGGER.info("LumiLink %s: manual lamp sync (reset)", self._address)
            await self._reset_and_wait()

    # ── HA service handlers ───────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        effect = kwargs.get(ATTR_EFFECT)
        # Supersede + cancel any running transition so the lock frees quickly.
        self._cmd_token += 1
        await self._cancel_transition()
        async with self._cmd_lock:
            await self._wait_for_feature(BUSY_BIT_LIGHT)
            if await self._write(_pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_ON)):
                self._is_on = True
                # Read back the real state (best-effort verification).
                await self._wait_for_new_busy_notification(max_wait=1.5)
                state = await self._refresh_light_state()
                if state is False:
                    _LOGGER.warning(
                        "LumiLink %s: module did not confirm ON – retrying once",
                        self._address,
                    )
                    await self._wait_for_feature(BUSY_BIT_LIGHT)
                    if await self._write(
                        _pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_ON)
                    ):
                        await self._wait_for_new_busy_notification(max_wait=1.5)
                        await self._refresh_light_state()
                self.async_write_ha_state()

        # Colour change runs in the background so the service returns promptly
        # (a sync-anchored transition can take ~30 s for the last auto mode).
        if effect and effect in COLOR_NAMES:
            target = COLOR_NAMES.index(effect)
            if self._sync_mode or target != self._color_index:
                self._start_color_transition(target)

    async def async_turn_off(self, **kwargs: Any) -> None:
        # Supersede + cancel any running colour transition so it can't keep the
        # lock (a full transition can otherwise hold it for over a minute).
        self._cmd_token += 1
        await self._cancel_transition()
        async with self._cmd_lock:
            await self._wait_for_feature(BUSY_BIT_LIGHT)
            if await self._write(
                _pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_OFF)
            ):
                self._is_on = False
                await self._wait_for_new_busy_notification(max_wait=1.5)
                state = await self._refresh_light_state()
                if state is True:
                    _LOGGER.warning(
                        "LumiLink %s: module did not confirm OFF – retrying once",
                        self._address,
                    )
                    await self._wait_for_feature(BUSY_BIT_LIGHT)
                    if await self._write(
                        _pkt(CMD_TOGGLE_LIGHT, PARAM_OUTPUT_1, VALUE_LIGHT_OFF)
                    ):
                        await self._wait_for_new_busy_notification(max_wait=1.5)
                        await self._refresh_light_state()
                self.async_write_ha_state()
