"""End-to-end tests of the integration running inside a real Home Assistant."""
from __future__ import annotations

from datetime import timedelta

import pytest

from havenlighting.exceptions import ApiError

from homeassistant.components.light import ColorMode
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from .conftest import DOMAIN, EMAIL, LOCATION_ID, PASSWORD, FakeHavenCloud


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance past one coordinator update interval and let it run."""
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    # scheduled coordinator refreshes run as background tasks
    await hass.async_block_till_done(wait_background_tasks=True)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


async def test_setup_creates_lights_zones_and_scenes(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every light, zone and scene becomes an entity without errors."""
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert "ERROR" not in [r.levelname for r in caplog.records]
    assert len(hass.states.async_all("light")) == 3
    assert len(hass.states.async_all("scene")) == 1

    palm = hass.states.get(_entity_id(hass, "light", "haven_light_1"))
    assert palm.state == STATE_ON
    assert palm.attributes["brightness"] == 128  # Haven 5/10
    assert palm.attributes["color_mode"] == ColorMode.HS
    assert palm.attributes["hs_color"] == (0.0, 100.0)  # RED
    assert palm.attributes["supported_color_modes"] == [ColorMode.HS]
    assert "Sparkle" in palm.attributes["effect_list"]

    path = hass.states.get(_entity_id(hass, "light", "haven_light_2"))
    assert path.state == STATE_OFF

    zone_device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "7"), config_entry.entry_id
    )
    assert zone_device is not None and zone_device.model == "Haven Zone"


async def test_scene_lives_on_location_device(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """Scenes attach to the location device and don't double their name."""
    await _setup(hass, config_entry)

    entity_id = _entity_id(hass, "scene", "haven_scene_31")
    entry = er.async_get(hass).async_get(entity_id)
    location = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, str(LOCATION_ID)), config_entry.entry_id
    )
    assert entry.device_id == location.id
    assert hass.states.get(entity_id).attributes["friendly_name"] == (
        "Pat Doe Movie Night"
    )

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert cloud.scene_runs == [31]


async def test_legacy_scene_devices_are_removed(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """0.6.x's per-scene devices are cleaned up, keeping the scene entity."""
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    old = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "31")},
        name="Movie Night",
        model="Haven Scene",
    )
    old_entity = er.async_get(hass).async_get_or_create(
        "scene", DOMAIN, "haven_scene_31", config_entry=config_entry,
        device_id=old.id, suggested_object_id="movie_night_movie_night",
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert device_registry.async_get(old.id) is None
    # the entity survives with its id, so automations keep working
    assert hass.states.get(old_entity.entity_id) is not None


async def test_turn_on_brightness_color_and_effect(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """Brightness, color and effect reach the Haven command payload."""
    await _setup(hass, config_entry)
    entity_id = _entity_id(hass, "light", "haven_light_2")

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "brightness": 128},
        blocking=True,
    )
    assert cloud.commands[-1]["lightBrightnessId"] == 5
    assert cloud.commands[-1]["lightingStatusId"] == 2

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "hs_color": [0, 100]},
        blocking=True,
    )
    assert cloud.commands[-1]["lightColorId"] == 11  # RED

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "effect": "Sparkle"},
        blocking=True,
    )
    assert cloud.commands[-1]["lightColorId"] == 80

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert cloud.commands[-1]["lightingStatusId"] == 1


async def test_effect_state_reports_brightness_mode(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """A running pattern shows as the effect with brightness color mode."""
    cloud.lights[1]["lightingStatusId"] = 2  # Back Path on, running "1 Wave"
    await _setup(hass, config_entry)
    state = hass.states.get(_entity_id(hass, "light", "haven_light_2"))
    assert state.attributes["effect"] == "1 Wave"
    assert state.attributes["color_mode"] == ColorMode.BRIGHTNESS
    assert state.attributes["hs_color"] is None


async def test_zone_command_targets_zone_ids(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """Zones are commanded through selectedZoneIds."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "light", "turn_off",
        {"entity_id": _entity_id(hass, "light", "haven_light_7")}, blocking=True,
    )
    assert cloud.commands[-1]["selectedZoneIds"] == [7]
    assert cloud.commands[-1]["selectedLightIds"] == []


async def test_command_failure_raises_home_assistant_error(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """A failed cloud command surfaces as a HomeAssistantError."""
    await _setup(hass, config_entry)
    cloud.fail_commands = ApiError("command rejected")
    with pytest.raises(HomeAssistantError, match="command rejected"):
        await hass.services.async_call(
            "light", "turn_on",
            {"entity_id": _entity_id(hass, "light", "haven_light_1")},
            blocking=True,
        )


async def test_services(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """rename updates Haven and the device; zones are refused."""
    await _setup(hass, config_entry)
    light_id = _entity_id(hass, "light", "haven_light_1")

    await hass.services.async_call(
        DOMAIN, "rename", {"entity_id": light_id, "name": "Big Palm"}, blocking=True
    )
    assert cloud.renames == [{"lightId": 1, "name": "Big Palm", "pinName": None}]
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "1"), config_entry.entry_id
    )
    assert device.name == "Big Palm"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "rename",
            {"entity_id": _entity_id(hass, "light", "haven_light_7"), "name": "x"},
            blocking=True,
        )

    await hass.services.async_call(
        DOMAIN, "set_pattern_speed", {"entity_id": light_id, "speed": 20},
        blocking=True,
    )
    assert cloud.commands[-1]["patternSpeedId"] == 20


async def test_update_failure_marks_unavailable_then_recovers(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed poll makes lights unavailable until the next good poll."""
    await _setup(hass, config_entry)
    cloud.fail_lights = ApiError("boom")
    await _poll(hass, freezer)
    assert {s.state for s in hass.states.async_all("light")} == {STATE_UNAVAILABLE}

    cloud.fail_lights = None
    await _poll(hass, freezer)
    assert STATE_UNAVAILABLE not in {s.state for s in hass.states.async_all("light")}


async def test_expired_session_logs_in_again(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A rejected token refresh is recovered with a fresh login, no reauth."""
    await _setup(hass, config_entry)
    cloud.token_valid = False
    await _poll(hass, freezer)
    assert not hass.config_entries.flow.async_progress()
    assert STATE_UNAVAILABLE not in {s.state for s in hass.states.async_all("light")}


async def test_changed_password_starts_reauth(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """When the stored password stops working, Home Assistant asks for it."""
    await _setup(hass, config_entry)
    cloud.token_valid = False
    cloud.password = "changed"
    await _poll(hass, freezer)
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]

    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {"password": "changed"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {"email": EMAIL, "password": "changed"}


async def test_bad_password_at_startup_starts_reauth(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """Setup with rejected credentials goes to reauth."""
    cloud.password = "changed"
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == [
        SOURCE_REAUTH
    ]


async def test_unload(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """The entry unloads cleanly."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_user_flow(hass: HomeAssistant, cloud: FakeHavenCloud) -> None:
    """The user step rejects a bad password, then creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"email": EMAIL, "password": PASSWORD}


async def test_user_flow_already_configured(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """The same account can't be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER},
        data={"email": EMAIL, "password": PASSWORD},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_translations_ship_for_custom_integration(hass: HomeAssistant) -> None:
    """Custom integrations only load translations/en.json, never strings.json."""
    from homeassistant.helpers.translation import async_get_translations

    await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    strings = await async_get_translations(hass, "en", "config", [DOMAIN])
    prefix = f"component.{DOMAIN}.config"
    assert strings[f"{prefix}.step.user.data.email"] == "Email"
    assert strings[f"{prefix}.error.invalid_auth"] == "Invalid authentication"
