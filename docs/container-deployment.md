# Generic container deployment

Milestones 25.1 and 25.2 established the generic network-connected SDS200 daemon
image and its supported source-built Docker Compose deployment. Milestone 25.4
established release-tag publication of that standalone multi-platform image to
Docker Hub as `theboyd78/sdsctl`. Milestone 25.5 added an opt-in, one-shot
daemon-client sidecar over the daemon's existing private Unix-domain services.
Milestone 25.6 added the isolated web-dashboard container foundation. Milestone
25.7 adds host-local browser reachability through Docker bridge networking and
explicit host-loopback port publication. Milestone 25.8 adds a separate,
one-shot USB scanner CLI workflow for native Linux Docker Engine. Milestone 25.9
records physical acceptance of those existing generic-container paths without
introducing a new runtime architecture. Milestone 25.10 documents the
network-only Docker Desktop host-network prerequisite and its validation
boundary without changing repository-root Compose. Milestone 25.11 adds the
narrow native-Linux rootless Podman network-daemon path while leaving Compose,
USB, sidecar, and cross-platform Podman behavior separate.

Milestone 25.12 adds the separate direct native-Linux rootless Podman one-shot
USB path below Compose. Milestone 25.13 adds the external Compose-provider and
rootless API-socket foundation for using the existing network `compose.yaml`
with native-Linux rootless Podman while preserving the separate Docker-specific
USB Compose contract.

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

- `network_mode: host` retains the scanner-owning daemon's existing SDS200 UDP
  control plus RTSP/RTP audio semantics on native Linux and on supported Docker
  Desktop host networking;
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
docker compose run --rm daemon-client snapshot
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

## Milestone 25.9 physical validation

Physical acceptance was completed on 2026-08-19 on host `BIGBOSS`, running
Ubuntu 26.04 LTS (`linux x86_64`), native Docker Engine client/server 29.7.2,
and Docker Compose v5.4.0. The scanner was a physical SDS200 running firmware
Version 1.26.01.

### Network daemon and client

The root Compose daemon built and started against
`udp://192.168.0.251:50536`, became healthy, connected to the SDS200, identified
its model and firmware, and ran PSI at 500 ms. Daemon-owned RTSP/RTP audio ran
from `rtsp://192.168.0.251/au:scanner.au`. One-shot daemon-client `status
--json` and authoritative `snapshot` calls succeeded, and `events --count 1
--json` returned the required initial `stream.snapshot`.

The daemon remained the sole scanner, network, and audio owner. It ran as
unprivileged UID/GID `10001:10001` with `network_mode: host`, no privileged
mode, and no devices. The exact validation project and volumes were removed
afterward.

### USB one-shot CLI

The standalone `compose.usb.yaml` was validated with no competing host or
container daemon. It mapped only
`/dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00`, resolved
as `/dev/ttyACM0`, to `/dev/sdsctl-scanner` with `rwm`. The host device was
`root:dialout`, mode `0660`, numeric GID 20. The container remained
unprivileged UID/GID `10001:10001` and received supplemental GID 20 only for
that selected device; it could read and write the character device.

`usb-scanner info` identified SDS200 / Version 1.26.01, `scanner-info` succeeded
against the scanner, and `health` reported healthy and connected. Two separate
ephemeral `info` invocations produced identical application stdout. Every
`run --rm` invocation released the device and left no validation container.
Physical unplug/replug and USB re-enumeration were not tested. This evidence
does not establish a USB daemon or a network-audio path through USB.

### Web sidecar

The daemon and web-dashboard became healthy. The web container ran as UID/GID
`10001:10001` on ordinary Docker bridge networking, not host networking; it was
not privileged, had no devices or added capabilities, and mounted only the
runtime volume. Validation published exactly host
`127.0.0.1:18080` to container port `8000`.

