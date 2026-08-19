# Generic container deployment

Milestones 25.1 and 25.2 established the generic network-connected SDS200 daemon
image and its supported source-built Docker Compose deployment. Milestone 25.4
established release-tag publication of that standalone multi-platform image to
Docker Hub as `theboyd78/sdsctl`. Milestone 25.5 added an opt-in, one-shot
daemon-client sidecar over the daemon's existing private Unix-domain services.
Milestone 25.6 adds an opt-in, long-running but network-isolated web-dashboard
container foundation. It does not make the dashboard reachable from a host
browser; that boundary remains deferred to Milestone 25.7.

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

- builds the local source into wheels and installs `sds200[mqtt,web]`;
- runs `sdsctl` as unprivileged UID/GID `10001`;
- sets `XDG_CONFIG_HOME=/config`, `XDG_STATE_HOME=/state`,
  `XDG_CACHE_HOME=/cache`, and `XDG_RUNTIME_DIR=/run`;
- keeps daemon API, event, PCMU, and recording-file sockets private under
  `/run/sdsctl/`;
- leaves persistent mounts operator-controlled instead of declaring image-level
  `VOLUME` paths, avoiding anonymous durable mounts in client services;
- uses `SIGTERM` as the container stop signal; and
- checks daemon health with the existing private API through
  `sdsctl daemon-client status --json`.

The image has `ENTRYPOINT ["sdsctl"]` and a safe `CMD ["--help"]`. Starting the
image without an explicit scanner command therefore does not acquire scanner
ownership.

No TCP port is exposed by this image. The standalone web dashboard remains
loopback-only and is not made remotely reachable by Milestone 25.6.

## Docker Compose contract

The repository-root `compose.yaml` defines `daemon` plus opt-in `daemon-client`
and `web-dashboard` services. All use `build: { context: . }` to build the
repository-root image, so a checked-out source tree is sufficient to build and
use them. This remains
distinct from the published standalone `theboyd78/sdsctl` image: Compose does
not contain `image:` and does not select a Docker Hub tag.

The service preserves the Milestone 25.1 runtime contract:

- `network_mode: host` retains Linux host-network reachability for SDS200 UDP
  control plus the existing RTSP/RTP audio path;
- `restart: unless-stopped` supplies the documented container restart policy;
- `/config`, `/state`, and `/cache` are backed by Compose named volumes;
- the image's `SIGTERM` stop signal and private Unix-domain healthcheck are
  inherited unchanged;
- `/run/sdsctl/` is backed by a dedicated transport volume shared only with the
  daemon-client sidecar; and
- no ports, wildcard web binding, privileged mode, or scanner device mapping are
  added.

The `daemon-client` service uses the `client` profile, so ordinary
`docker compose up --detach --build` does not start it. It overrides the image
entrypoint with `sdsctl daemon-client`, disables the inherited daemon
healthcheck, uses `network_mode: none`, and mounts only the runtime transport
volume. It has no restart policy, dependency on the daemon service, durable XDG
mounts, published or exposed ports, devices, added capabilities, or privileged
mode. It therefore cannot independently reach the scanner or remote services.

The `web-dashboard` service similarly uses the `web` profile, so ordinary
`docker compose up --detach --build` still starts only the unprofiled daemon.
It overrides the entrypoint with `sdsctl web`, deliberately uses ordinary
standalone web mode rather than `--home-assistant-ingress`, and does not pass a
`--listen-address`. The existing secure default therefore binds only
`127.0.0.1:8000` inside its own `network_mode: none` container. The service has
no `ports` or `expose` entries, devices, privileged mode, added capabilities,
scanner host argument, durable XDG mounts, or `depends_on`. Its
`restart: unless-stopped` policy is appropriate for a long-running dashboard
process and does not give it ownership of the separately started daemon.

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

Then build local source and start the daemon explicitly:

```bash
docker compose up --detach --build daemon
```

The required `SDS200_HOST` interpolation makes configuration fail before a
container is created when the scanner address is unset or empty.

After the daemon is running, invoke supported client commands on demand:

