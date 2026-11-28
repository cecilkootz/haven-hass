"""Platform for Haven light integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import voluptuous as vol

from .brightness import ha_to_haven_brightness, haven_to_ha_brightness
from .const import DOMAIN
from .coordinator import HavenConfigEntry, HavenCoordinator

from havenlighting.colors import (
    COLORS,
    PATTERNS,
    color_id_for_name,
    nearest_color_id,
)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HavenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Haven Light from a config entry."""
    coordinator = config_entry.runtime_data
    location = coordinator.location

    device_registry = dr.async_get(hass)
    location_device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, str(location.id))},
        manufacturer="Haven",
        name=location.name,
        model="Haven Location",
    )

    async_add_entities(
        HavenLight(coordinator, light_id, location_device.id)
        for light_id in coordinator.data
    )

    # Entity services (documented in services.yaml)
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        "rename",
        {vol.Required("name"): str},
        "async_rename",
    )
    platform.async_register_entity_service(
        "set_pattern_speed",
        {vol.Required("speed"): vol.All(int, vol.Range(min=1, max=63))},
        "async_set_pattern_speed",
    )

class HavenLight(CoordinatorEntity[HavenCoordinator], LightEntity):
    """Representation of a Haven light or zone."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.HS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS, ColorMode.HS}

    # Haven reports brightness on a 0-10 scale (lightBrightnessId); Home
    # Assistant uses 0-255. Conversion lives in brightness.py so it can be
    # unit-tested without Home Assistant installed.
    #
    # Colors and animated patterns ride the same lightColorId command field
    # (Haven's static catalog, havenlighting.colors): solid colors and white
    # temperatures surface as hs_color, animated patterns as effects.

    def __init__(
        self, coordinator: HavenCoordinator, light_id: int, location_device_id: str
    ) -> None:
        """Initialize a Haven Light."""
        super().__init__(coordinator, context=light_id)
        self._light_id = light_id
        light = coordinator.data[light_id]
        self._attr_unique_id = f"haven_light_{light_id}"
        # None, not light.name: this entity is the device's only feature, so
        # has_entity_name + a device name would otherwise show "Kitchen Kitchen".
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(light_id))},
            name=light.name,
            manufacturer="Haven",
            model="Haven Light" if not light.is_zone else "Haven Zone",
            via_device_id=location_device_id,
        )
        self._attr_effect_list = sorted(e.name for e in PATTERNS.values())

    @property
    def _light(self):
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
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        return haven_to_ha_brightness(self._light.brightness)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return hue/saturation of the current color when it is solid."""
        entry = COLORS.get(self._light._data.color)
        if entry is None or entry.is_pattern:
            return None
        return entry.hs

    @property
    def effect(self) -> str | None:
        """Return the current pattern name when a pattern is active."""
        entry = COLORS.get(self._light._data.color)
        if entry is None or not entry.is_pattern:
            return None
        return entry.name

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally setting brightness/color/effect."""
        light = self._light
        haven_brightness = None
        color_id = None

        if (ha_brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            haven_brightness = ha_to_haven_brightness(ha_brightness)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        hs = kwargs.get(ATTR_HS_COLOR)
        if rgb is not None or hs is not None:
            from homeassistant.util.color import color_hsv_to_RGB

            rgb_tuple = (
                tuple(int(c) for c in rgb)
                if rgb is not None
                else tuple(int(c) for c in color_hsv_to_RGB(hs[0], hs[1], 100))
            )
            color_id = nearest_color_id(rgb_tuple)
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            color_id = color_id_for_name(effect)

        await self.hass.async_add_executor_job(
            lambda: light.turn_on(brightness=haven_brightness, color_id=color_id)
        )
        self.async_write_ha_state()
        self.hass.async_create_task(
            self.coordinator.async_request_refresh(), eager_start=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        light = self._light
        await self.hass.async_add_executor_job(light.turn_off)
        self.async_write_ha_state()
        self.hass.async_create_task(
            self.coordinator.async_request_refresh(), eager_start=True
        )

    async def async_rename(self, name: str) -> None:
        """Rename the light server-side (haven_lighting.rename service).

        Only individual lights: zones use a different Haven endpoint the
        app manages separately, and their ids must not be sent as lightId.
        """
        light = self._light
        if light.is_zone:
            raise HomeAssistantError(
                "Zones cannot be renamed through this service; Haven renames "
                "zones through a separate endpoint not yet supported"
            )
        await self.hass.async_add_executor_job(light.rename, name)
        await self.coordinator.async_request_refresh()

    async def async_set_pattern_speed(self, speed: int) -> None:
        """Set pattern speed 1-63 (haven_lighting.set_pattern_speed service)."""
        light = self._light
        await self.hass.async_add_executor_job(light.set_pattern_speed, speed)
        self.async_write_ha_state()