`/healthz` succeeded, `/` returned HTTP 200 with the packaged HTML, and the
dashboard retained its Content Security Policy, `no-referrer`, `nosniff`, and
`X-Frame-Options: DENY` headers. `/api/v1/status` succeeded through the private
daemon socket and returned live SDS200, PSI, and daemon-owned audio state;
`/api/v1/snapshot` returned live SDS200 state. The exact validation project,
volumes, and bridge network were removed afterward. Interactive browser playback,
recording, scanner-control mutation, and browser UI interaction were not part of
this acceptance.

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

## Host networking and Docker Desktop

The foreground daemon owns SDS200 network control plus one RTSP/RTP audio
session. Repository-root Compose therefore continues to use
`network_mode: host` rather than introducing a second scanner-owner architecture
or changing the private daemon IPC boundary.

On native Linux Docker Engine, this is the existing host-network driver contract
physically accepted in Milestone 25.9. Docker Desktop 4.34 and later also
supports host networking for **Linux containers**, but it is an opt-in Desktop
feature. Before starting the generic daemon on Docker Desktop, enable:

**Settings → Resources → Network → Enable host networking → Apply and restart**

Docker documents host networking on Desktop as a layer-4 TCP/UDP feature. It
does not give a container direct access to host network interfaces or allow a
process to bind arbitrary host interface addresses, and it is incompatible with
Enhanced Container Isolation. The `sdsctl` daemon does not depend on direct
interface inspection: it uses normal IPv4 UDP control plus TCP RTSP and UDP RTP.
The daemon still publishes no Docker ports and remains unprivileged with no
device mapping.

