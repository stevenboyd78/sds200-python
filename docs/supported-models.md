# Supported scanner models

The library supports the shared remote-command family used by the Uniden
SDS100, SDS150, and SDS200. The model-neutral command is `sdsctl`; the Python
package remains `sds200`, and applications should use the model-neutral
`SDSScanner` class.

## Capability matrix

| Model | USB serial control | Native UDP control | RTSP/RTP audio | GSI battery value | GCS charge status | Volume | Squelch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SDS100 | Yes | No | No | Optional | No | 0–15 | 0–15 |
| SDS150 | Yes | No | No | No | Yes, unverified | 0–15 | 0–15 |
| SDS200 | Yes | Yes | Yes | No | No | 0–29 | 0–19 |

Native LAN discovery, network profiles, USB/Ethernet fallback, and RTSP/RTP
audio remain SDS200-only. The handheld models use USB serial control.

## Model detection and selection

The `MDL` command is used to verify the connected scanner. SDS150 firmware
reports `SDS150GBT`; the public API normalizes that value to `SDS150` while
`ModelResponse.reported_model` preserves the original response.

Automatic USB discovery recognizes model names in stable Linux
`/dev/serial/by-id` paths. Select a model explicitly when multiple scanners are
connected or when a device path does not identify the model:

```bash
sdsctl --model SDS100 info
sdsctl --model SDS150 --port /dev/ttyACM0 info
sdsctl --model SDS200 --host 192.168.0.251 info
```

A model mismatch is rejected after `MDL` verification rather than silently
operating the wrong scanner.

## Handheld battery information

SDS100 firmware 1.26.01 rejects `GCS` with a generic `ERR` response. The SDS100
remote-command specification instead documents an optional `Battery` attribute
inside the `Property` element returned by `GSI` and `PSI`. Hardware testing also
showed that the attribute can be omitted, so absence is reported as
`unavailable` rather than treated as a command failure:

```bash
sdsctl --model SDS100 battery
```

The SDS150 specification exposes detailed `GCS` status, voltage, estimated
capacity, current, and temperature. That path remains implemented but has not
been verified on physical SDS150 hardware:

```bash
sdsctl --model SDS150 battery
```

The SDS200 rejects the high-level battery operation because it is not a
battery-powered handheld.

## Python API

```python
from sds200 import SDSScanner

with SDSScanner.auto(model="SDS100") as radio:
    print(radio.get_model())
    print(radio.get_battery_level())

with SDSScanner.auto(model="SDS150") as radio:
    print(radio.get_charge_status())
```

The historical `SDS200` public name remains an alias of `SDSScanner`, so
existing applications continue to work.

## Validation status

SDS200 USB, Ethernet control, and RTSP/RTP audio behavior have been validated
against physical hardware running firmware 1.26.01. The audio path completed a
five-minute soak with 7,500 packets and every reliability counter at zero.
SDS100 USB discovery, model detection, firmware, volume, squelch, and GSI
scanner information have also been validated on firmware 1.26.01. SDS150 support
is based on Uniden's remote-command specification and hardware-independent
regression tests. Reports from physical SDS150 hardware are welcome; include
firmware, platform, USB path, and sanitized command results.

Bluetooth, U/AWARE integration, and scanner programming databases remain outside
this milestone.

## Capability reporting

Use `sdsctl capabilities` to inspect the connected model. Version 0.11 reports
USB and network control, scanner information, PSI updates, navigation control,
battery sources, model-specific volume and squelch limits, and validation status.
Network audio is intentionally exposed through its independent transport and CLI
rather than the scanner-control capability object.

SDS100 and SDS200 are marked `hardware-validated` for the core model and
transport paths tested during development. SDS150 remains `specification-only`
until physical hardware is tested. This status is not a blanket guarantee that
every individual command has been exercised on hardware. Replay changes only
the active transport endpoint, not the model capability result.

## Navigation commands

The SDS100/SDS200 remote-command specification documents `HLD`, `NXT`, and
`PRV`. Version 0.9 exposes a conservative shared target set for all supported
models. These new typed operations are specification-backed and replay-tested;
they still require release smoke testing on available SDS100 and SDS200
hardware. SDS150 remains specification-only.
