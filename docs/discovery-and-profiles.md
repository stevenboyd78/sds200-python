# LAN discovery and connection profiles

## Active LAN discovery

The SDS200 virtual-serial specification does not define a separate discovery
broadcast. The library therefore performs a bounded active probe: it sends the
read-only `MDL` command to each usable IPv4 address and accepts only
`MDL,SDS200` responses.

On Linux, directly connected networks are read from `/proc/net/route` when no
CIDR is supplied:

```bash
sds200 discover
```

Specify networks explicitly when route detection is unavailable or too broad:

```bash
sds200 discover --network 192.168.0.0/24
sds200 discover --network 10.20.30.0/24 --network-only
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

Profiles can represent either a stable serial path or an SDS200 network host.
They are stored in a human-readable TOML document.

```bash
sds200 profile add home --host 192.168.0.251
sds200 profile add usb --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00
sds200 profile show home
sds200 profile list
sds200 profile remove usb
```

Use a saved profile with any command:

```bash
sds200 --profile home info
sds200 --profile home scanner-info
sds200 --profile home monitor
```

The default file is `${XDG_CONFIG_HOME:-~/.config}/sds200/profiles.toml`.
Override it with `--config PATH`.

## Health and diagnostics

The `health` command sends `MDL` and `VER`, measures the `MDL` round-trip, and
prints transport statistics when supported:

```bash
sds200 --profile home health
```

UDP statistics include sent commands, automatic retries, received datagrams,
bytes, socket reopen counts, completed XML documents, dropped XML fragments,
and the most recent diagnostic.

Applications can subscribe to diagnostics:

```python
from sds200 import SDS200

with SDS200.network("192.168.0.251") as radio:
    radio.on_diagnostic(lambda diagnostic: print(diagnostic.message))
    print(radio.health_check())
```

See [Fallback profiles](fallback-profiles.md) for discovery-driven profile creation and live transport switching.
