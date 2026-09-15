"""Tests for integration setup, IP recovery and unload."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.connector_bridge.const import (
    CONF_KEY,
    DOMAIN,
    ISSUE_IP_CHANGED,
    ISSUE_MULTICAST_UNAVAILABLE,
)

from .conftest import GATEWAY_MAC
from .helpers import FakeGateway

FORMATTED_MAC = dr.format_mac(GATEWAY_MAC)


async def test_setup_and_unload(hass, setup_integration, fake_gateway):
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][entry.entry_id] is fake_gateway
    assert fake_gateway.listening is True

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert fake_gateway.stopped is True
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_gateway_device_registered(hass, setup_integration):
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, GATEWAY_MAC)})
    assert device is not None
    assert device.manufacturer == "Dooya"
    assert device.model == "Connector Bridge (DD7002B)"
    assert (dr.CONNECTION_NETWORK_MAC, FORMATTED_MAC) in device.connections


async def test_unique_id_migrated_from_host(hass, fake_gateway):
    """Entries created before MAC-based ids must be migrated on setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.50",
        data={CONF_HOST: "192.168.1.50", CONF_KEY: "a" * 16},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.connector_bridge.ConnectorGateway", return_value=fake_gateway
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.unique_id == FORMATTED_MAC


async def test_setup_recovers_from_changed_ip(hass, mock_config_entry):
    """A bridge that moved to a new DHCP lease is found and followed."""
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    gateway.get_device_list_error = TimeoutError("no answer")

    def _clear_error():
        gateway.get_device_list_error = None
        return {}

    mock_config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
        ),
        patch(
            "custom_components.connector_bridge.discover_gateways",
            side_effect=lambda *a, **k: (_clear_error(), {GATEWAY_MAC: "192.168.1.99"})[1],
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert gateway.ip == "192.168.1.99"
    assert mock_config_entry.data[CONF_HOST] == "192.168.1.99"
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE_IP_CHANGED) is not None


async def test_setup_retries_when_bridge_is_gone(hass, mock_config_entry):
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    gateway.get_device_list_error = TimeoutError("no answer")

    mock_config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
        ),
        patch("custom_components.connector_bridge.discover_gateways", return_value={}),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_on_network_error(hass, mock_config_entry):
    """An OSError from the socket layer must retry, not raise into HA."""
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    gateway.get_device_list_error = OSError(101, "Network is unreachable")

    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_multicast_issue_raised_when_push_unavailable(
    hass, mock_config_entry, fake_gateway
):
    fake_gateway.multicast_active = False
    fake_gateway.start_listening = lambda: None
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.connector_bridge.ConnectorGateway", return_value=fake_gateway
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE_MULTICAST_UNAVAILABLE) is not None


async def test_no_multicast_issue_when_push_works(hass, setup_integration):
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE_MULTICAST_UNAVAILABLE) is None


async def test_options_update_reloads_entry(hass, setup_integration, fake_gateway):
    entry = setup_integration
    hass.config_entries.async_update_entry(
        entry, options={"travel_times": {"abcdef1234560002": 30.0}}
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_unload_twice_is_safe(hass, setup_integration):
    entry = setup_integration
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # A second unload must not raise (e.g. KeyError on missing hass.data).
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_unreachable_network_still_triggers_ip_recovery(hass, mock_config_entry):
    """A bridge on another subnet answers with OSError, not silence.

    The gateway turns that into a TimeoutError so setup goes looking for the
    bridge's new address instead of just retrying the old one.
    """
    from unittest.mock import patch as _patch

    from custom_components.connector_bridge import bridge as bridge_module

    class DeadSocket:
        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, timeout):
            pass

        def sendto(self, data, addr):
            raise OSError(101, "Network is unreachable")

        def close(self):
            pass

    with (
        _patch.object(bridge_module, "MIN_SEND_INTERVAL", 0),
        _patch.object(bridge_module.socket, "socket", DeadSocket),
    ):
        gateway = bridge_module.ConnectorGateway(
            ip="192.168.1.50", key="a" * 16, timeout=0.01
        )
        gateway._mac = GATEWAY_MAC
        mock_config_entry.add_to_hass(hass)

        with (
            _patch(
                "custom_components.connector_bridge.ConnectorGateway",
                return_value=gateway,
            ),
            _patch(
                "custom_components.connector_bridge.discover_gateways",
                return_value={GATEWAY_MAC: "192.168.1.99"},
            ) as discover,
        ):
            await hass.config_entries.async_setup(mock_config_entry.entry_id)
            await hass.async_block_till_done()

    assert discover.called
    assert mock_config_entry.data[CONF_HOST] == "192.168.1.99"


async def test_recovery_skipped_without_a_unique_id(hass, mock_config_entry):
    """Without a MAC there is nothing to match the bridge against."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        data={CONF_HOST: "192.168.1.50", CONF_KEY: "a" * 16},
    )
    entry.add_to_hass(hass)
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    gateway.get_device_list_error = TimeoutError("no answer")

    with (
        patch(
            "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
        ),
        patch("custom_components.connector_bridge.discover_gateways") as discover,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert not discover.called
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_recovery_ignores_other_bridges(hass, mock_config_entry):
    """Another Connector bridge on the LAN must not hijack this entry."""
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    gateway.get_device_list_error = TimeoutError("no answer")
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
        ),
        patch(
            "custom_components.connector_bridge.discover_gateways",
            return_value={"ffffffffffff": "192.168.1.70"},
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert gateway.ip == "192.168.1.50"
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_recovery_declines_when_the_address_is_unchanged(hass, mock_config_entry):
    """Found at the same address means it is not an IP change; keep retrying."""
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    gateway.get_device_list_error = TimeoutError("no answer")
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
        ),
        patch(
            "custom_components.connector_bridge.discover_gateways",
            return_value={GATEWAY_MAC: "192.168.1.50"},
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_IP_CHANGED) is None


async def test_setup_without_a_gateway_mac(hass, mock_config_entry):
    """A gateway that never reported a MAC must not register a device."""
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    gateway.mac = None
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, GATEWAY_MAC)}) is None


async def test_setup_warns_when_no_blinds_are_paired(hass, mock_config_entry, caplog):
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {})
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert "has no paired blinds" in caplog.text
