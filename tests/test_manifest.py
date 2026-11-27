"""Structural tests for the custom component's metadata.

These run without Home Assistant installed; hassfest and the HACS
validation workflow cover the deeper integration checks.
"""

import json
from pathlib import Path

import pytest

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "haven_lighting"


def _load(name):
    return json.loads((COMPONENT_DIR / name).read_text())


def test_manifest_required_keys():
    manifest = _load("manifest.json")
    for key in (
        "domain",
        "name",
        "codeowners",
        "config_flow",
        "documentation",
        "iot_class",
        "issue_tracker",
        "requirements",
        "version",
    ):
        assert manifest.get(key), f"manifest.json is missing {key}"


def test_manifest_domain_matches_directory():
    manifest = _load("manifest.json")
    assert manifest["domain"] == "haven_lighting"
    assert COMPONENT_DIR.name == "haven_lighting"


def test_manifest_version_is_semver():
    version = _load("manifest.json")["version"]
    parts = version.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"version {version!r} is not MAJOR.MINOR.PATCH"
    )


def test_manifest_requirement_pins_the_client_fork():
    requirements = _load("manifest.json")["requirements"]
    assert any("haven-python" in req for req in requirements), (
        "the havenlighting requirement should point at the fixed client fork"
    )


def test_strings_json_is_valid_with_config_steps():
    strings = _load("strings.json")
    steps = strings["config"]["step"]
    assert "user" in steps
    assert "reauth_confirm" in steps
    assert "email" in steps["user"]["data"]
    assert "password" in steps["user"]["data"]


def test_const_domain_matches_manifest():
    const_text = (COMPONENT_DIR / "const.py").read_text()
    manifest_domain = _load("manifest.json")["domain"]
    assert f'DOMAIN = "{manifest_domain}"' in const_text


def test_all_component_files_compile():
    import py_compile

    for path in COMPONENT_DIR.glob("*.py"):
        py_compile.compile(str(path), doraise=True)


@pytest.mark.parametrize(
    "filename",
    ["hacs.json", "custom_components/haven_lighting/manifest.json",
     "custom_components/haven_lighting/strings.json"],
)
def test_repo_json_files_parse(filename):
    root = COMPONENT_DIR.parent.parent
    json.loads((root / filename).read_text())
