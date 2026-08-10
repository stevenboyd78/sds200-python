from __future__ import annotations

import re
import struct
from pathlib import Path

from sds200 import __version__
from sds200.home_assistant_app_runtime import (
    HOME_ASSISTANT_APP_INGRESS_PORT,
    HOME_ASSISTANT_APP_RTP_PORT,
)
from sds200.home_assistant_app_supervisor import (
    HOME_ASSISTANT_APP_DAEMON_STOP_TIMEOUT,
    HOME_ASSISTANT_APP_FORCE_STOP_TIMEOUT,
    HOME_ASSISTANT_APP_WEB_STOP_TIMEOUT,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_MANIFEST = _REPOSITORY_ROOT / "repository.yaml"
_APP_DIRECTORY = _REPOSITORY_ROOT / "home-assistant" / "sds200"
_APP_MANIFEST = _APP_DIRECTORY / "config.yaml"
_APP_TRANSLATIONS = _APP_DIRECTORY / "translations" / "en.yaml"
_APP_DOCKERFILE = _APP_DIRECTORY / "Dockerfile"
_APP_ICON = _APP_DIRECTORY / "icon.png"
_APP_LOGO = _APP_DIRECTORY / "logo.png"
_DOCKERIGNORE = _REPOSITORY_ROOT / ".dockerignore"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"

    return struct.unpack(">II", data[16:24])


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


def test_home_assistant_app_has_project_branding_assets() -> None:
    assert _png_size(_APP_ICON) == (128, 128)

    logo_width, logo_height = _png_size(_APP_LOGO)
    assert 1 <= logo_width <= 250
    assert 1 <= logo_height <= 100


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
    assert (
        "ports:\n"
        f"  {HOME_ASSISTANT_APP_RTP_PORT}/udp: "
        f"{HOME_ASSISTANT_APP_RTP_PORT}\n"
        in manifest
    )
    assert (
        "ports_description:\n"
        f'  {HOME_ASSISTANT_APP_RTP_PORT}/udp: "SDS200 RTP audio"\n'
        in manifest
    )
    assert "host_network: true\n" not in manifest
    assert (
        "map:\n"
        "  - type: media\n"
        "    read_only: false\n"
        "  - type: homeassistant_config\n"
        "    read_only: false\n"
        "    path: /homeassistant\n"
        in manifest
    )
    assert "homeassistant_api: true\n" not in manifest
    assert 'panel_icon: "mdi:radio-tower"\n' in manifest
    assert (
        'options:\n'
        '  mqtt_topic_prefix: "sdsctl"\n'
        '  recording_directory: "sdsctl/recordings"\n'
        in manifest
    )
    assert 'scanner_host: ""' not in manifest
    assert 'scanner_host: "str(1,)"\n' in manifest
    assert 'mqtt_topic_prefix: "str(1,)"\n' in manifest
    assert 'recording_directory: "str(1,)"\n' in manifest


def test_home_assistant_app_configuration_translations_cover_schema() -> None:
    translations = _APP_TRANSLATIONS.read_text(encoding="utf-8")

    assert translations.startswith("configuration:\n")

    translation_keys = set(
        re.findall(
            r"^  ([a-z][a-z0-9_]*):$",
            translations,
            flags=re.MULTILINE,
        )
    )
    manifest = _APP_MANIFEST.read_text(encoding="utf-8")
    schema_marker = "schema:\n"
    assert manifest.count(schema_marker) == 1

    schema_text = manifest.partition(schema_marker)[2]
    schema_keys = set(
        re.findall(
            r"^  ([a-z][a-z0-9_]*):",
            schema_text,
            flags=re.MULTILINE,
        )
    )

    assert translation_keys == schema_keys

    assert "name: Scanner host\n" in translations
    assert "LAN hostname or IP address of the Uniden SDS200 scanner." in translations

    assert "name: MQTT topic prefix\n" in translations
    assert (
        "MQTT topic root used by the sds200 daemon and Home Assistant Discovery."
        in translations
    )

    assert "name: Recording directory\n" in translations
    assert "Home Assistant /media root" in translations
    assert "sdsctl/recordings" in translations
    assert "/media/sdsctl/recordings" in translations


def test_home_assistant_app_image_includes_packaged_lovelace_card() -> None:
    card = (
        _REPOSITORY_ROOT
        / "src"
        / "sds200"
        / "web_assets"
        / "sds200-card.js"
    )

    assert card.is_file()
    assert (card.parent / "__init__.py").is_file()

    text = card.read_text(encoding="utf-8")
    assert 'const SDS200_CARD_TYPE = "sds200-card";' in text
    assert "window.customCards" in text
    assert "customElements.define" in text
    assert "new CustomEvent(" in text
    assert '"context-request"' in text
    assert 'event.context = "states";' in text
    assert "event.subscribe = true;" in text
    assert "disconnectedCallback()" in text
    assert "static getConfigForm()" in text
    assert "getGridOptions()" in text
    assert "this._hass.states" not in text
    assert "fetch(" not in text
    assert "WebSocket" not in text
    assert "innerHTML" not in text
    assert "callService" not in text


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


def test_home_assistant_app_dockerfile_has_complete_app_image_labels() -> None:
    dockerfile = _APP_DOCKERFILE.read_text(encoding="utf-8")

    for required in (
        'io.hass.name="sds200"',
        'io.hass.description="Uniden SDS200 scanner daemon and web dashboard for Home Assistant"',
        'io.hass.url="https://github.com/stevenboyd78/sds200-python"',
        'io.hass.type="app"',
        'org.opencontainers.image.licenses="MIT"',
    ):
        assert required in dockerfile


def test_home_assistant_app_image_workflow_uses_current_builder_actions() -> None:
    workflow = (
        _REPOSITORY_ROOT
        / ".github"
        / "workflows"
        / "home-assistant-app-image.yml"
    ).read_text(encoding="utf-8")

    assert 'ARCHITECTURES: \'["amd64", "aarch64"]\'' in workflow
    assert (
        "home-assistant/builder/actions/prepare-multi-arch-matrix@2026.06.0"
        in workflow
    )
    assert "home-assistant/builder/actions/build-image@2026.06.0" in workflow
    assert (
        "home-assistant/builder/actions/publish-multi-arch-manifest@2026.06.0"
        in workflow
    )
    assert "context: .\n" in workflow
    assert "file: ${{ env.APP_DOCKERFILE }}\n" in workflow


def test_home_assistant_app_image_workflow_limits_publish_credentials_to_release_job() -> None:
    workflow = (
        _REPOSITORY_ROOT
        / ".github"
        / "workflows"
        / "home-assistant-app-image.yml"
    ).read_text(encoding="utf-8")

    validation_job = workflow.split("  build:\n", 1)[1].split(
        "  publish-arch:\n",
        1,
    )[0]
    publish_job = workflow.split("  publish-arch:\n", 1)[1].split(
        "  manifest:\n",
        1,
    )[0]

    assert "if: needs.metadata.outputs.publish != 'true'\n" in validation_job
    assert "      contents: read\n" in validation_job
    assert "id-token: write" not in validation_job
    assert "packages: write" not in validation_job
    assert 'container-registry-password: "unused"\n' in validation_job
    assert "push: false\n" in validation_job
    assert "secrets.GITHUB_TOKEN" not in validation_job

    assert "if: needs.metadata.outputs.publish == 'true'\n" in publish_job
    assert "id-token: write\n" in publish_job
    assert "packages: write\n" in publish_job
    assert "container-registry-password: ${{ secrets.GITHUB_TOKEN }}\n" in publish_job
    assert "push: true\n" in publish_job


def test_home_assistant_app_image_workflow_publishes_only_release_versions() -> None:
    workflow = (
        _REPOSITORY_ROOT
        / ".github"
        / "workflows"
        / "home-assistant-app-image.yml"
    ).read_text(encoding="utf-8")

    assert 'tags:\n      - "v*"\n' in workflow
    assert 'release_version="${GITHUB_REF#refs/tags/v}"' in workflow
    assert '"${app_version}" != "${release_version}"' in workflow
    assert '"${package_version}" != "${release_version}"' in workflow
    assert "push: true\n" in workflow
    assert "if: needs.metadata.outputs.publish == 'true'\n" in workflow
    assert (
        "image-tags: |\n"
        "            ${{ needs.metadata.outputs.app_version }}\n"
        "            latest\n"
        in workflow
    )


def test_home_assistant_app_docker_context_is_minimal_and_source_complete() -> None:
    dockerignore = _DOCKERIGNORE.read_text(encoding="utf-8")

    assert dockerignore.startswith("*\n")
    for required in (
        "!pyproject.toml\n",
        "!README.md\n",
        "!LICENSE\n",
        "!src/**\n",
        "**/__pycache__/\n",
        "**/*.py[cod]\n",
        "!home-assistant/sds200/Dockerfile\n",
    ):
        assert required in dockerignore
