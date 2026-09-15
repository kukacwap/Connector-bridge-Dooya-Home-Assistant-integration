"""Shared fixtures for the Connector Bridge test suite."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.connector_bridge.const import CONF_KEY, DOMAIN, OPT_TRAVEL_TIMES

from .helpers import FakeGateway

pytest_plugins = "pytest_homeassistant_custom_component"

GATEWAY_MAC = "abcdef123456"
BLIND_MAC = "abcdef1234560001"
STATELESS_MAC = "abcdef1234560002"
TRAVEL_TIME = 20.0


@pytest.fixture(autouse=True)
def _allow_custom_integration(request):
    """Load custom_components for tests that actually run Home Assistant."""
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry matching a bridge that has already been onboarded."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Connector Bridge (192.168.1.50)",
        unique_id="ab:cd:ef:12:34:56",
        data={"host": "192.168.1.50", CONF_KEY: "a" * 16},
        options={OPT_TRAVEL_TIMES: {STATELESS_MAC: TRAVEL_TIME}},
    )


@pytest.fixture
def fake_gateway() -> FakeGateway:
    """A gateway stand-in with one positional and one stateless blind."""
    return FakeGateway(
        ip="192.168.1.50",
        mac=GATEWAY_MAC,
        blinds={BLIND_MAC: 30, STATELESS_MAC: None},
    )


@pytest.fixture
def patch_gateway(fake_gateway):
    """Keep ConnectorGateway patched for the whole test, reloads included."""
    with patch(
        "custom_components.connector_bridge.ConnectorGateway",
        return_value=fake_gateway,
    ):
        yield fake_gateway


@pytest.fixture
async def setup_integration(hass, mock_config_entry, patch_gateway):
    """Set up the integration with a fake gateway behind it."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
