# Daemon deployment and upgrade guide

This guide covers installation, configuration, systemd operation, local-client
access, upgrades, and rollback for the foreground `sdsctl daemon` introduced
during Milestone 19.

The daemon owns one SDS200 control connection, one PSI stream, one RTSP/RTP
audio session, one decoded-PCM router, four private local sockets, the
daemon-owned recording workflow, and any saved playback, recording, or
remote-stream destinations. It remains in the foreground and is intended to be
supervised by systemd or another process manager.

## Compatibility and migration

The v0.19.0 release preserves these existing names and files:

- the Python distribution remains `sds200`;
- the import package remains `sds200`;
- the command remains `sdsctl`;
- saved scanner connection profiles remain under the legacy `sds200`
  configuration root;
- saved remote-audio profiles remain under the legacy `sds200`
  configuration root; and
- application, daemon, state, cache, and service paths use the `sdsctl`
  namespace.

No configuration file is moved, rewritten, or deleted automatically. Existing
standalone CLI, TUI, scanner-control, recording, and network-audio commands
remain available. Daemon-backed CLI and TUI operation is explicit.

The relevant default paths are:

| Purpose | Default path |
| --- | --- |
| System application configuration | `/etc/sdsctl/config.toml` |
| User application configuration | `${XDG_CONFIG_HOME:-~/.config}/sdsctl/config.toml` |
| Daemon destination manifest | `${XDG_CONFIG_HOME:-~/.config}/sdsctl/daemon-destinations.toml` |
| Legacy scanner profiles | `${XDG_CONFIG_HOME:-~/.config}/sds200/profiles.toml` |
| Legacy remote-audio profiles | `${XDG_CONFIG_HOME:-~/.config}/sds200/remote-audio-profiles.toml` |
| User state fallback | `${XDG_STATE_HOME:-~/.local/state}/sdsctl/` |
| Daemon recording root | `${XDG_STATE_HOME:-~/.local/state}/sdsctl/recordings/` |
| User cache | `${XDG_CACHE_HOME:-~/.cache}/sdsctl/` |
| Runtime sockets with `XDG_RUNTIME_DIR` | `$XDG_RUNTIME_DIR/sdsctl/` |

When `XDG_RUNTIME_DIR` is absent, the API, event, PCMU, and recording-file
sockets fall back to the user-state directory. The daemon recording root remains
under the resolved user-state directory unless `--recording-directory PATH` is
selected explicitly.

## Install into a dedicated virtual environment

The following example installs the released package under `/opt/sdsctl`. Adapt
account-management commands to the local distribution.

```bash
sudo useradd \
  --system \
  --create-home \
  --home-dir /var/lib/sdsctl \
  --shell /usr/sbin/nologin \
  sdsctl

sudo python3 -m venv /opt/sdsctl
sudo /opt/sdsctl/bin/python -m pip install --upgrade pip
sudo /opt/sdsctl/bin/python -m pip install "sds200[tui,playback]"

sudo install -d -o root -g root -m 0755 /etc/sdsctl
sudo install -d -o sdsctl -g sdsctl -m 0700 \
  /var/lib/sdsctl/recordings
```

Install only the feature groups the service needs. Recording and remote
streaming do not require local playback. Local playback requires an operating
system audio backend and appropriate device permissions.

Verify the installed executable before creating the service:

```bash
/opt/sdsctl/bin/sdsctl --version
/opt/sdsctl/bin/sdsctl daemon --help
```

## Application configuration

Application-wide reconnect, presentation, and logging settings may be placed in
`/etc/sdsctl/config.toml`:

```toml
version = 1

[application]
reconnect_attempts = 0
reconnect_initial_delay = 1.0
reconnect_multiplier = 2.0
reconnect_max_delay = 30.0
health_history_limit = 250
log_level = "INFO"
```

System configuration has lower precedence than user configuration, environment
variables, and explicit CLI arguments. See
[Layered application configuration](configuration.md).

## Destination manifest

The daemon reads one strict version 1 destination manifest before opening scanner
hardware. An absent manifest means no daemon-owned destinations. A valid empty
manifest removes all active destinations during reload.

