# Troubleshooting

> [!IMPORTANT]
> This page provides common diagnostic steps. The repository documentation and
> current command help are authoritative for exact options and behavior.

## Collect the environment first

Record the software and scanner context before changing the system:

```bash
sdsctl --version
python --version
uname -a
```

For an SDS200 Ethernet connection:

```bash
sdsctl --host SCANNER_IP health
sdsctl -vv --host SCANNER_IP scanner-info
```

Include the scanner model and firmware reported by:

```bash
sdsctl --host SCANNER_IP info
```

## USB scanner is not discovered

Inspect stable Linux serial-device paths:

```bash
ls -l /dev/serial/by-id/
```

Check whether the current user can read and write the resolved device:

```bash
test -r /dev/ttyACM0 && test -w /dev/ttyACM0 \
  && echo "Scanner is accessible" \
  || echo "Scanner is not accessible"
```

Use the project's optional
[udev rule](https://github.com/stevenboyd78/sds200-python/blob/main/docs/udev.md)
when the device exists but the active user lacks access. Do not solve the
problem by making the serial device globally writable.

Select a scanner explicitly when several devices are attached:

```bash
sdsctl --model SDS100 info
sdsctl --model SDS150 --port /dev/ttyACM0 info
sdsctl --model SDS200 \
  --port /dev/serial/by-id/SCANNER_DEVICE \
  info
```

## SDS200 Ethernet discovery fails

Search only the directly relevant authorized network:

```bash
sdsctl discover \
  --network 192.168.0.0/24 \
  --network-only
```

Then test the known address directly:

```bash
sdsctl --host SCANNER_IP info
sdsctl --host SCANNER_IP health
```

Confirm that:

- the scanner and host are on reachable local networks;
- the scanner's Ethernet features are enabled;
- local firewall policy permits the SDS200 control and audio traffic;
- the address has not changed since a profile was saved.

Repair a stale profile through authorized discovery:

```bash
sdsctl profile repair PROFILE_NAME \
  --network 192.168.0.0/24 \
  --dry-run
```

Remove `--dry-run` only after reviewing the proposed repair.

## TUI reports stale PSI data

The Textual TUI can warn about stale scanner-information updates and perform a
rate-limited control reconnect. Enable informational logging to see the
lifecycle:

```bash
sdsctl --log-level INFO --host SCANNER_IP tui
```

Read the canonical
[operational logging guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/logging.md)
for expected recovery entries and configuration options.

SDS200 control recovery is independent from an active RTSP/RTP audio session,
so an ongoing WAV recording should not be stopped by PSI recovery.

## SDS200 network audio will not start

Stop other processes that may already own an SDS200 RTSP/RTP audio session.
Examples include:

- another `sdsctl audio` process;
- a TUI session with active network audio;
- an Asterisk custom Music-on-Hold source;
- a validation or integration process.

Then retry a minimal recording:

```bash
sdsctl --host SCANNER_IP audio \
  --output /tmp/scanner-test.wav \
  --duration 10
```

Review the canonical
[network audio guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/audio.md)
for transport behavior, reliability counters, Broadcastify, and Asterisk
configuration.

## Local playback fails

Confirm that the playback extra is installed in the active environment:

```bash
python -m pip install "sds200[playback]"
```

Test recording without playback to distinguish an RTSP/RTP problem from a local
PortAudio problem:

```bash
sdsctl --host SCANNER_IP audio \
  --output /tmp/scanner-test.wav \
  --duration 10
```

If recording succeeds but `--play` fails, capture the complete playback error,
operating system, audio backend, and selected output device.

## Broadcastify-compatible encoding fails

Confirm that FFmpeg and `libmp3lame` are available:

```bash
ffmpeg -version | head -n 1
ffmpeg -hide_banner -encoders 2>/dev/null | grep -F libmp3lame
```

Keep source passwords out of command history and logs. Supply secrets through
the documented environment-backed secret mechanism. Never attach an
unredacted environment listing or Icecast authorization header to an issue.

Production Broadcastify testing requires an approved feed and the assigned
Technicals settings.

## Asterisk Music-on-Hold source fails

Verify that Asterisk can see the custom class:

```bash
sudo asterisk -rx "module show like res_musiconhold"
sudo asterisk -rx "moh show classes"
```

Use:

- an absolute path to the installed `sdsctl` executable;
- a profile readable by the Asterisk service account;
- `format=slin`;
- process-group termination for the custom source.

Inspect service logs with the system's normal journal tooling. Do not place a
required executable or profile under a home directory the service account
cannot traverse.

The canonical configuration is in the
[network audio guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/audio.md).

## Daemon or web dashboard will not start

First confirm that the foreground daemon is healthy:

```bash
sdsctl daemon-client status
```

The normal daemon owns four private local services: `daemon.sock`,
`events.sock`, `pcmu.sock`, and `recordings.sock`. A daemon-backed client must
run with filesystem permission to connect to the required sockets. Do not make
the socket directory or socket files world-writable to work around an ownership
problem.

If the daemon is healthy but the browser service fails, run the loopback web
service directly and review its sanitized error:

```bash
sdsctl web
```

The standalone `sdsctl web` service binds only to localhost or an explicit
loopback address. Wildcard, LAN, and public standalone binds remain intentionally
rejected until authentication and transport-security support exists. The Home
Assistant App uses a separate explicit Ingress mode and does not publish the
dashboard port directly to the LAN.

See the canonical
[web dashboard guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/web-dashboard.md)
and
[daemon deployment guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/daemon-deployment.md)
for exact socket paths, permissions, and service behavior.

## Home Assistant App problems

### Repository is not visible

After adding `https://github.com/stevenboyd78/sds200-python` under
**Settings > Apps > App store > Repositories**, refresh the browser. If the
repository still does not appear, inspect the Supervisor log under
**Settings > System > Logs** for repository or App configuration errors.

### App does not start

Confirm:

- `scanner_host` contains the reachable SDS200 LAN hostname or address;
- the Home Assistant MQTT service is available;
- the App log does not report an unsupported TLS-enabled MQTT service; and
- host UDP `50000` is available.

The App intentionally fails startup rather than persisting the Supervisor MQTT
password or silently weakening an unsupported MQTT transport configuration.

### Scanner state works but audio stays buffering

Start a daemon-owned recording from the dashboard and watch its packet and sample
counters. If both remain at zero, verify the App Network configuration maps
`50000/udp` to host UDP `50000` and confirm the scanner can route RTP to the Home
Assistant host.

If recording packets advance but browser audio still buffers, collect the App
log and browser context details before changing the RTP mapping.

### MQTT Discovery entities are missing

Confirm Home Assistant's MQTT integration is active and inspect the App log for
MQTT service or broker connection errors. The v0.20.0 App publishes ten read-only
Discovery entities and does not enable semantic MQTT command subscriptions.

## Capture detailed diagnostics

Operational logs exclude raw scanner traffic by default:

```bash
sdsctl --log-level DEBUG --host SCANNER_IP monitor
```

Create a protocol trace only when needed:

```bash
sdsctl --trace scanner.trace --host SCANNER_IP monitor
```

Traces and captures can contain scanner names, channel names, unit identifiers,
and network addresses. Review and sanitize them before sharing.

## Open a useful issue

Before opening an issue:

1. Test the latest code from the default branch.
2. Search existing issues.
3. Run `sdsctl health` for the affected connection.
4. Capture a minimal reproducible command and complete error.

Include:

- installed package version or commit;
- Python and operating-system versions;
- scanner model and firmware;
- USB or Ethernet transport;
- exact command;
- complete sanitized error or traceback;
- minimal reproduction steps;
- whether another supported transport behaves differently.

See the repository
[support policy](https://github.com/stevenboyd78/sds200-python/blob/main/SUPPORT.md)
and
[GitHub Issues](https://github.com/stevenboyd78/sds200-python/issues).
