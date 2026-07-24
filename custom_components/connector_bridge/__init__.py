"""Connector Bridge integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .bridge import ConnectorGateway, discover_gateways
from .const import (
    CONF_KEY,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ISSUE_IP_CHANGED,
    ISSUE_MULTICAST_UNAVAILABLE,
    model_name,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.COVER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Connector Bridge from a config entry."""
    host = entry.data[CONF_HOST]
    key = entry.data[CONF_KEY]

    gateway = ConnectorGateway(ip=host, key=key, timeout=DEFAULT_TIMEOUT)

    try:
        device_list = await hass.async_add_executor_job(gateway.get_device_list)
    except TimeoutError as err:
        # The bridge did not answer at its configured address. It has most
        # likely been given a new IP by DHCP, so try to find it again.
        recovered = await _async_recover_ip(hass, entry, gateway)
        if not recovered:
            raise ConfigEntryNotReady(
                f"Connector Bridge at {host} did not respond"
            ) from err
        try:
            device_list = await hass.async_add_executor_job(gateway.get_device_list)
        except Exception as err2:
            raise ConfigEntryNotReady(
                f"Connector Bridge not reachable at {recovered}"
            ) from err2
        host = recovered
    except Exception as err:
        raise ConfigEntryNotReady(f"Error connecting to Connector Bridge: {err}") from err

    if not device_list:
        _LOGGER.warning("Connector Bridge at %s has no paired blinds", host)

    # Older entries used the host as the unique id. Migrate them to the
    # gateway MAC so the entry survives IP changes.
    if gateway.mac:
        formatted_mac = dr.format_mac(gateway.mac)
        if entry.unique_id != formatted_mac:
            hass.config_entries.async_update_entry(entry, unique_id=formatted_mac)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = gateway

    _async_register_gateway_device(hass, entry, gateway)

    # Start multicast listener for real-time push updates
    await hass.async_add_executor_job(gateway.start_listening)
    _async_check_multicast_issue(hass, gateway)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when options (e.g. travel times) change.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_recover_ip(
    hass: HomeAssistant, entry: ConfigEntry, gateway: ConnectorGateway
) -> str | None:
    """Find the gateway again after a DHCP address change.

    The config entry's unique id is the gateway MAC, so the bridge can be
    matched on the network regardless of which address it now holds.
    """
    target_mac = entry.unique_id
    if not target_mac:
        return None

    _LOGGER.debug("Bridge unreachable at %s, searching the network", gateway.ip)
    gateways = await hass.async_add_executor_job(discover_gateways)

    for mac, ip in gateways.items():
        if dr.format_mac(mac) != dr.format_mac(target_mac):
            continue
        if ip == gateway.ip:
            return None

        _LOGGER.info("Connector Bridge moved to %s, updating config entry", ip)
        gateway.set_ip(ip)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_HOST: ip},
            title=f"Connector Bridge ({ip})",
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_IP_CHANGED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_IP_CHANGED,
            translation_placeholders={"ip": ip},
        )
        return ip

    return None


@callback
def _async_register_gateway_device(
    hass: HomeAssistant, entry: ConfigEntry, gateway: ConnectorGateway
) -> None:
    """Create the gateway device so blinds can hang off it via via_device.

    Registering the MAC as a network connection also lets Home Assistant
    match DHCP leases for this bridge and hand us its new address.
    """
    if not gateway.mac:
        return

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, gateway.mac)},
        connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(gateway.mac))},
        manufacturer="Dooya",
        name="Connector Bridge",
        model=model_name(gateway.device_type),
        configuration_url=f"http://{gateway.ip}",
    )


@callback
def _async_check_multicast_issue(
    hass: HomeAssistant, gateway: ConnectorGateway
) -> None:
    """Raise a repair issue when push updates are unavailable."""
    if gateway.multicast_active:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MULTICAST_UNAVAILABLE)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_MULTICAST_UNAVAILABLE,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MULTICAST_UNAVAILABLE,
    )


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
