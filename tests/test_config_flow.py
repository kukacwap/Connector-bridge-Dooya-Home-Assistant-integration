"""Tests for the config and options flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from custom_components.connector_bridge.const import (
    CONF_KEY,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
    OPT_TRAVEL_TIMES,
)

from .conftest import BLIND_MAC, GATEWAY_MAC, STATELESS_MAC

HOST = "192.168.1.50"
KEY = "1234567890abcdef"
FORMATTED_MAC = "ab:cd:ef:12:34:56"


def _patch_connect(mac=GATEWAY_MAC, error=None):
    """Patch the gateway used by the flow's connection test."""

    class _Gateway:
        def __init__(self, *args, **kwargs):
            self.mac = mac

        def get_device_list(self):
            if error is not None:
                raise error
            return {}

    return patch("custom_components.connector_bridge.config_flow.ConnectorGateway", _Gateway)


async def test_user_flow_success(hass):
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={GATEWAY_MAC: HOST},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM

    with _patch_connect():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_KEY: KEY}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST, CONF_KEY: KEY}
    assert result["result"].unique_id == FORMATTED_MAC


async def test_user_flow_strips_whitespace(hass):
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with _patch_connect():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: f"  {HOST} ", CONF_KEY: f" {KEY}  "}
        )
    assert result["data"] == {CONF_HOST: HOST, CONF_KEY: KEY}


async def test_user_flow_cannot_connect(hass):
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with _patch_connect(error=TimeoutError("silent")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_KEY: KEY}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_rejects_bad_key(hass):
    """A key of the wrong length must be reported as such, not as 'unknown'."""
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_KEY: "tooshort"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_key"}


async def test_user_flow_unknown_error(hass):
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with _patch_connect(error=RuntimeError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_KEY: KEY}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_no_mac_is_cannot_connect(hass):
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with _patch_connect(mac=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_KEY: KEY}
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_entry_aborts(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with _patch_connect():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_KEY: KEY}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ----------------------------------------------------------------------
# DHCP discovery
# ----------------------------------------------------------------------


def _dhcp_info(ip=HOST, mac=GATEWAY_MAC):
    return DhcpServiceInfo(ip=ip, hostname="connector", macaddress=mac)


async def test_dhcp_updates_host_of_existing_entry(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=_dhcp_info(ip="192.168.1.99")
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == "192.168.1.99"


async def test_dhcp_rejects_non_connector_device(hass):
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_DHCP}, data=_dhcp_info()
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connector_bridge"


async def test_dhcp_offers_the_discovered_host(hass):
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={GATEWAY_MAC: HOST},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_DHCP}, data=_dhcp_info()
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with _patch_connect():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_KEY: KEY}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


# ----------------------------------------------------------------------
# Reconfigure
# ----------------------------------------------------------------------


async def test_reconfigure_updates_host(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    with (
        _patch_connect(),
        patch("custom_components.connector_bridge.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.77", CONF_KEY: KEY}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == "192.168.1.77"


async def test_reconfigure_cannot_connect(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    with _patch_connect(error=TimeoutError("silent")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.77", CONF_KEY: KEY}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert mock_config_entry.data[CONF_HOST] == HOST


async def test_reconfigure_pointing_at_another_bridge_aborts(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    other = type(mock_config_entry)(
        domain=DOMAIN, unique_id="11:22:33:44:55:66",
        data={CONF_HOST: "192.168.1.60", CONF_KEY: KEY},
    )
    other.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    with _patch_connect(mac="112233445566"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.60", CONF_KEY: KEY}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ----------------------------------------------------------------------
# Options flow
# ----------------------------------------------------------------------


async def test_options_flow_lists_known_blinds(hass, setup_integration):
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    keys = {str(k) for k in result["data_schema"].schema}
    assert STATELESS_MAC in keys

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {STATELESS_MAC: 12}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Fields left untouched keep their default, so every listed blind is saved.
    assert result["data"][OPT_TRAVEL_TIMES][STATELESS_MAC] == 12.0
    assert result["data"][OPT_TRAVEL_TIMES][BLIND_MAC] == DEFAULT_TRAVEL_TIME


async def test_options_flow_keeps_travel_times_of_absent_blinds(
    hass, setup_integration, fake_gateway
):
    """A blind that did not answer this time must not lose its travel time."""
    hass.config_entries.async_update_entry(
        setup_integration,
        options={OPT_TRAVEL_TIMES: {STATELESS_MAC: 20.0, "aabbccddeeff9999": 42.0}},
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {STATELESS_MAC: 25}
    )
    saved = result["data"][OPT_TRAVEL_TIMES]
    assert saved[STATELESS_MAC] == 25.0
    assert saved["aabbccddeeff9999"] == 42.0


async def test_options_flow_rejects_out_of_range(hass, setup_integration):
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    with pytest.raises(Exception):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {STATELESS_MAC: 0}
        )


async def test_options_flow_defaults_to_the_standard_travel_time(hass, setup_integration):
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if key.default
    }
    assert defaults[BLIND_MAC] == DEFAULT_TRAVEL_TIME


async def test_non_ascii_key_of_the_right_length_is_rejected(hass):
    """Sixteen characters can still be more than sixteen bytes for AES."""
    with patch(
        "custom_components.connector_bridge.config_flow.discover_gateways",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    with _patch_connect(error=ValueError("Incorrect AES key length (17 bytes)")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_KEY: "á" + "a" * 15}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_key"}
