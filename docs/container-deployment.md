# Generic container deployment

Milestones 25.1 and 25.2 established the generic network-connected SDS200 daemon
image and its supported source-built Docker Compose deployment. Milestone 25.4
establishes release-tag publication of that standalone multi-platform image to
Docker Hub as `theboyd78/sdsctl` without changing the Compose, scanner-ownership,
daemon IPC, web-binding, or transport contracts.

Native systemd deployment remains the preferred production option when direct
host-device, local-audio, or other operating-system integration is important.

## Image contract

Build the generic image directly from the repository root when Compose is not
being used:

```bash
docker build --tag sds200-daemon .
```

For future matching release tags, the workflow publishes standalone images for
`linux/amd64` and `linux/arm64`. Pull an exact published release for a
reproducible deployment:

```bash
docker pull theboyd78/sdsctl:VERSION
```

Run the exact release with the existing host-network and persistent-path
contract, substituting the trusted scanner address and operator-managed volumes:

```bash
docker run --detach \
  --name sdsctl \
  --network host \
  --restart unless-stopped \
  --volume sdsctl-config:/config \
  --volume sdsctl-state:/state \
  --volume sdsctl-cache:/cache \
  theboyd78/sdsctl:VERSION \
  --host SCANNER_IP daemon
```

Replace `VERSION` with an actually published package version, without a leading
`v`. The `latest` tag follows the newest successfully published release, but
exact version tags are recommended for reproducibility and controlled upgrades.

The image:

- builds the local source into wheels and installs `sds200[mqtt]`;
- runs `sdsctl` as unprivileged UID/GID `10001`;
- sets `XDG_CONFIG_HOME=/config`, `XDG_STATE_HOME=/state`,
  `XDG_CACHE_HOME=/cache`, and `XDG_RUNTIME_DIR=/run`;
- keeps daemon API, event, PCMU, and recording-file sockets private under
  `/run/sdsctl/`;
- declares `/config`, `/state`, and `/cache` as persistent volume roots;
- uses `SIGTERM` as the container stop signal; and
- checks daemon health with the existing private API through
  `sdsctl daemon-client status --json`.

The image has `ENTRYPOINT ["sdsctl"]` and a safe `CMD ["--help"]`. Starting the
image without an explicit scanner command therefore does not acquire scanner
ownership.

No TCP port is exposed by this image. The standalone web dashboard remains
loopback-only and is not made remotely reachable by Milestone 25.2.

## Docker Compose contract

The repository-root `compose.yaml` defines one `daemon` service and deliberately
uses `build: .`, so a checked-out source tree is sufficient to build and deploy
the daemon. This remains distinct from the published standalone
`theboyd78/sdsctl` image: Compose does not contain `image:` and does not select a
Docker Hub tag.

The service preserves the Milestone 25.1 runtime contract:

- `network_mode: host` retains Linux host-network reachability for SDS200 UDP
  control plus the existing RTSP/RTP audio path;
- `restart: unless-stopped` supplies the documented container restart policy;
- `/config`, `/state`, and `/cache` are backed by Compose named volumes;
- the image's `SIGTERM` stop signal and private Unix-domain healthcheck are
  inherited unchanged;
- `/run/sdsctl/` stays ephemeral and private to the daemon container; and
- no ports, wildcard web binding, privileged mode, or scanner device mapping are
  added.

Compose requires `SDS200_HOST` and inserts it into the existing global `--host`
CLI option before the `daemon` subcommand. `SDS200_LOG_LEVEL` is optional and
defaults to `INFO`. These are Compose interpolation variables; this milestone
does not add new `SDSCTL_*` application settings.

Copy the non-secret example and replace the TEST-NET-1 scanner address with the
address used on your trusted scanner network:

```bash
cp .env.example .env
$EDITOR .env
```

The repository ignores `.env`. Keep resolved credentials out of `.env`, the
Compose file, command lines, logs, traces, and captures. If an existing daemon
manifest references a credential environment variable, pass only that required
variable to the container through an operator-owned Compose override rather than
committing its value.

