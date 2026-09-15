"""Tests for the UDP protocol layer."""

from __future__ import annotations

import json
import re
import socket
import warnings

import pytest

from custom_components.connector_bridge import bridge as bridge_module
from custom_components.connector_bridge.bridge import (
    ConnectorBlind,
    ConnectorGateway,
    _compute_access_token,
    _timestamp,
    discover_gateways,
)
from custom_components.connector_bridge.const import (
    DEVICE_TYPE_BLIND,
    DEVICE_TYPE_TDBU,
    UDP_PORT_SEND,
)

from .helpers import FakeSocket

KEY = "1234567890abcdef"
GW_MAC = "abcdef123456"
BLIND_MAC = "abcdef1234560001"


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    """Remove the inter-send delay so tests do not sleep for real."""
    monkeypatch.setattr(bridge_module, "MIN_SEND_INTERVAL", 0)


def _gateway(script, monkeypatch, **kwargs):
    """Build a gateway whose socket replays ``script``."""
    fake = FakeSocket(script)
    monkeypatch.setattr(bridge_module.socket, "socket", fake.factory)
    gw = ConnectorGateway(ip="192.168.1.50", key=KEY, timeout=0.01, **kwargs)
    return gw, fake


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def test_timestamp_shape():
    stamp = _timestamp()
    assert re.fullmatch(r"\d{17}", stamp), stamp


