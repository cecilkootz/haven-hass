"""Platform for Haven light integration."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from havenlighting.colors import COLORS, PATTERNS, color_id_for_name, nearest_color_id
from havenlighting.devices.light import Light
from havenlighting.exceptions import AuthenticationError, HavenException

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util.color import color_hs_to_RGB

from .brightness import ha_to_haven_brightness, haven_to_ha_brightness
from .const import location_identifier
from .coordinator import HavenConfigEntry, HavenCoordinator, async_get_location_device

# Haven's cloud reports a light's new state only some seconds after a command
# is accepted. For this long after a command, a poll that still shows the old
# state is treated as stale and the commanded state is kept; after it, the
# cloud's answer wins.
OPTIMISTIC_WINDOW = timedelta(seconds=45)

# The zone Haven creates to address every light at once. It is presented as
# the Home device's own light ("Home") rather than as a separate zone.
_ALL_ZONES_NAMES = frozenset({"all zones"})


def is_all_zones(light: Light) -> bool:
    """Return whether this is Haven's ALL ZONES zone."""
    return light.is_zone and light.name.strip().casefold() in _ALL_ZONES_NAMES


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HavenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Haven Light from a config entry."""
    coordinator = config_entry.runtime_data
    # Every light and zone is an entity of the one Home device.
    async_get_location_device(hass, config_entry)

    async_add_entities(HavenLight(coordinator, key) for key in coordinator.data)

    # Entity services (documented in services.yaml)
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        "rename",
        {vol.Required("name"): vol.All(str, vol.Length(min=1))},
        "async_rename",
    )
    platform.async_register_entity_service(
        "set_pattern_speed",
        {vol.Required("speed"): vol.All(vol.Coerce(int), vol.Range(min=1, max=63))},
        "async_set_pattern_speed",
    )


class HavenLight(CoordinatorEntity[HavenCoordinator], LightEntity):
    """Representation of a Haven light or zone.

    Haven reports brightness on a 0-10 scale (lightBrightnessId); Home
    Assistant uses 0-255. Conversion lives in brightness.py so it can be
    unit-tested without Home Assistant installed.

    Colors and animated patterns ride the same lightColorId command field
    (Haven's static catalog, havenlighting.colors): solid colors and white
    temperatures surface as hs_color, animated patterns as effects.

    All lights and zones belong to the location's Home device. ALL ZONES is
    the device's main light (shown as just "Home"); every other light or
    zone is named after itself ("Home Drive Way").
    """

    _attr_has_entity_name = True
    # HS implies brightness. Home Assistant rejects BRIGHTNESS combined with
    # any other mode, so it must not be listed alongside HS.
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = sorted(entry.name for entry in PATTERNS.values())

    def __init__(self, coordinator: HavenCoordinator, key: str) -> None:
        """Initialize a Haven light or zone from its ``Light.key``."""
        super().__init__(coordinator, context=key)
        self._key = key
        # "haven_light_<id>" / "haven_zone_<id>": lights and zones are
        # numbered independently, so the kind must be part of the id.
        self._attr_unique_id = f"haven_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={location_identifier(coordinator.location.id)},
        )
        # The state a command asked for, kept until the cloud reports it
        # (see OPTIMISTIC_WINDOW): the commanded Light object, the values
        # that must match, and when to stop waiting.
        self._optimistic: Light | None = None
        self._expected: dict[str, Any] = {}
        self._optimistic_until: datetime | None = None
        self._last_name: str | None = None

    @property
    def name(self) -> str | None:
        """Name the entity after its light; ALL ZONES is the device itself."""
        if (light := self.coordinator.data.get(self._key)) is not None:
            self._last_name = None if is_all_zones(light) else light.name
        return self._last_name

    @property
    def _light(self) -> Light:
        """The state to show: the commanded one while it settles, else polled."""
        if self._optimistic is not None:
            return self._optimistic
        return self.coordinator.data[self._key]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Drop the commanded state once the cloud agrees or the wait is over."""
        if self._optimistic is not None:
            fresh = self.coordinator.data.get(self._key)
            if (
                fresh is None
                or self._matches_expected(fresh)
                or self._optimistic_until is None
                or dt_util.utcnow() >= self._optimistic_until
            ):
                self._clear_optimistic()
        super()._handle_coordinator_update()

    def _matches_expected(self, light: Light) -> bool:
        return all(
            value is None or getattr(light, attr) == value
            for attr, value in (
                ("is_on", self._expected.get("is_on")),
                ("brightness", self._expected.get("brightness")),
            )
        ) and (
            self._expected.get("color") is None
            or light._data.color == self._expected["color"]  # noqa: SLF001
        )

    def _clear_optimistic(self) -> None:
        self._optimistic = None
        self._expected = {}
        self._optimistic_until = None

    @callback
    def _async_hold_commanded_state(self, light: Light, **expected: Any) -> None:
        """Show the commanded state now and re-read the cloud once it settles."""
        self._optimistic = light
        self._expected = expected
        self._optimistic_until = dt_util.utcnow() + OPTIMISTIC_WINDOW
        self.async_write_ha_state()
        self.coordinator.async_refresh_after_command()

    @property
    def available(self) -> bool:
        """Return True if the light is still reported by the coordinator."""
        return super().available and self._key in self.coordinator.data

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._light.is_on

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        if (value := self._light.brightness) is None:
            return None
        return haven_to_ha_brightness(value)

    @property
    def _color_entry(self):
        return COLORS.get(self._light._data.color)  # noqa: SLF001

    @property
    def color_mode(self) -> ColorMode:
        """Return the active color mode.

        While an animated pattern runs there is no single color to report,
        so the light reports brightness-only, which Home Assistant allows
        while an effect is active.
        """
        entry = self._color_entry
        if entry is not None and entry.is_pattern:
            return ColorMode.BRIGHTNESS
        return ColorMode.HS

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return hue/saturation of the current color when it is solid."""
        entry = self._color_entry
        if entry is None or entry.is_pattern or entry.kind == "off":
            return None
        return entry.hs

    @property
    def effect(self) -> str | None:
        """Return the current pattern name when a pattern is active."""
        entry = self._color_entry
        if entry is None or not entry.is_pattern:
            return None
        return entry.name

    async def _async_command(self, func: Callable[..., Any], *args: Any) -> None:
        """Run a blocking client command, mapping client errors for HA."""
        try:
            await self.hass.async_add_executor_job(func, *args)
        except AuthenticationError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                f"Haven Lighting authentication failed: {err}"
            ) from err
        except HavenException as err:
            raise HomeAssistantError(
                f"Haven Lighting command for {self.entity_id} failed: {err}"
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally setting brightness/color/effect."""
        light = self._light
        haven_brightness = None
        color_id = None

        if (ha_brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            haven_brightness = ha_to_haven_brightness(ha_brightness)
        if (hs := kwargs.get(ATTR_HS_COLOR)) is not None:
            rgb = color_hs_to_RGB(hs[0], hs[1])
            color_id = nearest_color_id(rgb)
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            color_id = color_id_for_name(effect)
            if color_id is None:
                raise ServiceValidationError(
                    f"Unknown Haven effect {effect!r}; expected one of "
                    f"{', '.join(self.effect_list or [])}"
                )

        await self._async_command(
            lambda: light.turn_on(brightness=haven_brightness, color_id=color_id)
        )
        self._async_hold_commanded_state(
            light, is_on=True, brightness=haven_brightness, color=color_id
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        light = self._light
        await self._async_command(light.turn_off)
        self._async_hold_commanded_state(light, is_on=False)

    async def async_rename(self, name: str) -> None:
        """Rename the light server-side (haven_lighting.rename service).

        Only individual lights: zones use a different Haven endpoint the
        app manages separately, and their ids must not be sent as lightId.
        """
        light = self._light
        if light.is_zone:
            raise ServiceValidationError(
                "Zones cannot be renamed through this service; Haven renames "
                "zones through a separate endpoint not yet supported"
            )
        await self._async_command(light.rename, name)
        # The entity name follows the light's name from the next poll.
        await self.coordinator.async_request_refresh()

    async def async_set_pattern_speed(self, speed: int) -> None:
        """Set pattern speed 1-63 (haven_lighting.set_pattern_speed service)."""
        await self._async_command(self._light.set_pattern_speed, speed)
        self.async_write_ha_state()
