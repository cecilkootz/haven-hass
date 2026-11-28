"""End-to-end tests of the integration running inside a real Home Assistant."""
from __future__ import annotations

from datetime import timedelta
import logging

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
    """Every light, zone and scene becomes an entity of one Home device."""
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert "ERROR" not in [r.levelname for r in caplog.records]
    assert len(hass.states.async_all("light")) == 4
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

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )
    assert [(d.name, d.model) for d in devices] == [("Home", "Haven Location")]
    entity_registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        assert entry.device_id == devices[0].id

    def friendly(unique_id: str) -> str:
        state = hass.states.get(_entity_id(hass, "light", unique_id))
        return state.attributes["friendly_name"]

    # ALL ZONES is the Home device's own light; the rest are named after
    # themselves
    assert friendly("haven_zone_9") == "Home"
    assert friendly("haven_zone_7") == "Home Pool Zone"
    assert friendly("haven_light_1") == "Home Front Palm"


async def test_scene_lives_on_location_device(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """Scenes attach to the location device and don't double their name."""
    await _setup(hass, config_entry)

    entity_id = _entity_id(hass, "scene", "haven_scene_31")
    entry = er.async_get(hass).async_get(entity_id)
    location = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"location_{LOCATION_ID}"), config_entry.entry_id
    )
    assert entry.device_id == location.id
    assert hass.states.get(entity_id).attributes["friendly_name"] == (
        "Home Movie Night"
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
        {"entity_id": _entity_id(hass, "light", "haven_zone_7")}, blocking=True,
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
    """rename updates Haven and the entity name; zones are refused."""
    await _setup(hass, config_entry)
    light_id = _entity_id(hass, "light", "haven_light_1")

    await hass.services.async_call(
        DOMAIN, "rename", {"entity_id": light_id, "name": "Big Palm"}, blocking=True
    )
    await hass.async_block_till_done()
    assert cloud.renames == [{"lightId": 1, "name": "Big Palm", "pinName": None}]
    assert hass.states.get(light_id).attributes["friendly_name"] == "Home Big Palm"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "rename",
            {"entity_id": _entity_id(hass, "light", "haven_zone_7"), "name": "x"},
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


async def test_haven_unreachable_at_startup_retries(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """An outage during setup retries later instead of asking to reauth."""
    cloud.fail_auth = ApiError("Request failed: Could not resolve host")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, cloud: FakeHavenCloud
) -> None:
    """An outage while adding the account says so instead of "invalid auth"."""
    cloud.fail_auth = ApiError("Service Unavailable", code=503)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_zone_and_light_with_the_same_id_both_appear(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """Lights and zones are numbered independently; neither may be dropped."""
    cloud.lights.append(
        {"id": 1, "name": "Garden Zone", "isZone": True, "lightingStatusId": 1}
    )
    await _setup(hass, config_entry)

    assert len(hass.states.async_all("light")) == 5
    palm = _entity_id(hass, "light", "haven_light_1")
    garden = _entity_id(hass, "light", "haven_zone_1")
    assert hass.states.get(palm).attributes["friendly_name"] == "Home Front Palm"
    assert hass.states.get(garden).attributes["friendly_name"] == "Home Garden Zone"

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": garden}, blocking=True
    )
    assert cloud.commands[-1]["selectedZoneIds"] == [1]
    assert cloud.commands[-1]["selectedLightIds"] == []
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": palm}, blocking=True
    )
    assert cloud.commands[-1]["selectedLightIds"] == [1]
    assert cloud.commands[-1]["selectedZoneIds"] == []


def _old_device(hass, entry, identifier, **kwargs):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, identifier)}, **kwargs
    )


def _old_light_entity(hass, entry, unique_id, device, object_id):
    return er.async_get(hass).async_get_or_create(
        "light", DOMAIN, unique_id, config_entry=entry,
        device_id=device.id, suggested_object_id=object_id,
    ).entity_id


async def _assert_only_home_device(hass, entry, old_devices) -> None:
    device_registry = dr.async_get(hass)
    for device_id in old_devices:
        assert device_registry.async_get(device_id) is None
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert [d.name for d in devices] == ["Home"]


