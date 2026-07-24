"""Gateway connectivity sensor for the Connector Bridge."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .bridge import ConnectorGateway
from .const import DOMAIN, GATEWAY_OFFLINE_AFTER, model_name

_LOGGER = logging.getLogger(__name__)

# Checked periodically so the sensor can go offline on silence, not just on
# a failed command.
SCAN_INTERVAL = timedelta(seconds=60)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    gateway: ConnectorGateway = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ConnectorBridgeConnectivity(gateway, entry)])


class ConnectorBridgeConnectivity(BinarySensorEntity):
    """Reports whether the bridge is currently reachable."""

    _attr_has_entity_name = True
    _attr_name = "Connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = True

    def __init__(self, gateway: ConnectorGateway, entry: ConfigEntry) -> None:
        self._gateway = gateway
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_{gateway.mac}_connectivity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, gateway.mac or entry.entry_id)},
            manufacturer="Dooya",
            name="Connector Bridge",
            model=model_name(gateway.device_type),
        )

    async def async_added_to_hass(self) -> None:
        self._gateway.register_gateway_callback(self.entity_id, self._on_gateway_update)

    async def async_will_remove_from_hass(self) -> None:
        self._gateway.remove_gateway_callback(self.entity_id)

    def _on_gateway_update(self) -> None:
        """Called from the gateway's listener/executor thread."""
        self.hass.loop.call_soon_threadsafe(self._handle_gateway_update)

    @callback
    def _handle_gateway_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """The connectivity sensor itself is always available."""
        return True

    @property
    def is_on(self) -> bool:
        """True when the bridge is considered connected."""
        if not self._gateway.available:
            return False

        # Treat prolonged silence as disconnected, so a bridge that vanished
        # without a failed command still shows up as offline.
        last_seen = self._gateway.last_seen
        if last_seen is None:
            return False
        return (dt_util.utcnow().timestamp() - last_seen) < GATEWAY_OFFLINE_AFTER

    @property
    def extra_state_attributes(self) -> dict:
        last_seen = self._gateway.last_seen
        return {
            "ip_address": self._gateway.ip,
            "mac": self._gateway.mac,
            "push_updates": self._gateway.multicast_active,
            "blinds": len(self._gateway.device_list),
            "last_seen": (
                dt_util.utc_from_timestamp(last_seen).isoformat()
                if last_seen is not None
                else None
            ),
        }

    async def async_update(self) -> None:
        """Re-evaluate connectivity on the polling interval.

        State is derived from the gateway, so there is nothing to fetch; the
        poll exists so silence eventually flips the sensor to off.
        """
        return
