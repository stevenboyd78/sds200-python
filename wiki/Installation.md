# Installation

> [!IMPORTANT]
> This page is a task-oriented guide. The
> [repository README](https://github.com/stevenboyd78/sdsctl/blob/main/README.md)
> is the canonical installation reference.

## Requirements

- Python 3.11 or newer
- A Uniden SDS100, SDS150, or SDS200 scanner
- A USB serial connection for any supported model, or a trusted local network
  for native SDS200 Ethernet features
- FFmpeg with `libmp3lame` for the Broadcastify adapter
- A working PortAudio environment for optional local playback

## Install from PyPI

Install the library and `sdsctl` command:

```bash
python -m pip install sds200
```

Install the optional Textual full-screen interface:

```bash
python -m pip install "sds200[tui]"
```

Install optional local audio playback:

```bash
python -m pip install "sds200[playback]"
```

Install the optional loopback web dashboard:

```bash
python -m pip install "sds200[web]"
```

Install the TUI, playback, and web feature groups together:

```bash
python -m pip install "sds200[tui,playback,web]"
```

Verify the installation:

```bash
sdsctl --version
sdsctl --help
```

## Use a virtual environment

A virtual environment keeps the package and optional dependencies isolated:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "sds200[tui,playback]"
```

Activate the environment again before using its `sdsctl` command:

```bash
source .venv/bin/activate
```

## Install from source

```bash
git clone https://github.com/stevenboyd78/sdsctl.git
cd sdsctl

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the project checks before contributing:

```bash
ruff check .
mypy src/sds200
pytest
python scripts/check_docs.py
git diff --check
```

## Linux USB permissions

Some Linux systems do not automatically grant the active user access to the
scanner serial port. The project includes an optional udev rule that uses
systemd-logind `uaccess`, retains a `dialout` fallback, and prevents
ModemManager from probing matching scanners.

Follow the canonical
[Linux udev guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/udev.md)
rather than making scanner devices globally writable.

Inspect stable device paths with:

```bash
ls -l /dev/serial/by-id/
```

## Optional FFmpeg support

Check that FFmpeg exposes the MP3 encoder required by the Broadcastify adapter:

```bash
ffmpeg -version | head -n 1
ffmpeg -hide_banner -encoders 2>/dev/null | grep -F libmp3lame
```

The encoder listing should contain `libmp3lame`.

## First connection

Try automatic USB discovery:

```bash
sdsctl discover
sdsctl info
```

For an SDS200 on Ethernet:

```bash
sdsctl discover --network 192.168.0.0/24 --network-only
sdsctl --host SCANNER_IP info
```

Only scan networks you own or are authorized to probe.

## Install the Home Assistant App

Home Assistant OS users can install the published App without copying a Local
App into `/addons`.

1. Open **Settings > Apps > App store**.
2. Open the top-right three-dot menu and choose **Repositories**.
3. Add `https://github.com/stevenboyd78/sdsctl`.
4. Open the repository's **sds200** App.
5. Install it.
6. Set `scanner_host` to the SDS200 LAN hostname or IP address.
7. Leave `recording_directory` at `sdsctl/recordings` unless another Home
   Assistant media subdirectory is preferred.
8. Start the App and open **Web UI**.

The App requires the Home Assistant MQTT service and uses UDP `50000` for
scanner RTP audio. See the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md)
before changing network or MQTT settings.

The Local App workflow under `/addons` remains available for development but is
not required for normal release installation.

## Upgrade to v0.21.0

v0.21.0 is the first release after v0.20.2 and freezes the completed Milestones
21 through 25 release scope: Favorites Workspace and verified storage workflows,
assisted RadioReference synchronization, advanced protocol and analysis
foundations, the user-facing `sdsctl` repository/product identity, and the
generic Docker/Compose/Podman deployment foundation.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the Python package with:

```bash
python -m pip install --upgrade "sds200==0.21.0"
sdsctl --version
```

The v0.21.0 release is also the first release using the generic Docker Hub
publication path. After the genuine matching release tag has published
successfully, prefer the immutable exact image:

