"""DataUpdateCoordinator for the Haven Lighting integration."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from havenlighting import HavenClient
from havenlighting.devices.light import Light
from havenlighting.devices.location import Location
from havenlighting.devices.scene import Scene
from havenlighting.exceptions import ApiError, AuthenticationError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import location_identifier

_LOGGER = logging.getLogger(__name__)

# Conservative on purpose: other users of Haven's community integrations have
# had their accounts rate-limited/flagged for polling too aggressively (see
# haven-python's project history). Poll gently rather than aggressively.
UPDATE_INTERVAL = timedelta(seconds=60)

# Haven's cloud takes a few seconds to report the state a command set, so an
# immediate re-read after a command returns the old state. Re-read after this.
COMMAND_SETTLE_DELAY = timedelta(seconds=10)

type HavenConfigEntry = ConfigEntry["HavenCoordinator"]


class HavenCoordinator(DataUpdateCoordinator[dict[str, Light]]):
    """Fetch all lights and zones for one Haven location on a shared schedule.

    Data is keyed by ``Light.key`` ("light_<id>" / "zone_<id>"): lights and
    zones are numbered independently, so the raw id is not unique.
    """

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
        self._unsub_settle_refresh: CALLBACK_TYPE | None = None

    @callback
    def async_refresh_after_command(self) -> None:
        """Re-read the cloud once a command has had time to take effect.

        Several commands in quick succession share one re-read.
        """
        if self._unsub_settle_refresh is not None:
            self._unsub_settle_refresh()
        self._unsub_settle_refresh = async_call_later(
            self.hass, COMMAND_SETTLE_DELAY, self._async_settle_refresh
        )

    async def _async_settle_refresh(self, _now: datetime) -> None:
        self._unsub_settle_refresh = None
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Cancel a pending post-command re-read on unload."""
        if self._unsub_settle_refresh is not None:
            self._unsub_settle_refresh()
            self._unsub_settle_refresh = None
        await super().async_shutdown()

    def _fetch_lights(self) -> dict[str, Light]:
        """Fetch lights, logging in again once if the session has expired.

        The client already retries a 401 after a token refresh; when the
        refresh itself is rejected (expired refresh token), a fresh login
        with the stored credentials recovers without bothering the user.
        """
        try:
            lights = self.location.get_lights()
        except AuthenticationError:
            _LOGGER.debug("Haven session expired; logging in again")
            if not self.client.authenticate(
                self.config_entry.data[CONF_EMAIL],
                self.config_entry.data[CONF_PASSWORD],
            ):
                raise
            lights = self.location.get_lights()
        if _LOGGER.isEnabledFor(logging.DEBUG):
            for key, light in lights.items():
                _LOGGER.debug(
                    "Haven reports %s (%s): on=%s status=%s brightness=%s color=%s",
                    key,
                    light.name,
                    light.is_on,
                    light._data.status,  # noqa: SLF001
                    light.brightness,
                    light._data.color,  # noqa: SLF001
                )
        return lights

    async def _async_update_data(self) -> dict[str, Light]:
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
        identifiers={location_identifier(location.id)},
        manufacturer="Haven",
        # The account owner's name is all Haven exposes for the location.
        name="Home",
        model="Haven Location",
    )
