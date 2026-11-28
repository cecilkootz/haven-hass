"""Platform for Haven light integration."""
from __future__ import annotations

from collections.abc import Callable
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
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import color_hs_to_RGB

from .brightness import ha_to_haven_brightness, haven_to_ha_brightness
from .const import DOMAIN
from .coordinator import HavenConfigEntry, HavenCoordinator, async_get_location_device


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HavenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Haven Light from a config entry."""
    coordinator = config_entry.runtime_data
    location_device = async_get_location_device(hass, config_entry)

    async_add_entities(
        HavenLight(coordinator, light_id, location_device.id)
        for light_id in coordinator.data
    )

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
    """

    _attr_has_entity_name = True
    # HS implies brightness. Home Assistant rejects BRIGHTNESS combined with
    # any other mode, so it must not be listed alongside HS.
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = sorted(entry.name for entry in PATTERNS.values())
    # None, not light.name: this entity is the device's only feature, so
    # has_entity_name + a device name would otherwise show "Kitchen Kitchen".
    _attr_name = None

    def __init__(
        self, coordinator: HavenCoordinator, light_id: int, location_device_id: str
    ) -> None:
        """Initialize a Haven Light."""
        super().__init__(coordinator, context=light_id)
        self._light_id = light_id
        light = coordinator.data[light_id]
        self._attr_unique_id = f"haven_light_{light_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(light_id))},
            name=light.name,
            manufacturer="Haven",
            model="Haven Zone" if light.is_zone else "Haven Light",
            via_device_id=location_device_id,
        )

    @property
    def _light(self) -> Light:
        return self.coordinator.data[self._light_id]

    @property
    def available(self) -> bool:
        """Return True if the light is still reported by the coordinator."""
        return super().available and self._light_id in self.coordinator.data

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
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_command(self._light.turn_off)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

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
        # The device name is only applied when the entity is added; update it
        # in place so the new name shows without a reload.
        if self.device_entry is not None:
            dr.async_get(self.hass).async_update_device(
                self.device_entry.id, name=name
            )
        await self.coordinator.async_request_refresh()

    async def async_set_pattern_speed(self, speed: int) -> None:
        """Set pattern speed 1-63 (haven_lighting.set_pattern_speed service)."""
        await self._async_command(self._light.set_pattern_speed, speed)
        self.async_write_ha_state()
