"""Tests for the cover platform."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    DOMAIN as COVER_DOMAIN,
    CoverDeviceClass,
    CoverState,
)
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
    STATE_UNAVAILABLE,
)
from homeassistant.core import State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import frame
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.connector_bridge import travelcalculator as tc_module
from custom_components.connector_bridge.const import (
    DEVICE_TYPE_SUNBLIND,
    DEVICE_TYPE_WIFI_CURTAIN,
    DOMAIN,
    EVENT_BLIND_MOVED,
    SERVICE_MOVE_FOR_DURATION,
)

from .conftest import BLIND_MAC, GATEWAY_MAC, STATELESS_MAC, TRAVEL_TIME
from .helpers import FakeGateway

POSITIONAL = "cover.blind_0001"
STATELESS = "cover.blind_0002"


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock for the travel estimator."""

    class Clock:
        def __init__(self):
            self.now = 1000.0

        def monotonic(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    c = Clock()
    monkeypatch.setattr(tc_module, "time", c)
    return c


async def _advance(hass, clock, seconds):
    """Move both the travel clock and Home Assistant's timers forward."""
    clock.advance(seconds)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds + 0.5))
    await hass.async_block_till_done()


def _blind(gateway, mac):
    return gateway.device_list[mac]


def _moves(blind):
    """Commands sent to a blind, minus the status polls HA interleaves."""
    return [c for c in blind.commands if c[0] != "update"]


# ----------------------------------------------------------------------
# Entity registration and naming
# ----------------------------------------------------------------------


async def test_entities_created(hass, setup_integration):
    assert hass.states.get(POSITIONAL) is not None
    assert hass.states.get(STATELESS) is not None


async def test_unique_ids(hass, setup_integration):
    registry = er.async_get(hass)
    entry = registry.async_get(POSITIONAL)
    assert entry.unique_id == f"{DOMAIN}_{BLIND_MAC}"


async def test_friendly_name_is_not_doubled(hass, setup_integration):
    """The cover is its device's primary entity, so it takes the device name."""
    state = hass.states.get(POSITIONAL)
    assert state.attributes[ATTR_FRIENDLY_NAME] == "Blind 0001"


async def test_blind_device_is_linked_to_the_bridge(
    hass, mock_config_entry, patch_gateway, caplog
):
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    bridge = device_registry.async_get_device_by_identifier(
        (DOMAIN, GATEWAY_MAC), mock_config_entry.entry_id
    )
    blind = device_registry.async_get_device_by_identifier(
        (DOMAIN, BLIND_MAC), mock_config_entry.entry_id
    )
    assert blind.via_device_id == bridge.id
    assert "deprecated `via_device`" not in caplog.text


async def test_blinds_load_when_deprecated_calls_raise(
    hass, mock_config_entry, patch_gateway, monkeypatch
):
    """Reproduce how Home Assistant 2026.9 added the blinds in production.

    When Home Assistant cannot tell which integration made a deprecated call
    it raises instead of warning. That is what happened to the deprecated
    ``via_device`` device info key, and no blind could be added.
    """

    def _unattributable(*args, **kwargs):
        raise frame.MissingIntegrationFrame

    monkeypatch.setattr(frame, "get_integration_frame", _unattributable)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(POSITIONAL).state != STATE_UNAVAILABLE
    assert hass.states.get(STATELESS).state != STATE_UNAVAILABLE


async def test_blind_without_a_bridge_device_is_not_linked(hass, mock_config_entry):
    """A gateway that never reported its MAC has no device to link to."""
    gateway = FakeGateway("192.168.1.50", GATEWAY_MAC, {BLIND_MAC: 30})
    gateway.mac = None
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.connector_bridge.ConnectorGateway", return_value=gateway
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    blind = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, BLIND_MAC), mock_config_entry.entry_id
    )
    assert blind.via_device_id is None
    assert hass.states.get(POSITIONAL) is not None


# ----------------------------------------------------------------------
# Position reporting motors
# ----------------------------------------------------------------------


async def test_position_is_inverted_from_bridge_convention(hass, setup_integration):
    """Bridge reports 30 (0=open), Home Assistant shows 70 (0=closed)."""
    state = hass.states.get(POSITIONAL)
    assert state.attributes[ATTR_CURRENT_POSITION] == 70
    assert ATTR_ASSUMED_STATE not in state.attributes


