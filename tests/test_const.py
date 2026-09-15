"""Tests for shared constants and helpers."""

from __future__ import annotations

import pytest

from custom_components.connector_bridge.const import (
    DEVICE_TYPE_BLIND,
    DEVICE_TYPE_NAMES,
    DEVICE_TYPES_GATEWAY,
    OPERATION_CLOSE,
    OPERATION_OPEN,
    OPERATION_STOP,
    model_name,
)


def test_every_gateway_type_has_a_model_name():
    for device_type in DEVICE_TYPES_GATEWAY:
        assert device_type in DEVICE_TYPE_NAMES


def test_model_name_known():
    assert model_name(DEVICE_TYPE_BLIND) == "Roller Blind"


def test_model_name_unknown_keeps_the_code():
    assert model_name("deadbeef") == "Unknown device (deadbeef)"


@pytest.mark.parametrize("empty", [None, ""])
def test_model_name_missing(empty):
    assert model_name(empty) == "Unknown device"


def test_operation_codes_are_distinct():
    assert len({OPERATION_CLOSE, OPERATION_OPEN, OPERATION_STOP}) == 3
