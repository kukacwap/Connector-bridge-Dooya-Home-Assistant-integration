"""Constants for the Connector Bridge integration."""

DOMAIN = "connector_bridge"

CONF_INTERFACE = "interface"
CONF_KEY = "key"

DEFAULT_INTERFACE = "any"
DEFAULT_TIMEOUT = 5.0
DEFAULT_MCAST_TIMEOUT = 8.0
# Polling is only a fallback: real-time state arrives via multicast push.
# The DD7002B gateway is fragile, so keep baseline polling infrequent.
DEFAULT_SCAN_INTERVAL = 120

# Minimum delay (seconds) between consecutive UDP transmissions to the
# gateway. The DD7002B is single-threaded and drops off the network when hit
# with concurrent or back-to-back requests, so all traffic is serialized and
# spaced out by at least this interval.
MIN_SEND_INTERVAL = 1.0

# Time-based position estimation for stateless (unidirectional) blinds.
# Option key holding a {mac: seconds} map of full open/close travel times.
OPT_TRAVEL_TIMES = "travel_times"
DEFAULT_TRAVEL_TIME = 15.0

MULTICAST_ADDRESS = "238.0.0.18"
UDP_PORT_SEND = 32100
UDP_PORT_RECEIVE = 32101
SOCKET_BUFSIZE = 4096

# Supported gateway device types
DEVICE_TYPES_GATEWAY = ["02000001", "02000002"]

# Blind device types
DEVICE_TYPE_BLIND = "10000000"
DEVICE_TYPE_TDBU = "10000001"
DEVICE_TYPE_DR = "10000002"
DEVICE_TYPE_SUNBLIND = "10000011"
DEVICE_TYPE_WIFI_BLIND = "22000002"
DEVICE_TYPE_WIFI_CURTAIN = "22000000"

# Human-readable model names shown in the device registry, keyed by the raw
# deviceType code reported by the gateway.
DEVICE_TYPE_NAMES = {
    "02000001": "Connector Bridge (DD7002B)",
    "02000002": "Connector Bridge",
    DEVICE_TYPE_BLIND: "Roller Blind",
    DEVICE_TYPE_TDBU: "Top-Down/Bottom-Up Blind",
    DEVICE_TYPE_DR: "Double Roller Blind",
    DEVICE_TYPE_SUNBLIND: "Sunblind",
    DEVICE_TYPE_WIFI_BLIND: "Wi-Fi Blind",
    DEVICE_TYPE_WIFI_CURTAIN: "Wi-Fi Curtain",
}


def model_name(device_type: str | None) -> str:
    """Return a human-readable model name for a raw device type code."""
    if not device_type:
        return "Unknown device"
    return DEVICE_TYPE_NAMES.get(device_type, f"Unknown device ({device_type})")


# Repair issue identifiers
ISSUE_MULTICAST_UNAVAILABLE = "multicast_unavailable"
ISSUE_IP_CHANGED = "ip_changed"

# Fired when a blind reports movement, including moves Home Assistant did not
# initiate (for example a physical remote).
EVENT_BLIND_MOVED = "connector_bridge_blind_moved"

# A gateway report arriving within this many seconds of a command we sent is
# treated as the echo of that command rather than an external move.
EXTERNAL_COMMAND_GRACE = 8.0

# Service names
SERVICE_MOVE_FOR_DURATION = "move_for_duration"

# Seconds without any gateway contact before the connectivity sensor reports
# the bridge as disconnected.
GATEWAY_OFFLINE_AFTER = 300

# Operation codes
OPERATION_CLOSE = 0
OPERATION_OPEN = 1
OPERATION_STOP = 2
OPERATION_STATUS = 5
