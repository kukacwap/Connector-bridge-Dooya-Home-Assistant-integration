"""Tests for the gateway connectivity sensor."""

from __future__ import annotations

import time

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.helpers import entity_registry as er

from custom_components.connector_bridge.const import DOMAIN, GATEWAY_OFFLINE_AFTER

from .conftest import GATEWAY_MAC

SENSOR = "binary_sensor.connector_bridge_connectivity"


async def test_sensor_created(hass, setup_integration):
    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.CONNECTIVITY
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Connector Bridge Connectivity"


async def test_unique_id_and_diagnostic_category(hass, setup_integration):
    registry = er.async_get(hass)
    entry = registry.async_get(SENSOR)
    assert entry.unique_id == f"{DOMAIN}_{GATEWAY_MAC}_connectivity"
    assert entry.entity_category == "diagnostic"


async def test_attributes(hass, setup_integration):
    attrs = hass.states.get(SENSOR).attributes
    assert attrs["ip_address"] == "192.168.1.50"
    assert attrs["mac"] == GATEWAY_MAC
    assert attrs["push_updates"] is True
    assert attrs["blinds"] == 2
    assert attrs["last_seen"] is not None


async def test_goes_off_when_gateway_unavailable(hass, setup_integration, fake_gateway):
    fake_gateway.available = False
    fake_gateway.fire_gateway()
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == STATE_OFF


async def test_goes_off_after_prolonged_silence(hass, setup_integration, fake_gateway):
    """A bridge that stopped talking must go offline even without a failed send."""
    fake_gateway.last_seen = time.time() - GATEWAY_OFFLINE_AFTER - 10
    fake_gateway.fire_gateway()
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == STATE_OFF


async def test_off_when_never_seen(hass, setup_integration, fake_gateway):
    fake_gateway.last_seen = None
    fake_gateway.fire_gateway()
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == STATE_OFF


async def test_sensor_itself_stays_available(hass, setup_integration, fake_gateway):
    """The sensor reports the outage; it must not become unavailable itself."""
    fake_gateway.available = False
    fake_gateway.fire_gateway()
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == STATE_OFF  # not "unavailable"


async def test_callback_removed_on_unload(hass, setup_integration, fake_gateway):
    assert fake_gateway._gateway_callbacks
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert fake_gateway._gateway_callbacks == {}
