# sds200-python

[![CI](https://github.com/stevenboyd78/sds200-python/actions/workflows/ci.yml/badge.svg)](https://github.com/stevenboyd78/sds200-python/actions/workflows/ci.yml)
![Python 3.11–3.14](https://img.shields.io/badge/python-3.11--3.14-blue)
![Development status: alpha](https://img.shields.io/badge/status-alpha-orange)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Python control and monitoring library for the **Uniden SDS200** scanner over USB
serial or Ethernet.

The project provides a typed Python API and an `sds200` command-line tool for
scanner discovery, status monitoring, commands, connection profiles, diagnostics,
and live state updates.

> [!IMPORTANT]
> This project is alpha software. The public API may change before version 1.0.
> It is not affiliated with or endorsed by Uniden.

## Features

- USB serial control using stable Linux `/dev/serial/by-id` paths
- Native SDS200 Ethernet control over UDP
- Automatic USB and bounded LAN discovery
- Saved serial, network, and automatic fallback profiles
- Preferred transport ordering with live USB/Ethernet failover
- Typed commands and responses
- Structured `GSI` and continuous `PSI` scanner information
- Thread-safe synchronized radio state and change events
- Live terminal monitoring
- Traffic tracing, continuous health watching, and reconnect diagnostics
- Separate public audio-stream architecture for future network audio
- UDP XML fragment validation, statistics, and bounded retries
- Bash and Zsh tab completion
- Strict MyPy typing, Ruff checks, and hardware-independent tests

Network audio streaming is not implemented yet; its control-independent API groundwork is available.

## Requirements

- Python 3.11 or newer
- A Uniden SDS200
- For USB: scanner connected as a serial device
- For Ethernet: scanner and computer on a trusted local network

Linux USB and Ethernet operation have been validated with an SDS200 running
firmware version 1.26.01. Explicit network hosts work on any platform supported
by Python's UDP sockets. Automatic route detection and `/dev/serial/by-id`
discovery are Linux-specific.

## Installation

The project has not been published to PyPI yet. Install it from source:

```bash
git clone https://github.com/stevenboyd78/sds200-python.git
cd sds200-python

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

## Quick start

### Find connected scanners

Search USB and directly connected IPv4 networks:

```bash
sds200 discover
```

Search a specific network:

```bash
sds200 discover --network 192.168.0.0/24 --network-only
```

Active LAN discovery sends the read-only `MDL` command to each usable host.
Only scan networks you own or are authorized to probe.

### USB serial

Show scanner information:

```bash
sds200 info
```

Start the live monitor:

```bash
sds200 monitor
```

Use an explicit port when automatic discovery is not appropriate:

```bash
sds200 \
  --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00 \
  info
```

### Ethernet

```bash
sds200 --host 192.168.0.251 info
sds200 --host 192.168.0.251 scanner-info
sds200 --host 192.168.0.251 monitor
```

The SDS200 virtual serial service uses UDP port `50536` by default.

### Connection profiles and fallback

Create a profile directly from USB and LAN discovery:

```bash
sds200 profile discover home \
  --network 192.168.0.0/24 \
  --prefer network
```

When both endpoints are found, the profile automatically falls back between
Ethernet and USB. The saved preference can be overridden for one command:

```bash
sds200 --profile home --prefer serial monitor
```

Manual profiles remain supported:

```bash
sds200 profile add network-only --host 192.168.0.251
sds200 profile add usb-only \
  --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00
```

Profiles are stored in `${XDG_CONFIG_HOME:-~/.config}/sds200/profiles.toml`.

### Health and diagnostics

```bash
sds200 --profile home health
sds200 --profile home health --watch 5
sds200 --profile home health --json
sds200 --host 192.168.0.251 --trace scanner.trace monitor
```

### Raw protocol commands

```bash
sds200 command MDL
sds200 command VER
sds200 command VOL
sds200 command SQL
sds200 command STS
```

Raw command access is intended for documented scanner commands and protocol
development. Prefer the typed Python methods when they are available.

## Shell completion

Activate Bash completion for the current shell:

```bash
eval "$(sds200 completion bash)"
```

Enable it whenever Bash starts:

```bash
echo 'eval "$(sds200 completion bash)"' >> ~/.bashrc
```

For Zsh:

```zsh
eval "$(sds200 completion zsh)"
```

## Python API

### USB

```python
from sds200 import SDS200

with SDS200.auto() as radio:
    print(radio.get_model())
    print(radio.get_firmware())
    print(radio.get_volume())
    print(radio.get_squelch())
```

### Ethernet

```python
from sds200 import SDS200

with SDS200.network("192.168.0.251") as radio:
    info = radio.get_scanner_info()
    print(info.system)
    print(info.department)
    print(info.channel)
    print(info.frequency)
```

### Continuous state updates

```python
from sds200 import SDS200

with SDS200.network("192.168.0.251") as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )

    with radio.scanner_info_push(interval_ms=500):
        radio.wait()
```

### LAN discovery

```python
from sds200 import discover_network_scanners

for scanner in discover_network_scanners(["192.168.0.0/24"]):
    print(scanner.endpoint, scanner.model, scanner.latency_ms)
```

## Security

The SDS200 network-control protocol is unauthenticated and unencrypted. Keep it
on a trusted LAN or access it through a secured VPN. Do not expose UDP port
`50536` directly to the public Internet.

This library is not a safety-critical or emergency-dispatch system. Do not rely
on it as the sole means of receiving urgent communications.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[docs/transports.md](docs/transports.md) for transport limitations.

## Documentation

- [Control transports](docs/transports.md)
- [LAN discovery and profiles](docs/discovery-and-profiles.md)
- [Fallback profiles](docs/fallback-profiles.md)
- [Audio subsystem architecture](docs/audio.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Changelog](CHANGELOG.md)
- [Release process](docs/releasing.md)

## Development

```bash
python -m pip install -e ".[dev]"

ruff check .
mypy src/sds200
pytest
python scripts/check_docs.py
python -m build
python -m twine check dist/*
```

Tests must not require physical scanner hardware. Hardware validation is
documented separately in pull requests and release notes.

## Project status

Version `0.6.0` adds discovery-driven fallback profiles and expanded health diagnostics. The control, discovery,
monitoring, profile, and diagnostic paths have been validated against real
SDS200 hardware over both USB and Ethernet. API compatibility is not guaranteed
until version 1.0.

See [CHANGELOG.md](CHANGELOG.md) for development history and planned changes.

## License

MIT. See [LICENSE](LICENSE).
