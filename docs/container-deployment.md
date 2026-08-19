# Generic container deployment

Milestones 25.1 and 25.2 established the generic network-connected SDS200 daemon
image and its supported source-built Docker Compose deployment. Milestone 25.4
established release-tag publication of that standalone multi-platform image to
Docker Hub as `theboyd78/sdsctl`. Milestone 25.5 added an opt-in, one-shot
daemon-client sidecar over the daemon's existing private Unix-domain services.
Milestone 25.6 added the isolated web-dashboard container foundation. Milestone
25.7 adds host-local browser reachability through Docker bridge networking and
explicit host-loopback port publication. Milestone 25.8 adds a separate,
one-shot USB scanner CLI workflow for native Linux Docker Engine.

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

No TCP port is exposed by the image metadata. The standalone web dashboard
invoked by `sdsctl web` remains loopback-only; Compose publication is an
explicit service-level contract.

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
- no daemon ports, privileged mode, or scanner device mapping are added.

The `daemon-client` service uses the `client` profile, so ordinary
`docker compose up --detach --build` does not start it. It overrides the image
entrypoint with `sdsctl daemon-client`, disables the inherited daemon
healthcheck, uses `network_mode: none`, and mounts only the runtime transport
volume. It has no restart policy, dependency on the daemon service, durable XDG
mounts, published or exposed ports, devices, added capabilities, or privileged
mode. It therefore cannot independently reach the scanner or remote services.

The `web-dashboard` service similarly uses the `web` profile, so ordinary
`docker compose up --detach --build` still starts only the unprofiled daemon.
It overrides the entrypoint with `sdsctl web --container-exposure`, uses ordinary
Docker bridge networking, and binds exactly `0.0.0.0:8000` inside the container.
Compose publishes exactly
`127.0.0.1:${SDSCTL_WEB_PORT:-8000}:8000`, so LAN and public clients cannot
reach it by default. The service has no `expose` entry, devices, privileged
mode, added capabilities, scanner host argument, durable XDG mounts, or
`depends_on`. Its
`restart: unless-stopped` policy is appropriate for a long-running dashboard
process and does not give it ownership of the separately started daemon.

Compose requires `SDS200_HOST` and inserts it into the existing global `--host`
CLI option before the `daemon` subcommand. `SDS200_LOG_LEVEL` is optional and
defaults to `INFO`. `SDSCTL_WEB_PORT` optionally selects the Docker-host
loopback publication port and defaults to 8000; it does not change the fixed
container listener port 8000. These are Compose interpolation variables, not
application configuration settings.

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

To exercise the supported Milestone 25.7 web lifecycle, start the daemon first,
then explicitly activate the web profile and service:

```bash
docker compose up --detach --build daemon
docker compose --profile web up --detach --build web-dashboard
docker compose ps web-dashboard
docker compose logs web-dashboard
```

Open `http://127.0.0.1:8000/` by default. To select another host-loopback port,
set `SDSCTL_WEB_PORT`, recreate the service, and open that port instead. The web
process consumes the daemon API, ordered-event, PCMU, and finalized
recording-file Unix sockets from the shared runtime volume. The daemon remains
the sole scanner, network control, RTSP/RTP, socket-production, and audio owner.
Do not use host networking for the web service. Generic exposure does not enable
Home Assistant Ingress or its middleware; Ingress remains a separate Supervisor
peer-guarded mode.

The internal `0.0.0.0` wildcard is safe only because Compose constrains
publication to `127.0.0.1` on the Docker host. Do not copy
`--container-exposure` into arbitrary LAN/public publication without a separate
authentication and TLS design. Standalone arbitrary remote/LAN exposure remains
unsupported and deferred.

## Native Linux USB scanner CLI

The standalone `compose.usb.yaml` is the supported Milestone 25.8
Linux USB serial passthrough workflow. Select it explicitly and use it on its
own; do not layer it on `compose.yaml`. The root file remains the network-SDS200
daemon, daemon-client, web-dashboard, socket, and network-audio deployment. The
USB service does not start or replace the daemon, exposes no Unix socket, and
provides no SDS200 network audio path.

