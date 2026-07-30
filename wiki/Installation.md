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

Install both optional feature groups:

```bash
python -m pip install "sds200[tui,playback]"
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

## Next steps

- Launch the terminal monitor with `sdsctl monitor`.
- Launch the optional TUI with `sdsctl tui`.
- Read the canonical
  [TUI guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/tui.md).
- Read the canonical
  [network audio guide](https://github.com/stevenboyd78/sds200-python/blob/main/docs/audio.md).
- Open [Troubleshooting](Troubleshooting.md) when discovery or startup fails.
