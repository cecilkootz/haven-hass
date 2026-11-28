"""The Haven Lighting integration."""
from __future__ import annotations

from havenlighting import HavenClient
from havenlighting.devices.light import Light
from havenlighting.exceptions import ApiError, AuthenticationError

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, location_identifier
from .coordinator import HavenConfigEntry, HavenCoordinator

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SCENE]


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
    _async_migrate_zone_unique_ids(hass, coordinator.data)

    try:
        coordinator.scenes = await hass.async_add_executor_job(location.get_scenes)
    except (ApiError, AuthenticationError) as err:
        raise ConfigEntryNotReady(f"Error fetching Haven Lighting scenes: {err}") from err

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_remove_stale_devices(hass, entry)
    return True


def _current_identifiers(entry: HavenConfigEntry) -> set[tuple[str, str]]:
    """Device identifiers in use: the one Home device holds every entity."""
    return {location_identifier(entry.runtime_data.location.id)}


@callback
def _async_migrate_zone_unique_ids(
    hass: HomeAssistant, lights: dict[str, Light]
) -> None:
    """Move zone entities from "haven_light_<id>" to "haven_zone_<id>".

    Before 0.7.0 zones shared the light unique-id scheme, which collided
    with any light of the same number. Renaming the unique id in place
    keeps the zone's entity_id (and so the user's automations). Skipped
    when a light with that number exists: the old id was ambiguous and
    the light keeps it.
    """
    entity_registry = er.async_get(hass)
    for key, light in lights.items():
        if not light.is_zone or f"light_{light.id}" in lights:
            continue
        old_unique_id = f"haven_light_{light.id}"
        new_unique_id = f"haven_{key}"
        entity_id = entity_registry.async_get_entity_id("light", DOMAIN, old_unique_id)
        if entity_id is None or entity_registry.async_get_entity_id(
            "light", DOMAIN, new_unique_id
        ):
            continue
        entity_registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


@callback
def _async_remove_stale_devices(hass: HomeAssistant, entry: HavenConfigEntry) -> None:
    """Remove this entry's devices that are empty and no longer reported.

    Covers the per-scene devices from 0.6.x, the bare-number devices from
    before 0.7.0 and the per-light/per-zone devices from 0.7.x: by now their
    entities have moved to the Home device, so they are left empty. Devices
    that still hold an entity are kept; the user can delete those from the
    UI.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    current = _current_identifiers(entry)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.identifiers & current:
            continue
        if er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        ):
            continue
        device_registry.async_remove_device(device.id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HavenConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting any device but the Home device from the UI."""
    if entry.state is not ConfigEntryState.LOADED:
        return True
    return not device_entry.identifiers & _current_identifiers(entry)

async def async_unload_entry(hass: HomeAssistant, entry: HavenConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
