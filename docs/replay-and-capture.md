# Session capture and replay

Version 0.9 adds deterministic protocol replay so parser, command, and
CLI behavior can be reproduced without a connected scanner.

## Record a session

Use `--capture` with any hardware-backed command:

```bash
sdsctl --model SDS100 --capture captures/sds100-info.jsonl info
sdsctl --host 192.168.0.251 --capture captures/sds200-gsi.jsonl scanner-info
```

The file is JSON Lines. It contains a schema header followed by connection,
transmitted-command, and received-line events. Timing between events is retained
in milliseconds.

Captures contain raw scanner responses. They may include system names,
department names, channel names, frequencies, scanner addresses, and other
local information. Inspect every capture before sharing or committing it.

Literal redactions can be applied while recording:

```bash
sdsctl \
  --host 192.168.0.251 \
  --capture captures/sds200-redacted.jsonl \
  --redact 192.168.0.251 \
  --redact "Private System Name" \
  scanner-info
```

Each repeated `--redact` value is replaced consistently with a numbered placeholder. Choose replacements that preserve valid protocol structure when the capture will also be replayed.

## Replay a session

Use `--replay` in place of a USB port, network host, or saved profile:

```bash
sdsctl --replay captures/sds100-info.jsonl --model SDS100 info
```

Replay is strict by default. Commands must be sent in the same order and
with the same wire values as the capture. A mismatch fails immediately with the
expected and received commands instead of timing out.

Captured timing is skipped by default so regression tests run quickly. Reproduce original timing with:

```bash
sdsctl --replay captures/session.jsonl --replay-speed 1 info
```

A factor of `2` runs at twice the captured speed. A value of `0` processes events immediately.

## Python API

```python
from sds200 import SDSScanner

with SDSScanner.replay(
    "captures/sds100-info.jsonl",
    expected_model="SDS100",
) as radio:
    print(radio.get_model())
    print(radio.get_firmware())
```

Record a custom transport directly:

```python
from sds200 import RecordingTransport, SDSScanner

recorded = RecordingTransport(
    my_transport,
    "captures/session.jsonl",
    redactions=("192.168.0.251",),
)

with SDSScanner.from_transport(recorded) as radio:
    print(radio.get_scanner_info())
```

`CaptureEvent`, `CaptureSession`, `load_capture`, and `write_capture` are
public for building deterministic test fixtures.

## Fixture guidance

Keep fixtures focused on one behavior or command sequence. Prefer generic
or redacted names and documentation-only addresses. Hardware-derived captures
should state the scanner model, firmware, and transport in nearby test or
documentation text without exposing private channel data.
