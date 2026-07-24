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


async def _test_connection(
    hass: HomeAssistant, host: str, key: str
) -> tuple[str | None, str | None]:
    """Try to connect to the bridge.

    Returns a ``(error_key, mac)`` tuple. ``error_key`` is ``None`` on
    success, and ``mac`` is the gateway's MAC address (used as the stable
    unique id, since the IP can change).
    """
    def _connect():
        gw = ConnectorGateway(ip=host, key=key, timeout=DEFAULT_TIMEOUT)
        gw.get_device_list()
        return gw.mac

    try:
        mac = await hass.async_add_executor_job(_connect)
        if mac is None:
            return "cannot_connect", None
        return None, mac
    except TimeoutError:
        return "cannot_connect", None
    except Exception as err:
        _LOGGER.error("Unexpected error connecting to bridge: %s", err)
        return "unknown", None


class ConnectorBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Connector Bridge."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            key = user_input[CONF_KEY].strip()

            error, mac = await _test_connection(self.hass, host, key)
            if error:
                errors["base"] = error
            else:
                # Use the gateway MAC as the unique id so the entry survives
                # IP changes.
                await self.async_set_unique_id(mac)
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

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        """Handle reconfiguration of an existing entry (e.g. changed IP)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            key = user_input[CONF_KEY].strip()

            error, mac = await _test_connection(self.hass, host, key)
            if error:
                errors["base"] = error
            elif mac and any(
                other.entry_id != entry.entry_id and other.unique_id == mac
                for other in self._async_current_entries()
            ):
                # The supplied address points at a different bridge that is
                # already set up under another entry.
                return self.async_abort(reason="already_configured")
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    title=f"Connector Bridge ({host})",
                    unique_id=mac or entry.unique_id,
                    data={CONF_HOST: host, CONF_KEY: key},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                {
                    CONF_HOST: entry.data.get(CONF_HOST, ""),
                    CONF_KEY: entry.data.get(CONF_KEY, ""),
                },
            ),
            errors=errors,
            description_placeholders={
                "key_hint": "Settings → About → tap 5× in Connector+ app"
            },
        )
