"""Config flow for Connector Bridge integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr

try:  # Home Assistant 2024.8+
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:  # pragma: no cover - older cores
    from homeassistant.components.dhcp import DhcpServiceInfo

from .bridge import ConnectorGateway, discover_gateways
from .const import (
    CONF_KEY,
    DEFAULT_TIMEOUT,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
    OPT_TRAVEL_TIMES,
)

_LOGGER = logging.getLogger(__name__)

DISCOVERY_TIMEOUT = 5.0

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_KEY): str,
    }
)


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

    def __init__(self) -> None:
        self._discovered_host: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "ConnectorBridgeOptionsFlow":
        """Return the options flow handler."""
        return ConnectorBridgeOptionsFlow()

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Handle a bridge found via DHCP.

        For an already-configured bridge this quietly updates the stored
        address, which is how the integration follows a changed DHCP lease.
        """
        mac = dr.format_mac(discovery_info.macaddress)
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})

        # Unknown device: confirm it really is a Connector bridge before
        # offering it, since the DHCP matcher is deliberately broad.
        gateways = await self.hass.async_add_executor_job(discover_gateways)
        if not any(dr.format_mac(found) == mac for found in gateways):
            return self.async_abort(reason="not_connector_bridge")

        self._discovered_host = discovery_info.ip
        self.context["title_placeholders"] = {"name": f"Connector Bridge ({discovery_info.ip})"}
        return await self.async_step_user()

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
                await self.async_set_unique_id(dr.format_mac(mac))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Connector Bridge ({host})",
                    data={CONF_HOST: host, CONF_KEY: key},
                )

            suggested_host = host
        else:
            # Offer the bridge's address automatically so onboarding is just
            # entering the API key.
            suggested_host = self._discovered_host
            if suggested_host is None:
                gateways = await self.hass.async_add_executor_job(
                    discover_gateways, DISCOVERY_TIMEOUT
                )
                suggested_host = next(iter(gateways.values()), "")

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, {CONF_HOST: suggested_host or ""}
            ),
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
                other.entry_id != entry.entry_id
                and other.unique_id == dr.format_mac(mac)
                for other in self._async_current_entries()
            ):
                # The supplied address points at a different bridge that is
                # already set up under another entry.
                return self.async_abort(reason="already_configured")
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    title=f"Connector Bridge ({host})",
                    unique_id=dr.format_mac(mac) if mac else entry.unique_id,
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


class ConnectorBridgeOptionsFlow(config_entries.OptionsFlow):
    """Options flow to set per-blind full open/close travel times."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Configure the travel time (seconds) for each blind."""
        if user_input is not None:
            travel_times = {mac: float(value) for mac, value in user_input.items()}
            return self.async_create_entry(
                title="", data={OPT_TRAVEL_TIMES: travel_times}
            )

        gateway = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        current: dict = self.config_entry.options.get(OPT_TRAVEL_TIMES, {})
        macs = list(gateway.device_list) if gateway else list(current)

        schema_dict = {
            vol.Optional(
                mac, default=current.get(mac, DEFAULT_TRAVEL_TIME)
            ): vol.All(vol.Coerce(float), vol.Range(min=1, max=120))
            for mac in macs
        }

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict)
        )