def test_timestamp_is_not_deprecated():
    """The msgID must not be built from a deprecated datetime API."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        _timestamp()


def test_access_token_is_deterministic_hex():
    token = _compute_access_token("1234567890abcdef", KEY)
    assert token == token.upper()
    assert len(token) == 32
    int(token, 16)  # valid hex
    assert _compute_access_token("1234567890abcdef", KEY) == token


def test_access_token_rejects_bad_key_length():
    with pytest.raises(ValueError):
        _compute_access_token("1234567890abcdef", "tooshort")


# ----------------------------------------------------------------------
# discover_gateways
# ----------------------------------------------------------------------


def test_discover_gateways_collects_acks(monkeypatch):
    fake = FakeSocket(
        [
            [
                {"msgType": "GetDeviceListAck", "mac": GW_MAC},
                {"msgType": "Heartbeat", "mac": "ignored"},
            ]
        ]
    )
    monkeypatch.setattr(bridge_module.socket, "socket", fake.factory)
    found = discover_gateways(timeout=0.01)
    assert found == {GW_MAC: "192.168.1.50"}
    assert fake.closed == 1


def test_discover_gateways_survives_garbage(monkeypatch):
    fake = FakeSocket([[b"not json", {"msgType": "GetDeviceListAck", "mac": GW_MAC}]])
    monkeypatch.setattr(bridge_module.socket, "socket", fake.factory)
    assert discover_gateways(timeout=0.01) == {GW_MAC: "192.168.1.50"}


def test_discover_gateways_returns_empty_on_oserror(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(bridge_module.socket, "socket", boom)
    assert discover_gateways(timeout=0.01) == {}


# ----------------------------------------------------------------------
# ConnectorGateway._send
# ----------------------------------------------------------------------


def test_send_returns_single_response(monkeypatch):
    gw, fake = _gateway([[{"msgType": "Ack"}]], monkeypatch)
    assert gw._send({"msgType": "Ping"}) == [{"msgType": "Ack"}]
    assert gw.available is True
    payload, addr = fake.sent[0]
    assert addr == ("192.168.1.50", UDP_PORT_SEND)
    assert json.loads(payload)["msgType"] == "Ping"


def test_send_collects_multi_packet_response(monkeypatch):
    big = {"msgType": "GetDeviceListAck", "pad": "x" * 1200}
    gw, _ = _gateway([[big, {"msgType": "GetDeviceListAck", "tail": True}]], monkeypatch)
    responses = gw._send({"msgType": "GetDeviceList"})
    assert len(responses) == 2


def test_send_retries_three_times_then_raises(monkeypatch):
    gw, fake = _gateway(["timeout", "timeout", "timeout"], monkeypatch)
    with pytest.raises(TimeoutError):
        gw._send({"msgType": "Ping"})
    assert len(fake.sent) == 3
    assert gw.available is False


def test_send_recovers_on_later_attempt(monkeypatch):
    gw, fake = _gateway(["timeout", [{"msgType": "Ack"}]], monkeypatch)
    assert gw._send({"msgType": "Ping"}) == [{"msgType": "Ack"}]
    assert len(fake.sent) == 2
    assert gw.available is True


def test_send_closes_socket_on_every_attempt(monkeypatch):
    gw, fake = _gateway(["timeout", "timeout", "timeout"], monkeypatch)
    with pytest.raises(TimeoutError):
        gw._send({"msgType": "Ping"})
    assert fake.closed == 3


def test_send_survives_undecodable_packet(monkeypatch):
    """A malformed packet must not escape as a JSON error to the caller."""
    gw, _ = _gateway([[b"{not json"], [{"msgType": "Ack"}]], monkeypatch)
    assert gw._send({"msgType": "Ping"}) == [{"msgType": "Ack"}]


def test_send_raises_timeout_not_oserror_on_network_failure(monkeypatch):
    """An unreachable network must surface as TimeoutError, like a silent bridge."""

    class ExplodingSocket(FakeSocket):
        def sendto(self, data, addr):
            raise OSError(101, "Network is unreachable")

    fake = ExplodingSocket([])
    monkeypatch.setattr(bridge_module.socket, "socket", fake.factory)
    gw = ConnectorGateway(ip="192.168.1.50", key=KEY, timeout=0.01)
    with pytest.raises(TimeoutError):
        gw._send({"msgType": "Ping"})
    assert gw.available is False


def test_throttle_spaces_out_sends(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(bridge_module, "MIN_SEND_INTERVAL", 1.0)
    monkeypatch.setattr(bridge_module.time, "sleep", slept.append)
    gw, _ = _gateway([[{"msgType": "Ack"}], [{"msgType": "Ack"}]], monkeypatch)
    gw._send({"msgType": "Ping"})
    gw._send({"msgType": "Ping"})
    assert slept and slept[-1] > 0


def test_availability_transition_fires_gateway_callbacks(monkeypatch):
    gw, _ = _gateway([[{"msgType": "Ack"}], "timeout", "timeout", "timeout"], monkeypatch)
    calls: list[int] = []
    gw.register_gateway_callback("test", lambda: calls.append(1))

    gw._send({"msgType": "Ping"})
    assert len(calls) == 1  # unavailable -> available

    with pytest.raises(TimeoutError):
        gw._send({"msgType": "Ping"})
    assert len(calls) == 2  # available -> unavailable

    gw.remove_gateway_callback("test")


# ----------------------------------------------------------------------
# Device messages
# ----------------------------------------------------------------------


def test_write_device_message_shape(monkeypatch):
    gw, fake = _gateway([[{"msgType": "GetDeviceListAck", "mac": GW_MAC,
                           "deviceType": "02000001", "token": "1234567890abcdef",
                           "data": []}],
                         [{"msgType": "WriteDeviceAck"}]], monkeypatch)
    gw.get_device_list()
    gw._write_device(BLIND_MAC, DEVICE_TYPE_BLIND, {"operation": 1})
    sent = json.loads(fake.sent[-1][0])
    assert sent["msgType"] == "WriteDevice"
    assert sent["mac"] == BLIND_MAC
    assert sent["deviceType"] == DEVICE_TYPE_BLIND
    assert sent["data"] == {"operation": 1}
    assert sent["AccessToken"] == _compute_access_token("1234567890abcdef", KEY)


def test_read_device_message_shape(monkeypatch):
    gw, fake = _gateway([[{"msgType": "ReadDeviceAck"}]], monkeypatch)
    gw._read_device(BLIND_MAC, DEVICE_TYPE_BLIND)
    sent = json.loads(fake.sent[-1][0])
    assert sent["msgType"] == "ReadDevice"
    assert "data" not in sent


# ----------------------------------------------------------------------
# get_device_list
# ----------------------------------------------------------------------


def _device_list_ack(devices, token="1234567890abcdef"):
    return {
        "msgType": "GetDeviceListAck",
        "mac": GW_MAC,
        "deviceType": "02000001",
        "token": token,
        "data": devices,
    }


def test_get_device_list_registers_supported_blinds(monkeypatch):
    gw, _ = _gateway(
        [[_device_list_ack([
            {"mac": GW_MAC, "deviceType": "02000001"},
            {"mac": BLIND_MAC, "deviceType": DEVICE_TYPE_BLIND},
            {"mac": "abcdef1234560002", "deviceType": DEVICE_TYPE_TDBU},
            {"mac": "abcdef1234560003", "deviceType": "99999999"},
        ])]],
        monkeypatch,
    )
    devices = gw.get_device_list()
    assert set(devices) == {BLIND_MAC, "abcdef1234560002"}
    assert gw.mac == GW_MAC
    assert gw.device_type == "02000001"
    assert gw.available is True


def test_get_device_list_is_idempotent(monkeypatch):
    ack = _device_list_ack([{"mac": BLIND_MAC, "deviceType": DEVICE_TYPE_BLIND}])
    gw, _ = _gateway([[ack], [ack]], monkeypatch)
    first = gw.get_device_list()[BLIND_MAC]
    second = gw.get_device_list()[BLIND_MAC]
    assert first is second


def test_access_token_recomputed_when_token_rotates(monkeypatch):
    gw, _ = _gateway(
        [
            [_device_list_ack([], token="1111111111111111")],
            [_device_list_ack([], token="2222222222222222")],
        ],
        monkeypatch,
    )
    gw.get_device_list()
    first = gw.access_token
    gw.get_device_list()
    assert gw.access_token != first
    assert gw.access_token == _compute_access_token("2222222222222222", KEY)


def test_get_device_list_with_bad_key_raises_clear_error(monkeypatch):
    """A wrong-length key must fail loudly rather than silently half-setting up."""
    fake = FakeSocket([[_device_list_ack([])]])
    monkeypatch.setattr(bridge_module.socket, "socket", fake.factory)
    gw = ConnectorGateway(ip="192.168.1.50", key="short", timeout=0.01)
    with pytest.raises(ValueError):
        gw.get_device_list()


# ----------------------------------------------------------------------
# ConnectorBlind
# ----------------------------------------------------------------------


class StubGateway:
    """Captures device reads/writes without touching the network."""

    def __init__(self, response=None):
        self.response = response or {}
        self.writes: list[dict] = []

    def _write_device(self, mac, device_type, data):
        self.writes.append(data)
        return self.response

    def _read_device(self, mac, device_type):
        return self.response


def _blind(response=None):
    gw = StubGateway(response)
    return ConnectorBlind(gw, BLIND_MAC, DEVICE_TYPE_BLIND), gw


def test_blind_starts_unavailable_with_no_position():
    blind, _ = _blind()
    assert blind.available is False
    assert blind.position is None
    assert blind.ha_position is None


def test_blind_parses_read_ack():
    blind, _ = _blind(
        {"msgType": "ReadDeviceAck", "data": {"type": 1, "operation": 2, "currentPosition": 30}}
    )
    blind.update()
    assert blind.available is True
    assert blind.position == 30
    assert blind.ha_position == 70
    assert blind.operation == 2
    assert blind.blind_type == 1


def test_blind_update_marks_unavailable_on_wrong_msgtype():
    blind, _ = _blind({"msgType": "Error"})
    blind.update()
    assert blind.available is False


def test_blind_action_result_does_not_claim_availability():
    """A rejected command must not be read as proof the blind is reachable."""
    blind, _ = _blind({"msgType": "ReadDeviceAck", "actionResult": "AccessToken error"})
    blind.update()
    assert blind.available is False


def test_blind_commands_send_expected_payloads():
    blind, gw = _blind({"msgType": "WriteDeviceAck", "data": {"operation": 1}})
    blind.open()
    blind.close()
    blind.stop()
    blind.set_position(55)
    assert gw.writes == [
        {"operation": 1},
        {"operation": 0},
        {"operation": 2},
        {"targetPosition": 55},
    ]


def test_blind_commands_fire_state_callbacks():
    blind, _ = _blind({"msgType": "WriteDeviceAck", "data": {"currentPosition": 10}})
    seen: list[int] = []
    blind.register_callback("cb", lambda: seen.append(1))
    blind.open()
    assert seen == [1]
    blind.remove_callback("cb")
    blind.close()
    assert seen == [1]


def test_callback_exception_does_not_break_others():
    blind, _ = _blind({"msgType": "WriteDeviceAck", "data": {}})
    seen: list[str] = []

    def boom():
        raise RuntimeError("nope")

    blind.register_callback("bad", boom)
    blind.register_callback("good", lambda: seen.append("ok"))
    blind.open()
    assert seen == ["ok"]


def test_multicast_update_fires_event_on_change():
    blind, _ = _blind()
    events: list[dict] = []
    blind.register_event_callback("ev", events.append)

    blind.multicast_update(
        {"msgType": "Report", "data": {"operation": 1, "currentPosition": 40}}
    )
    assert len(events) == 1
    assert events[0]["source"] == "external"
    assert events[0]["position"] == 40
    assert events[0]["ha_position"] == 60


def test_multicast_update_deduplicates_identical_reports():
    blind, _ = _blind()
    events: list[dict] = []
    blind.register_event_callback("ev", events.append)
    report = {"msgType": "Report", "data": {"operation": 2, "currentPosition": 40}}
    blind.multicast_update(report)
    blind.multicast_update(report)
    assert len(events) == 1


def test_multicast_update_attributes_our_own_commands():
    blind, _ = _blind()
    events: list[dict] = []
    blind.register_event_callback("ev", events.append)
    blind.note_command()
    blind.multicast_update(
        {"msgType": "Report", "data": {"operation": 1, "currentPosition": 10}}
    )
    assert events[0]["source"] == "homeassistant"


def test_commanded_recently_expires(monkeypatch):
    blind, _ = _blind()
    assert blind.commanded_recently is False
    blind.note_command()
    assert blind.commanded_recently is True
    monkeypatch.setattr(
        bridge_module.time, "monotonic", lambda: blind._last_command + 100
    )
    assert blind.commanded_recently is False


def test_multicast_update_without_position_keeps_previous():
    """Stateless motors report operation only; the estimate must not be reset."""
    blind, _ = _blind()
    blind.multicast_update({"msgType": "Report", "data": {"currentPosition": 50}})
    blind.multicast_update({"msgType": "Report", "data": {"operation": 1}})
    assert blind.position == 50
    assert blind.operation == 1


# ----------------------------------------------------------------------
# Multicast listener
# ----------------------------------------------------------------------


def test_multicast_listener_dispatches_reports(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    blind = ConnectorBlind(gw, BLIND_MAC, DEVICE_TYPE_BLIND)
    gw._device_list[BLIND_MAC] = blind

    payloads = [
        json.dumps({"msgType": "Report", "mac": BLIND_MAC,
                    "data": {"operation": 1, "currentPosition": 20}}).encode(),
        json.dumps({"msgType": "Heartbeat", "mac": GW_MAC}).encode(),
    ]

    class Listener:
        def recvfrom(self, bufsize):
            if payloads:
                return payloads.pop(0), ("192.168.1.50", 32101)
            gw._listening = False
            raise TimeoutError

    gw._mcast_socket = Listener()
    gw._listening = True
    gw._multicast_listen()

    assert blind.position == 20
    assert gw.available is True


def test_multicast_listener_ignores_other_hosts(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    blind = ConnectorBlind(gw, BLIND_MAC, DEVICE_TYPE_BLIND)
    gw._device_list[BLIND_MAC] = blind

    class Listener:
        def __init__(self):
            self.sent = False

        def recvfrom(self, bufsize):
            if not self.sent:
                self.sent = True
                return (
                    json.dumps({"msgType": "Report", "mac": BLIND_MAC,
                                "data": {"currentPosition": 99}}).encode(),
                    ("10.0.0.9", 32101),
                )
            gw._listening = False
            raise TimeoutError

    gw._mcast_socket = Listener()
    gw._listening = True
    gw._multicast_listen()
    assert blind.position is None


def test_start_listening_falls_back_when_socket_unavailable(monkeypatch):
    gw, _ = _gateway([], monkeypatch)

    def boom():
        raise OSError("multicast blocked")

    monkeypatch.setattr(gw, "_create_mcast_socket", boom)
    gw.start_listening()
    assert gw.multicast_active is False
    assert gw._listening is False


def test_stop_listening_is_safe_when_never_started(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    gw.stop_listening()
    assert gw.multicast_active is False


def test_rediscover_updates_ip(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    gw._mac = GW_MAC
    monkeypatch.setattr(
        bridge_module, "discover_gateways", lambda timeout=5.0: {GW_MAC: "192.168.1.77"}
    )
    assert gw.rediscover(timeout=0.01) == "192.168.1.77"
    assert gw.ip == "192.168.1.77"


def test_rediscover_returns_none_when_unchanged(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    gw._mac = GW_MAC
    monkeypatch.setattr(
        bridge_module, "discover_gateways", lambda timeout=5.0: {GW_MAC: "192.168.1.50"}
    )
    assert gw.rediscover(timeout=0.01) is None


def test_event_callback_exception_does_not_break_others():
    blind, _ = _blind()
    seen: list[dict] = []

    def boom(payload):
        raise RuntimeError("nope")

    blind.register_event_callback("bad", boom)
    blind.register_event_callback("good", seen.append)
    blind.multicast_update({"msgType": "Report", "data": {"currentPosition": 5}})
    assert len(seen) == 1
    blind.remove_event_callback("good")
    blind.multicast_update({"msgType": "Report", "data": {"currentPosition": 6}})
    assert len(seen) == 1


def test_gateway_callback_exception_does_not_break_others(monkeypatch):
    gw, _ = _gateway([[{"msgType": "Ack"}]], monkeypatch)
    seen: list[int] = []

    def boom():
        raise RuntimeError("nope")

    gw.register_gateway_callback("bad", boom)
    gw.register_gateway_callback("good", lambda: seen.append(1))
    gw._send({"msgType": "Ping"})
    assert seen == [1]


def test_blind_properties_expose_identity(monkeypatch):
    blind, _ = _blind()
    assert blind.mac == BLIND_MAC
    assert blind.device_type == DEVICE_TYPE_BLIND


def test_multicast_listener_ignores_undecodable_and_unknown(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    payloads = [
        b"{not json",
        json.dumps({"msgType": "Report", "mac": "unknown"}).encode(),
        json.dumps({"msgType": "GetDeviceListAck", "mac": GW_MAC}).encode(),
    ]

    class Listener:
        def recvfrom(self, bufsize):
            if payloads:
                return payloads.pop(0), ("192.168.1.50", 32101)
            gw._listening = False
            raise TimeoutError

    gw._mcast_socket = Listener()
    gw._listening = True
    gw._multicast_listen()
    assert gw.available is True


def test_multicast_listener_survives_socket_errors(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    calls = {"n": 0}

    class Listener:
        def recvfrom(self, bufsize):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("socket went away")
            gw._listening = False
            raise TimeoutError

    gw._mcast_socket = Listener()
    gw._listening = True
    gw._multicast_listen()
    assert calls["n"] == 2


def test_start_and_stop_listening_round_trip(monkeypatch):
    gw, _ = _gateway([], monkeypatch)

    class Listener:
        def __init__(self):
            self.closed = False

        def recvfrom(self, bufsize):
            raise TimeoutError

        def close(self):
            self.closed = True

    listener = Listener()
    monkeypatch.setattr(gw, "_create_mcast_socket", lambda: listener)

    gw.start_listening()
    assert gw.multicast_active is True
    gw.start_listening()  # idempotent

    gw.stop_listening()
    assert gw.multicast_active is False
    assert listener.closed is True
    assert gw._mcast_thread is None


def test_rediscover_without_a_known_mac(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    assert gw.rediscover(timeout=0.01) is None


def test_gateway_properties_before_discovery(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    assert gw.ip == "192.168.1.50"
    assert gw.mac is None
    assert gw.device_type is None
    assert gw.last_seen is None
    assert gw.device_list == {}
    gw.set_ip("192.168.1.88")
    assert gw.ip == "192.168.1.88"


def test_access_token_is_none_without_a_token(monkeypatch):
    gw, _ = _gateway([], monkeypatch)
    assert gw.access_token is None


def test_get_device_list_ignores_unrelated_messages(monkeypatch):
    gw, _ = _gateway([[{"msgType": "Heartbeat", "mac": GW_MAC}]], monkeypatch)
    assert gw.get_device_list() == {}
    assert gw.mac is None


def test_get_device_list_warns_on_unknown_gateway_type(monkeypatch, caplog):
    ack = _device_list_ack([])
    ack["deviceType"] = "99999999"
    gw, _ = _gateway([[ack]], monkeypatch)
    gw.get_device_list()
    assert "Unexpected gateway deviceType" in caplog.text


def test_send_returns_what_arrived_before_a_mid_stream_timeout(monkeypatch):
    """A device list split across packets is kept even if the tail never lands."""
    fake = FakeSocket([[{"msgType": "GetDeviceListAck", "pad": "x" * 1200}]])
    monkeypatch.setattr(bridge_module.socket, "socket", fake.factory)
    gw = ConnectorGateway(ip="192.168.1.50", key=KEY, timeout=0.01)
    responses = gw._send({"msgType": "GetDeviceList"})
    assert len(responses) == 1
    assert gw.available is True


def test_create_mcast_socket_joins_the_group(monkeypatch):
    """The listener must join the multicast group and bind the receive port."""
    calls = {"opts": [], "bound": None, "timeout": None}

    class Sock:
        def setsockopt(self, level, option, value):
            calls["opts"].append((level, option))

        def settimeout(self, timeout):
            calls["timeout"] = timeout

        def bind(self, addr):
            calls["bound"] = addr

    monkeypatch.setattr(bridge_module.socket, "socket", lambda *a, **k: Sock())
    gw = ConnectorGateway(ip="192.168.1.50", key=KEY, timeout=0.01)
    gw._create_mcast_socket()

    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR) in calls["opts"]
    assert (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP) in calls["opts"]
    assert calls["bound"] == ("", bridge_module.UDP_PORT_RECEIVE)
    assert calls["timeout"] == 2.0


def test_multicast_listener_exits_without_a_socket(monkeypatch):
    """A missing socket must end the thread, not spin the CPU."""
    gw, _ = _gateway([], monkeypatch)
    gw._mcast_socket = None
    gw._listening = True
    gw._multicast_listen()  # returns instead of looping forever
