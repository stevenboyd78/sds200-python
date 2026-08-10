from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_lovelace import (
    HOME_ASSISTANT_LOVELACE_CARD_FILENAME,
    HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL,
    install_home_assistant_lovelace_card,
)

EXPECTED_ENTITY_FIELDS = {
    "scanner_connected",
    "system",
    "department",
    "channel",
    "signal",
    "rssi",
    "audio_running",
    "recording_active",
    "recording_status",
    "daemon_state",
}


def lovelace_target(tmp_path: Path) -> Path:
    return tmp_path / "homeassistant" / "www" / "sds200" / HOME_ASSISTANT_LOVELACE_CARD_FILENAME


def card_text() -> str:
    return (
        files("sds200.web_assets")
        .joinpath(HOME_ASSISTANT_LOVELACE_CARD_FILENAME)
        .read_text(encoding="utf-8")
    )


def test_lovelace_card_resource_url_uses_home_assistant_local_path() -> None:
    assert HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL == "/local/sds200/sds200-card.js"


def test_lovelace_card_packaged_asset_is_importable() -> None:
    text = card_text()

    assert 'const SDS200_CARD_TYPE = "sds200-card";' in text


def test_lovelace_card_covers_all_discovery_entity_fields() -> None:
    fields = set(
        re.findall(
            r'key: "([a-z_]+)"',
            card_text(),
        )
    )

    assert fields == EXPECTED_ENTITY_FIELDS


def test_lovelace_card_uses_builtin_graphical_configuration_form() -> None:
    text = card_text()

    assert "static getConfigForm()" in text
    assert 'type: "expandable"' in text
    assert 'name: "entities"' in text
    assert "selector: {" in text
    assert "entity: {" in text
    assert 'domain: "binary_sensor"' in text
    assert 'domain: "sensor"' in text
    assert "computeLabel:" in text
    assert "computeHelper:" in text
    assert "assertConfig:" in text


def test_lovelace_card_uses_supported_state_context_subscription() -> None:
    text = card_text()

    assert "new CustomEvent(" in text
    assert '"context-request"' in text
    assert 'event.context = "states";' in text
    assert "event.subscribe = true;" in text
    assert "event.callback = this._updateStates;" in text
    assert "disconnectedCallback()" in text
    assert "this._unsubscribe();" in text

    assert "set hass(value)" not in text
    assert "this._hass.states" not in text


def test_lovelace_card_has_sections_grid_sizing() -> None:
    text = card_text()

    assert "getGridOptions()" in text
    assert "columns: 6" in text
    assert "min_columns: 3" in text


def test_lovelace_card_remains_read_only_and_transport_free() -> None:
    text = card_text()

    for forbidden in (
        "innerHTML",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "callService",
        "callApi",
        "callWS",
    ):
        assert forbidden not in text


def test_lovelace_card_registers_with_card_picker() -> None:
    text = card_text()

    assert "window.customCards" in text
    assert "customElements.define" in text
    assert 'name: "SDS200 Scanner"' in text
    assert "documentationURL:" in text


def test_lovelace_card_install_copies_packaged_asset_atomically(
    tmp_path: Path,
) -> None:
    target = lovelace_target(tmp_path)

    installed = install_home_assistant_lovelace_card(target)

    assert installed == target
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0o644

    text = target.read_text(encoding="utf-8")
    assert text == card_text()


def test_lovelace_card_install_is_idempotent(
    tmp_path: Path,
) -> None:
    target = lovelace_target(tmp_path)

    first = install_home_assistant_lovelace_card(target)
    before = target.stat()

    second = install_home_assistant_lovelace_card(target)
    after = target.stat()

    assert first == second == target
    assert before.st_ino == after.st_ino
    assert before.st_mtime_ns == after.st_mtime_ns


def test_lovelace_card_install_normalizes_existing_file_mode(
    tmp_path: Path,
) -> None:
    target = lovelace_target(tmp_path)

    install_home_assistant_lovelace_card(target)
    target.chmod(0o600)

    install_home_assistant_lovelace_card(target)

    assert target.stat().st_mode & 0o777 == 0o644


def test_lovelace_card_install_replaces_stale_regular_file(
    tmp_path: Path,
) -> None:
    target = lovelace_target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(
        "old card",
        encoding="utf-8",
    )

    install_home_assistant_lovelace_card(target)

    assert target.read_text(encoding="utf-8") == card_text()


@pytest.mark.parametrize(
    "symlink_part",
    [
        "www",
        "sds200",
        "sds200-card.js",
    ],
)
def test_lovelace_card_install_refuses_symlink_paths(
    tmp_path: Path,
    symlink_part: str,
) -> None:
    homeassistant = tmp_path / "homeassistant"
    www = homeassistant / "www"
    card_directory = www / "sds200"
    target = card_directory / HOME_ASSISTANT_LOVELACE_CARD_FILENAME
    outside = tmp_path / "outside"

    homeassistant.mkdir()
    outside.mkdir()

    if symlink_part == "www":
        www.symlink_to(
            outside,
            target_is_directory=True,
        )
    elif symlink_part == "sds200":
        www.mkdir()
        card_directory.symlink_to(
            outside,
            target_is_directory=True,
        )
    else:
        card_directory.mkdir(parents=True)
        outside_file = outside / "outside.js"
        outside_file.write_text(
            "outside",
            encoding="utf-8",
        )
        target.symlink_to(outside_file)

    with pytest.raises(
        SDS200Error,
        match="refuses symlinks",
    ):
        install_home_assistant_lovelace_card(target)


def test_lovelace_card_install_rejects_relative_destination() -> None:
    with pytest.raises(
        ValueError,
        match="must be absolute",
    ):
        install_home_assistant_lovelace_card(Path("www/sds200/sds200-card.js"))


def test_lovelace_card_install_rejects_other_filename(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="must use",
    ):
        install_home_assistant_lovelace_card(tmp_path / "other-card.js")


def test_lovelace_card_install_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    target = lovelace_target(tmp_path)

    install_home_assistant_lovelace_card(target)

    assert list(target.parent.glob(".*.tmp")) == []


def test_lovelace_card_install_preserves_unrelated_files(
    tmp_path: Path,
) -> None:
    card_directory = tmp_path / "homeassistant" / "www" / "sds200"
    card_directory.mkdir(parents=True)

    unrelated = card_directory / "notes.txt"
    unrelated.write_text(
        "keep",
        encoding="utf-8",
    )

    install_home_assistant_lovelace_card(card_directory / HOME_ASSISTANT_LOVELACE_CARD_FILENAME)

    assert unrelated.read_text(encoding="utf-8") == "keep"
