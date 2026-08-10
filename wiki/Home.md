# sds200-python Wiki

> [!IMPORTANT]
> This wiki is a task-oriented guide. The version-controlled documentation in
> the [main repository](https://github.com/stevenboyd78/sds200-python) is
> authoritative when a wiki page and repository document differ.

`sds200-python` is an alpha Python library and command-line toolkit for the
Uniden SDS100, SDS150, and SDS200 scanners. It provides the model-neutral
`sdsctl` command, a typed Python API, USB serial control for all supported
models, and native Ethernet control and RTSP/RTP audio for the SDS200.

The project is not affiliated with or endorsed by Uniden.

## Start here

- [Installation](Installation.md) — install the package and optional features.
- [Troubleshooting](Troubleshooting.md) — diagnose USB, network, TUI, and audio
  problems.
- [Repository README](https://github.com/stevenboyd78/sds200-python/blob/main/README.md)
  — canonical overview, examples, and project status.
- [Supported scanner models](https://github.com/stevenboyd78/sds200-python/blob/main/docs/supported-models.md)
  — capability and hardware-validation matrix.
- [Textual TUI guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/tui.md)
  — full-screen monitoring, controls, recording, and playback.
- [Network audio guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/audio.md)
  — SDS200 playback, recording, Broadcastify, and Asterisk integration.
- [Web dashboard guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/web-dashboard.md)
  — loopback browser monitoring, live events, audio playback, recording, and
  finalized-WAV playback/download.
- [Home Assistant App guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/home-assistant-app.md)
  — repository installation, Ingress, scanner controls, audio, persistent
  recordings, MQTT Discovery, and security boundaries.
- [Daemon deployment and upgrade guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/daemon-deployment.md)
  — systemd, destination manifests, local clients, migration, and upgrades.

## Supported scanners

| Model | USB control | Native Ethernet control | RTSP/RTP audio |
| --- | --- | --- | --- |
| SDS100 | Yes | No | No |
| SDS150 | Yes | No | No |
| SDS200 | Yes | Yes | Yes |

SDS200 USB, Ethernet control, and network audio have been validated on physical
hardware. SDS100 core USB behavior has also been hardware-validated. SDS150
support is specification-backed and awaits physical-hardware validation. See
the canonical
[supported-models guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/supported-models.md)
for exact validation scope and tested firmware.

## Common tasks

### Find scanners

```bash
sdsctl discover
sdsctl discover --network 192.168.0.0/24 --network-only
```

Only probe networks you own or are authorized to scan.

### Show scanner information

```bash
sdsctl info
sdsctl --host SCANNER_IP info
```

### Start monitoring

```bash
sdsctl monitor
sdsctl --host SCANNER_IP monitor
sdsctl --host SCANNER_IP tui
```

### Play or record SDS200 network audio

```bash
sdsctl --host SCANNER_IP audio --play

sdsctl --host SCANNER_IP audio \
  --output scanner-audio.wav \
  --duration 30
```

### Run the daemon-backed web dashboard

Install the web extra, start the daemon, then start the loopback web service in
another terminal:

```bash
python -m pip install "sds200[web]"
sdsctl --log-level INFO --host SCANNER_IP daemon
sdsctl web
```

Open `http://127.0.0.1:8000/` on the same host. The dashboard consumes daemon
state and services; it does not open a second scanner control or RTSP/RTP audio
connection. Current browser workflows include ordered live updates, explicit
audio playback, daemon-owned recording, and safe playback/download of finalized
recordings.

See the canonical
[web dashboard guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/web-dashboard.md)
for security boundaries and exact behavior.

### Install the Home Assistant App

On Home Assistant OS, add
`https://github.com/stevenboyd78/sds200-python` under
**Settings > Apps > App store > Repositories**, then install **sds200**.

The published App uses Home Assistant Ingress for its dashboard, obtains the
configured MQTT service from Supervisor, stores recordings in writable Home
Assistant media storage, and publishes UDP `50000` for SDS200 RTP audio. The
default recording library is `/media/sdsctl/recordings`; the media-relative
`recording_directory` option can select another location below `/media`.

See the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/home-assistant-app.md)
for configuration and security details.

## Project documentation

- [Roadmap](https://github.com/stevenboyd78/sds200-python/blob/main/ROADMAP.md)
- [Project vision](https://github.com/stevenboyd78/sds200-python/blob/main/docs/project-vision.md)
- [Changelog](https://github.com/stevenboyd78/sds200-python/blob/main/CHANGELOG.md)
- [Operational logging](https://github.com/stevenboyd78/sds200-python/blob/main/docs/logging.md)
- [Daemon deployment and upgrades](https://github.com/stevenboyd78/sds200-python/blob/main/docs/daemon-deployment.md)
- [Layered application configuration](https://github.com/stevenboyd78/sds200-python/blob/main/docs/configuration.md)
- [Capture and replay](https://github.com/stevenboyd78/sds200-python/blob/main/docs/replay-and-capture.md)
- [Linux udev rule](https://github.com/stevenboyd78/sds200-python/blob/main/docs/udev.md)

## Getting help

Review [Troubleshooting](Troubleshooting.md) first. Reproducible bugs and
feature requests belong in
[GitHub Issues](https://github.com/stevenboyd78/sds200-python/issues). Include
the package version or commit, Python and operating-system versions, scanner
model and firmware, transport, exact command, and complete sanitized error.

The project support scope is defined in
[SUPPORT.md](https://github.com/stevenboyd78/sds200-python/blob/main/SUPPORT.md).
Report security vulnerabilities through
[SECURITY.md](https://github.com/stevenboyd78/sds200-python/blob/main/SECURITY.md).
