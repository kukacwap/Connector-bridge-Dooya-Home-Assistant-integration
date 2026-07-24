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

# Operation codes
OPERATION_CLOSE = 0
OPERATION_OPEN = 1
OPERATION_STOP = 2
OPERATION_STATUS = 5
