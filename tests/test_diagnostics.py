"""Tests for the diagnostics payload."""

from __future__ import annotations

from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.connector_bridge.const import CONF_KEY

from .conftest import BLIND_MAC, GATEWAY_MAC, STATELESS_MAC, TRAVEL_TIME


async def test_diagnostics_payload(hass, hass_client, setup_integration):
    data = await get_diagnostics_for_config_entry(hass, hass_client, setup_integration)

    assert data["gateway"]["model"] == "Connector Bridge (DD7002B)"
    assert data["gateway"]["available"] is True
    assert data["gateway"]["push_updates_active"] is True
    assert data["gateway"]["blind_count"] == 2

    by_position = {b["position"]: b for b in data["blinds"]}
    assert by_position[30]["reports_position"] is True
    assert by_position[30]["ha_position"] == 70
    assert by_position[None]["reports_position"] is False
    assert by_position[None]["travel_time"] == TRAVEL_TIME


async def test_diagnostics_redacts_secrets(hass, hass_client, setup_integration):
    data = await get_diagnostics_for_config_entry(hass, hass_client, setup_integration)
    entry_data = data["entry"]["data"]
    assert entry_data[CONF_KEY] == "**REDACTED**"
    assert entry_data["host"] == "**REDACTED**"

    serialized = str(data)
    assert "a" * 16 not in serialized
    assert "192.168.1.50" not in serialized
    assert GATEWAY_MAC not in serialized
    assert BLIND_MAC not in serialized
    assert STATELESS_MAC not in serialized


async def test_diagnostics_without_a_loaded_gateway(hass, hass_client, setup_integration):
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    data = await get_diagnostics_for_config_entry(hass, hass_client, setup_integration)
    assert data["gateway"] is None
