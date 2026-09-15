"""Structural checks on the manifest, services and translation files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import voluptuous as vol
import yaml

COMPONENT = Path(__file__).parent.parent / "custom_components" / "connector_bridge"
TRANSLATIONS = COMPONENT / "translations"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(data, prefix="") -> set[str]:
    """Every key path in a nested dict, so two files can be compared."""
    found = set()
    for key, value in data.items():
        found.add(prefix + key)
        if isinstance(value, dict):
            found |= _keys(value, f"{prefix}{key}.")
    return found


def _translation_files() -> list[Path]:
    return sorted(TRANSLATIONS.glob("*.json"))


def test_english_source_and_translation_match():
    """strings.json is the English source; translations/en.json mirrors it."""
    assert _load(COMPONENT / "strings.json") == _load(TRANSLATIONS / "en.json")


def test_strings_json_is_english():
    """Home Assistant reads strings.json as English, so it must not be localised."""
    strings = _load(COMPONENT / "strings.json")
    assert strings["config"]["step"]["user"]["title"] == "Set up Connector Bridge"


@pytest.mark.parametrize("path", _translation_files(), ids=lambda p: p.stem)
def test_translations_have_the_same_keys(path: Path):
    expected = _keys(_load(COMPONENT / "strings.json"))
    assert _keys(_load(path)) == expected


def test_a_hungarian_translation_is_shipped():
    """The Hungarian text only reaches users from translations/hu.json."""
    hu = _load(TRANSLATIONS / "hu.json")
    assert hu["config"]["step"]["user"]["title"] == "Connector Bridge beállítása"


def test_all_flow_errors_are_translated():
    """Every error key the config flow can return needs a string."""
    from custom_components.connector_bridge import config_flow

    source = Path(config_flow.__file__).read_text()
    used = {"cannot_connect", "invalid_key", "unknown"}
    for key in used:
        assert f'"{key}"' in source, f"{key} is no longer returned by the flow"

    for path in [COMPONENT / "strings.json", *_translation_files()]:
        assert used <= set(_load(path)["config"]["error"]), path.name


def test_all_abort_reasons_are_translated():
    from custom_components.connector_bridge import config_flow

    source = Path(config_flow.__file__).read_text()
    reasons = {"already_configured", "not_connector_bridge"}
    for reason in reasons:
        assert f'reason="{reason}"' in source
    for path in [COMPONENT / "strings.json", *_translation_files()]:
        assert reasons <= set(_load(path)["config"]["abort"]), path.name


def test_repair_issues_are_translated():
    from custom_components.connector_bridge.const import (
        ISSUE_IP_CHANGED,
        ISSUE_MULTICAST_UNAVAILABLE,
    )

    for path in [COMPONENT / "strings.json", *_translation_files()]:
        issues = _load(path)["issues"]
        assert ISSUE_IP_CHANGED in issues
        assert ISSUE_MULTICAST_UNAVAILABLE in issues


def test_service_fields_match_the_registered_schema():
    """services.yaml must describe exactly the fields the platform accepts."""
    from custom_components.connector_bridge.const import SERVICE_MOVE_FOR_DURATION

    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    assert set(services) == {SERVICE_MOVE_FOR_DURATION}
    assert set(services[SERVICE_MOVE_FOR_DURATION]["fields"]) == {"direction", "duration"}

    for path in [COMPONENT / "strings.json", *_translation_files()]:
        described = _load(path)["services"][SERVICE_MOVE_FOR_DURATION]["fields"]
        assert set(described) == {"direction", "duration"}, path.name


def test_service_selector_matches_the_validation_range():
    """A value the selector offers must not be rejected by the schema."""
    from custom_components.connector_bridge.const import SERVICE_MOVE_FOR_DURATION

    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    duration = services[SERVICE_MOVE_FOR_DURATION]["fields"]["duration"]["selector"]
    schema = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=300))
    schema(duration["number"]["min"])
    schema(duration["number"]["max"])

    directions = services[SERVICE_MOVE_FOR_DURATION]["fields"]["direction"]["selector"]
    offered = {opt["value"] for opt in directions["select"]["options"]}
    assert offered == {"open", "close"}


def test_manifest_is_consistent():
    manifest = _load(COMPONENT / "manifest.json")
    assert manifest["domain"] == "connector_bridge"
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == ["pycryptodomex==3.21.0"]
    assert COMPONENT.name == manifest["domain"]
