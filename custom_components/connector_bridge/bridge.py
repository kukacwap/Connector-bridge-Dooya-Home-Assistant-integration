"""Connector Bridge UDP communication layer for Dooya/Motionblinds DD7002B."""

from __future__ import annotations

import datetime
import json
import logging
import socket
import struct
import time
from threading import Lock, Thread

from Cryptodome.Cipher import AES

from .const import (
    DEVICE_TYPES_GATEWAY,
    DEVICE_TYPE_BLIND,
    DEVICE_TYPE_DR,
    DEVICE_TYPE_SUNBLIND,
    DEVICE_TYPE_TDBU,
    DEVICE_TYPE_WIFI_BLIND,
    DEVICE_TYPE_WIFI_CURTAIN,
    MIN_SEND_INTERVAL,
    MULTICAST_ADDRESS,
    OPERATION_CLOSE,
    OPERATION_OPEN,
    OPERATION_STATUS,
    OPERATION_STOP,
    SOCKET_BUFSIZE,
    UDP_PORT_RECEIVE,
    UDP_PORT_SEND,
)

_LOGGER = logging.getLogger(__name__)

SUPPORTED_BLIND_TYPES = [
    DEVICE_TYPE_BLIND,
    DEVICE_TYPE_DR,
    DEVICE_TYPE_SUNBLIND,
    DEVICE_TYPE_TDBU,
    DEVICE_TYPE_WIFI_BLIND,
    DEVICE_TYPE_WIFI_CURTAIN,
]


def _timestamp() -> str:
    """Return current UTC time formatted as HA msgID."""
    now = datetime.datetime.utcnow()
    return now.strftime("%Y%m%d%H%M%S%f")[:-3]


def _compute_access_token(token: str, key: str) -> str:
    """AES-ECB encrypt the gateway token with the user key."""
    cipher = AES.new(key.encode("utf-8"), AES.MODE_ECB)
    encrypted = cipher.encrypt(token.encode("utf-8"))
    return encrypted.hex().upper()


