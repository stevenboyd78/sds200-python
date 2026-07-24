# USB/LAN discovery and connection profiles

## Active LAN discovery

USB discovery scans stable Linux `/dev/serial/by-id` paths for the SDS100,
SDS150, and SDS200. Model names are inferred when the stable path identifies
them, and `--model` can narrow discovery.

The SDS200 virtual-serial specification does not define a separate LAN
discovery broadcast. The library therefore performs a bounded active probe: it
sends the read-only `MDL` command to each usable IPv4 address and accepts only
SDS200 model responses. SDS100 and SDS150 discovery is USB-only.

On Linux, directly connected networks are read from `/proc/net/route` when no
CIDR is supplied:

```bash
sdsctl discover
```

Specify networks explicitly when route detection is unavailable or too broad:

```bash
sdsctl discover --network 192.168.0.0/24
sdsctl discover --network 10.20.30.0/24 --network-only
```

The default maximum is 4096 hosts. This protects against accidentally scanning
a large VPN, corporate, or routed network. Only probe networks you own or are
authorized to test.

Python API:

```python
from sds200 import discover_network_scanners

for scanner in discover_network_scanners(["192.168.0.0/24"]):
    print(scanner.endpoint, scanner.latency_ms)
```

## Connection profiles

Profiles can represent a model-aware USB serial path for any supported model,
an SDS200 network host, or an SDS200 serial/network fallback pair. They are
stored in a human-readable TOML document.

```bash
sdsctl profile add home --host 192.168.0.251
sdsctl profile add handheld --port /dev/ttyACM0 --model SDS150
sdsctl profile add usb --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00 --model SDS200
sdsctl profile show home
sdsctl profile list
sdsctl profile remove usb
```

Use a saved profile with any command:

```bash
sdsctl --profile home info
sdsctl --profile home scanner-info
sdsctl --profile home monitor
```

The default file is `${XDG_CONFIG_HOME:-~/.config}/sds200/profiles.toml`.
Override it with `--config PATH`.

## Health and diagnostics

The `health` command sends `MDL` and `VER`, measures the `MDL` round-trip, and
prints transport statistics when supported:

```bash
sdsctl --profile home health
```

UDP statistics include sent commands, automatic retries, received datagrams,
bytes, socket reopen counts, completed XML documents, dropped XML fragments,
and the most recent diagnostic.

Applications can subscribe to diagnostics:

```python
from sds200 import SDSScanner

with SDSScanner.network("192.168.0.251") as radio:
    radio.on_diagnostic(lambda diagnostic: print(diagnostic.message))
    print(radio.health_check())
```

See [Fallback profiles](fallback-profiles.md) for discovery-driven profile creation and live transport switching.


## Repairing stale profiles

USB enumeration and DHCP can change a saved endpoint. Repair discovery updates
only endpoints it can identify unambiguously, preserves the scanner model, bind
settings, and fallback preference, and can learn the model for a legacy serial
profile.

```bash
sdsctl profile repair home --network 192.168.0.0/24 --dry-run
sdsctl profile repair home --network 192.168.0.0/24
```

For a fallback profile, discovering only one transport updates that endpoint and
leaves the other saved endpoint intact. Repair refuses to guess when multiple
unmatched scanners of the required transport are found.
