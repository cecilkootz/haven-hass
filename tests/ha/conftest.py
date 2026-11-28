"""Fixtures for the Home Assistant integration tests.

The Haven cloud is faked at the havenlighting ``Credentials`` HTTP boundary
(``_make_request_internal``), so the real ``HavenClient``/``Location``/``Light``
/``Scene`` objects run unmodified and only the network is simulated.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from havenlighting.credentials import Credentials
from havenlighting.exceptions import ApiError, AuthenticationError

from pytest_homeassistant.common import MockConfigEntry

DOMAIN = "haven_lighting"
EMAIL = "user@example.com"
PASSWORD = "hunter2"
LOCATION_ID = 4242


class FakeHavenCloud:
    """In-memory stand-in for Haven's two API hosts."""

    def __init__(self) -> None:
        self.password = PASSWORD
        self.lights: list[dict[str, Any]] = [
            {"lightId": 1, "name": "Front Palm", "lightingStatusId": 2,
             "lightBrightnessId": 5, "colorId": 11},
            {"lightId": 2, "name": "Back Path", "lightingStatusId": 1,
             "lightBrightnessId": 10, "colorId": 81},
            {"id": 7, "name": "Pool Zone", "isZone": True,
             "lightingStatusId": 2, "lightBrightnessId": 10, "colorId": 1},
        ]
        self.scenes: list[dict[str, Any]] = [
            {"id": 31, "name": "Movie Night", "isPlayGroundScene": False},
        ]
        self.commands: list[dict[str, Any]] = []
        self.scene_runs: list[int] = []
        self.fail_lights: Exception | None = None
        self.fail_commands: Exception | None = None
        self.fail_auth: Exception | None = None
        self.renames: list[dict[str, Any]] = []
        self.token_valid = True

    def request(self, method: str, path: str, auth_required: bool = True,
                base_url: str = "", **kwargs: Any) -> Any:
        body = kwargs.get("json")
        if path == "/Auth/authenticate":
            if self.fail_auth:
                raise self.fail_auth
            if body["password"] != self.password:
                raise ApiError("Invalid credentials", code=400)
            self.token_valid = True
            return {"token": "t", "refreshToken": "r", "id": 99}
        if path == "/Auth/refresh":
            raise ApiError("refresh rejected", code=400)
        if not self.token_valid:
            raise AuthenticationError("Received 401 Unauthorized response")
        if path == "/user/GetUserInfo/":
            return {"defaultLocationId": LOCATION_ID, "firstName": "Pat",
                    "lastName": "Doe"}
        if path.startswith("/LightAndZones/OrderedList/"):
            if self.fail_lights:
                raise self.fail_lights
            return [dict(item) for item in self.lights]
        if path.startswith("/v2/Light/CommandV1"):
            if self.fail_commands:
                raise self.fail_commands
            self.commands.append(body)
            return {"success": True}
        if path.startswith("/Scene/UserScenesByLocation/"):
            return list(self.scenes)
        if path.startswith("/Commands/Scene/Execute/"):
            self.scene_runs.append(int(path.split("=")[-1]))
            return {"success": True}
        if path == "/Light/UpdateName":
            self.renames.append(body)
            for item in self.lights:
                if item.get("lightId") == body["lightId"]:
                    item["name"] = body["name"]
            return {}
        raise AssertionError(f"unexpected request {method} {path}")


@pytest.fixture
def cloud() -> Generator[FakeHavenCloud]:
    """Patch every havenlighting HTTP call onto a fake cloud."""
    fake = FakeHavenCloud()

    def _internal(self, method, path, auth_required=True, timeout=30,
                  base_url="", **kwargs):
        if auth_required and not self.is_authenticated:
            raise AuthenticationError("Authentication required")
        return fake.request(method, path, auth_required, base_url, **kwargs)

    with patch.object(Credentials, "_make_request_internal", _internal):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured Haven Lighting entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={"email": EMAIL, "password": PASSWORD},
    )