A system-service deployment can select an explicit manifest at
`/etc/sdsctl/daemon-destinations.toml`:

```toml
version = 1

[destinations.archive]
kind = "recording"
path = "/var/lib/sdsctl/recordings/live.wav"
overwrite = true
buffer_seconds = 5.0

[destinations.speakers]
kind = "playback"
backend = "auto"
buffer_ms = 250

[destinations.feed]
kind = "remote-profile"
profile = "county-feed"
publish_metadata = true
metadata_minimum_update_interval = 2.5
```

Destination names must be unique. Recording paths must be absolute and their
parent directories must already exist. Supported playback backends are `auto`,
`sounddevice`, `pipewire`, `pulseaudio`, and `alsa`. The optional `device`
field is a PortAudio name or index for `sounddevice`, or the backend-specific
text device or target for command-backed adapters.

A `remote-profile` destination resolves its named profile through the existing
legacy remote-audio profile store. For a service account whose home is
`/var/lib/sdsctl`, the default store is:

```text
/var/lib/sdsctl/.config/sds200/remote-audio-profiles.toml
```

Remote profiles retain environment-variable references rather than resolved
credentials. Supply referenced secrets through a root-owned environment file:

```bash
sudo install -o root -g root -m 0600 /dev/null /etc/sdsctl/sdsctl.env
```

Do not place resolved credentials in the destination manifest, application
configuration, unit file, logs, traces, or captures.

## systemd service

Create `/etc/systemd/system/sdsctl.service`:

```ini
[Unit]
Description=SDS200 scanner ownership daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sdsctl
Group=sdsctl
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/sdsctl/sdsctl.env
ExecStart=/opt/sdsctl/bin/sdsctl --log-level INFO --host 192.168.0.251 daemon --destination-config /etc/sdsctl/daemon-destinations.toml --recording-directory /var/lib/sdsctl/recordings --socket-path /run/sdsctl/daemon.sock --event-socket-path /run/sdsctl/events.sock --pcmu-socket-path /run/sdsctl/pcmu.sock --recording-file-socket-path /run/sdsctl/recordings.sock
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20
RuntimeDirectory=sdsctl
RuntimeDirectoryMode=0700
StateDirectory=sdsctl
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Global scanner and logging options must precede `daemon`; daemon-specific
options follow it. Replace the explicit host with a saved network-capable
profile when appropriate:

```ini
ExecStart=/opt/sdsctl/bin/sdsctl --log-level INFO --profile home daemon --destination-config /etc/sdsctl/daemon-destinations.toml --recording-directory /var/lib/sdsctl/recordings --socket-path /run/sdsctl/daemon.sock --event-socket-path /run/sdsctl/events.sock --pcmu-socket-path /run/sdsctl/pcmu.sock --recording-file-socket-path /run/sdsctl/recordings.sock
```

A fallback profile may use serial control while retaining its SDS200 network host
for RTSP/RTP audio. Serial-only profiles, replay captures, and non-SDS200
network-audio selections are rejected.

`ProtectSystem=strict` leaves the system read-only except for locations managed
by systemd, including `RuntimeDirectory` and `StateDirectory`. The explicit
`--recording-directory /var/lib/sdsctl/recordings` path above is writable because
it is beneath `StateDirectory=sdsctl`. Add other narrowly scoped writable paths
only when a selected destination requires them.

The fourth private socket, `recordings.sock`, serves only finalized daemon
recordings selected by inventory-relative identifier. It is separate from
`pcmu.sock`: saved-file playback or download does not create scanner-audio or
browser-PCMU ownership. On shutdown the daemon stops recording-file readers,
finalizes any active daemon recording and metadata, then tears down destinations
and the shared audio runtime.

Local playback from a system service requires access to the selected audio
device. An ALSA deployment may require `SupplementaryGroups=audio`. A per-user
PulseAudio or PipeWire session may not be available to a system service; verify
the chosen backend with `sdsctl audio-devices` under the service account.

Validate and start the unit:

```bash
sudo systemd-analyze verify /etc/systemd/system/sdsctl.service
sudo systemctl daemon-reload
sudo systemctl enable --now sdsctl.service
sudo systemctl status sdsctl.service
```

Inspect logs with:

```bash
journalctl -u sdsctl.service
journalctl -u sdsctl.service --since today
journalctl -u sdsctl.service -f
```

## Reload destinations

`SIGHUP` reloads the exact manifest selected at startup. The daemon loads and
validates the replacement before beginning activation.

```bash
sudo systemctl reload sdsctl.service
```

A successful reload transactionally replaces the committed destination set.
Load or activation failure leaves the prior committed destinations running.
Post-commit cleanup failure is reported without rolling back a successfully
activated replacement. `SIGTERM` remains the orderly stop signal.

Before reloading, validate a manifest through the same loader without starting
scanner hardware:

```bash
/opt/sdsctl/bin/python - <<'PY'
from sds200 import load_daemon_destination_configuration

