"""Connector Bridge integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .bridge import ConnectorGateway
from .const import CONF_KEY, DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.COVER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Connector Bridge from a config entry."""
    host = entry.data[CONF_HOST]
    key = entry.data[CONF_KEY]

    gateway = ConnectorGateway(ip=host, key=key, timeout=DEFAULT_TIMEOUT)

    try:
        device_list = await hass.async_add_executor_job(gateway.get_device_list)
    except TimeoutError as err:
        raise ConfigEntryNotReady(f"Connector Bridge at {host} did not respond") from err
    except Exception as err:
        raise ConfigEntryNotReady(f"Error connecting to Connector Bridge: {err}") from err

    if not device_list:
        _LOGGER.warning("Connector Bridge at %s has no paired blinds", host)

    # Older entries used the host as the unique id. Migrate them to the
    # gateway MAC so the entry survives IP changes.
    if gateway.mac and entry.unique_id != gateway.mac:
        hass.config_entries.async_update_entry(entry, unique_id=gateway.mac)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = gateway

    # Start multicast listener for real-time push updates
    await hass.async_add_executor_job(gateway.start_listening)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when options (e.g. travel times) change.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        gateway: ConnectorGateway = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(gateway.stop_listening)

    return unload_ok
