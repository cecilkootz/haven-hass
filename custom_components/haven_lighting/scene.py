"""Platform for Haven scene integration."""
from __future__ import annotations

from typing import Any

from havenlighting.devices.scene import Scene as HavenSceneData
from havenlighting.exceptions import AuthenticationError, HavenException

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import location_identifier
from .coordinator import HavenConfigEntry, HavenCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: HavenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Haven scenes from a config entry."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        HavenScene(coordinator, scene) for scene in coordinator.scenes.values()
    )


class HavenScene(Scene):
    """A saved Haven scene, activated through Commands/Scene/Execute.

    Scenes are attached to the location device rather than getting a device
    each: a scene is not a piece of hardware, and a per-scene device named
    after the scene doubled the friendly name ("Movie Night Movie Night").
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: HavenCoordinator, scene: HavenSceneData) -> None:
        """Initialize a Haven scene."""
        self.coordinator = coordinator
        self.scene = scene
        self._attr_unique_id = f"haven_scene_{scene.id}"
        self._attr_name = scene.name
        self._attr_device_info = DeviceInfo(
            identifiers={location_identifier(coordinator.location.id)},
        )
        self._attr_extra_state_attributes = {"playground": scene.is_playground}

    async def async_activate(self, **kwargs: Any) -> None:
        """Apply the scene to the location's lights and zones."""
        try:
            await self.hass.async_add_executor_job(self.scene.activate)
        except AuthenticationError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                f"Haven Lighting authentication failed: {err}"
            ) from err
        except HavenException as err:
            raise HomeAssistantError(
                f"Activating Haven scene {self.scene.name} failed: {err}"
            ) from err
        # A scene changes every light; re-read once the cloud has caught up.
        self.coordinator.async_refresh_after_command()
