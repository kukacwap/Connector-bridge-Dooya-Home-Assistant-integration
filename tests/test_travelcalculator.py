"""Tests for the time-based position estimator."""

from __future__ import annotations

import pytest

from custom_components.connector_bridge import travelcalculator as tc_module
from custom_components.connector_bridge.travelcalculator import TravelCalculator


@pytest.fixture
def clock(monkeypatch):
    """Drive TravelCalculator from a controllable monotonic clock."""

    class Clock:
        """Stands in for the ``time`` module inside travelcalculator only."""

        def __init__(self):
            self.now = 1000.0

        def monotonic(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    c = Clock()
    monkeypatch.setattr(tc_module, "time", c)
    return c


def test_initial_position_is_closed():
    assert TravelCalculator(10).current_position() == 0.0


@pytest.mark.parametrize(
    ("given", "expected"),
    [(-50, 0.0), (0, 0.0), (42, 42.0), (100, 100.0), (150, 100.0)],
)
def test_set_position_clamps(given, expected):
    calc = TravelCalculator(10)
    calc.set_position(given)
    assert calc.current_position() == expected


def test_set_position_cancels_travel(clock):
    calc = TravelCalculator(10)
    calc.start_travel(100)
    clock.advance(2)
    calc.set_position(75)
    assert calc.is_traveling() is False
    clock.advance(10)
    assert calc.current_position() == 75.0


def test_opening_progresses_linearly(clock):
    calc = TravelCalculator(10)
    calc.start_travel(100)
    clock.advance(2.5)
    assert calc.current_position() == pytest.approx(25.0)
    clock.advance(2.5)
    assert calc.current_position() == pytest.approx(50.0)


def test_opening_never_overshoots(clock):
    calc = TravelCalculator(10)
    calc.start_travel(100)
    clock.advance(30)
    assert calc.current_position() == 100.0
    assert calc.is_traveling() is False


def test_closing_progresses_and_stops_at_target(clock):
    calc = TravelCalculator(10)
    calc.set_position(100)
    calc.start_travel(0)
    clock.advance(4)
    assert calc.current_position() == pytest.approx(60.0)
    clock.advance(30)
    assert calc.current_position() == 0.0


def test_partial_travel_stops_at_intermediate_target(clock):
    calc = TravelCalculator(20)
    calc.start_travel(50)
    clock.advance(10)
    assert calc.current_position() == pytest.approx(50.0)
    clock.advance(10)
    assert calc.current_position() == pytest.approx(50.0)


def test_stop_freezes_at_estimate(clock):
    calc = TravelCalculator(10)
    calc.start_travel(100)
    clock.advance(3)
    calc.stop()
    assert calc.current_position() == pytest.approx(30.0)
    clock.advance(100)
    assert calc.current_position() == pytest.approx(30.0)
    assert calc.is_traveling() is False


def test_travel_direction(clock):
    calc = TravelCalculator(10)
    assert calc.travel_direction() is None

    calc.start_travel(100)
    assert calc.travel_direction() == 1

    calc.set_position(100)
    calc.start_travel(0)
    assert calc.travel_direction() == -1

    clock.advance(30)
    assert calc.travel_direction() is None


def test_redirect_mid_travel_uses_current_estimate(clock):
    calc = TravelCalculator(10)
    calc.start_travel(100)
    clock.advance(4)  # at 40
    calc.start_travel(0)
    assert calc.current_position() == pytest.approx(40.0)
    clock.advance(2)  # 20 more, downwards
    assert calc.current_position() == pytest.approx(20.0)


def test_finalize_if_arrived(clock):
    calc = TravelCalculator(10)
    calc.start_travel(100)
    clock.advance(5)
    assert calc.finalize_if_arrived() is False
    clock.advance(5)
    assert calc.finalize_if_arrived() is True
    assert calc.current_position() == 100.0
    # Nothing left to finalize once travel has been consumed.
    assert calc.finalize_if_arrived() is False


def test_travel_to_current_position_is_not_travelling(clock):
    calc = TravelCalculator(10)
    calc.set_position(50)
    calc.start_travel(50)
    assert calc.is_traveling() is False
    assert calc.travel_direction() is None


@pytest.mark.parametrize("bad_time", [0, -5, 0.0])
def test_non_positive_travel_time_does_not_divide_by_zero(bad_time, clock):
    calc = TravelCalculator(bad_time)
    calc.start_travel(100)
    clock.advance(1)
    assert calc.current_position() == 100.0
