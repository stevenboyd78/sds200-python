# sds200-python

Python 3.11+ control and monitoring library for Uniden SDS-series scanners.

Milestone 1 provides:

- Linux scanner discovery through `/dev/serial/by-id`
- Stable SDS200 device selection
- Threaded serial transport
- CR-terminated packet framing
- Thread-safe command writes
- Optional automatic reconnect
- Raw packet events and typed core responses
- `MDL`, `VER`, `VOL`, `SQL`, and `STS` helpers
- Command-line discovery, information, raw-monitor, and command tools
- Unit tests without scanner hardware

The library defaults to the stable Linux device path matching:

```text
/dev/serial/by-id/*UNIDEN*SDS200*
```

## Set up development

```bash
git clone https://github.com/stevenboyd78/sds200-python.git
cd sds200-python

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Copy the files from this milestone into the repository before installing if the
repository is currently empty.

## Verify the scanner

```bash
sds200 discover
sds200 info
sds200 raw
```

Send any documented command:

```bash
sds200 command MDL
sds200 command VER
sds200 command VOL
sds200 command SQL
sds200 command STS
```

## Python example

```python
from sds200 import SDS200

with SDS200.auto() as radio:
    print(radio.get_model())
    print(radio.get_firmware())
    print(radio.get_volume())
    print(radio.get_squelch())
```

## Raw monitoring

```python
from sds200 import SDS200

with SDS200.auto() as radio:
    radio.on_packet(lambda packet: print(packet.raw))
    radio.wait()
```

## Notes

The current-status `STS` response represents scanner display lines rather than a
simple system/department/channel tuple. Milestone 1 preserves those display
fields safely. Higher-level semantic mapping will be added after collecting real
packets from the target scanner and implementing `GSI`/`PSI`.


## Milestone 1.1 fixes

- Ruff cleanups for Python 3.11+
- Added `types-pyserial` for strict MyPy checks
- Removed ambiguous `**kwargs` constructor forwarding
- Added a precisely typed `SDS200.auto()` constructor
- Replaced redundant generic cast
- Kept all eight unit tests passing


## Milestone 2

Milestone 2 adds:

- Typed command objects through `radio.execute(...)`
- Structured `GSI` XML parsing
- A synchronized `radio.state`
- Field-specific state change events
- Optional raw traffic tracing
- `sds200 scanner-info`
- Additional parser and command tests

Example:

```python
from sds200 import SDS200

with SDS200.auto(trace_path="scanner.trace") as radio:
    info = radio.get_scanner_info()
    print(info.system, info.department, info.channel)
    print(radio.state.snapshot)
```

CLI:

```bash
sds200 --trace scanner.trace scanner-info
```


## Milestone 2.1

- Fixes a Linux/PySerial shutdown race that could raise:
  `TypeError: 'NoneType' object cannot be interpreted as an integer`
- Stops and joins the serial reader before closing the file descriptor
- Defensively treats a shutdown-time PySerial `TypeError` as harmless
- Adds a regression test proving the port is not closed during an active read


## Milestone 2.2

- Aligns the serial protocol with the `types-pyserial` return type:
  `Serial.write()` may return `int | None`
- Replaces the untyped `**kwargs: object` PySerial wrapper with explicit,
  MyPy-checkable constructor parameters
- Resolves all eight strict MyPy errors reported for `transport.py`


## Milestone 2.3: shell tab completion

The CLI supports command, subcommand, option, flag, scanner-port, and common raw
protocol-command completion through `argcomplete`.

### Bash

Activate completion for the current terminal:

```bash
eval "$(sds200 completion bash)"
```

Enable it whenever Bash starts:

```bash
echo 'eval "$(sds200 completion bash)"' >> ~/.bashrc
source ~/.bashrc
```

### Zsh

Activate completion for the current terminal:

```zsh
eval "$(sds200 completion zsh)"
```

Enable it whenever Zsh starts:

```zsh
echo 'eval "$(sds200 completion zsh)"' >> ~/.zshrc
source ~/.zshrc
```

Examples after activation:

```text
sds200 <TAB>                 # subcommands and global options
sds200 --<TAB>               # --port, --trace, --verbose, --help
sds200 command --<TAB>       # --timeout and --help
sds200 command V<TAB>        # VER and VOL
sds200 --port /dev/<TAB>     # detected SDS200 device paths
```

Completion suggestions do not restrict raw commands; undocumented or future
scanner commands can still be entered manually.


## Milestone 2.4

- Replaces Ruff B010-triggering constant `setattr()` calls.
- Uses a typed completion-action protocol so strict MyPy remains clean.


## Milestone 3

Milestone 3 adds continuous scanner monitoring and establishes the transport
boundary needed for future SDS200 Ethernet control:

- A public `ControlTransport` protocol
- Backward-compatible `SerialTransport`
- `SDS200.from_transport(...)` for alternate transports
- Continuous `PSI,<interval>` start/stop helpers
- Automatic PSI restart after a transport reconnect
- Rich, thread-safe state snapshots and `StateChange` events
- State events only when values actually change
- A live `sds200 monitor` terminal display
- Timestamped UTC traffic traces
- Richer site, frequency, modulation, service, talkgroup, unit, volume, squelch,
  RSSI, mute, and recording extraction
- XML stream resynchronization after truncated documents

Start the live monitor:

```bash
sds200 monitor
sds200 monitor --interval 250
sds200 monitor --no-clear
```

Python API:

```python
from sds200 import SDS200

