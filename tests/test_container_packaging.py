from __future__ import annotations

from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _REPOSITORY_ROOT / "Dockerfile"
_DOCKERIGNORE = _REPOSITORY_ROOT / ".dockerignore"
_COMPOSE = _REPOSITORY_ROOT / "compose.yaml"
_USB_COMPOSE = _REPOSITORY_ROOT / "compose.usb.yaml"
_ENV_EXAMPLE = _REPOSITORY_ROOT / ".env.example"
_GITIGNORE = _REPOSITORY_ROOT / ".gitignore"
_README = _REPOSITORY_ROOT / "README.md"
_ROADMAP = _REPOSITORY_ROOT / "ROADMAP.md"
_CONTAINER_DOC = _REPOSITORY_ROOT / "docs" / "container-deployment.md"
_DOCKER_HUB_WORKFLOW = (
    _REPOSITORY_ROOT / ".github" / "workflows" / "docker-hub-image.yml"
)


def test_generic_container_dockerfile_builds_with_mqtt_and_web_support() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert dockerfile.count("FROM python:3.14-slim") == 2
    assert '".[mqtt,web]"' in dockerfile
    assert '"sds200[mqtt,web]"' in dockerfile
    assert 'ENTRYPOINT ["sdsctl"]' in dockerfile
    assert 'CMD ["--help"]' in dockerfile
    assert "home_assistant_app_supervisor" not in dockerfile
    assert "EXPOSE " not in dockerfile
    assert 'org.opencontainers.image.title="sdsctl"' in dockerfile


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
    ):
        assert required in dockerfile

    assert "VOLUME " not in dockerfile


