"""Tests for the Haven <-> Home Assistant brightness conversion.

Loads brightness.py directly from its path so the test does not import the
component package (whose __init__ requires Home Assistant installed).
"""

import importlib.util
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).parent.parent
    / "custom_components" / "haven_lighting" / "brightness.py"
)


def _load_brightness_module():
    spec = importlib.util.spec_from_file_location("haven_brightness", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_b = _load_brightness_module()
ha_to_haven_brightness = _b.ha_to_haven_brightness
haven_to_ha_brightness = _b.haven_to_ha_brightness


def test_haven_to_ha_full_scale():
    assert haven_to_ha_brightness(10) == 255
    assert haven_to_ha_brightness(0) == 0


def test_haven_to_ha_rounding():
    # 5 -> 127.5 rounds to 128 (banker's rounding in Python gives 128 here
    # since 127.5 rounds to the even neighbor 128)
    assert haven_to_ha_brightness(5) == 128
    assert haven_to_ha_brightness(1) == 26  # 25.5 -> 26


def test_ha_to_haven_full_scale():
    assert ha_to_haven_brightness(255) == 10
    assert ha_to_haven_brightness(0) == 1  # set-commands must stay in 1-10


def test_ha_to_haven_rounding():
    assert ha_to_haven_brightness(128) == 5  # 5.02 -> 5
    assert ha_to_haven_brightness(26) == 1  # 1.02 -> 1
    assert ha_to_haven_brightness(25) == 1  # 0.98 clamps up to 1


def test_round_trip_stays_within_haven_range():
    for ha_value in range(0, 256):
        haven = ha_to_haven_brightness(ha_value)
        assert 1 <= haven <= 10


def test_round_trip_preserves_values_at_scale_points():
    # 0 is excluded: a set-command never maps below 1 (turn-off is a
    # separate command), so 0 -> 0 -> 1 by design.
    for haven_value in range(1, 11):
        assert ha_to_haven_brightness(haven_to_ha_brightness(haven_value)) == haven_value