class ConnectorBlind:
    """Represents a single blind connected to the Connector Bridge."""

    def __init__(self, gateway: ConnectorGateway, mac: str, device_type: str) -> None:
        self._gateway = gateway
        self._mac = mac
        self._device_type = device_type
        self._position: int | None = None  # 0=open, 100=closed (bridge convention)
        self._available = False
        self._callbacks: dict[str, callable] = {}
        self._blind_type: int | None = None
        self._status: int | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self, data: dict) -> dict:
        return self._gateway._write_device(self._mac, self._device_type, data)

    def _parse(self, response: dict) -> None:
        """Update internal state from a bridge response."""
        if response.get("actionResult") is not None:
            _LOGGER.error("Blind %s actionResult: %s", self._mac, response["actionResult"])
            return

        rd = response.get("data", {})
        self._blind_type = rd.get("type", self._blind_type)
        self._status = rd.get("operation", self._status)

        pos = rd.get("currentPosition")
        if pos is not None:
            self._position = int(pos)

        self._available = True

    def _fire_callbacks(self) -> None:
        for cb in self._callbacks.values():
            try:
                cb()
            except Exception:
                _LOGGER.exception("Callback error for blind %s", self._mac)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Fetch current status from the gateway cache."""
        response = self._gateway._read_device(self._mac, self._device_type)
        msgType = response.get("msgType")
        if msgType != "ReadDeviceAck":
            _LOGGER.error("Unexpected response to ReadDevice: %s", msgType)
            self._available = False
            return
        self._parse(response)

    def open(self) -> None:
        """Open the blind (move to 0%)."""
        self._parse(self._write({"operation": OPERATION_OPEN}))
        self._fire_callbacks()

    def close(self) -> None:
        """Close the blind (move to 100%)."""
        self._parse(self._write({"operation": OPERATION_CLOSE}))
        self._fire_callbacks()

    def stop(self) -> None:
        """Stop any movement."""
        self._parse(self._write({"operation": OPERATION_STOP}))
        self._fire_callbacks()

    def set_position(self, position: int) -> None:
        """Set target position (0=open, 100=closed in bridge convention)."""
        self._parse(self._write({"targetPosition": position}))
        self._fire_callbacks()

    def multicast_update(self, message: dict) -> None:
        """Handle a pushed Report message from the gateway."""
        self._parse(message)
        self._fire_callbacks()

    def register_callback(self, cb_id: str, callback: callable) -> None:
        self._callbacks[cb_id] = callback

    def remove_callback(self, cb_id: str) -> None:
        self._callbacks.pop(cb_id, None)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mac(self) -> str:
        return self._mac

    @property
    def device_type(self) -> str:
        return self._device_type

    @property
    def available(self) -> bool:
        return self._available

    @property
    def position(self) -> int | None:
        """Position in bridge convention: 0=open, 100=closed."""
        return self._position

    @property
    def ha_position(self) -> int | None:
        """Position in HA convention: 0=closed, 100=open."""
        if self._position is None:
            return None
        return 100 - self._position

    @property
    def operation(self) -> int | None:
        """Last reported operation: 0=closing, 1=opening, 2=stopped."""
        return self._status

    @property
    def blind_type(self) -> int | None:
        """Blind type code reported by the gateway, if known."""
        return self._blind_type


class ConnectorGateway:
    """Connector Bridge (DD7002B) gateway."""

    def __init__(self, ip: str, key: str, timeout: float = 5.0) -> None:
        self._ip = ip
        self._key = key
        self._timeout = timeout

        self._mac: str | None = None
        self._device_type: str | None = None
        self._token: str | None = None
        self._access_token: str | None = None
        self._available = False
        self._device_list: dict[str, ConnectorBlind] = {}

        # Serialize and rate-limit all outbound traffic. The gateway is
        # single-threaded and drops off the network when it receives
        # concurrent or back-to-back requests, so every send goes through
        # this lock and is spaced out by MIN_SEND_INTERVAL.
        self._send_lock = Lock()
        self._last_send = 0.0

        # Multicast listener
        self._listening = False
        self._mcast_socket: socket.socket | None = None
        self._mcast_thread: Thread | None = None

    # ------------------------------------------------------------------
    # Token / auth
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str | None:
        if self._token is None or self._key is None:
            return None
        self._access_token = _compute_access_token(self._token, self._key)
        return self._access_token

    @property
    def access_token(self) -> str | None:
        if self._access_token is None:
            self._get_access_token()
        return self._access_token

    # ------------------------------------------------------------------
    # Low-level UDP send/receive
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """Space out transmissions so the gateway is never flooded.

        Must be called while holding ``self._send_lock``.
        """
        delta = time.monotonic() - self._last_send
        if delta < MIN_SEND_INTERVAL:
            time.sleep(MIN_SEND_INTERVAL - delta)
        self._last_send = time.monotonic()

    def _send(self, message: dict) -> list[dict]:
        """Send a UDP message and collect all response packets.

        All communication is serialized through ``self._send_lock`` and
        rate-limited via ``_throttle`` so the gateway is never hit by
        concurrent or back-to-back requests, which causes the DD7002B to
        drop off the network.
        """
        raw = json.dumps(message).encode("utf-8")
        responses: list[dict] = []
        attempt = 0

        with self._send_lock:
            while attempt < 3:
                self._throttle()
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.settimeout(self._timeout)
                    s.sendto(raw, (self._ip, UDP_PORT_SEND))

                    while True:
                        data, _ = s.recvfrom(SOCKET_BUFSIZE)
                        responses.append(json.loads(data))
                        if len(data) < int(0.9 * 1024):
                            break
                        s.settimeout(0.2)

                    return responses
                except socket.timeout:
                    if responses:
                        return responses
                    attempt += 1
                    _LOGGER.debug(
                        "Timeout attempt %d sending %s", attempt, message.get("msgType")
                    )
                finally:
                    s.close()

        self._available = False
        raise TimeoutError(f"No response from bridge at {self._ip} after 3 attempts")

    # ------------------------------------------------------------------
    # Device-level messages
    # ------------------------------------------------------------------

    def _write_device(self, mac: str, device_type: str, data: dict) -> dict:
        msg = {
            "msgType": "WriteDevice",
            "mac": mac,
            "deviceType": device_type,
            "AccessToken": self.access_token,
            "msgID": _timestamp(),
            "data": data,
        }
        responses = self._send(msg)
        return responses[0] if responses else {}

    def _read_device(self, mac: str, device_type: str) -> dict:
        msg = {
            "msgType": "ReadDevice",
            "mac": mac,
            "deviceType": device_type,
            "AccessToken": self.access_token,
            "msgID": _timestamp(),
        }
        responses = self._send(msg)
        return responses[0] if responses else {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def get_device_list(self) -> dict[str, ConnectorBlind]:
        """Query the gateway for its device list and populate blinds."""
        msg = {"msgType": "GetDeviceList", "msgID": _timestamp()}
        responses = self._send(msg)

        for response in responses:
            if response.get("msgType") != "GetDeviceListAck":
                continue

            gw_type = response.get("deviceType", "")
            if gw_type not in DEVICE_TYPES_GATEWAY:
                _LOGGER.warning("Unexpected gateway deviceType: %s", gw_type)

            # Update gateway metadata
            if self._token != response.get("token"):
                self._access_token = None
            self._mac = response.get("mac")
            self._device_type = gw_type
            self._token = response.get("token")
            self._available = True
            self._get_access_token()

            # Register blinds
            for blind_info in response.get("data", []):
                dtype = blind_info.get("deviceType", "")
                if dtype in DEVICE_TYPES_GATEWAY:
                    continue
                if dtype not in SUPPORTED_BLIND_TYPES:
                    _LOGGER.info("Skipping unsupported device type %s", dtype)
                    continue
                bmac = blind_info["mac"]
                if bmac not in self._device_list:
                    self._device_list[bmac] = ConnectorBlind(self, bmac, dtype)

        return self._device_list

    # ------------------------------------------------------------------
    # Multicast listener
    # ------------------------------------------------------------------

    def _create_mcast_socket(self) -> socket.socket:
        mreq = struct.pack("=4sl", socket.inet_aton(MULTICAST_ADDRESS), socket.INADDR_ANY)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(2.0)
        sock.bind(("", UDP_PORT_RECEIVE))
        return sock

    def _multicast_listen(self) -> None:
        """Background thread that receives multicast pushes from the bridge."""
        while self._listening:
            if self._mcast_socket is None:
                continue
            try:
                data, (ip, _) = self._mcast_socket.recvfrom(SOCKET_BUFSIZE)
            except socket.timeout:
                continue
            except Exception:
                _LOGGER.exception("Error reading multicast socket")
                continue

            if ip != self._ip:
                continue

            try:
                msg = json.loads(data)
            except Exception:
                continue

            msgType = msg.get("msgType")
            mac = msg.get("mac")

            if msgType == "Report" and mac in self._device_list:
                self._device_list[mac].multicast_update(msg)
            elif msgType == "Heartbeat":
                self._available = True
            elif msgType == "GetDeviceListAck":
                pass  # already handled during setup

    def start_listening(self) -> None:
        """Start the multicast listener thread."""
        if self._listening:
            return
        self._listening = True
        try:
            self._mcast_socket = self._create_mcast_socket()
        except OSError as err:
            _LOGGER.warning("Cannot open multicast socket (%s); falling back to polling", err)
            self._listening = False
            return

        self._mcast_thread = Thread(target=self._multicast_listen, daemon=True)
        self._mcast_thread.start()
        _LOGGER.info("Connector Bridge multicast listener started for %s", self._ip)

    def stop_listening(self) -> None:
        """Stop the multicast listener thread."""
        self._listening = False
        if self._mcast_thread:
            self._mcast_thread.join(timeout=5)
            self._mcast_thread = None
        if self._mcast_socket:
            self._mcast_socket.close()
            self._mcast_socket = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def mac(self) -> str | None:
        return self._mac

    @property
    def available(self) -> bool:
        return self._available

    @property
    def device_list(self) -> dict[str, ConnectorBlind]:
        return self._device_list