with SDS200.auto() as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )

    with radio.scanner_info_push(interval_ms=500):
        radio.wait()
```

See `docs/transports.md` for the transport contract and network implementation.

## Milestone 3.0.1

- Accepts the SDS200's immediate `PSI` acknowledgement packet.
- Waits for the first periodic `PSI` XML document before starting the monitor.
- Uses one timeout budget for both the acknowledgement and first XML update.
- Rejects negative `PSI,NG` acknowledgements as protocol errors.
- Adds regression coverage for acknowledgement-then-XML behavior.

## Milestone 4: SDS200 Ethernet control

Milestone 4 adds native control through the SDS200's Ethernet interface while
preserving the same command, state, event, trace, and monitor APIs used over
USB.

CLI examples:

```bash
sds200 --host 192.168.1.50 info
sds200 --host 192.168.1.50 scanner-info
sds200 --host 192.168.1.50 monitor
sds200 --host scanner.local command MDL
```

The scanner listens on UDP port 50536 by default. Override it or choose a local
bind interface when needed:

```bash
sds200 \
  --host 192.168.1.50 \
  --udp-port 50536 \
  --bind-address 192.168.1.10 \
  --bind-port 42000 \
  monitor
```

Python API:

```python
from sds200 import SDS200

with SDS200.network("192.168.1.50") as radio:
    print(radio.get_model())
    with radio.scanner_info_push(500):
        radio.wait()
```

Network XML responses are reassembled using their numbered Footer nodes before
they enter the existing XML parser. USB remains the default when `--host` is
not supplied.

The control protocol is unauthenticated and unencrypted. Use it on a trusted
LAN or through a VPN rather than exposing UDP 50536 to the public Internet.
Network audio streaming is a separate future milestone.

See `docs/transports.md` for transport behavior and limitations.


## Milestone 4.0.1

Some SDS200 firmware/network paths return `GSI` and `PSI` scanner-information XML
without the serial-style `GSI,<XML>,` or `PSI,<XML>,` prefix. The UDP decoder now
tracks the command that requested XML, wraps bare documents for the shared parser,
and preserves periodic bare `PSI` updates. Debug logging also records the raw UDP
datagram representation before protocol decoding.


## Milestone 4.0.2

- Fixes strict MyPy type inference in the bare-XML UDP response path.
- Uses a separately narrowed `xml_command` value instead of reassigning a
  previously inferred `str` local variable with `str | None`.


## Milestone 5: discovery, profiles, and network resilience

Milestone 5 adds active LAN discovery, saved connection profiles, a command
round-trip health check, UDP statistics, transport diagnostics, and automatic
retries when numbered XML fragments are missing.

Find USB and network scanners on directly connected IPv4 networks:

```bash
sds200 discover
sds200 discover --network 192.168.0.0/24 --network-only
```

Discovery sends the harmless `MDL` model query to each host. A safety limit
prevents accidentally probing an unexpectedly large route; narrow the CIDR or
set `--max-hosts` explicitly when needed.

Save reusable connections:

```bash
sds200 profile add home --host 192.168.0.251
sds200 profile add desk --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00
sds200 profile list
sds200 --profile home monitor
```

Profiles are stored in `${XDG_CONFIG_HOME:-~/.config}/sds200/profiles.toml`.
Use `--config PATH` to select a different profile file.

Run a health check and show UDP counters:

```bash
sds200 --profile home health
sds200 --host 192.168.0.251 health
```

The UDP transport records command, datagram, byte, timeout, reconnect, XML,
fragment-loss, and retry counters. Numbered XML sequence gaps trigger up to two
automatic request retries by default; use `--max-xml-retries` to change that
policy.


## Milestone 5.0.1

- Starts the final LAN discovery response window after all probes are sent
- Probes hosts in batches and drains replies between batches
- Avoids flooding Linux's ARP/neighbour queue on `/24` networks
- Adds a regression test for slow host-probe loops

For a targeted diagnostic probe, a single scanner can also be checked with:

```bash
sds200 discover --network 192.168.0.251/32 --network-only
```


## Milestone 5.0.2

- Continues LAN discovery after ICMP port-unreachable responses from
  ordinary hosts that do not listen on UDP port 50536
- Handles Linux `ConnectionRefusedError`, Windows `ConnectionResetError`,
  and transient host/network-unreachable receive errors
- Adds a regression test proving an unrelated UDP refusal cannot hide a
  later valid SDS200 `MDL` response


## Milestone 5.0.3

LAN discovery now uses one isolated UDP socket per target with bounded
parallelism. This prevents ARP delays and ICMP errors from unrelated hosts
in a `/24` from interfering with a valid SDS200 response.

The default is 32 concurrent probes:

```bash
sds200 discover --network 192.168.0.0/24 --network-only
```

The concurrency limit can be adjusted:

```bash
sds200 discover \
  --network 192.168.0.0/24 \
  --network-only \
  --workers 16
```

`--timeout` is now explicitly a per-host response timeout.
