from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CURRENT_REPOSITORY_URL = "https://github.com/stevenboyd78/sdsctl"
LEGACY_REPOSITORY_URL = "https://github.com/stevenboyd78/sds200-python"
LIVE_REPOSITORY_OWNED_FILES = (
    "pyproject.toml",
    "Dockerfile",
    "repository.yaml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/workflows/home-assistant-app-image.yml",
    "home-assistant/sds200/config.yaml",
    "home-assistant/sds200/Dockerfile",
    "src/sds200/daemon_mqtt_home_assistant.py",
    "src/sds200/web_assets/sds200-card.js",
    "docs/releasing.md",
    "wiki/Home.md",
    "wiki/_Sidebar.md",
)
BRANDING_ASSET_NAMES = (
    "logo.svg",
    "icon.svg",
    "logo-4k.png",
    "icon-2048.png",
    "wallpaper-1080p.png",
    "wallpaper-4k.png",
)


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def _quoted_yaml_scalar(text: str, key: str) -> str:
    match = re.search(
        rf'^[ ]*{re.escape(key)}: "([^"\n]*)"$',
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing quoted YAML scalar {key!r}"
    return match.group(1)


def test_python_distribution_package_and_entry_point_remain_compatible() -> None:
    project = tomllib.loads(_read("pyproject.toml"))

    assert project["project"]["name"] == "sds200"
    assert (REPOSITORY_ROOT / "src" / "sds200" / "__init__.py").is_file()
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/sds200"
    ]
    assert project["tool"]["mypy"]["packages"] == ["sds200"]
    assert project["project"]["scripts"]["sdsctl"] == "sds200.cli:main"


def test_current_project_urls_use_sdsctl_repository() -> None:
    project = tomllib.loads(_read("pyproject.toml"))
    urls = project["project"]["urls"]

    assert urls["Homepage"] == CURRENT_REPOSITORY_URL
    assert urls["Repository"] == CURRENT_REPOSITORY_URL
    assert urls["Issues"] == f"{CURRENT_REPOSITORY_URL}/issues"
    assert urls["Changelog"] == f"{CURRENT_REPOSITORY_URL}/blob/main/CHANGELOG.md"


def test_home_assistant_compatibility_identity_remains_sds200() -> None:
    app_directory = REPOSITORY_ROOT / "home-assistant" / "sds200"
    manifest = _read("home-assistant/sds200/config.yaml")
    workflow = _read(".github/workflows/home-assistant-app-image.yml")
    lovelace_installer = _read("src/sds200/home_assistant_lovelace.py")

    assert app_directory.is_dir()
    assert _quoted_yaml_scalar(manifest, "name") == "sds200"
    assert _quoted_yaml_scalar(manifest, "slug") == "sds200"
    assert _quoted_yaml_scalar(manifest, "panel_title") == "sds200"
    assert (
        _quoted_yaml_scalar(manifest, "image")
        == "ghcr.io/stevenboyd78/sds200-home-assistant"
    )
    assert _quoted_yaml_scalar(workflow, "IMAGE_NAME") == "sds200-home-assistant"
    assert 'HOME_ASSISTANT_LOVELACE_CARD_FILENAME = "sds200-card.js"' in lovelace_installer
    assert (
        'HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL = "/local/sds200/sds200-card.js"'
        in lovelace_installer
    )


def test_generic_container_documentation_preserves_local_image_tag() -> None:
    assert "# docker build -t sds200-daemon ." in _read("Dockerfile")


def test_live_repository_owned_files_use_sdsctl_repository_url() -> None:
    for relative_path in LIVE_REPOSITORY_OWNED_FILES:
        contents = _read(relative_path)

        assert CURRENT_REPOSITORY_URL in contents, relative_path
        assert LEGACY_REPOSITORY_URL not in contents, relative_path


def test_generic_docker_hub_identity_is_isolated_from_python_and_home_assistant() -> None:
    workflow = _read(".github/workflows/docker-hub-image.yml")

    assert 'IMAGE_NAME: "theboyd78/sdsctl"' in workflow
    assert 'IMAGE_NAME: "sds200-home-assistant"' in _read(
        ".github/workflows/home-assistant-app-image.yml"
    )
    assert 'name = "sds200"' in _read("pyproject.toml")
    assert '"sds200[mqtt,web]"' in _read("Dockerfile")


def test_roadmap_names_milestone_25_11_as_rootless_podman_network_boundary() -> None:
    roadmap = _read("ROADMAP.md")
    active_milestone = roadmap.split("## Active milestone", 1)[1].split(
        "## Deferred hardware validation", 1
    )[0]
    normalized_active_milestone = " ".join(active_milestone.split())

    for required in (
        "### Milestone 25.11 — Rootless Podman network portability foundation",
        "Milestone 25.10 is closed",
        "native-Linux rootless Podman",
        "`podman build --format docker`",
        "existing image `HEALTHCHECK`",
        "`--network host`",
        "Podman 5.7.0",
        "Ubuntu 26.04 LTS",
        "Netavark",
        "cgroup v2/systemd",
        "crun 1.21",
        "default OCI image format discarded",
        "UID/GID `10001:10001`",
        "`SIGTERM`",
        "firmware 1.26.01",
        "live PSI startup",
        "TCP 554 RTSP listener",
        "native host",
        "Do not attribute that scanner-side RTSP failure to Podman",
        "Podman Compose providers",
        "Podman USB serial/supplemental-group semantics",
        "Windows/macOS Podman behavior",
        "Docker Desktop USB/IP",
        "physical Windows/macOS Docker validation",
        "shares the host network namespace",
        "Native systemd remains preferred",
    ):
        assert required in normalized_active_milestone


def test_branding_asset_paths_use_sdsctl_identity() -> None:
    for asset_name in BRANDING_ASSET_NAMES:
        assert (REPOSITORY_ROOT / "docs" / "assets" / f"sdsctl-{asset_name}").is_file()
        assert not (
            REPOSITORY_ROOT / "docs" / "assets" / f"sds200-python-{asset_name}"
        ).exists()


def test_readme_uses_sdsctl_logo_path() -> None:
    readme = _read("README.md")

    assert "docs/assets/sdsctl-logo.svg" in readme
    assert "docs/assets/sds200-python-logo.svg" not in readme


def test_branding_documentation_uses_sdsctl_asset_names() -> None:
    branding_readme = _read("docs/assets/README.md")

    for asset_name in BRANDING_ASSET_NAMES:
        assert f"sdsctl-{asset_name}" in branding_readme
        assert f"sds200-python-{asset_name}" not in branding_readme


def test_horizontal_logo_uses_sdsctl_identity() -> None:
    logo = _read("docs/assets/sdsctl-logo.svg")

    assert "sdsctl neon logo" in logo
    assert "SDSCTL" in logo
    assert "SDS200-PYTHON" not in logo
    assert "sds200-python neon logo" not in logo


def test_icon_uses_sdsctl_identity() -> None:
    icon = _read("docs/assets/sdsctl-icon.svg")

    assert "sdsctl neon icon" in icon
    assert "sds200-python neon icon" not in icon