configuration = load_daemon_destination_configuration(
    "/etc/sdsctl/daemon-destinations.toml"
)
print(configuration.as_dict())
PY
```

## Local client access

The daemon sockets use mode `0600` inside a private `0700` directory. With the
system unit above, they are owned by the `sdsctl` account. Run administrative
client checks as that account unless an intentionally designed local access
policy changes ownership or permissions outside the application.

```bash
sudo -u sdsctl /opt/sdsctl/bin/sdsctl \
  daemon-client \
  --socket-path /run/sdsctl/daemon.sock \
  status

sudo -u sdsctl /opt/sdsctl/bin/sdsctl \
  daemon-client \
  --socket-path /run/sdsctl/daemon.sock \
  events \
  --event-socket-path /run/sdsctl/events.sock \
  --count 10

sudo -u sdsctl /opt/sdsctl/bin/sdsctl \
  daemon-client \
  --socket-path /run/sdsctl/daemon.sock \
  audio \
  --pcmu-socket-path /run/sdsctl/pcmu.sock \
  --duration 10 \
  --output /var/lib/sdsctl/recordings/client-check.wav
```

A daemon-backed TUI needs all three explicit socket paths when it runs under a
different environment:

```bash
sudo -u sdsctl /opt/sdsctl/bin/sdsctl tui \
  --daemon-client \
  --daemon-socket-path /run/sdsctl/daemon.sock \
  --daemon-event-socket-path /run/sdsctl/events.sock \
  --daemon-pcmu-socket-path /run/sdsctl/pcmu.sock
```

Closing a daemon-backed CLI or TUI client does not stop daemon ownership.

## Upgrade from v0.18.x

1. Record the installed version and service state.
2. Stop the service.
3. Back up `/etc/sdsctl/`, the service account's `sdsctl` and legacy `sds200`
   configuration directories, and any recording or metadata paths.
4. Upgrade the package in the dedicated virtual environment.
5. Confirm both the CLI and Python package report the intended version.
6. Review the destination manifest and environment-backed secret references.
7. Start the service and verify API, event, PCMU, scanner, PSI, and audio health.
8. Exercise one daemon CLI client and one daemon-backed TUI client before
   returning the service to normal operation.

Example:

```bash
sudo systemctl stop sdsctl.service

sudo /opt/sdsctl/bin/python -m pip install --upgrade "sds200==0.19.0"

/opt/sdsctl/bin/sdsctl --version
/opt/sdsctl/bin/python -c \
  "import sds200; print(sds200.__version__)"

sudo systemctl start sdsctl.service
sudo systemctl status sdsctl.service
```

The upgrade does not rename the distribution, import package, executable, or
legacy profile stores. It does not move or rewrite configuration automatically.

For rollback, stop the service, restore the previously installed package and
backed-up configuration, then restart and repeat the same health checks. Do not
move or reuse a published version tag.

## Security and operational limits

The SDS200 network protocols are unauthenticated and unencrypted. Keep scanner
control and RTSP/RTP audio on a trusted LAN or secured VPN. The daemon sockets
are local Unix-domain sockets and are not TCP services.

The daemon does not fork, create a pidfile, change privileges, install a unit,
perform socket activation, or expose unrestricted raw scanner commands.
Decoded-PCM subscriptions and automatic daemon discovery remain follow-on work.