```bash
docker compose run --rm daemon-client status --json
docker compose run --rm daemon-client snapshot --json
docker compose run --rm daemon-client hold TGID 12345
docker compose run --rm daemon-client next TGID 12345 --count 1
docker compose run --rm daemon-client previous TGID 12345 --count 1
docker compose run --rm daemon-client reconnect
docker compose run --rm daemon-client events --count 10 --json
```

Compose activates the service targeted by `run` even though it is assigned to
the `client` profile. The sidecar has no implicit `depends_on`: it neither starts
nor owns the daemon. If the daemon is absent or its private service is not ready,
the command fails through the existing daemon-client error contract. Scanner
controls are requested by the sidecar but executed by the daemon owner through
its existing safe semantic control dispatcher; the sidecar never opens scanner
hardware or creates another scanner control or RTSP/RTP session.

To exercise the supported Milestone 25.6 web lifecycle, start the daemon first,
then explicitly activate the web profile and service:

```bash
docker compose up --detach --build daemon
docker compose --profile web up --detach --build web-dashboard
docker compose ps web-dashboard
docker compose logs web-dashboard
```

The web process consumes the daemon API, ordered-event, PCMU, and finalized
recording-file Unix sockets from the shared runtime volume. The daemon remains
the sole scanner, network control, RTSP/RTP, socket-production, and audio owner.
There is intentionally no host browser URL in Milestone 25.6: a listener on
`127.0.0.1` inside a network-disabled container with no published or exposed
port is unreachable from the host and other containers. Do not use host
networking or `--home-assistant-ingress` to bypass this boundary. Actual
dashboard reachability, explicit wildcard binding inside the container, bridge
networking, and explicit host port publication are Milestone 25.7 work.

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

The daemon service uses named volumes for the three persistent XDG roots. New
volumes are initialized against image directories prepared for UID/GID `10001`;
the Compose runtime acceptance check verifies those mounted roots remain writable
by the unprivileged service account. The named volumes persist across normal
container replacement and `docker compose down`.

The separate `runtime` named volume is transport state, not durable application
data. It is mounted at `/run/sdsctl` by the daemon, daemon-client, and
web-dashboard services and nowhere else. The
daemon remains the sole producer and owner of `daemon.sock`, `events.sock`,
`pcmu.sock`, and `recordings.sock`. Because all three containers run as UID/GID
`10001`, the clients can traverse the daemon-owned `0700` directory and connect
to its `0600` sockets without widening permissions. No TCP daemon API is
introduced. A named volume can retain Unix-socket directory entries across
container replacement; the daemon's existing startup logic remains authoritative
for safe stale-socket cleanup.

Normal persistent configuration, state, recordings, and cache semantics are
unchanged. `docker compose down --volumes` removes both the durable named
volumes and the runtime transport volume. Treat that command as destructive
when configuration, recordings, or other state must be retained.

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

The web-dashboard overrides that inherited check with a Python-standard-library
request to `http://127.0.0.1:8000/healthz` inside its own container. This is a
local web-process health check. The `/healthz` route intentionally does not
contact the daemon, so a healthy result does not prove daemon, scanner, event,
audio, or recording-file availability.

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

## Milestone 25.6 boundary

The supported sidecar workflows remain negotiated daemon API status and snapshot,
safe semantic scanner controls, and bounded consumption of the daemon's ordered
event stream. The supported web-dashboard foundation adds consumption of the
daemon API, event, PCMU, and recording-file sockets without changing their
private Unix-domain transport. No daemon IPC is exposed over TCP. The standalone
web listener still rejects wildcard, LAN, public, and non-local hostname
listeners outside explicitly guarded Home Assistant Ingress mode, which this
generic service does not use or repurpose.

This container work still does **not** establish:

- a daemon-backed TUI sidecar;
- browser reachability from the host or other containers;
- remote or wildcard generic standalone web binding;
- bridge networking or explicit UDP/TCP port-mapping recipes;
- Linux USB serial passthrough or device-group permissions;
- broadly privileged container operation;
- Windows or macOS Docker behavior; or
- physical scanner validation of the generic Compose deployment.

Those remain separate Milestone 25 work. In particular, the existing standalone
web security boundary continues to reject wildcard, LAN, public, and non-local
hostname listeners outside the explicit Home Assistant Ingress mode.