async def test_open_close_stop_commands(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)
    for service in (SERVICE_OPEN_COVER, SERVICE_CLOSE_COVER, SERVICE_STOP_COVER):
        await hass.services.async_call(
            COVER_DOMAIN, service, {ATTR_ENTITY_ID: POSITIONAL}, blocking=True
        )
    assert _moves(blind) == [("open",), ("close",), ("stop",)]


async def test_set_position_inverts_for_the_bridge(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: POSITIONAL, ATTR_POSITION: 25},
        blocking=True,
    )
    assert _moves(blind) == [("set_position", 75)]


async def test_closed_state(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)
    blind._position = 100  # fully closed in bridge terms
    blind.operation = 2
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL).state == CoverState.CLOSED


async def test_opening_and_closing_states(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)

    blind.operation = 1
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL).state == CoverState.OPENING

    blind.operation = 0
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL).state == CoverState.CLOSING

    blind.operation = 2
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL).state == CoverState.OPEN


async def test_unavailable_blind(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)
    blind.available = False
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL).state == STATE_UNAVAILABLE


# ----------------------------------------------------------------------
# Device class
# ----------------------------------------------------------------------


async def test_device_class_from_reported_blind_type(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)
    blind.blind_type = 8
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL).attributes[ATTR_DEVICE_CLASS] == (
        CoverDeviceClass.AWNING
    )


async def test_device_class_falls_back_to_device_type(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, STATELESS_MAC)
    blind.device_type = DEVICE_TYPE_WIFI_CURTAIN
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(STATELESS).attributes[ATTR_DEVICE_CLASS] == (
        CoverDeviceClass.CURTAIN
    )


async def test_device_class_default(hass, setup_integration):
    assert hass.states.get(POSITIONAL).attributes[ATTR_DEVICE_CLASS] == (
        CoverDeviceClass.BLIND
    )


# ----------------------------------------------------------------------
# Stateless (time estimated) motors
# ----------------------------------------------------------------------


async def test_stateless_blind_is_assumed_state(hass, setup_integration):
    state = hass.states.get(STATELESS)
    assert state.attributes[ATTR_ASSUMED_STATE] is True
    assert state.attributes[ATTR_CURRENT_POSITION] == 0


async def test_stateless_open_animates_position(hass, setup_integration, fake_gateway, clock):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    assert _moves(blind) == [("open",)]
    assert hass.states.get(STATELESS).state == CoverState.OPENING

    await _advance(hass, clock, TRAVEL_TIME / 2)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == pytest.approx(
        50, abs=4
    )

    await _advance(hass, clock, TRAVEL_TIME)
    state = hass.states.get(STATELESS)
    assert state.attributes[ATTR_CURRENT_POSITION] == 100
    assert state.state == CoverState.OPEN


async def test_stateless_close_animates_position(hass, setup_integration, fake_gateway, clock):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    await _advance(hass, clock, TRAVEL_TIME * 1.5)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == 100

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    assert hass.states.get(STATELESS).state == CoverState.CLOSING
    await _advance(hass, clock, TRAVEL_TIME * 1.5)
    state = hass.states.get(STATELESS)
    assert state.attributes[ATTR_CURRENT_POSITION] == 0
    assert state.state == CoverState.CLOSED
    assert _moves(blind) == [("open",), ("close",)]


async def test_stateless_stop_freezes_estimate(hass, setup_integration, fake_gateway, clock):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    await _advance(hass, clock, TRAVEL_TIME / 4)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_STOP_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    frozen = hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION]
    assert frozen == pytest.approx(25, abs=6)

    await _advance(hass, clock, TRAVEL_TIME)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == frozen
    assert _moves(blind) == [("open",), ("stop",)]


async def test_stateless_partial_position_runs_then_stops(
    hass, setup_integration, fake_gateway, clock
):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 50},
        blocking=True,
    )
    assert _moves(blind) == [("open",)]

    # Half of a 20 s travel = 10 s, then an automatic stop.
    await _advance(hass, clock, TRAVEL_TIME / 2 + 1)
    assert _moves(blind) == [("open",), ("stop",)]
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == pytest.approx(
        50, abs=6
    )


async def test_stateless_position_to_zero_uses_close(hass, setup_integration, fake_gateway, clock):
    blind = _blind(fake_gateway, STATELESS_MAC)
    blind._position = None
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 100},
        blocking=True,
    )
    assert _moves(blind) == [("open",)]
    await _advance(hass, clock, TRAVEL_TIME * 1.5)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 0},
        blocking=True,
    )
    assert _moves(blind)[-1] == ("close",)