```bash
docker pull theboyd78/sdsctl:0.21.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release, but
the exact version tag is recommended for reproducible deployments.
Repository-root `compose.yaml` and `compose.usb.yaml` remain source-built and do
not switch automatically to the Docker Hub image.

The Home Assistant App version tracks 0.21.0 for the release while preserving
its compatibility-sensitive `sds200` name, slug, and GHCR image identity. The
single-owner daemon, MQTT Discovery/control, Ingress, browser audio, and
recording architecture remain unchanged by release preparation.

## Upgrade to v0.20.2

v0.20.2 publishes the three Home Assistant slices completed after v0.20.1 while
preserving the existing single-owner daemon architecture.

The Home Assistant Configuration page now has user-facing names and descriptions
for `scanner_host`, `mqtt_topic_prefix`, and `recording_directory`. The recording
directory remains media-relative and the default still resolves to:

```text
/media/sdsctl/recordings
```

The App installs the bundled read-only SDS200 Lovelace card at
`/local/sds200/sds200-card.js`. Register that path once under
**Settings > Dashboards > Resources** as a JavaScript Module. If the App had to
create Home Assistant's `www` directory for the first time, restart Home
Assistant Core once before registering the `/local` resource. Then add
**SDS200 Scanner** from the card picker and use its graphical editor to choose
the SDS200 Discovery state entities.

The MQTT-discovered SDS200 device now contains seventeen components: the existing
ten state/diagnostic components plus System, Department, Site, and Channel Hold
switches and Previous Channel, Next Channel, and Reconnect Scanner buttons. The
seven controls use a dedicated bounded Home Assistant adapter; the App still
does not enable the generic daemon MQTT request-envelope command topic.

Existing v0.20.1 recordings and configuration remain in the same Home Assistant
media/configuration locations. Back up important configuration and recordings
before any upgrade.

See the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md)
for the Lovelace registration, entity, storage, networking, control, and security
contracts.

## Upgrade to v0.20.1

v0.20.1 is a corrective Home Assistant App release. It keeps the distribution
and Python import package named `sds200`, the executable named `sdsctl`, and the
existing single-owner daemon architecture.

Home Assistant recordings now use writable media storage instead of the
App-private legacy recording directory. The default `recording_directory` value
`sdsctl/recordings` resolves to:

```text
/media/sdsctl/recordings
```

When upgrading a v0.20.0 Home Assistant App installation, startup migrates files
from `/data/recordings` into the configured media library before starting the
daemon. Nested paths and metadata sidecars are preserved. Migration preflights
destination conflicts, never overwrites a differing file, verifies copied file
contents before removing the legacy source, and can resume when an identical
destination already exists.

The dashboard also separates active Capture from Recent recordings, moves
Reconnect scanner into Scanner connection, groups daemon runtime with scanner
connection, and gives the finalized recording library its own responsive panel.

Existing standalone scanner profiles, remote-audio profiles, daemon
configuration, and other non-App data are not moved by this Home Assistant
recording migration. Back up important configuration and recordings before any
upgrade.

See the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md)
for exact storage, migration, networking, and security behavior.

## Run the SDS200 daemon

The foreground daemon is intended for process-manager ownership. It exposes
private local API, event, PCMU, and finalized-recording sockets and can activate
saved playback, recording, and remote-profile destinations.

```bash
sdsctl --log-level INFO --host SCANNER_IP daemon
sdsctl daemon-client status
sdsctl tui --daemon-client
```

Standalone scanner commands and the standalone TUI remain the default. Daemon
client mode is explicit.

To run the loopback web dashboard in another terminal:

```bash
sdsctl web
```

Open `http://127.0.0.1:8000/` locally. The web service remains a daemon client
and does not open scanner hardware directly.

## Next steps

- Launch the terminal monitor with `sdsctl monitor`.
- Launch the optional TUI with `sdsctl tui`.
- Read the canonical
  [TUI guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/tui.md).
- Read the canonical
  [network audio guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/audio.md).
- Read the canonical
  [web dashboard guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/web-dashboard.md).
- Open [Troubleshooting](Troubleshooting.md) when discovery or startup fails.
