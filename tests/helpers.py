"""Test doubles for the Connector Bridge test suite."""

from __future__ import annotations

import json
import time

from custom_components.connector_bridge.bridge import ConnectorBlind, ConnectorGateway
from custom_components.connector_bridge.const import DEVICE_TYPE_BLIND


class FakeSocket:
    """A socket stand-in that replays a scripted exchange.

    ``script`` is a list of per-attempt response lists. Each entry is either a
    list of payloads to hand back from ``recvfrom`` or the string ``"timeout"``
    to make the attempt time out with nothing received.
    """

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = 0
        self._pending: list = []
        self._attempt_started = False

    # -- factory ------------------------------------------------------
    def factory(self, *args, **kwargs):
        """Return self, so patching ``socket.socket`` reuses one instance."""
        self._attempt_started = False
        return self

    # -- socket API ---------------------------------------------------
    def settimeout(self, timeout):
        self.timeout = timeout

    def setsockopt(self, *args):
        pass

    def bind(self, addr):
        self.bound = addr

    def sendto(self, data, addr):
        self.sent.append((data, addr))
        if not self._attempt_started:
            self._attempt_started = True
            step = self.script.pop(0) if self.script else "timeout"
            self._pending = [] if step == "timeout" else list(step)

    def recvfrom(self, bufsize):
        if not self._pending:
            raise TimeoutError
        payload = self._pending.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return payload, ("192.168.1.50", 32100)

    def close(self):
        self.closed += 1


def make_gateway(ip: str = "192.168.1.50", key: str = "a" * 16) -> ConnectorGateway:
    """Build a real gateway object with throttling effectively disabled."""
    gateway = ConnectorGateway(ip=ip, key=key, timeout=0.01)
    return gateway


class FakeBlind:
    """Minimal ConnectorBlind stand-in driven directly by the tests."""

    def __init__(self, mac: str, position: int | None, device_type: str = DEVICE_TYPE_BLIND):
        self.mac = mac
        self.device_type = device_type
        self.available = True
        self.blind_type = None
        self.operation = None
        self._position = position
        self.commands: list[tuple] = []
        self._callbacks: dict = {}
        self._event_callbacks: dict = {}

    @property
    def position(self):
        return self._position

    @property
    def ha_position(self):
        return None if self._position is None else 100 - self._position

    def register_callback(self, cb_id, callback):
        self._callbacks[cb_id] = callback

    def remove_callback(self, cb_id):
        self._callbacks.pop(cb_id, None)

    def register_event_callback(self, cb_id, callback):
        self._event_callbacks[cb_id] = callback

    def remove_event_callback(self, cb_id):
        self._event_callbacks.pop(cb_id, None)

    def fire(self):
        for cb in list(self._callbacks.values()):
            cb()

    def fire_event(self, payload):
        for cb in list(self._event_callbacks.values()):
            cb(payload)

    # -- commands -----------------------------------------------------
    def open(self):
        self.commands.append(("open",))
        self.operation = 1

    def close(self):
        self.commands.append(("close",))
        self.operation = 0

    def stop(self):
        self.commands.append(("stop",))
        self.operation = 2

    def set_position(self, position):
        self.commands.append(("set_position", position))
        self._position = position

    def update(self):
        self.commands.append(("update",))


class FakeGateway:
    """ConnectorGateway stand-in used by the HA-level tests."""

    def __init__(self, ip: str, mac: str, blinds: dict[str, int | None]) -> None:
        self.ip = ip
        self.mac = mac
        self.device_type = "02000001"
        self.available = True
        self.last_seen = time.time()
        self.multicast_active = True
        self.device_list = {
            bmac: FakeBlind(bmac, pos) for bmac, pos in blinds.items()
        }
        self.listening = False
        self.stopped = False
        self._gateway_callbacks: dict = {}
        self.get_device_list_error: Exception | None = None

    def get_device_list(self):
        if self.get_device_list_error is not None:
            raise self.get_device_list_error
        return self.device_list

    def start_listening(self):
        self.listening = True

    def stop_listening(self):
        self.listening = False
        self.stopped = True

    def set_ip(self, ip):
        self.ip = ip

    def register_gateway_callback(self, cb_id, callback):
        self._gateway_callbacks[cb_id] = callback

    def remove_gateway_callback(self, cb_id):
        self._gateway_callbacks.pop(cb_id, None)

    def fire_gateway(self):
        for cb in list(self._gateway_callbacks.values()):
            cb()
