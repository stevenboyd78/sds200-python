from __future__ import annotations

from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _REPOSITORY_ROOT / "Dockerfile"
_DOCKERIGNORE = _REPOSITORY_ROOT / ".dockerignore"
_README = _REPOSITORY_ROOT / "README.md"
_ROADMAP = _REPOSITORY_ROOT / "ROADMAP.md"
_CONTAINER_DOC = _REPOSITORY_ROOT / "docs" / "container-deployment.md"


def test_generic_container_dockerfile_builds_local_source_with_mqtt_support() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert dockerfile.count("FROM python:3.14-slim") == 2
    assert '"sds200[mqtt]"' in dockerfile
    assert 'ENTRYPOINT ["sdsctl"]' in dockerfile
    assert 'CMD ["--help"]' in dockerfile
    assert "home_assistant_app_supervisor" not in dockerfile
    assert "EXPOSE " not in dockerfile


def test_generic_container_runs_unprivileged_with_deterministic_xdg_roots() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    for required in (
        "HOME=/home/sdsctl",
        "XDG_CONFIG_HOME=/config",
        "XDG_STATE_HOME=/state",
        "XDG_CACHE_HOME=/cache",
        "XDG_RUNTIME_DIR=/run",
        "--uid 10001",
        "--gid 10001",
        "USER 10001:10001",
        'VOLUME ["/config", "/state", "/cache"]',
    ):
        assert required in dockerfile


def test_generic_container_uses_existing_daemon_health_and_signal_contract() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert (
        'CMD ["sdsctl", "daemon-client", "status", "--json"]'
        in dockerfile
    )
    assert "HEALTHCHECK --interval=30s" in dockerfile


def test_generic_container_docker_context_remains_minimal_and_ha_complete() -> None:
    dockerignore = _DOCKERIGNORE.read_text(encoding="utf-8")

    assert dockerignore.startswith("*\n")
    for required in (
        "!.dockerignore\n",
        "!Dockerfile\n",
        "!pyproject.toml\n",
        "!README.md\n",
        "!LICENSE\n",
        "!src/**\n",
        "**/__pycache__/\n",
        "**/*.py[cod]\n",
        "!home-assistant/sds200/Dockerfile\n",
    ):
        assert required in dockerignore


def test_generic_container_documentation_preserves_first_slice_boundary() -> None:
    document = _CONTAINER_DOC.read_text(encoding="utf-8")
    readme = _README.read_text(encoding="utf-8")
    roadmap = _ROADMAP.read_text(encoding="utf-8")

    for required in (
        "Milestone 25.1",
        "`--network host`",
        "UID/GID `10001`",
        "`sdsctl daemon-client status --json`",
        "No TCP port is exposed",
        "does not require `--privileged`",
        "Docker Compose workflows",
        "bridge networking",
        "Linux USB serial passthrough",
        "Windows or macOS Docker behavior",
    ):
        assert required in document

    assert (
        "[generic container deployment guide](docs/container-deployment.md)"
        in readme
    )
    assert (
        "### Milestone 25.1 — Generic network-container packaging foundation"
        in roadmap
    )
    assert "This slice does not add Docker Compose" in roadmap