async def test_stateless_partial_position_already_reached_sends_nothing(
    hass, setup_integration, fake_gateway, clock
):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 50},
        blocking=True,
    )
    await _advance(hass, clock, TRAVEL_TIME / 2 + 1)
    assert _moves(blind) == [("open",), ("stop",)]

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 50},
        blocking=True,
    )
    assert _moves(blind) == [("open",), ("stop",)]


async def test_stateless_end_positions_are_always_sent(
    hass, setup_integration, fake_gateway, clock
):
    """Closing a blind the estimate already calls closed must still close it.

    HomeKit's open and close arrive here as positions 100 and 0, and the
    estimate is wrong whenever the blind moved without us (a wall remote), so
    skipping them left blinds that HomeKit could not close or open at all.
    """
    blind = _blind(fake_gateway, STATELESS_MAC)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == 0

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 0},
        blocking=True,
    )
    assert _moves(blind) == [("close",)]
    assert hass.states.get(STATELESS).state == CoverState.CLOSED

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    await _advance(hass, clock, TRAVEL_TIME * 1.5)
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 100},
        blocking=True,
    )
    assert _moves(blind) == [("close",), ("open",), ("open",)]
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == 100


async def test_new_command_cancels_the_pending_stop(
    hass, setup_integration, fake_gateway, clock
):
    """A second command must not be followed by the first move's timed stop."""
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 80},
        blocking=True,
    )
    await _advance(hass, clock, 2)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_STOP_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    assert _moves(blind) == [("open",), ("stop",)]

    await _advance(hass, clock, TRAVEL_TIME * 2)
    assert _moves(blind) == [("open",), ("stop",)]


# ----------------------------------------------------------------------
# move_for_duration service
# ----------------------------------------------------------------------


async def test_move_for_duration(hass, setup_integration, fake_gateway, clock):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_MOVE_FOR_DURATION,
        {ATTR_ENTITY_ID: STATELESS, "direction": "open", "duration": 5},
        blocking=True,
    )
    assert _moves(blind) == [("open",)]

    await _advance(hass, clock, 6)
    assert _moves(blind) == [("open",), ("stop",)]
    # 5 s of a 20 s full travel is a quarter of the way open.
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == pytest.approx(
        25, abs=6
    )


async def test_move_for_duration_close(hass, setup_integration, fake_gateway, clock):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    await _advance(hass, clock, TRAVEL_TIME * 1.5)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_MOVE_FOR_DURATION,
        {ATTR_ENTITY_ID: STATELESS, "direction": "close", "duration": 5},
        blocking=True,
    )
    await _advance(hass, clock, 6)
    assert _moves(blind)[-1] == ("stop",)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == pytest.approx(
        75, abs=6
    )


async def test_move_for_duration_rejects_bad_direction(hass, setup_integration):
    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MOVE_FOR_DURATION,
            {ATTR_ENTITY_ID: STATELESS, "direction": "sideways", "duration": 5},
            blocking=True,
        )


# ----------------------------------------------------------------------
# Push updates and events
# ----------------------------------------------------------------------


async def test_push_update_refreshes_position(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)
    blind._position = 10
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL).attributes[ATTR_CURRENT_POSITION] == 90


async def test_external_push_drives_stateless_estimate(
    hass, setup_integration, fake_gateway, clock
):
    """A move started from a wall remote must still update the estimate."""
    blind = _blind(fake_gateway, STATELESS_MAC)
    blind.operation = 1  # opening
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(STATELESS).state == CoverState.OPENING

    await _advance(hass, clock, TRAVEL_TIME / 2)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == pytest.approx(
        50, abs=6
    )

    blind.operation = 2  # stopped
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(STATELESS).state != CoverState.OPENING


