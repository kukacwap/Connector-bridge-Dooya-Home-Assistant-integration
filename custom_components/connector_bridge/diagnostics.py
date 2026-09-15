"""Diagnostics support for the Connector Bridge integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .bridge import ConnectorGateway
from .const import CONF_KEY, DOMAIN, OPT_TRAVEL_TIMES, model_name

# The API key authenticates against the bridge, and the MAC/host identify the
# user's network, so keep them out of shared diagnostics.
TO_REDACT = {CONF_KEY, "host", "mac", "ip_address"}


def _redact_options(options: dict[str, Any]) -> dict[str, Any]:
    """Strip hardware addresses out of the stored options.

    The travel time map is keyed by blind MAC. The values themselves are
    reported per blind below, keyed by index, so only the count is kept here.
    """
    redacted = dict(options)
    travel_times = redacted.pop(OPT_TRAVEL_TIMES, None)
    if travel_times is not None:
        redacted["travel_times_configured"] = len(travel_times)
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    gateway: ConnectorGateway | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    data: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": _redact_options(dict(entry.options)),
            "version": entry.version,
        }
    }

    if gateway is None:
        data["gateway"] = None
        return data

    last_seen = gateway.last_seen
    data["gateway"] = {
        "model": model_name(gateway.device_type),
        "device_type": gateway.device_type,
        "available": gateway.available,
        "push_updates_active": gateway.multicast_active,
        "last_seen": (
            dt_util.utc_from_timestamp(last_seen).isoformat()
            if last_seen is not None
            else None
        ),
        "blind_count": len(gateway.device_list),
    }

    travel_times = entry.options.get(OPT_TRAVEL_TIMES, {})
    blinds = []
    for index, blind in enumerate(gateway.device_list.values()):
        blinds.append(
            {
                # Index rather than MAC so devices stay distinguishable
                # without exposing hardware addresses.
                "index": index,
                "device_type": blind.device_type,
                "model": model_name(blind.device_type),
                "available": blind.available,
                "reports_position": blind.position is not None,
                "position": blind.position,
                "ha_position": blind.ha_position,
                "operation": blind.operation,
                "blind_type": blind.blind_type,
                "travel_time": travel_times.get(blind.mac),
            }
        )
    data["blinds"] = blinds

    return data
