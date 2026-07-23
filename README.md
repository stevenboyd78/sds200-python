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
