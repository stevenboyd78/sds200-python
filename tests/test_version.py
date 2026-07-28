from __future__ import annotations

import tomllib
from pathlib import Path

from sds200 import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_matches_project_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["version"] == __version__


def test_changelog_contains_current_release_heading() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"## [{__version__}] - " in changelog
