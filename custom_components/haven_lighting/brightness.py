"""Brightness scale conversion between Haven and Home Assistant.

Kept free of homeassistant imports so the unit tests can run in plain CI
without installing Home Assistant itself.

Haven's API uses a 0-10 scale (lightBrightnessId); Home Assistant uses
0-255. A set-command must land in the 1-10 range the API accepts.
"""

from typing import Final

HAVEN_BRIGHTNESS_MAX: Final[int] = 10
HA_BRIGHTNESS_MAX: Final[int] = 255


def haven_to_ha_brightness(value: int) -> int:
    """Convert Haven's 0-10 brightness to Home Assistant's 0-255."""
    return round(value * HA_BRIGHTNESS_MAX / HAVEN_BRIGHTNESS_MAX)


def ha_to_haven_brightness(value: int) -> int:
    """Convert Home Assistant's 0-255 brightness to Haven's 1-10.

    A set-command must stay in the 1-10 range the API accepts, so a
    nonzero HA value never maps below 1.
    """
    return max(1, round(value * HAVEN_BRIGHTNESS_MAX / HA_BRIGHTNESS_MAX))