Validate the resolved non-secret Compose model before starting it:

```bash
docker compose config
```

Then build local source and start the daemon:

```bash
docker compose up --detach --build
```

The required `SDS200_HOST` interpolation makes configuration fail before a
container is created when the scanner address is unset or empty.

## Persistent paths

The existing XDG path resolver produces these container paths:

| Purpose | Container path |
| --- | --- |
| Application configuration root | `/config/sdsctl/` |
| Destination manifest | `/config/sdsctl/daemon-destinations.toml` |
| MQTT manifest | `/config/sdsctl/daemon-mqtt.toml` |
| State root | `/state/sdsctl/` |
| Default daemon recordings | `/state/sdsctl/recordings/` |
| Cache root | `/cache/sdsctl/` |
| Private runtime sockets | `/run/sdsctl/` |

The Compose service uses named volumes for the three persistent XDG roots. New
volumes are initialized against image directories prepared for UID/GID `10001`;
the Compose runtime acceptance check verifies those mounted roots remain writable
by the unprivileged service account. The named volumes persist across normal
container replacement and `docker compose down`.

`docker compose down --volumes` removes the Compose-managed persistent volumes.
Treat that command as destructive when configuration, recordings, or other state
must be retained.

`/run/sdsctl/` is intentionally ephemeral. Daemon clients in the same container
can use the default socket resolution; multi-container socket sharing remains a
separate later Milestone 25 boundary.

For deployments that intentionally use bind mounts instead of the supported
Compose defaults, make the persistent roots writable by UID/GID `10001`. One
Linux example is:

```bash
sudo install -d -m 0750 -o 10001 -g 10001 \
  /srv/sds200/config \
  /srv/sds200/state \
  /srv/sds200/cache
```

## Linux host networking

The foreground daemon owns SDS200 network control plus one RTSP/RTP audio
session. Milestone 25.2 therefore retains Linux host networking so those existing
host-reachable network semantics are preserved without inventing bridge-network
callback behavior.

Host networking intentionally gives the container the host network stack. The
Compose service does not publish ports because port mapping is neither needed nor
part of this deployment model. Do not add a standalone web-dashboard process to
this service as a way to expose the dashboard remotely; its existing loopback-only
security policy remains unchanged.

## Health and status

The Dockerfile healthcheck is inherited by Compose and uses the daemon's private
Unix-domain API. Inspect health with:

```bash
docker compose ps
```

The same negotiated status can be queried manually inside the running service:

```bash
docker compose exec daemon sdsctl daemon-client status --json
```

A successful status query proves that the local daemon API is responding. It is
not a substitute for application-level scanner or audio diagnosis; the returned
runtime snapshot remains the authoritative source for scanner connectivity and
daemon state.

## Stop and restart behavior

`docker compose stop` sends the image's declared `SIGTERM` to PID 1. Because the
image executes `sdsctl` directly, the existing `DaemonSignalController` receives
that signal and the foreground daemon performs its established ordered cleanup.

The service uses `restart: unless-stopped`. Restart policy does not change the
daemon's own scanner reconnect, PSI recovery, destination reload, or MQTT retry
semantics.

Use:

```bash
docker compose down
```

to stop and remove the service while preserving the named persistent volumes.

## Milestone 25.4 boundary

Generic Docker Hub publication is established only for genuine matching release
tag pushes. Pull requests, pushes to `main`, and manually dispatched workflow
runs validate both supported platforms without authenticating or publishing.

This container work still does **not** establish:

- separate daemon-client or web-dashboard containers;
- remote or wildcard standalone web binding;
- bridge networking or explicit UDP/TCP port-mapping recipes;
- Linux USB serial passthrough or device-group permissions;
- broadly privileged container operation;
- Windows or macOS Docker behavior; or
- physical scanner validation of the generic Compose deployment.

Those remain separate Milestone 25 work. In particular, the existing standalone
web security boundary continues to reject wildcard, LAN, public, and non-local
hostname listeners outside the explicit Home Assistant Ingress mode.
