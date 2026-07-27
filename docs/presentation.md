# Semantic presentation model

Version 0.12 development introduces a renderer-independent presentation layer
between scanner state and user interfaces. The module contains no Rich, Textual,
ANSI-color, or terminal dependencies.

```text
scanner state and events
        ↓
semantic presentation model
       ↙ ↘
CLI/Rich   TUI/Textual
```

`present_radio_state()` converts a `RadioStateSnapshot` into an immutable
`ScannerPresentation`. The result describes meaning rather than appearance:

- connection: unknown, connected, degraded, or disconnected
- activity: unknown, idle, scanning, receiving, or holding
- signal: unknown, none, weak, fair, good, or strong
- hold: unknown, none, or active
- availability: unknown, available, stale, or unavailable
- severity: normal, informational, warning, or error
- normalized mute, recording, service-type, and raw-signal values

The SDS scanner signal scale is grouped into stable semantic bands: zero or less
is none, one is weak, two is fair, three is good, and four or greater is strong.
Renderers may map those bands to text, symbols, colors, or other accessible cues.
They must not infer domain meaning from a specific color.

```python
from sds200 import RadioStateSnapshot, present_radio_state

snapshot = RadioStateSnapshot(
    mode="Trunk Scan",
    signal=5,
    mute="Unmute",
    service_type="Interop",
)
presentation = present_radio_state(snapshot, connected=True)

assert presentation.activity == "receiving"
assert presentation.signal == "strong"
assert presentation.as_dict()["severity"] == "normal"
```

Freshness is explicit. Callers set `stale=True` when their own age threshold is
exceeded; the presentation layer does not contain a clock or impose a polling
policy. Likewise, `degraded=True` represents transport or health information
provided by the caller without coupling this module to a particular transport.
