"""The Haven Lighting integration."""
from __future__ import annotations

from havenlighting import HavenClient
from havenlighting.exceptions import ApiError, AuthenticationError

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .coordinator import HavenConfigEntry, HavenCoordinator

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SCENE]

# Models of the per-scene devices created by 0.6.x, before scenes moved onto
# the location device.
_LEGACY_SCENE_DEVICE_MODELS = {"Haven Scene", "Haven Playground Scene"}


async def async_setup_entry(hass: HomeAssistant, entry: HavenConfigEntry) -> bool:
    """Set up Haven Lighting from a config entry."""
    client = HavenClient()

    try:
        authenticated = await hass.async_add_executor_job(
            client.authenticate,
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
        )
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed("Invalid Haven Lighting credentials") from err
    except ApiError as err:
        raise ConfigEntryNotReady(f"Error connecting to Haven Lighting: {err}") from err
    if not authenticated:
        raise ConfigEntryAuthFailed("Invalid Haven Lighting credentials")

    try:
        locations = await hass.async_add_executor_job(client.discover_locations)
    except (ApiError, AuthenticationError) as err:
        raise ConfigEntryNotReady(f"Error connecting to Haven Lighting: {err}") from err

    if not locations:
        raise ConfigEntryNotReady("No Haven Lighting location found for this account")

    location = next(iter(locations.values()))

    coordinator = HavenCoordinator(hass, entry, client, location)
    await coordinator.async_config_entry_first_refresh()

    try:
        coordinator.scenes = await hass.async_add_executor_job(location.get_scenes)
    except (ApiError, AuthenticationError) as err:
        raise ConfigEntryNotReady(f"Error fetching Haven Lighting scenes: {err}") from err

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_remove_legacy_scene_devices(hass, entry)
    return True


@callback
def _async_remove_legacy_scene_devices(
    hass: HomeAssistant, entry: HavenConfigEntry
) -> None:
    """Drop the empty per-scene devices left behind by 0.6.x.

    By now the scene entities have been re-linked to the location device,
    so only devices with no entities left are removed.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.model not in _LEGACY_SCENE_DEVICE_MODELS:
            continue
        if er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        ):
            continue
        device_registry.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: HavenConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
