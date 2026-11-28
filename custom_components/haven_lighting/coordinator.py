"""DataUpdateCoordinator for the Haven Lighting integration."""
from __future__ import annotations

from datetime import timedelta
import logging

from havenlighting import HavenClient
from havenlighting.devices.light import Light
from havenlighting.devices.location import Location
from havenlighting.devices.scene import Scene
from havenlighting.exceptions import ApiError, AuthenticationError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Conservative on purpose: other users of Haven's community integrations have
# had their accounts rate-limited/flagged for polling too aggressively (see
# haven-python's project history). Poll gently rather than aggressively.
UPDATE_INTERVAL = timedelta(seconds=60)

type HavenConfigEntry = ConfigEntry["HavenCoordinator"]


class HavenCoordinator(DataUpdateCoordinator[dict[int, Light]]):
    """Fetch all lights for one Haven location on a shared schedule."""

    config_entry: HavenConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HavenConfigEntry,
        client: HavenClient,
        location: Location,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Haven Lighting",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.location = location
        self.scenes: dict[int, Scene] = {}

    def _fetch_lights(self) -> dict[int, Light]:
        """Fetch lights, logging in again once if the session has expired.

        The client already retries a 401 after a token refresh; when the
        refresh itself is rejected (expired refresh token), a fresh login
        with the stored credentials recovers without bothering the user.
        """
        try:
            return self.location.get_lights()
        except AuthenticationError:
            _LOGGER.debug("Haven session expired; logging in again")
            if not self.client.authenticate(
                self.config_entry.data[CONF_EMAIL],
                self.config_entry.data[CONF_PASSWORD],
            ):
                raise
            return self.location.get_lights()

    async def _async_update_data(self) -> dict[int, Light]:
        """Fetch the latest light states for this location."""
        try:
            return await self.hass.async_add_executor_job(self._fetch_lights)
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Haven Lighting authentication expired"
            ) from err
        except ApiError as err:
            raise UpdateFailed(f"Error communicating with Haven Lighting: {err}") from err


@callback
def async_get_location_device(
    hass: HomeAssistant, config_entry: HavenConfigEntry
) -> dr.DeviceEntry:
    """Return (creating if needed) the device for the entry's Haven location."""
    location = config_entry.runtime_data.location
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, str(location.id))},
        manufacturer="Haven",
        name=location.name or "Haven Lighting",
        model="Haven Location",
    )