This workflow requires native Linux Docker Engine with access to the host
character device. A VM-backed Docker Desktop Linux context observed during
validation could not see the host's `/dev/ttyACM0` even though the host shell
could, so direct host USB passthrough through that context is not a supported
25.8 path. Rootless Podman has different supplemental-group preservation
semantics and is not part of this Docker Compose contract.

Prefer the scanner's stable `/dev/serial/by-id/...` symlink as the Docker device
source. Native Docker Engine accepts that symlink directly; retain it rather
than replacing it with its `/dev/ttyACM*` target. A `/dev/ttyACM*` path is an
explicit fallback, but it can change after reconnect or device re-enumeration.
Set the supplemental GID from the resolved character device while retaining the
stable path as the mapping source:

```bash
export SDSCTL_USB_DEVICE=/dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00
export SDSCTL_USB_GID="$(stat -Lc '%g' "$SDSCTL_USB_DEVICE")"

test -c "$(readlink -f "$SDSCTL_USB_DEVICE")"
test -r "$SDSCTL_USB_DEVICE"
test -w "$SDSCTL_USB_DEVICE"
```

The `stat -L` behavior is important because permissions and ownership belong to
the resolved character device, not the symlink. The tests above verify that the
source resolves to a character device and that the current host operator can
read and write it. If host policy assigns the device to a group other than
`dialout`, use the device's actual numeric GID; do not assume GID 20.

Validate interpolation without creating a container, then run an explicit
one-shot command:

```bash
docker compose -f compose.usb.yaml config
docker compose -f compose.usb.yaml run --rm usb-scanner info
docker compose -f compose.usb.yaml run --rm usb-scanner scanner-info
docker compose -f compose.usb.yaml run --rm usb-scanner health
docker compose -f compose.usb.yaml run --rm usb-scanner monitor
```

Both `SDSCTL_USB_DEVICE` and `SDSCTL_USB_GID` are required Compose interpolation
variables. If either is unset or empty, `docker compose ... config` and `run`
fail during model interpolation before container creation. Normal arguments
after `usb-scanner` become top-level scanner CLI actions behind the fixed
`sdsctl --port /dev/sdsctl-scanner` entrypoint.

The service maps exactly the selected source to `/dev/sdsctl-scanner`, uses
`network_mode: none`, disables the image's daemon healthcheck, and adds only the
operator-supplied numeric device GID to the image's unprivileged UID/GID
`10001:10001`. It has no restart policy, ports, capabilities, privileged mode,
durable XDG or runtime volumes, daemon dependency, scanner network host, or
broad device-directory mount. Do not map all of `/dev`, run privileged, change
the host node to `0666`, or bake a hard-coded `dialout` GID into the image.
The existing udev `uaccess` plus `dialout`/`0660` fallback remains host policy;
the container receives only the selected device and its numeric group.

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

## Milestone 25.8 boundary

The supported sidecar workflows remain negotiated daemon API status and snapshot,
safe semantic scanner controls, and bounded consumption of the daemon's ordered
event stream. The supported web-dashboard foundation adds consumption of the
daemon API, event, PCMU, and recording-file sockets without changing their
private Unix-domain transport. No daemon IPC is exposed over TCP. The standalone
web listener still rejects wildcard, LAN, public, and non-local hostname
listeners. Only the explicit generic-container mode owns the internal wildcard,
and it neither uses nor repurposes Home Assistant Ingress.

The standalone USB service establishes only direct, model-neutral serial-safe
CLI commands on native Linux Docker Engine. It is not a USB daemon and does not
weaken the network-only generic daemon boundary. This container work still does
**not** establish:

- a daemon-backed TUI sidecar;
- arbitrary remote/LAN standalone web exposure;
- generic LAN/public web publication or authentication/TLS termination;
- broadly privileged container operation;
- Windows or macOS Docker behavior; or
- physical scanner validation of the generic Compose deployment.

The remaining items stay deferred to separate Milestone 25 work. In particular,
the existing standalone
web security boundary continues to reject wildcard, LAN, public, and non-local
hostname listeners outside the explicit Home Assistant Ingress mode.
