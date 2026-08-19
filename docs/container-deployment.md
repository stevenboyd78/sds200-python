# Generic container deployment

Milestone 25.1 adds the first generic container packaging foundation for the
existing foreground `sdsctl daemon`. This slice is intentionally narrower than
the complete Milestone 25 container plan: it packages the network-connected
SDS200 daemon on Linux without changing scanner ownership, daemon IPC, web
binding, or transport behavior.

Native systemd deployment remains the preferred production option when direct
host-device, local-audio, or other operating-system integration is important.

## Image contract

Build the generic image from the repository root:

```bash
docker build --tag sds200-daemon .
```

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
loopback-only and is not made remotely reachable by Milestone 25.1.

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

`/run/sdsctl/` is intentionally ephemeral. Daemon clients in the same container
can use the default socket resolution; future multi-container socket-sharing
belongs to a later Milestone 25 slice.

For bind mounts, make the persistent roots writable by UID/GID `10001`. One
Linux example is:

```bash
sudo install -d -m 0750 -o 10001 -g 10001 \
  /srv/sds200/config \
  /srv/sds200/state \
  /srv/sds200/cache
```

Do not put resolved credentials in the image, command line, logs, traces, or
captures. Existing configuration may continue to reference secrets through
environment variables.

## Initial Linux network-daemon workflow

The current foreground daemon owns SDS200 network control plus one RTSP/RTP
audio session. Milestone 25.1 therefore documents Linux host networking as the
initial container execution model so those existing host-reachable network
semantics are preserved without inventing bridge-network callback behavior.
Use `--network host` for this initial Linux workflow.

Use a documentation/example scanner address appropriate for your deployment;
the example below uses TEST-NET-1 rather than a real scanner address:

```bash
docker run --detach \
  --name sds200-daemon \
  --restart unless-stopped \
  --network host \
  --mount type=bind,src=/srv/sds200/config,dst=/config \
  --mount type=bind,src=/srv/sds200/state,dst=/state \
  --mount type=bind,src=/srv/sds200/cache,dst=/cache \
  sds200-daemon \
  --log-level INFO \
  --host 192.0.2.10 \
  daemon
```

Global options such as `--log-level` and `--host` precede `daemon`; daemon
options follow it, exactly as in the native CLI.

This image does not require `--privileged` and Milestone 25.1 does not recommend
adding scanner devices to the container.

## Health and status

The Dockerfile health check uses the daemon's private Unix-domain API. Inspect
the container health state with:

```bash
docker inspect --format '{{json .State.Health}}' sds200-daemon
```

The same negotiated status can be queried manually inside the running container:

```bash
docker exec sds200-daemon sdsctl daemon-client status --json
```

A successful status query proves that the local daemon API is responding. It is
not a substitute for application-level scanner or audio diagnosis; the returned
runtime snapshot remains the authoritative source for scanner connectivity and
daemon state.

## Stop and restart behavior

`docker stop` sends the image's declared `SIGTERM` to PID 1. Because the image
executes `sdsctl` directly, the existing `DaemonSignalController` receives that
signal and the foreground daemon performs its established ordered cleanup.

Use a bounded container stop timeout that gives recordings and local service
workers time to finalize. The image does not add another supervisor or signal
translation layer.

The example uses Docker's `unless-stopped` restart policy. Restart policy does
not change the daemon's own scanner reconnect, PSI recovery, destination reload,
or MQTT retry semantics.

## Milestone 25.1 boundary

This foundation does **not** establish:

- Docker Compose workflows;
- separate daemon-client or web-dashboard containers;
- remote or wildcard standalone web binding;
- bridge networking or explicit UDP/TCP port-mapping recipes;
- Linux USB serial passthrough or device-group permissions;
- broadly privileged container operation;
- published generic image tags or registry automation;
- Windows or macOS Docker behavior; or
- physical scanner validation of the generic image.

Those remain separate Milestone 25 work. In particular, the existing standalone
web security boundary continues to reject wildcard, LAN, public, and non-local
hostname listeners outside the explicit Home Assistant Ingress mode.
