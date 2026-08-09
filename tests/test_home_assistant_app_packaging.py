from __future__ import annotations

import re
from pathlib import Path

from sds200 import __version__
from sds200.home_assistant_app_runtime import HOME_ASSISTANT_APP_INGRESS_PORT
from sds200.home_assistant_app_supervisor import (
    HOME_ASSISTANT_APP_DAEMON_STOP_TIMEOUT,
    HOME_ASSISTANT_APP_FORCE_STOP_TIMEOUT,
    HOME_ASSISTANT_APP_WEB_STOP_TIMEOUT,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_MANIFEST = _REPOSITORY_ROOT / "repository.yaml"
_APP_DIRECTORY = _REPOSITORY_ROOT / "home-assistant" / "sds200"
_APP_MANIFEST = _APP_DIRECTORY / "config.yaml"
_APP_DOCKERFILE = _APP_DIRECTORY / "Dockerfile"
_DOCKERIGNORE = _REPOSITORY_ROOT / ".dockerignore"


def _quoted_scalar(text: str, key: str) -> str:
    match = re.search(
        rf'^{re.escape(key)}: "([^"]*)"$',
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing quoted scalar {key!r}"
    return match.group(1)


def _integer_scalar(text: str, key: str) -> int:
    match = re.search(
        rf"^{re.escape(key)}: ([0-9]+)$",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing integer scalar {key!r}"
    return int(match.group(1))


def test_home_assistant_repository_manifest_is_present() -> None:
    manifest = _REPOSITORY_MANIFEST.read_text(encoding="utf-8")

    assert _quoted_scalar(manifest, "name") == "sds200"
    assert (
        _quoted_scalar(manifest, "url")
        == "https://github.com/stevenboyd78/sds200-python"
    )
    assert "maintainer:" in manifest


def test_home_assistant_app_manifest_tracks_package_release_version() -> None:
    manifest = _APP_MANIFEST.read_text(encoding="utf-8")
    app_version = _quoted_scalar(manifest, "version")

    assert app_version in {__version__, f"{__version__}-dev"}
    assert (
        _quoted_scalar(manifest, "image")
        == "ghcr.io/stevenboyd78/sds200-home-assistant"
    )


def test_home_assistant_app_manifest_uses_ingress_and_required_mqtt_service() -> None:
    manifest = _APP_MANIFEST.read_text(encoding="utf-8")

    assert "arch:\n  - aarch64\n  - amd64\n" in manifest
    assert "init: false\n" in manifest
    assert 'services:\n  - "mqtt:need"\n' in manifest
    assert "ingress: true\n" in manifest
    assert "ingress_stream: true\n" in manifest
    assert (
        _integer_scalar(manifest, "ingress_port")
        == HOME_ASSISTANT_APP_INGRESS_PORT
    )
    assert 'options:\n  mqtt_topic_prefix: "sdsctl"\n' in manifest
    assert 'scanner_host: ""' not in manifest
    assert 'scanner_host: "str(1,)"\n' in manifest
    assert 'mqtt_topic_prefix: "str(1,)"\n' in manifest


def test_home_assistant_app_outer_timeout_covers_ordered_child_shutdown() -> None:
    manifest = _APP_MANIFEST.read_text(encoding="utf-8")
    outer_timeout = _integer_scalar(manifest, "timeout")
    worst_case_supervisor_shutdown = (
        HOME_ASSISTANT_APP_WEB_STOP_TIMEOUT
        + HOME_ASSISTANT_APP_DAEMON_STOP_TIMEOUT
        + (2 * HOME_ASSISTANT_APP_FORCE_STOP_TIMEOUT)
    )

    assert outer_timeout > worst_case_supervisor_shutdown


def test_home_assistant_app_dockerfile_builds_local_source_with_required_extras() -> None:
    dockerfile = _APP_DOCKERFILE.read_text(encoding="utf-8")

    assert dockerfile.count("FROM python:3.14-slim") == 2
    assert 'io.hass.type="app"' in dockerfile
    assert 'io.hass.version="${BUILD_VERSION}"' in dockerfile
    assert 'io.hass.arch="${BUILD_ARCH}"' in dockerfile
    assert '"sds200[web,mqtt]"' in dockerfile
    assert (
        'CMD ["python", "-m", "sds200.home_assistant_app_supervisor"]'
        in dockerfile
    )


def test_home_assistant_app_docker_context_is_minimal_and_source_complete() -> None:
    dockerignore = _DOCKERIGNORE.read_text(encoding="utf-8")

    assert dockerignore.startswith("*\n")
    for required in (
        "!pyproject.toml\n",
        "!README.md\n",
        "!LICENSE\n",
        "!src/**\n",
        "!home-assistant/sds200/Dockerfile\n",
    ):
        assert required in dockerignore
