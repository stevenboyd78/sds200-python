# Installation

> [!IMPORTANT]
> This page is a task-oriented guide. The
> [repository README](https://github.com/stevenboyd78/sds200-python/blob/main/README.md)
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
git clone https://github.com/stevenboyd78/sds200-python.git
cd sds200-python

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
[Linux udev guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/udev.md)
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
3. Add `https://github.com/stevenboyd78/sds200-python`.
4. Open the repository's **sds200** App.
5. Install it.
6. Set `scanner_host` to the SDS200 LAN hostname or IP address.
7. Start the App and open **Web UI**.

The App requires the Home Assistant MQTT service and uses UDP `50000` for
scanner RTP audio. See the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/home-assistant-app.md)
before changing network or MQTT settings.

The Local App workflow under `/addons` remains available for development but is
not required for normal release installation.

## Upgrade to v0.20.0

The v0.20.0 release keeps the distribution and Python import package named
`sds200` and keeps the executable named `sdsctl`. Application and service paths
use the `sdsctl` namespace, while existing scanner and remote-audio profiles
remain under the legacy `sds200` configuration root.

v0.20.0 adds the Home Assistant App distribution path, Ingress dashboard,
Supervisor MQTT adaptation, persistent App recordings, and release-built amd64
and aarch64 images. Existing standalone, daemon, TUI, and web workflows remain
available.

No file is moved or rewritten automatically. Before upgrading, back up system
and user configuration, legacy profile files, destination manifests, recordings,
and metadata. Upgrade the package, verify `sdsctl --version` and
`sds200.__version__`, then exercise both standalone and daemon-backed workflows.

See the canonical
[daemon deployment and upgrade guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/daemon-deployment.md)
for a complete systemd unit, destination manifest, reload, client, upgrade, and
rollback procedure.

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
  [TUI guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/tui.md).
- Read the canonical
  [network audio guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/audio.md).
- Read the canonical
  [web dashboard guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/web-dashboard.md).
- Open [Troubleshooting](Troubleshooting.md) when discovery or startup fails.
