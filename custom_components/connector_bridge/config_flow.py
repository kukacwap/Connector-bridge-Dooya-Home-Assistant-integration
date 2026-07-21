"""Config flow for Connector Bridge integration."""

from __future__ import annotations

import logging
import socket
import struct

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .bridge import ConnectorGateway
from .const import (
    CONF_KEY,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MULTICAST_ADDRESS,
    SOCKET_BUFSIZE,
    UDP_PORT_SEND,
)

_LOGGER = logging.getLogger(__name__)

DISCOVERY_TIMEOUT = 5.0

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_KEY): str,
    }
)


def _discover_bridge_ip(timeout: float = DISCOVERY_TIMEOUT) -> str | None:
    """Attempt to discover the bridge IP via multicast."""
    import datetime
    import json

    try:
        mreq = struct.pack("=4sl", socket.inet_aton(MULTICAST_ADDRESS), socket.INADDR_ANY)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(timeout)
        sock.bind(("", 32101))

        msg = json.dumps(
            {"msgType": "GetDeviceList", "msgID": datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S000")}
        ).encode()
        sock.sendto(msg, (MULTICAST_ADDRESS, UDP_PORT_SEND))

        data, (ip, _) = sock.recvfrom(SOCKET_BUFSIZE)
        response = json.loads(data)
        if response.get("msgType") == "GetDeviceListAck":
            return ip
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


async def _test_connection(hass: HomeAssistant, host: str, key: str) -> str | None:
    """Try to connect to the bridge. Returns error key or None on success."""
    def _connect():
        gw = ConnectorGateway(ip=host, key=key, timeout=DEFAULT_TIMEOUT)
        gw.get_device_list()
        return gw.mac

    try:
        mac = await hass.async_add_executor_job(_connect)
        if mac is None:
            return "cannot_connect"
        return None
    except TimeoutError:
        return "cannot_connect"
    except Exception as err:
        _LOGGER.error("Unexpected error connecting to bridge: %s", err)
        return "unknown"


class ConnectorBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Connector Bridge."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            key = user_input[CONF_KEY].strip()

            error = await _test_connection(self.hass, host, key)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Connector Bridge ({host})",
                    data={CONF_HOST: host, CONF_KEY: key},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "key_hint": "Settings → About → tap 5× in Connector+ app"
            },
        )