async def test_upgrade_from_0_6_keeps_entity_ids(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """<=0.6 entities keep their entity_id; the bare-number devices go away."""
    config_entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    old_location = _old_device(hass, config_entry, str(LOCATION_ID), name="Pat Doe")
    palm_device = _old_device(hass, config_entry, "1")
    zone_device = _old_device(hass, config_entry, "7")  # a zone, old scheme
    palm = _old_light_entity(hass, config_entry, "haven_light_1", palm_device, "palm")
    pool = _old_light_entity(hass, config_entry, "haven_light_7", zone_device, "pool")

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(palm).unique_id == "haven_light_1"
    assert entity_registry.async_get(pool).unique_id == "haven_zone_7"
    assert len(hass.states.async_all("light")) == 4  # no duplicates
    await _assert_only_home_device(
        hass, config_entry, {old_location.id, palm_device.id, zone_device.id}
    )


async def test_upgrade_from_0_7_folds_zone_devices_into_home(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """0.7.x's device per zone is replaced by entities on the Home device."""
    config_entry.add_to_hass(hass)
    location = _old_device(
        hass, config_entry, f"location_{LOCATION_ID}", name="Pat Doe"
    )
    zone_device = _old_device(
        hass, config_entry, "zone_7", name="Pool Zone", via_device_id=location.id
    )
    pool = _old_light_entity(hass, config_entry, "haven_zone_7", zone_device, "pool")

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    entry = er.async_get(hass).async_get(pool)
    assert entry.device_id == location.id
    assert dr.async_get(hass).async_get(location.id).name == "Home"
    await _assert_only_home_device(hass, config_entry, {zone_device.id})


async def test_only_the_home_device_is_protected_from_removal(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry
) -> None:
    """The user may delete any leftover device, but not the Home device."""
    await _setup(hass, config_entry)
    home = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"location_{LOCATION_ID}"), config_entry.entry_id
    )
    leftover = _old_device(hass, config_entry, "zone_99")

    from custom_components.haven_lighting import async_remove_config_entry_device

    assert not await async_remove_config_entry_device(hass, config_entry, home)
    assert await async_remove_config_entry_device(hass, config_entry, leftover)


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_commanded_state_survives_a_lagging_cloud(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The toggle must not flip back while Haven's cloud still reports the
    old state (the reported 'shows off, but on' bug)."""
    await _setup(hass, config_entry)
    all_zones = _entity_id(hass, "light", "haven_zone_9")
    cloud.lag = True
    reads_before = cloud.list_reads

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": all_zones}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(all_zones).state == STATE_ON
    assert cloud.list_reads == reads_before  # no immediate, stale re-read

    # the post-command re-read still sees "off": keep showing "on"
    await _advance(hass, freezer, 11)
    assert cloud.list_reads == reads_before + 1
    assert hass.states.get(all_zones).state == STATE_ON

    # the cloud catches up; later polls agree and the hold is released
    cloud.release_commands()
    await _advance(hass, freezer, 61)
    assert hass.states.get(all_zones).state == STATE_ON
    cloud.lights[3]["lightingStatusId"] = 1  # turned off in the Haven app
    await _advance(hass, freezer, 61)
    assert hass.states.get(all_zones).state == STATE_OFF


async def test_cloud_wins_once_the_hold_expires(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """If Haven never reports the commanded state, its answer wins."""
    await _setup(hass, config_entry)
    path = _entity_id(hass, "light", "haven_light_2")
    cloud.lag = True  # the command never takes effect

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": path}, blocking=True
    )
    assert hass.states.get(path).state == STATE_ON
    await _advance(hass, freezer, 11)
    assert hass.states.get(path).state == STATE_ON
    await _advance(hass, freezer, 61)
    assert hass.states.get(path).state == STATE_OFF


async def test_turn_off_holds_off(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Turning off is held the same way as turning on."""
    await _setup(hass, config_entry)
    palm = _entity_id(hass, "light", "haven_light_1")
    cloud.lag = True
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": palm}, blocking=True
    )
    await _advance(hass, freezer, 11)
    assert hass.states.get(palm).state == STATE_OFF
    cloud.release_commands()
    await _advance(hass, freezer, 61)
    assert hass.states.get(palm).state == STATE_OFF


async def test_debug_log_shows_reported_state(
    hass: HomeAssistant, cloud: FakeHavenCloud, config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Debug logging records what Haven reports, to diagnose state issues."""
    caplog.set_level(logging.DEBUG, logger="custom_components.haven_lighting")
    await _setup(hass, config_entry)
    assert "Haven reports zone_9 (ALL ZONES): on=False status=1" in caplog.text
