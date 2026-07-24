# sds200-python

[![CI](https://github.com/stevenboyd78/sds200-python/actions/workflows/ci.yml/badge.svg)](https://github.com/stevenboyd78/sds200-python/actions/workflows/ci.yml)
![Python 3.11–3.14](https://img.shields.io/badge/python-3.11--3.14-blue)
![Development status: alpha](https://img.shields.io/badge/status-alpha-orange)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Python control and monitoring library for the **Uniden SDS100, SDS150, and
SDS200** scanners. All three models support USB serial control; the SDS200 also
supports native Ethernet control.

The project provides a typed Python API and an `sdsctl` command-line tool for
scanner discovery, status monitoring, commands, connection profiles, diagnostics,
and live state updates.

> [!IMPORTANT]
> This project is alpha software. The public API may change before version 1.0.
> It is not affiliated with or endorsed by Uniden.

## Features

- USB serial control for SDS100, SDS150, and SDS200 scanners
- Native SDS200 Ethernet control over UDP
- Model detection, aliases, capability reporting, and model-specific limits
- SDS100/SDS150 battery and charge-status reporting
- Automatic USB and bounded LAN discovery
- Saved serial, network, and automatic fallback profiles
- Preferred transport ordering with live USB/Ethernet failover
- Typed commands and responses
- Structured `GSI` and continuous `PSI` scanner information
- Thread-safe synchronized radio state and change events
- Live terminal monitoring
- Exponential reconnect backoff with configurable retry limits
- Traffic tracing, bounded health history, and failover diagnostics
- JSON Lines events for connection, retry, failover, and state changes
- Discovery-based repair for stale USB paths and scanner IP addresses
- Separate public audio-stream architecture for future network audio
- UDP XML fragment validation, statistics, and bounded retries
- Bash and Zsh tab completion
- Strict MyPy typing, Ruff checks, and hardware-independent tests

Network audio streaming remains on the roadmap but is deferred while control-path reliability matures. Its control-independent API groundwork remains available.

## Requirements

- Python 3.11 or newer
- A Uniden SDS100, SDS150, or SDS200
- For USB: scanner connected as a serial device
- For Ethernet: scanner and computer on a trusted local network

Linux USB and Ethernet operation have been validated with an SDS200 running
firmware version 1.26.01. SDS100 and SDS150 support follows Uniden's shared
SDS-series remote-command specification and still needs physical-hardware
validation. Explicit SDS200 network hosts work on any platform supported by
Python's UDP sockets. Automatic route detection and `/dev/serial/by-id`
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
sdsctl discover
```

Search a specific network:

```bash
sdsctl discover --network 192.168.0.0/24 --network-only
```

Active LAN discovery sends the read-only `MDL` command to each usable host.
Only scan networks you own or are authorized to probe.

### USB serial

Show scanner information using automatic model detection:

```bash
sdsctl info
```

Select a specific model when multiple USB scanners are connected:

```bash
sdsctl --model SDS100 info
sdsctl --model SDS150 info
```

Start the live monitor:

```bash
sdsctl monitor
```

Use an explicit port when automatic discovery is not appropriate:

```bash
sdsctl \
  --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00 \
  info
```

### SDS200 Ethernet

```bash
sdsctl --host 192.168.0.251 info
sdsctl --host 192.168.0.251 scanner-info
sdsctl --host 192.168.0.251 monitor
```

The SDS200 virtual serial service uses UDP port `50536` by default.

### Connection profiles and fallback

Create a profile directly from USB and LAN discovery:

```bash
sdsctl profile discover home \
  --network 192.168.0.0/24 \
  --prefer network
```

When both endpoints are found, the profile automatically falls back between
Ethernet and USB. The saved preference can be overridden for one command:

```bash
sdsctl --profile home --prefer serial monitor
```

Manual profiles remain supported:

```bash
sdsctl profile add network-only --host 192.168.0.251
sdsctl profile add usb-only \
  --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00 \
  --model SDS200
sdsctl profile add handheld --port /dev/ttyACM0 --model SDS150
```

Profiles are stored in `${XDG_CONFIG_HOME:-~/.config}/sds200/profiles.toml`.

Repair stale USB paths or a changed scanner IP address without losing the saved
transport preference:

```bash
sdsctl profile repair home --network 192.168.0.0/24
sdsctl profile repair home --network 192.168.0.0/24 --dry-run
```

### Reliability, health, and events

```bash
sdsctl --profile home health
sdsctl --profile home health --watch 5 --history
sdsctl --profile home health --watch 5 --history --json
sdsctl --profile home events --json
sdsctl --host 192.168.0.251 --trace scanner.trace monitor
```


Reconnects use capped exponential backoff. Retry forever by default, or set a
finite recovery budget:

```bash
sdsctl --profile home \
  --reconnect-attempts 8 \
  --reconnect-initial-delay 1 \
  --reconnect-multiplier 2 \
  --reconnect-max-delay 30 \
  monitor
```

`events --json` emits one JSON object per line for connection changes,
transport diagnostics, reconnect scheduling, failovers, and live state changes.

### Raw protocol commands

```bash
sdsctl command MDL
sdsctl command VER
sdsctl command GCS  # SDS100/SDS150 charge status
sdsctl command VOL
sdsctl command SQL
sdsctl command STS
```

Raw command access is intended for documented scanner commands and protocol
development. Prefer the typed Python methods when they are available.

## Shell completion

Activate Bash completion for the current shell:

```bash
eval "$(sdsctl completion bash)"
```

Enable it whenever Bash starts:

```bash
echo 'eval "$(sdsctl completion bash)"' >> ~/.bashrc
```

For Zsh:

```zsh
eval "$(sdsctl completion zsh)"
```

## Python API

### USB

```python
from sds200 import SDSScanner

with SDSScanner.auto(model="SDS150") as radio:
    print(radio.get_model())
    print(radio.get_firmware())
    print(radio.get_volume())
    print(radio.get_squelch())
```

### SDS200 Ethernet

```python
from sds200 import SDSScanner

with SDSScanner.network("192.168.0.251") as radio:
    info = radio.get_scanner_info()
    print(info.system)
    print(info.department)
    print(info.channel)
    print(info.frequency)
```

### Continuous state updates

```python
from sds200 import SDSScanner

with SDSScanner.network("192.168.0.251") as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )

    with radio.scanner_info_push(interval_ms=500):
        radio.wait()
```

### Reconnect policy and health history

```python
from sds200 import ReconnectPolicy, SDSScanner

policy = ReconnectPolicy(
    initial_delay=1.0,
    multiplier=2.0,
    max_delay=30.0,
    max_attempts=8,
)

with SDSScanner.network("192.168.0.251", reconnect_policy=policy) as radio:
    print(radio.health_check().as_dict())
    print(radio.health_summary().as_dict())
```

### LAN discovery

```python
from sds200 import discover_network_scanners

for scanner in discover_network_scanners(["192.168.0.0/24"]):
    print(scanner.endpoint, scanner.model, scanner.latency_ms)
```

## Project naming

The model-neutral executable is `sdsctl`. The distribution, Python import package,
configuration directory, and repository remain named `sds200`; Python applications
should use `SDSScanner`, while the historical `SDS200` class name remains an alias.

## Security

The SDS200 network-control protocol is unauthenticated and unencrypted. Keep it
on a trusted LAN or access it through a secured VPN. Do not expose UDP port
`50536` directly to the public Internet.

This library is not a safety-critical or emergency-dispatch system. Do not rely
on it as the sole means of receiving urgent communications.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[docs/transports.md](docs/transports.md) for transport limitations.

## Documentation

- [Supported scanner models](docs/supported-models.md)
- [Control transports](docs/transports.md)
- [LAN discovery and profiles](docs/discovery-and-profiles.md)
- [Fallback profiles](docs/fallback-profiles.md)
- [Reliability and observability](docs/reliability.md)
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

Version `0.8.0` adds model-aware SDS100 and SDS150 USB support while preserving
the validated SDS200 USB, Ethernet, fallback, monitoring, profile, and
reliability paths. SDS100 and SDS150 hardware validation is still in progress.
API compatibility is not guaranteed until version 1.0.

See [CHANGELOG.md](CHANGELOG.md) for development history and planned changes.

## License

MIT. See [LICENSE](LICENSE).
