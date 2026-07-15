#!/usr/bin/env python3
"""
LumiLink BLE ↔ MQTT bridge for a Raspberry Pi placed next to the module.

Why this exists: on a Home Assistant host with a virtualised / USB-passthrough
Bluetooth adapter, LE connections to the SEAMAID LumiLink module are cancelled
by BlueZ (br-connection-canceled). A dedicated Pi right next to the module uses
its native Bluetooth to hold the connection reliably (like a phone/Mac does),
and exposes the light to Home Assistant over MQTT (auto-discovery).

Self-healing: the module only accepts NEW connections for ~3 minutes after
power-on. This bridge power-cycles the module through Home Assistant (a Meross
smart plug) whenever it needs to (re)connect, then grabs the connection inside
that window and holds it.

Protocol reverse-engineered from com.alphadif.seamaid.lumilink (hardware
verified). Single output; both lamps wired in parallel; colours cycle via NEXT;
RESET syncs both lamps to white.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys

import yaml

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:
    print("ERROR: bleak missing. Run install.sh (pip install bleak).")
    sys.exit(1)
try:
    import aiomqtt
except ImportError:
    print("ERROR: aiomqtt missing. Run install.sh (pip install aiomqtt).")
    sys.exit(1)
try:
    import aiohttp
except ImportError:
    print("ERROR: aiohttp missing. Run install.sh (pip install aiohttp).")
    sys.exit(1)

_LOG = logging.getLogger("lumilink-bridge")

# ── Protocol ────────────────────────────────────────────────────────────────
SVC = "bc3b4e71-ee54-4f09-8f28-e865150c20b0"
CHAR_COMMAND = "bc3b4e72-ee54-4f09-8f28-e865150c20b0"
CHAR_BUSY = "bc3b4e74-ee54-4f09-8f28-e865150c20b0"
CHAR_ERROR = "bc3b4e75-ee54-4f09-8f28-e865150c20b0"

CMD_TOGGLE, CMD_NEXT, CMD_RESET = 0x01, 0x03, 0x04
PARAM_OUT1 = 0x01
VAL_ON, VAL_OFF = 0x01, 0x00
LIGHT_STATE_BIT = 0x10
BUSY_LIGHT, BUSY_NEXT, BUSY_RESET = 0x02, 0x04, 0x08

COLORS = [
    "Weiß", "Blau", "Cyan", "Türkis", "Magenta", "Grün", "Orange", "Gelb",
    "Bernstein", "Rot", "Rosa/Pink",
    "Auto: Langsam", "Auto: Mittel", "Auto: Schnell", "Auto: Flash",
    "Auto: Stroboskop",
]
NUM_FIXED = 11


def pkt(cmd: int, param: int, value: int = 0) -> bytes:
    return bytes([cmd, param, value, 0, 0, 0])


# ── BLE device wrapper ──────────────────────────────────────────────────────
class LumiLink:
    def __init__(self, address: str | None, name_prefix: str, step_delay: float):
        self.address = address
        self.name_prefix = name_prefix.upper()
        self.step_delay = step_delay
        self.client: BleakClient | None = None
        self.color_index = 0
        self.is_on = False
        self._busy = None
        self._busy_count = 0
        self._notifications = False
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    async def _find_device(self, timeout: float):
        if self.address:
            dev = await BleakScanner.find_device_by_address(self.address, timeout=timeout)
            if dev:
                return dev
        # fall back to scanning by name
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
        for _addr, (dev, adv) in devices.items():
            nm = (dev.name or "").upper()
            if self.name_prefix and self.name_prefix in nm:
                return dev
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if SVC in uuids:
                return dev
        return None

    async def connect(self, timeout: float = 20.0, on_drop=None) -> bool:
        dev = await self._find_device(min(timeout, 12.0))
        if dev is None:
            _LOG.info("module not found in scan")
            return False
        if self.address is None:
            self.address = dev.address
        _LOG.info("connecting to %s (%s)", dev.name, dev.address)

        def _disc(_c):
            self._notifications = False
            if on_drop:
                on_drop()

        self.client = BleakClient(dev, disconnected_callback=_disc)
        try:
            await self.client.connect(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("connect failed: %s", exc)
            self.client = None
            return False
        if not self.client.is_connected:
            self.client = None
            return False
        _LOG.info("connected")
        self._busy = None
        self._notifications = False
        try:
            await self.client.start_notify(CHAR_BUSY, self._on_busy)
            self._notifications = True
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("BUSY subscribe failed: %s (commands sent blind)", exc)
        return True

    async def disconnect(self):
        c = self.client
        self.client = None
        self._notifications = False
        if c:
            try:
                await c.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def _on_busy(self, _char, data: bytearray):
        if data:
            self._busy = data[0]
            self._busy_count += 1

    async def _wait_new_busy(self, max_wait=2.0):
        if not self._notifications:
            return
        start = self._busy_count
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max_wait
        while self._busy_count == start:
            if loop.time() >= deadline:
                return
            await asyncio.sleep(0.1)

    async def _wait_feature(self, bit: int, tries: int = 10) -> bool:
        if not self._notifications or not self.connected:
            return True
        for _ in range(tries):
            s = self._busy
            if s is None:
                try:
                    v = await self.client.read_gatt_char(CHAR_BUSY)
                    if v:
                        s = v[0]
                        self._busy = s
                except Exception:  # noqa: BLE001
                    pass
            if s is not None and (s & bit):
                return True
            await asyncio.sleep(1.0)
        return False

    async def _write(self, data: bytes) -> bool:
        if not self.connected:
            return False
        for resp in (True, False):
            try:
                await asyncio.wait_for(
                    self.client.write_gatt_char(CHAR_COMMAND, data, response=resp),
                    timeout=6.0)
                return True
            except Exception as exc:  # noqa: BLE001
                if not self.connected:
                    return False
                _LOG.debug("write resp=%s failed: %s", resp, exc)
        return False

    async def read_state(self) -> bool | None:
        if not self.connected:
            return None
        try:
            v = await self.client.read_gatt_char(CHAR_COMMAND)
            if v:
                self.is_on = bool(v[0] & LIGHT_STATE_BIT)
                return self.is_on
        except Exception:  # noqa: BLE001
            pass
        return None

    async def turn_on(self) -> bool:
        async with self._lock:
            await self._wait_feature(BUSY_LIGHT)
            if await self._write(pkt(CMD_TOGGLE, PARAM_OUT1, VAL_ON)):
                self.is_on = True
                return True
        return False

    async def turn_off(self) -> bool:
        async with self._lock:
            await self._wait_feature(BUSY_LIGHT)
            if await self._write(pkt(CMD_TOGGLE, PARAM_OUT1, VAL_OFF)):
                self.is_on = False
                return True
        return False

    async def reset_sync(self) -> bool:
        """RESET → both lamps land on white in sync (~4 s animation)."""
        async with self._lock:
            await self._wait_feature(BUSY_RESET, tries=30)
            if not await self._write(pkt(CMD_RESET, PARAM_OUT1)):
                return False
            self.color_index = 0
            await self._wait_new_busy(3.0)
            await self._wait_feature(BUSY_NEXT, tries=45)
            return True

    async def set_color(self, target: int, sync: bool = True) -> bool:
        target %= len(COLORS)
        async with self._lock:
            if sync:
                await self._wait_feature(BUSY_RESET, tries=30)
                if not await self._write(pkt(CMD_RESET, PARAM_OUT1)):
                    return False
                self.color_index = 0
                await self._wait_new_busy(3.0)
                await self._wait_feature(BUSY_NEXT, tries=45)
                steps = target
            else:
                steps = (target - self.color_index) % len(COLORS)
            for _ in range(steps):
                await self._wait_feature(BUSY_NEXT, tries=45)
                if not await self._write(pkt(CMD_NEXT, PARAM_OUT1)):
                    return False
                self.color_index = (self.color_index + 1) % len(COLORS)
                await self._wait_new_busy(2.0)
                await asyncio.sleep(self.step_delay)
            return True


# ── Home Assistant power-cycle via Meross plug ──────────────────────────────
class HAPowerCycle:
    def __init__(self, base_url, token, switch_entity, off_seconds):
        self.base = base_url.rstrip("/") if base_url else None
        self.token = token
        self.switch = switch_entity
        self.off_seconds = off_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.base and self.token and self.switch)

    async def _svc(self, service):
        url = f"{self.base}/api/services/switch/{service}"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers,
                              json={"entity_id": self.switch}, timeout=15) as r:
                return r.status

    async def cycle(self):
        if not self.enabled:
            _LOG.warning("power-cycle not configured — please power-cycle the "
                         "module manually (15 s off) so the bridge can connect")
            await asyncio.sleep(self.off_seconds + 10)
            return
        _LOG.info("power-cycling module via %s (off %ss)", self.switch, self.off_seconds)
        try:
            await self._svc("turn_off")
            await asyncio.sleep(self.off_seconds)
            await self._svc("turn_on")
            await asyncio.sleep(10)  # boot + advertise
        except Exception as exc:  # noqa: BLE001
            _LOG.error("power-cycle failed: %s", exc)


# ── MQTT bridge ─────────────────────────────────────────────────────────────
class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        node = cfg.get("node_id", "lumilink_pool")
        self.node = node
        self.disc = cfg.get("discovery_prefix", "homeassistant")
        self.base = f"lumilink/{node}"
        self.t_light_cfg = f"{self.disc}/light/{node}/config"
        self.t_btn_cfg = f"{self.disc}/button/{node}_sync/config"
        self.t_state = f"{self.base}/state"
        self.t_cmd = f"{self.base}/set"
        self.t_btn_cmd = f"{self.base}/sync"
        self.t_avail = f"{self.base}/availability"
        self.sync_mode = bool(cfg.get("sync_mode", True))

        self.dev = LumiLink(cfg.get("address"), cfg.get("name_prefix", "LUMILINK"),
                            float(cfg.get("step_delay", 1.5)))
        ha = cfg.get("home_assistant", {}) or {}
        self.power = HAPowerCycle(ha.get("url"), ha.get("token"),
                                  ha.get("switch_entity"),
                                  int(cfg.get("power_off_seconds", 15)))
        self._mqtt: aiomqtt.Client | None = None
        self._drop_evt = asyncio.Event()
        self._last_cycle = 0.0

    def _device_block(self):
        return {
            "identifiers": [f"lumilink_{self.node}"],
            "name": self.cfg.get("device_name", "LumiLink Pool Light"),
            "manufacturer": "SEAMAID / Alphadif",
            "model": "LumiLink Bluetooth Module (504017) via Pi bridge",
        }

    async def _publish(self, topic, payload, retain=False):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        await self._mqtt.publish(topic, payload, retain=retain)

    async def publish_discovery(self):
        light = {
            "schema": "json",
            "name": None,
            "unique_id": f"lumilink_{self.node}",
            "command_topic": self.t_cmd,
            "state_topic": self.t_state,
            "availability_topic": self.t_avail,
            "effect": True,
            "effect_list": COLORS,
            "device": self._device_block(),
        }
        btn = {
            "name": "Sync lamps",
            "unique_id": f"lumilink_{self.node}_sync",
            "command_topic": self.t_btn_cmd,
            "availability_topic": self.t_avail,
            "icon": "mdi:sync",
            "device": self._device_block(),
        }
        await self._publish(self.t_light_cfg, light, retain=True)
        await self._publish(self.t_btn_cfg, btn, retain=True)

    async def publish_state(self):
        st = {
            "state": "ON" if self.dev.is_on else "OFF",
            "effect": COLORS[self.dev.color_index] if 0 <= self.dev.color_index < len(COLORS) else None,
        }
        await self._publish(self.t_state, st)

    async def set_available(self, online: bool):
        await self._publish(self.t_avail, "online" if online else "offline", retain=True)

    def _on_drop(self):
        _LOG.warning("BLE link dropped")
        self._drop_evt.set()

    # -- command handling --
    async def handle_light_cmd(self, payload: str):
        try:
            data = json.loads(payload)
        except Exception:  # noqa: BLE001
            data = {"state": payload.strip().upper()}
        if not self.dev.connected:
            _LOG.info("command while disconnected — ignored")
            return
        effect = data.get("effect")
        state = str(data.get("state", "")).upper()
        if effect and effect in COLORS:
            await self.dev.set_color(COLORS.index(effect), sync=self.sync_mode)
        if state == "ON":
            await self.dev.turn_on()
        elif state == "OFF":
            await self.dev.turn_off()
        await self.dev.read_state()
        await self.publish_state()

    async def handle_sync_cmd(self, _payload: str):
        if self.dev.connected:
            _LOG.info("sync button → reset")
            await self.dev.reset_sync()
            await self.publish_state()

    # -- connection manager --
    async def connection_manager(self):
        while True:
            if not self.dev.connected:
                await self.set_available(False)
                ok = await self.dev.connect(timeout=20, on_drop=self._on_drop)
                if not ok:
                    loop = asyncio.get_event_loop()
                    if loop.time() - self._last_cycle > 45:
                        self._last_cycle = loop.time()
                        await self.power.cycle()
                    ok = await self.dev.connect(timeout=25, on_drop=self._on_drop)
                if ok:
                    # Lock the connection in: read state (a GATT op) so the
                    # module keeps us, then advertise availability.
                    self._drop_evt.clear()
                    await self.dev.read_state()
                    await self.set_available(True)
                    await self.publish_state()
                    _LOG.info("online — holding connection")
                else:
                    await asyncio.sleep(10)
                    continue
            # hold until a drop happens
            try:
                await asyncio.wait_for(self._drop_evt.wait(), timeout=30)
            except asyncio.TimeoutError:
                # periodic liveness check
                if not self.dev.connected:
                    self._drop_evt.set()
            if self._drop_evt.is_set():
                self._drop_evt.clear()
                await self.dev.disconnect()
                await self.set_available(False)

    async def run(self):
        mqtt_cfg = self.cfg.get("mqtt", {})
        will = aiomqtt.Will(self.t_avail, "offline", retain=True)
        async with aiomqtt.Client(
            hostname=mqtt_cfg.get("host", "127.0.0.1"),
            port=int(mqtt_cfg.get("port", 1883)),
            username=mqtt_cfg.get("username") or None,
            password=mqtt_cfg.get("password") or None,
            will=will,
        ) as client:
            self._mqtt = client
            await self.publish_discovery()
            await self.set_available(False)
            await client.subscribe(self.t_cmd)
            await client.subscribe(self.t_btn_cmd)
            _LOG.info("MQTT connected, discovery published")

            asyncio.create_task(self.connection_manager())

            async for msg in client.messages:
                topic = str(msg.topic)
                payload = msg.payload.decode(errors="ignore")
                try:
                    if topic == self.t_cmd:
                        await self.handle_light_cmd(payload)
                    elif topic == self.t_btn_cmd:
                        await self.handle_sync_cmd(payload)
                except Exception as exc:  # noqa: BLE001
                    _LOG.error("command error: %s", exc)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


async def _amain(cfg):
    bridge = Bridge(cfg)
    while True:
        try:
            await bridge.run()
        except aiomqtt.MqttError as exc:
            _LOG.warning("MQTT error: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)


def main():
    logging.basicConfig(
        level=os.environ.get("LUMILINK_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    path = sys.argv[1] if len(sys.argv) > 1 else "/etc/lumilink-bridge/config.yaml"
    if not os.path.exists(path):
        _LOG.error("config not found: %s", path)
        sys.exit(1)
    cfg = load_config(path)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, loop.stop)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(_amain(cfg))
    except (KeyboardInterrupt, RuntimeError):
        pass


if __name__ == "__main__":
    main()
