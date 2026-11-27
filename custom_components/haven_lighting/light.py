"""Platform for Haven light integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HavenConfigEntry, HavenCoordinator

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

class HavenLight(CoordinatorEntity[HavenCoordinator], LightEntity):
    """Representation of a Haven Light."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.ONOFF, ColorMode.BRIGHTNESS}

    # Haven reports brightness on a 0-10 scale (lightBrightnessId); Home
    # Assistant uses 0-255. Convert both ways, clamping to the 1-10 range the
    # API accepts for a set command.
    HAVEN_BRIGHTNESS_MAX = 10

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
            model="Haven Light",
            via_device_id=location_device_id,
        )

    @property
    def available(self) -> bool:
        """Return True if the light is still reported by the coordinator."""
        return super().available and self._light_id in self.coordinator.data

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self.coordinator.data[self._light_id].is_on

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        haven_brightness = self.coordinator.data[self._light_id].brightness
        return round(haven_brightness * 255 / self.HAVEN_BRIGHTNESS_MAX)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally setting brightness."""
        light = self.coordinator.data[self._light_id]
        haven_brightness = None
        if (ha_brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            haven_brightness = max(
                1, round(ha_brightness * self.HAVEN_BRIGHTNESS_MAX / 255)
            )
        await self.hass.async_add_executor_job(
            lambda: light.turn_on(brightness=haven_brightness)
        )
        self.async_write_ha_state()
        self.hass.async_create_task(
            self.coordinator.async_request_refresh(), eager_start=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        light = self.coordinator.data[self._light_id]
        await self.hass.async_add_executor_job(light.turn_off)
        self.async_write_ha_state()
        self.hass.async_create_task(
            self.coordinator.async_request_refresh(), eager_start=True
        )
