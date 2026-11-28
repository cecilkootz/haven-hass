"""Platform for Haven scene integration."""
from __future__ import annotations

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HavenConfigEntry, HavenCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HavenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Haven scenes from a config entry."""
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

    scenes = await hass.async_add_executor_job(location.get_scenes)

    async_add_entities(
        HavenScene(coordinator, scene, location_device.id) for scene in scenes.values()
    )


class HavenScene(Scene):
    """A saved Haven scene, activated through Commands/Scene/Execute."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HavenCoordinator,
        scene,
        location_device_id: str,
    ) -> None:
        """Initialize a Haven scene."""
        self.coordinator = coordinator
        self.scene = scene
        self._attr_unique_id = f"haven_scene_{scene.id}"
        self._attr_name = scene.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(scene.id))},
            name=scene.name,
            manufacturer="Haven",
            model="Haven Playground Scene" if scene.is_playground else "Haven Scene",
            via_device_id=location_device_id,
        )

    async def async_activate(self, **kwargs) -> None:
        """Apply the scene to the location's lights and zones."""
        await self.hass.async_add_executor_job(self.scene.activate)
        # scenes change every light in the location; refresh promptly
        await self.coordinator.async_request_refresh()