async def test_blind_moved_event_is_fired(hass, setup_integration, fake_gateway):
    events = []
    hass.bus.async_listen(EVENT_BLIND_MOVED, events.append)

    _blind(fake_gateway, BLIND_MAC).fire_event(
        {"source": "external", "operation": 1, "position": 20, "ha_position": 80}
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["entity_id"] == POSITIONAL
    assert events[0].data["mac"] == BLIND_MAC
    assert events[0].data["source"] == "external"


async def test_callbacks_removed_on_unload(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)
    assert blind._callbacks
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert blind._callbacks == {}
    assert blind._event_callbacks == {}


# ----------------------------------------------------------------------
# Polling
# ----------------------------------------------------------------------


async def test_poll_failure_does_not_break_the_entity(hass, setup_integration, fake_gateway):
    blind = _blind(fake_gateway, BLIND_MAC)

    def boom():
        raise TimeoutError("bridge silent")

    blind.update = boom
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=180))
    await hass.async_block_till_done()
    assert hass.states.get(POSITIONAL) is not None


# ----------------------------------------------------------------------
# Regression guards
# ----------------------------------------------------------------------


async def test_zero_travel_time_does_not_crash(
    hass, mock_config_entry, patch_gateway, clock
):
    """A stored travel time of zero must not divide by zero."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"travel_times": {STATELESS_MAC: 0}}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_MOVE_FOR_DURATION,
        {ATTR_ENTITY_ID: STATELESS, "direction": "open", "duration": 1},
        blocking=True,
    )
    assert _moves(_blind(patch_gateway, STATELESS_MAC)) == [("open",)]


async def test_position_restored_after_restart(hass, mock_config_entry, patch_gateway):
    """A stateless blind cannot be read back, so its estimate is restored."""
    from homeassistant.const import STATE_OPEN
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(
        hass,
        (State(STATELESS, STATE_OPEN, {ATTR_CURRENT_POSITION: 65}),),
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == 65


@pytest.mark.parametrize(
    ("operation", "expected"), [(1, 100), (0, 0), (2, 0), (None, 0)]
)
async def test_first_estimate_follows_the_last_bridge_command(
    hass, mock_config_entry, patch_gateway, operation, expected
):
    """With nothing to restore, the bridge's last open/close is the best guess."""
    patch_gateway.device_list[STATELESS_MAC].operation = operation
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == expected


async def test_restored_position_beats_the_last_bridge_command(
    hass, mock_config_entry, patch_gateway
):
    """A partial position from before the restart is more precise."""
    from homeassistant.const import STATE_OPEN
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(
        hass,
        (State(STATELESS, STATE_OPEN, {ATTR_CURRENT_POSITION: 40}),),
    )
    patch_gateway.device_list[STATELESS_MAC].operation = 1
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == 40


async def test_corrupt_restored_position_is_ignored(hass, mock_config_entry, patch_gateway):
    from homeassistant.const import STATE_OPEN
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(
        hass,
        (State(STATELESS, STATE_OPEN, {ATTR_CURRENT_POSITION: "not a number"}),),
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == 0


async def test_external_close_push_drives_the_estimate(
    hass, setup_integration, fake_gateway, clock
):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    await _advance(hass, clock, TRAVEL_TIME * 1.5)

    blind.operation = 0  # closing, started elsewhere
    blind.fire()
    await hass.async_block_till_done()
    assert hass.states.get(STATELESS).state == CoverState.CLOSING

    await _advance(hass, clock, TRAVEL_TIME / 2)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == pytest.approx(
        50, abs=6
    )


async def test_stateless_partial_close_runs_downwards(
    hass, setup_integration, fake_gateway, clock
):
    blind = _blind(fake_gateway, STATELESS_MAC)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: STATELESS}, blocking=True
    )
    await _advance(hass, clock, TRAVEL_TIME * 1.5)

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 40},
        blocking=True,
    )
    assert _moves(blind)[-1] == ("close",)

    await _advance(hass, clock, TRAVEL_TIME * 0.6 + 1)
    assert _moves(blind)[-1] == ("stop",)
    assert hass.states.get(STATELESS).attributes[ATTR_CURRENT_POSITION] == pytest.approx(
        40, abs=6
    )


async def test_failed_timed_stop_is_logged_not_raised(
    hass, setup_integration, fake_gateway, clock, caplog
):
    """If the bridge misses the stop, the entity must stay usable."""
    blind = _blind(fake_gateway, STATELESS_MAC)

    def boom():
        raise TimeoutError("bridge silent")

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: STATELESS, ATTR_POSITION: 50},
        blocking=True,
    )
    blind.stop = boom
    await _advance(hass, clock, TRAVEL_TIME / 2 + 1)

    assert "Failed to stop blind" in caplog.text
    assert hass.states.get(STATELESS) is not None