def test_generic_container_uses_existing_daemon_health_and_signal_contract() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert "STOPSIGNAL SIGTERM" in dockerfile
    assert 'CMD ["sdsctl", "daemon-client", "health"]' in dockerfile
    assert (
        'CMD ["sdsctl", "daemon-client", "status", "--json"]'
        not in dockerfile
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
    assert compose.count("    build:\n      context: .\n") == 3


def test_generic_compose_preserves_network_lifecycle_and_private_api_boundary() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")

    assert "    network_mode: host\n" in compose
    assert "    restart: unless-stopped\n" in compose
    assert compose.count("    network_mode: host\n") == 1


def test_generic_compose_persists_xdg_roots_with_named_volumes() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")

    for required in (
        "      - config:/config\n",
        "      - state:/state\n",
        "      - cache:/cache\n",
        "      - runtime:/run/sdsctl\n",
        "\nvolumes:\n",
        "  config:\n",
        "  state:\n",
        "  cache:\n",
        "  runtime:\n",
    ):
        assert required in compose

    assert compose.count("      - runtime:/run/sdsctl\n") == 3


def test_generic_compose_defines_isolated_on_demand_daemon_client() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")
    client = compose.split("\n  daemon-client:\n", 1)[1].split(
        "\n  web-dashboard:\n", 1
    )[0]

    for required in (
        "    profiles:\n      - client\n",
        "    build:\n      context: .\n",
        "    entrypoint:\n      - sdsctl\n      - daemon-client\n",
        "    network_mode: none\n",
        "    healthcheck:\n      disable: true\n",
        "    volumes:\n      - runtime:/run/sdsctl\n",
    ):
        assert required in client

    for forbidden in (
        "config:/config",
        "state:/state",
        "cache:/cache",
        "ports:",
        "expose:",
        "privileged:",
        "devices:",
        "cap_add:",
        "restart:",
        "depends_on:",
    ):
        assert forbidden not in client


def test_generic_compose_preserves_daemon_contract_with_shared_runtime() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")
    daemon = compose.split("  daemon:\n", 1)[1].split("\n  daemon-client:\n", 1)[0]

    assert "    network_mode: host\n" in daemon
    assert "    restart: unless-stopped\n" in daemon
    assert '${SDS200_HOST:?Set SDS200_HOST to the scanner IPv4 address}' in daemon
    assert "      - config:/config\n" in daemon
    assert "      - state:/state\n" in daemon
    assert "      - cache:/cache\n" in daemon
    assert "      - runtime:/run/sdsctl\n" in daemon
    for forbidden in ("ports:", "expose:", "privileged:", "devices:", "cap_add:"):
        assert forbidden not in daemon


def test_generic_compose_defines_bridge_networked_long_running_web_dashboard() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")
    web = compose.split("\n  web-dashboard:\n", 1)[1].split("\nvolumes:\n", 1)[0]

    for required in (
        "    profiles:\n      - web\n",
        "    build:\n      context: .\n",
        "    entrypoint:\n      - sdsctl\n      - web\n      - --container-exposure\n",
        '    ports:\n      - "127.0.0.1:${SDSCTL_WEB_PORT:-8000}:8000"\n',
        "    restart: unless-stopped\n",
        "        - CMD-SHELL\n",
        'python -c "import urllib.request;',
        "http://127.0.0.1:8000/healthz",
        "    volumes:\n      - runtime:/run/sdsctl\n",
    ):
        assert required in web

    for forbidden in (
        "config:/config",
        "state:/state",
        "cache:/cache",
        "expose:",
        "privileged:",
        "devices:",
        "cap_add:",
        "depends_on:",
        "--home-assistant-ingress",
        "--listen-address",
        "--host",
        "network_mode: none",
        "network_mode: host",
    ):
        assert forbidden not in web


def test_generic_compose_profiles_leave_default_daemon_only() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")
    daemon = compose.split("  daemon:\n", 1)[1].split("\n  daemon-client:\n", 1)[0]
    client = compose.split("\n  daemon-client:\n", 1)[1].split(
        "\n  web-dashboard:\n", 1
    )[0]
    web = compose.split("\n  web-dashboard:\n", 1)[1].split("\nvolumes:\n", 1)[0]

    assert "profiles:" not in daemon
    assert "    profiles:\n      - client\n" in client
    assert "    profiles:\n      - web\n" in web


def test_usb_compose_preserves_standalone_one_shot_serial_cli() -> None:
    compose = _USB_COMPOSE.read_text(encoding="utf-8")
    scanner = compose.split("\n  usb-scanner:\n", 1)[1].split(
        "\n  daemon-client:\n", 1
    )[0]

    for required in (
        "    build:\n      context: .\n",
        "    entrypoint:\n      - sdsctl\n      - --port\n"
        "      - /dev/sdsctl-scanner\n",
        "    network_mode: none\n",
        "    healthcheck:\n      disable: true\n",
        '    annotations:\n      run.oci.keep_original_groups: "1"\n',
        '      - "${SDSCTL_USB_DEVICE:?Set SDSCTL_USB_DEVICE to the stable '
        'Linux /dev/serial/by-id/... scanner path when available}'
        ':/dev/sdsctl-scanner"\n',
        '      - "${SDSCTL_USB_GID:?Set SDSCTL_USB_GID to the host scanner '
        'character-device group GID}"\n',
    ):
        assert required in scanner

    for forbidden in (
        "image:",
        "privileged:",
        "cap_add:",
        "ports:",
        "expose:",
        "restart:",
        "volumes:",
        "depends_on:",
        "--host",
        "SDS200_HOST",
        "/dev:/dev",
        "network_mode: host",
    ):
        assert forbidden not in scanner


def test_usb_compose_defines_persistent_serial_daemon() -> None:
    compose = _USB_COMPOSE.read_text(encoding="utf-8")
    daemon = compose.split("  daemon:\n", 1)[1].split(
        "\n  usb-scanner:\n", 1
    )[0]

    for required in (
        "    build:\n      context: .\n",
        "    entrypoint:\n      - sdsctl\n      - --port\n"
        "      - /dev/sdsctl-scanner\n",
        "    command:\n      - --log-level\n"
        '      - "${SDS200_LOG_LEVEL:-INFO}"\n'
        "      - daemon\n",
        "    network_mode: none\n",
        "    restart: unless-stopped\n",
        '    annotations:\n      run.oci.keep_original_groups: "1"\n',
        '      - "${SDSCTL_USB_DEVICE:?Set SDSCTL_USB_DEVICE to the stable '
        'Linux /dev/serial/by-id/... scanner path when available}'
        ':/dev/sdsctl-scanner"\n',
        '      - "${SDSCTL_USB_GID:?Set SDSCTL_USB_GID to the host scanner '
        'character-device group GID}"\n',
        "      - config:/config\n",
        "      - state:/state\n",
        "      - cache:/cache\n",
        "      - runtime:/run/sdsctl\n",
    ):
        assert required in daemon

    for forbidden in (
        "image:",
        "privileged:",
        "cap_add:",
        "ports:",
        "expose:",
        "depends_on:",
        "--host",
        "SDS200_HOST",
        "/dev:/dev",
        "network_mode: host",
    ):
        assert forbidden not in daemon


def test_usb_compose_defines_private_runtime_sidecars() -> None:
    compose = _USB_COMPOSE.read_text(encoding="utf-8")
    client = compose.split("\n  daemon-client:\n", 1)[1].split(
        "\n  web-dashboard:\n", 1
    )[0]
    web = compose.split("\n  web-dashboard:\n", 1)[1].split(
        "\nvolumes:\n", 1
    )[0]

    for required in (
        "    profiles:\n      - client\n",
        "    entrypoint:\n      - sdsctl\n      - daemon-client\n",
        "    network_mode: none\n",
        "    healthcheck:\n      disable: true\n",
        "    volumes:\n      - runtime:/run/sdsctl\n",
    ):
        assert required in client

    for forbidden in (
        "config:/config",
        "state:/state",
        "cache:/cache",
        "ports:",
        "expose:",
        "privileged:",
        "devices:",
        "group_add:",
        "annotations:",
        "cap_add:",
        "restart:",
        "depends_on:",
    ):
        assert forbidden not in client

    for required in (
        "    profiles:\n      - web\n",
        "    entrypoint:\n      - sdsctl\n      - web\n"
        "      - --container-exposure\n",
        '    ports:\n'
        '      - "127.0.0.1:${SDSCTL_WEB_PORT:-8000}:8000"\n',
        "    restart: unless-stopped\n",
        "        - CMD-SHELL\n",
        "http://127.0.0.1:8000/healthz",
        "    volumes:\n      - runtime:/run/sdsctl\n",
    ):
        assert required in web

    for forbidden in (
        "config:/config",
        "state:/state",
        "cache:/cache",
        "privileged:",
        "devices:",
        "group_add:",
        "annotations:",
        "cap_add:",
        "depends_on:",
        "--host",
        "SDS200_HOST",
        "network_mode: host",
    ):
        assert forbidden not in web


def test_usb_compose_keeps_device_access_on_scanner_owners_only() -> None:
    compose = _USB_COMPOSE.read_text(encoding="utf-8")

    assert compose.count("    devices:\n") == 2
    assert compose.count(":/dev/sdsctl-scanner") == 2
    assert compose.count("    annotations:\n") == 2
    assert compose.count('run.oci.keep_original_groups: "1"') == 2
    assert compose.count("    group_add:\n") == 2
    assert compose.count("      - runtime:/run/sdsctl\n") == 3

    for required in (
        "\nvolumes:\n",
        "  config:\n",
        "  state:\n",
        "  cache:\n",
        "  runtime:\n",
    ):
        assert required in compose

    assert "--host" not in compose
    assert "SDS200_HOST" not in compose
    assert "network_mode: host" not in compose



def test_root_compose_does_not_gain_usb_interpolation_contract() -> None:
    compose = _COMPOSE.read_text(encoding="utf-8")

    assert "SDSCTL_USB_DEVICE" not in compose
    assert "SDSCTL_USB_GID" not in compose
    assert "usb-scanner" not in compose


def test_generic_compose_example_configuration_is_non_secret_and_ignored_locally() -> None:
    env_example = _ENV_EXAMPLE.read_text(encoding="utf-8")
    gitignore = _GITIGNORE.read_text(encoding="utf-8")

    assert env_example == (
        "SDS200_HOST=192.0.2.10\n"
        "SDS200_LOG_LEVEL=INFO\n"
        "SDSCTL_WEB_PORT=8000\n"
    )
    assert ".env\n" in gitignore


def test_generic_container_documentation_preserves_compose_security_boundary() -> None:
    document = _CONTAINER_DOC.read_text(encoding="utf-8")
    normalized_document = " ".join(document.split())
    readme = _README.read_text(encoding="utf-8")
    roadmap = _ROADMAP.read_text(encoding="utf-8")

    for required in (
        "Milestone 25.7",
        "theboyd78/sdsctl",
        "`build: { context: . }`",
        "`network_mode: host`",
        "`SDS200_HOST`",
        "docker compose up --detach --build daemon",
        "docker compose run --rm daemon-client status --json",
        "docker compose run --rm daemon-client snapshot",
        "docker compose run --rm daemon-client events --count 10 --json",
        "docker compose --profile web up --detach --build web-dashboard",
        "http://127.0.0.1:8000/healthz",
        "http://127.0.0.1:8000/",
        "`SDSCTL_WEB_PORT`",
        "`--container-exposure`",
        "127.0.0.1:${SDSCTL_WEB_PORT:-8000}:8000",
        "`network_mode: none`",
        "runtime transport volume",
        "sole producer and owner",
        "No TCP daemon API",
        "stale-socket cleanup",
        "UID/GID `10001`",
        "`sdsctl daemon-client status --json`",
        "named volumes",
        "`docker compose down`",
        "`docker compose down --volumes`",
        "No TCP port is exposed",
        "standalone web dashboard",
        "bridge networking",
        "Linux USB serial passthrough",
        "`compose.usb.yaml`",
        "native Linux Docker Engine",
        "SDSCTL_USB_DEVICE",
        "SDSCTL_USB_GID",
        "stat -Lc '%g'",
        "network_mode: none",
        "docker compose -f compose.usb.yaml config",
        "docker compose -f compose.usb.yaml run --rm usb-scanner info",
        "docker compose -f compose.usb.yaml run --rm usb-scanner scanner-info",
        "docker compose -f compose.usb.yaml run --rm usb-scanner health",
        "does not start or replace the daemon",
        "no SDS200 network audio path",
        "Milestone 25.9 physical validation",
        "Ubuntu 26.04 LTS",
        "Docker Engine client/server 29.7.2",
        "Docker Compose v5.4.0",
        "Version 1.26.01",
        "udp://192.168.0.251:50536",
        "rtsp://192.168.0.251/au:scanner.au",
        "supplemental GID 20 only",
        "Physical unplug/replug and USB re-enumeration were not tested",
        "127.0.0.1:18080",
        "Interactive browser playback",
        "Docker Desktop 4.34",
        "Enable host networking",
        "Enhanced Container Isolation",
        "Docker Desktop for Linux 4.87.0",
        "`hostNetworkingEnabled` key",
        "persistent",
        "settings-file state",
        "authentication and TLS termination remain",
        "physical SDS200 validation on Docker Desktop",
        "physical Windows and macOS Docker validation remains outstanding",
        "USB/IP",
        "Rootless Podman network daemon",
        "podman build --format docker --tag sdsctl:local .",
        "Podman 5.7.0",
        "Netavark",
        "cgroup v2",
        "crun 1.21",
        "defaulted to OCI image format",
        "localhost-bound host services",
        "physical rootless-Podman UDP control/PSI acceptance",
        "`podman compose` delegates to an external Compose provider",
        "Rootless Podman USB scanner CLI",
        "`--group-add keep-groups`",
        '--device "$SDSCTL_USB_DEVICE:/dev/sdsctl-scanner:rwm"',
        "rootless Podman USB serial acceptance",
        "one-shot scanner CLI only",
        "SELinux device-policy acceptance",
        "Rootless Podman Compose provider",
        "systemctl --user start podman.socket",
        "/run/user/$UID/podman/podman.sock",
        "Docker Compose v5.4.0",
        "podman compose -f compose.yaml config",
        "podman compose -f compose.yaml run --rm daemon-client --help",
        "native TCP port 554",
        "Connection refused",
        "restart loop",
        "numeric `group_add`",
        "Rootless Podman Compose web-dashboard sidecar",
        "Rootless Podman Compose USB runtime",
        '`run.oci.keep_original_groups: "1"`',
        "ordinary container group name",
        "one-shot scanner CLI boundary",
        "`CMD-SHELL`",
        "`podman healthcheck run`",
        "HTTP 503",
        "daemon-unavailable",
        "Rootless Podman Compose USB daemon and sidecars",
        "persistent serial owner",
        "neither requires `SDS200_HOST`",
        "no network audio source",
        "Recording operations are not advertised",
        "docker compose -f compose.usb.yaml up --detach --build daemon",
        "podman compose -f compose.usb.yaml up --detach --build daemon",
        "docker compose -f compose.usb.yaml run --rm daemon-client status --json",
        "docker compose -f compose.usb.yaml run --rm daemon-client snapshot",
        "docker compose -f compose.usb.yaml --profile web up --detach --build web-dashboard",
        "Milestone 25.17",
        "Milestone 25.18",
        "cross-runtime compatibility matrix",
        "Docker Compose v5.5.0",
        "`PODMAN_COMPOSE_PROVIDER`",
        "byte-for-byte identical",
        "`podman --remote`",
        "`--group-add keep-groups`",
        "not supported in remote mode",
        "client-side USB",
        "`podman-compose`",
        "Linux virtual machine",
        "WSL2",
    ):
        assert required in document or required in normalized_document

    assert (
        "[generic container deployment guide](docs/container-deployment.md)"
        in readme
    )
    assert "docker compose run --rm daemon-client status --json" in readme
    assert "docker compose -f compose.usb.yaml run --rm usb-scanner info" in readme
    assert "snapshot --json" not in document
    assert "physical scanner validation of the generic Compose deployment" not in document
    assert (
        "### Milestone 25.18 — Alternate and remote container-runtime "
        "portability foundation"
        in roadmap
    )
    assert "Milestone 25.17 is closed" in roadmap
    assert "remote client-side USB" in roadmap


def test_generic_docker_hub_workflow_has_safe_trigger_and_publication_contract() -> None:
    workflow = _DOCKER_HUB_WORKFLOW.read_text(encoding="utf-8")

    assert 'IMAGE_NAME: "theboyd78/sdsctl"' in workflow
    for trigger in ("pull_request:", "push:", "- main", '- "v*"', "workflow_dispatch:"):
        assert trigger in workflow

    assert '[[ "${GITHUB_EVENT_NAME}" == "push" ]]' in workflow
    assert '[[ "${GITHUB_REF}" == refs/tags/v* ]]' in workflow
    assert '[[ "${GITHUB_REF_NAME}" != "v${package_version}" ]]' in workflow
    assert "github.event_name == 'push'" in workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in workflow
    assert "needs.metadata.outputs.publish == 'true'" in workflow
    assert "github.event_name == 'workflow_dispatch'" not in workflow

    assert "import ast" in workflow
    assert "import tomllib" in workflow
    assert "ast.parse" in workflow
    assert "ast.Assign" in workflow
    assert 'target.id == "__version__"' in workflow
    assert "isinstance(version_node.value.value, str)" in workflow
    assert "version_node.value.value != package_version" in workflow
    assert "import sds200" not in workflow
    assert workflow.count("platforms: linux/amd64,linux/arm64") == 2
    assert workflow.count("push: false") == 1
    assert workflow.count("push: true") == 1
    assert "${{ env.IMAGE_NAME }}:${{ needs.metadata.outputs.package_version }}" in workflow
    assert "${{ env.IMAGE_NAME }}:latest" in workflow

    publish_job = workflow.split("\n  publish:\n", 1)[1]
    validation_jobs = workflow.split("\n  publish:\n", 1)[0]
    assert "vars.DOCKERHUB_USERNAME" in publish_job
    assert "secrets.DOCKERHUB_TOKEN" in publish_job
    assert "secrets.DOCKERHUB_TOKEN" not in validation_jobs
    assert '"${DOCKERHUB_USERNAME}" != "theboyd78"' in publish_job


def test_generic_docker_hub_workflow_pins_docker_actions() -> None:
    workflow = _DOCKER_HUB_WORKFLOW.read_text(encoding="utf-8")
    expected_pins = {
        "docker/setup-qemu-action v4.2.0": (
            "docker/setup-qemu-action@"
            "96fe6ef7f33517b61c61be40b68a1882f3264fb8"
        ),
        "docker/setup-buildx-action v4.2.0": (
            "docker/setup-buildx-action@"
            "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c"
        ),
        "docker/login-action v4.6.0": (
            "docker/login-action@"
            "dbcb813823bdd20940b903addbd779551569679f"
        ),
        "docker/build-push-action v7.2.0": (
            "docker/build-push-action@"
            "f9f3042f7e2789586610d6e8b85c8f03e5195baf"
        ),
    }

    for version_comment, pinned_action in expected_pins.items():
        assert f"# {version_comment}" in workflow
        assert pinned_action in workflow
