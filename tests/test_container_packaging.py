from __future__ import annotations

from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _REPOSITORY_ROOT / "Dockerfile"
_DOCKERIGNORE = _REPOSITORY_ROOT / ".dockerignore"
_COMPOSE = _REPOSITORY_ROOT / "compose.yaml"
_ENV_EXAMPLE = _REPOSITORY_ROOT / ".env.example"
_GITIGNORE = _REPOSITORY_ROOT / ".gitignore"
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


def test_generic_compose_builds_local_source_and_requires_scanner_host() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")

    assert "services:\n  daemon:\n    build:\n      context: .\n" in compose
    assert "\n    image:" not in compose
    assert '${SDS200_HOST:?Set SDS200_HOST to the scanner IPv4 address}' in compose
    assert "${SDS200_LOG_LEVEL:-INFO}" in compose
    assert "      - --host\n" in compose
    assert compose.index("      - --host\n") < compose.index("      - daemon\n")


def test_generic_compose_preserves_network_lifecycle_and_private_api_boundary() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")

    assert "    network_mode: host\n" in compose
    assert "    restart: unless-stopped\n" in compose
    assert "    ports:" not in compose
    assert "    expose:" not in compose
    assert "    privileged:" not in compose
    assert "    healthcheck:" not in compose
    assert "web" not in compose


def test_generic_compose_persists_xdg_roots_with_named_volumes() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")

    for required in (
        "      - config:/config\n",
        "      - state:/state\n",
        "      - cache:/cache\n",
        "\nvolumes:\n",
        "  config:\n",
        "  state:\n",
        "  cache:\n",
    ):
        assert required in compose


def test_generic_compose_example_configuration_is_non_secret_and_ignored_locally() -> None:
    env_example = _ENV_EXAMPLE.read_text(encoding="utf-8")
    gitignore = _GITIGNORE.read_text(encoding="utf-8")

    assert env_example == (
        "SDS200_HOST=192.0.2.10\n"
        "SDS200_LOG_LEVEL=INFO\n"
    )
    assert ".env\n" in gitignore


def test_generic_container_documentation_preserves_compose_security_boundary() -> None:
    document = _CONTAINER_DOC.read_text(encoding="utf-8")
    readme = _README.read_text(encoding="utf-8")
    roadmap = _ROADMAP.read_text(encoding="utf-8")

    for required in (
        "Milestone 25.2",
        "`build: .`",
        "`network_mode: host`",
        "`SDS200_HOST`",
        "docker compose up --detach --build",
        "UID/GID `10001`",
        "`sdsctl daemon-client status --json`",
        "named volumes",
        "`docker compose down`",
        "`docker compose down --volumes`",
        "No TCP port is exposed",
        "standalone web dashboard",
        "generic image publication",
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
        "### Milestone 25.2 — Docker Compose deployment foundation"
        in roadmap
    )
    assert "does not add generic image publication" in roadmap