See Docker's current
[host networking documentation](https://docs.docker.com/engine/network/drivers/host/)
for the Desktop prerequisite and limitations. Docker Desktop settings and
feature availability can change independently of `sdsctl`; validate the current
Docker documentation and Desktop UI rather than editing Desktop settings files
directly.

A reversible 2026-08-19 Docker Desktop for Linux 4.87.0 / Engine 29.7.2
experiment demonstrated that a host-networked Linux container could exchange
both TCP and UDP with Docker-host loopback after a Desktop restart. The settings
file no longer exposed an explicit `hostNetworkingEnabled` key after that
restart, so this records observed runtime behavior rather than persistent
settings-file state. Docker's documented UI prerequisite remains authoritative.
This is not a claim of end-to-end physical SDS200 validation on Docker Desktop.

During the same investigation, scanner UDP control and PSI worked from an
exploratory bridge-network daemon path, but the physical scanner's RTSP listener
on TCP 554 became persistently unavailable. The listener also refused native-host
connections and remained unavailable after a cold power cycle with Ethernet
removed and reconnected. Because the scanner-side RTSP service was unavailable
outside Docker as well, the investigation could not establish or reject a
bridge-NAT RTP callback design. No bridge-network daemon override, fixed RTP
publication, or other new generic Compose contract is adopted in Milestone
25.10.

Physical Windows and macOS Docker-host validation remains outstanding. This
milestone establishes the documented Docker Desktop prerequisite and preserves
the architecture needed for later physical validation; it does not claim that a
Windows or macOS host has completed SDS200 control/audio acceptance.

Docker Desktop USB access is a separate concern. Docker documents a USB/IP
workflow for Desktop, but that workflow is not equivalent to native Linux
`--device` passthrough and is not adopted by `compose.usb.yaml`. The supported
generic USB contract remains the native-Linux one-shot workflow documented
above. See Docker's
[USB/IP documentation](https://docs.docker.com/desktop/features/usbip/) only as
a separate operator workaround, not as part of the `sdsctl` USB contract.

The web-dashboard container remains on ordinary bridge networking with explicit
host-loopback publication. Do not move it onto host networking to expose the
dashboard remotely; its existing standalone exposure and security boundary is
unchanged. Generic LAN/public authentication and TLS termination remain
unsupported and deferred.

## Rootless Podman network daemon

Milestone 25.11 adds a separate native-Linux rootless Podman path for the
existing generic image and scanner-owning daemon. It does not turn
repository-root Compose into a Podman Compose contract and does not add a second
scanner-owner architecture.

Build the same repository Dockerfile explicitly in Docker image format:

```bash
podman build --format docker --tag sdsctl:local .
```

The explicit format matters. Rootless Podman 5.7.0 defaulted to OCI image format
during validation and warned that the Dockerfile `HEALTHCHECK` was unsupported;
the resulting image had no healthcheck metadata. Rebuilding with
`--format docker` preserved the existing private daemon-client healthcheck plus
the image's unprivileged `10001:10001` user, `sdsctl` entrypoint, `--help`
default command, and `SIGTERM` stop signal.

Run the scanner-owning daemon directly with host networking:

```bash
podman run --rm \
  --network host \
  sdsctl:local \
  --host SCANNER_IP daemon
```

Podman host networking uses the host network namespace. That is necessary here
to preserve the daemon's existing scanner-facing UDP control and RTSP/RTP
semantics, but it also weakens network-namespace isolation. In particular,
localhost-bound host services are reachable from the container. Do not describe
this as ordinary rootless network isolation, and do not use host networking for
the existing web sidecar as a way to expose the dashboard. See Podman's current
[run networking documentation](https://docs.podman.io/en/latest/markdown/podman-run.1.html)
for the host-network namespace and security semantics, and its
[build documentation](https://docs.podman.io/en/latest/markdown/podman-build.1.html)
for image-format options.

The validated 2026-08-20 host used Ubuntu 26.04 LTS, rootless Podman 5.7.0,
Netavark, cgroup v2 with the systemd manager, and crun 1.21. A disposable
host-network container exchanged both TCP and UDP with native-host loopback. The
unchanged generic Dockerfile then built successfully with `--format docker`, and
ephemeral SDS200 commands over `--network host` identified an SDS200 running
firmware 1.26.01 and returned live scanner state.

A bounded daemon startup under the same rootless Podman runtime opened
`udp://192.168.0.251:50536`, identified SDS200 / Version 1.26.01, enabled PSI at
500 ms, and received live PSI state before audio startup failed. The scanner's
TCP 554 RTSP listener had already refused connections from the native host before
that Podman daemon test. This establishes
physical rootless-Podman UDP control/PSI acceptance but does **not** establish
or reject Podman RTSP/RTP audio. Do not attribute that independently reproduced
scanner-side RTSP failure to Podman.

The Milestone 25.11 network contract intentionally stops below the Compose
layer. `podman compose` delegates to an external Compose provider, as documented
by Podman's current
[Compose documentation](https://docs.podman.io/en/latest/markdown/podman-compose.1.html),
so repository-root `compose.yaml` is not claimed as a supported Podman Compose
deployment. `compose.usb.yaml` also remains the native-Linux Docker Engine USB
Compose contract. Milestone 25.12 adds a separate direct rootless Podman USB path
below rather than redefining that file as a Podman Compose contract.
Windows/macOS Podman behavior and Docker Desktop USB/IP remain separate work;
physical Windows and macOS Docker validation remains outstanding.

## Rootless Podman USB scanner CLI

Milestone 25.12 adds a separate native-Linux rootless Podman one-shot USB serial
path for the existing generic image. It stays below the Compose layer:
`compose.usb.yaml` remains the native-Linux Docker Engine USB Compose contract,
and `podman compose` is not required or claimed here.

Build the same image using the Docker image format already required by the
rootless Podman network path:

```bash
podman build --format docker --tag sdsctl:local .
```

Select the stable scanner device when available and verify that the current
rootless operator can already read and write the host device:

```bash
export SDSCTL_USB_DEVICE=/dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00

test -c "$(readlink -f "$SDSCTL_USB_DEVICE")"
test -r "$SDSCTL_USB_DEVICE"
test -w "$SDSCTL_USB_DEVICE"
```

Run one-shot scanner commands with no container networking, only the selected
device, the image healthcheck disabled, and the rootless operator's
supplementary-group access preserved:

```bash
podman run --rm \
  --network none \
  --health-cmd none \
  --device "$SDSCTL_USB_DEVICE:/dev/sdsctl-scanner:rwm" \
  --group-add keep-groups \
  sdsctl:local \
  --port /dev/sdsctl-scanner \
  info
```

The same bounded pattern supports other standalone scanner CLI actions such as
`scanner-info` and `health`. It does not start or replace the daemon, publish
ports, create a scanner network path, or provide SDS200 network audio.

Podman's current
[create documentation](https://docs.podman.io/en/latest/markdown/podman-create.1.html)
documents the rootless device and supplementary-group behavior used here. In
rootless mode Podman bind-mounts the selected host device rather than creating a
new device node. If the rootless operator has access only through a
supplementary host group, `--group-add keep-groups` passes that access through
to the container process. Podman currently documents `keep-groups` as available
only with the crun OCI runtime and unavailable to remote commands, including
macOS and Windows remote clients except WSL2. The numeric group shown inside the
user namespace need not equal the host device's numeric GID; the contract is
preserved access, not preserved numeric identity.

Do not work around host permission failures by running privileged, mapping all
of `/dev`, changing the scanner device to `0666`, or baking a host `dialout` GID
into the image. The rootless operator must already have legitimate host access
to the selected device. Podman's rootless device documentation also describes
additional SELinux policy considerations; the 25.12 Ubuntu validation host did
not have SELinux enabled, so SELinux device-policy acceptance is not claimed by
this milestone.

Physical acceptance on 2026-08-20 used Ubuntu 26.04 LTS, rootless Podman 5.7.0,
the crun runtime already established in Milestone 25.11, and an SDS200 running
firmware Version 1.26.01. The stable by-id path resolved to `/dev/ttyACM0`,
owned by `root:dialout` with mode `0660` and host numeric GID 20; the rootless
operator belonged to that supplementary group.

A permission-only experiment first mapped the selected device into the
unprivileged `10001:10001` image process. Without `--group-add keep-groups`,
the process could neither read nor write the device. With `keep-groups`, the
same process could read and write it without privileged mode, a broad device
mount, or any host permission change.

Four separate `--rm` containers then ran `info`, `scanner-info`, `health`, and
a repeated `info` with `--network none`. They identified SDS200 / Version
1.26.01, returned live scanner state, reported healthy connected serial
transport, and demonstrated clean device release and reacquisition between
ephemeral invocations. The host device remained `root:dialout` mode `0660`, and
no validation containers remained. USB unplug/replug and re-enumeration were not
tested.

This establishes physical rootless Podman USB serial acceptance for the
one-shot scanner CLI only. It does not establish a USB daemon, RTSP/RTP or other
network audio through USB, Podman Compose, daemon-client/web Podman sidecars,
SELinux device-policy acceptance, or Windows/macOS remote Podman USB behavior.

## Rootless Podman Compose provider

Milestone 25.13 adds a native-Linux rootless Podman Compose-provider foundation
for the existing generic network `compose.yaml`. It does not introduce a second
Compose file or a Podman-specific scanner-owner architecture. The existing
daemon, opt-in daemon-client, web-dashboard, named-volume, host-network, and
loopback-publication definitions remain the same repository contracts.

Podman's `podman compose` command is a thin wrapper around an external Compose
provider rather than an independent Compose implementation. Provider selection
is therefore part of the deployment environment. On the 2026-08-20 validation
host, `podman compose version` selected the installed
`~/.docker/cli-plugins/docker-compose` provider and reported Docker Compose
v5.4.0. Other installations may select a different provider, so do not assume
that exact executable or version without checking the local host.

The observed Docker Compose provider communicates with rootless Podman through
Podman's Docker-compatible API. Podman's default rootless service socket uses
the path `/run/user/$UID/podman/podman.sock`. On the validation host, Compose
model parsing worked while the service was stopped, but provider-backed build
and runtime operations failed until the user socket was started. Start it for
the current login session with:

```bash
systemctl --user start podman.socket
podman compose version
```

`systemctl --user start podman.socket` does not enable the unit persistently.
Operators who choose persistent socket activation should make that an explicit
host-service decision rather than treating it as an image or repository
requirement. Do not publish the Podman API over an unauthenticated TCP listener.
A filesystem socket inode may remain after the unit stops; verify service state
rather than assuming that the pathname alone means an API server is listening.

With a non-secret TEST-NET scanner value used only to satisfy Compose
interpolation, validate the unchanged network model without contacting scanner
hardware:

```bash
SDS200_HOST=192.0.2.10 podman compose -f compose.yaml config
SDS200_HOST=192.0.2.10 podman compose -f compose.yaml build daemon-client
SDS200_HOST=192.0.2.10 podman compose -f compose.yaml run --rm daemon-client --help
```

The 2026-08-20 scanner-independent acceptance completed all three operations.
The provider built the existing Dockerfile through rootless Podman, launched
the isolated daemon-client service, created its Compose-labeled runtime volume,
and removed the ephemeral client container normally. `podman compose ... ps
--all` also succeeded, and `down --volumes --remove-orphans` removed the
dedicated validation resources.

For a real scanner deployment, replace the interpolation value with the trusted
scanner address before starting the existing daemon service:

```bash
export SDS200_HOST=SCANNER_IP
podman compose -f compose.yaml up --detach --build daemon
podman compose -f compose.yaml ps --all
podman compose -f compose.yaml logs daemon
```

Physical SDS200 acceptance on 2026-08-20 used `192.168.0.251`, firmware Version
1.26.01, rootless Podman 5.7.0, Netavark, crun 1.21, and Docker Compose v5.4.0
as the external provider. The Compose-managed daemon repeatedly opened physical
UDP control on port 50536 and started the 500 ms PSI stream through
`network_mode: host`. This confirms provider-backed container creation, host
networking, scanner UDP control, and PSI behavior through the existing Compose
model.

Full Podman Compose daemon readiness was not accepted during that run. The
scanner's RTSP startup at `rtsp://192.168.0.251/au:scanner.au` failed before
the private daemon socket became stable, and the existing
`restart: unless-stopped` contract correctly produced a restart loop. A separate
native-host test with the Podman API inactive independently received
`Connection refused` from native TCP port 554 in about 5 ms. Because the same
failure existed with no container involved, the evidence does not establish a
Podman Compose RTSP/RTP defect. Physical
Podman Compose daemon-client `status --json` and `snapshot` acceptance
remains deferred until the scanner's RTSP endpoint is available again.

Milestone 25.13 established only parse compatibility for the standalone
`compose.usb.yaml`. At that point its numeric `group_add` entry remained the
native-Linux Docker Engine USB Compose contract, while the rootless Podman host
still required supplementary host-group preservation. Milestone 25.15 closes
that runtime gap using the OCI annotation documented below while retaining the
numeric Docker-oriented group contract.

Milestone 25.13 did not claim alternate external Compose providers,
web-dashboard sidecar acceptance under Podman, physical daemon-client
acceptance under Podman Compose, Podman Compose USB runtime, SELinux socket
or device-policy acceptance, remote Podman on Windows or macOS, or
successful RTSP/RTP audio while the scanner's native TCP port 554 is
unavailable.

### Rootless Podman Compose web-dashboard sidecar

Milestone 25.14 closes the scanner-independent native-Linux rootless Podman
Compose boundary for the existing `web-dashboard` profile. The 2026-08-20
acceptance host remained Ubuntu 26.04 LTS with rootless Podman 5.7.0, Netavark,
cgroup v2, crun 1.21, and Docker Compose v5.4.0 as the external provider. As in
Milestone 25.13, provider-backed runtime operations required the rootless Podman
Docker-compatible API socket to be active.

A non-secret TEST-NET scanner value can satisfy the repository-wide Compose
interpolation while starting only the web sidecar:

```bash
SDS200_HOST=192.0.2.10 SDSCTL_WEB_PORT=18081 \
  podman compose -f compose.yaml --profile web up --detach --build web-dashboard
```

This command does not implicitly start the daemon. Physical acceptance confirmed
that no daemon container existed in the validation project. The web container
ran as UID/GID `10001:10001`, used ordinary bridge networking, remained
unprivileged, had no added capabilities or mapped devices, mounted only
`/run/sdsctl`, and published container TCP 8000 only to
`127.0.0.1:18081` on the host.

The scanner-independent web surface behaved as designed. `/healthz` and `/`
returned HTTP 200, and the packaged root response retained the Content Security
Policy, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, and
`X-Frame-Options: DENY` headers. With no daemon service present,
`/api/v1/status` returned HTTP 503 with the existing daemon-unavailable error
contract. This verifies the sidecar-local listener, static application,
loopback publication, and daemon-absence boundary without claiming scanner
connectivity.

The acceptance run also found a portability issue in the original web
healthcheck encoding. The Compose file used exec-form `CMD` with `python -c`
followed by a Python expression. Through the observed Podman-compatible API
runtime, that expression was split into separate healthcheck arguments and
Python received only `import`, producing `SyntaxError` even though `/healthz`
itself returned HTTP 200. The repository therefore uses `CMD-SHELL` for this
web-only healthcheck so the complete Python expression remains one command.
A temporary proof of that exact encoding transitioned the Podman container to
`healthy` on its normal scheduled probe and made `podman healthcheck run`
succeed. The healthcheck still contacts only
`http://127.0.0.1:8000/healthz` inside the web container.

This acceptance does not establish the daemon-backed web APIs under Podman
Compose. Live status, snapshot, scanner-control mutation, event streaming,
audio, recording, and recording-file paths remain dependent on a ready daemon
and are deferred while the physical scanner's native RTSP TCP port 554 remains
unavailable. Physical daemon-client acceptance under Podman Compose, full
RTSP/RTP acceptance, SELinux socket or device-policy acceptance, remote Podman
on Windows/macOS, and alternate Compose providers remain separate work. Native
systemd remains preferred where broader direct host-device, local-audio, or
operating-system integration is important.

### Rootless Podman Compose USB runtime

Milestone 25.15 closes the native-Linux one-shot USB runtime boundary for the
existing `compose.usb.yaml` through the same external Docker Compose provider
used by Milestones 25.13 and 25.14. Provider-backed runtime operations require
the rootless Podman Docker-compatible API socket to be active.

The Compose file intentionally keeps both parts of the cross-runtime contract.
`SDSCTL_USB_GID` remains the ordinary numeric `group_add` value used by the
native-Linux Docker Engine path, while the service now also carries
`run.oci.keep_original_groups: "1"` as an OCI container annotation. Rootless
Podman with crun consumes that annotation to preserve the invoking operator's
supplementary host groups without requiring privileged mode or changing scanner
device permissions.

Set the stable device path and derive its current host group rather than baking a
distribution-specific `dialout` number into the repository:

    export SDSCTL_USB_DEVICE=/dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00
    export SDSCTL_USB_GID="$(stat -Lc '%g' "$(readlink -f "$SDSCTL_USB_DEVICE")")"

Then resolve and run the same Compose model:

    podman compose -f compose.usb.yaml config
    podman compose -f compose.usb.yaml run --rm usb-scanner info
    podman compose -f compose.usb.yaml run --rm usb-scanner scanner-info
    podman compose -f compose.usb.yaml run --rm usb-scanner health

Do not replace the annotation with `group_add: keep-groups` when using the
Docker Compose external provider. Physical investigation showed that the
Docker-compatible API treated that value as an ordinary container group name
rather than Podman's special `--group-add keep-groups` CLI behavior. Numeric
`group_add` alone also parsed successfully but could not open the physical
scanner from the rootless `10001:10001` process.

Physical acceptance on 2026-08-20 used Ubuntu 26.04 LTS, rootless Podman 5.7.0,
Netavark, cgroup v2, crun 1.21, Docker Compose v5.4.0, and an SDS200 running
firmware Version 1.26.01. The stable by-id path resolved to `/dev/ttyACM0`;
the device remained `root:dialout` mode `0660` with host GID 20, and the
operator already had legitimate host access through that supplementary group.

Separate ephemeral Compose containers successfully ran `info`, `scanner-info`,
`health`, and a repeated `info`. The repeated command demonstrated clean device
release and reacquisition, all `--rm` containers disappeared after each command,
and cleanup left no project containers or images. The service remained
unprivileged, retained `network_mode: none`, mapped only the selected scanner
device, and did not modify host ownership or mode.

This remains a one-shot scanner CLI boundary. It does not establish a USB daemon,
daemon-client or web sidecars over USB, RTSP/RTP or other network audio through
USB, unplug/replug or USB re-enumeration behavior, SELinux device-policy
acceptance, remote Podman USB on Windows/macOS, or alternate Compose providers.

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
request to `http://127.0.0.1:8000/healthz` inside its own container. The
Compose test uses `CMD-SHELL` so the complete `python -c` expression is
preserved across Docker Compose and the rootless Podman Docker-compatible API
runtime. This is a local web-process health check. The `/healthz` route
intentionally does not contact the daemon, so a healthy result does not prove
daemon, scanner, event, audio, or recording-file availability.

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

## Milestone 25.12 boundary

The supported Docker sidecar workflows remain negotiated daemon API status and
snapshot, safe semantic scanner controls, and bounded consumption of the
daemon's ordered event stream. The supported Docker web-dashboard foundation
still consumes the daemon API, event, PCMU, and recording-file sockets without
changing their private Unix-domain transport. Milestone 25.12 does not claim
those sidecars under Podman.

Repository-root `compose.yaml` and `compose.usb.yaml` remain Docker Compose
contracts. Milestone 25.12 does not turn either file into a Podman Compose
contract and does not add or modify a Compose service.

Milestone 25.11's native-Linux rootless Podman network-daemon foundation remains
unchanged:

- rootless Podman 5.7.0 with Netavark and crun exercised host-network TCP/UDP
  reachability on Ubuntu 26.04 LTS;
- the supported build command uses `--format docker` so the existing Dockerfile
  healthcheck is preserved;
- physical SDS200 validation established host-network UDP control, identity,
  scanner-info, daemon identity probing, and PSI; and
- Podman RTSP/RTP acceptance remains unproven because the scanner's TCP 554 RTSP
  listener was independently unavailable from the native host before that
  Podman daemon attempt.

Milestone 25.12 adds only the native-Linux rootless Podman one-shot USB serial
foundation:

- only the selected scanner character device is mapped to
  `/dev/sdsctl-scanner`, with container networking disabled;
- the rootless operator must already have host read/write access to that device;
- `--group-add keep-groups` preserves supplementary-group device access through
  the validated crun runtime without privileged mode or broad `/dev` access;
- physical SDS200 firmware 1.26.01 validation established `info`,
  `scanner-info`, `health`, and repeated device reacquisition through separate
  ephemeral containers; and
- the host device remained `root:dialout` mode `0660`, with no validation
  containers left behind.

Podman Compose providers, daemon-client/web sidecars under Podman, USB
unplug/replug or re-enumeration behavior, SELinux device-policy acceptance,
Windows/macOS remote Podman USB, Docker Desktop USB/IP, physical Windows/macOS
Docker validation, daemon-backed TUI sidecars, arbitrary remote/LAN standalone
web exposure, generic LAN/public web publication, and authentication/TLS
termination remain outside this slice. Native systemd remains preferred when
broader direct host-device, local-audio, or operating-system integration
matters.
