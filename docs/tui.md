# Textual TUI

Version 0.13 development introduces an optional full-screen Textual interface for
SDS scanners. Textual is deliberately kept out of the core installation so the
library and existing CLI remain lightweight.

Install the optional interface from PyPI:

```bash
python -m pip install "sds200[tui]"
```

For source development, the `dev` extra includes Textual:

```bash
python -m pip install -e ".[dev]"
```

Launch the shell with the same USB, network, profile, or replay selectors used by
other `sdsctl` commands:

```bash
sdsctl tui
sdsctl --host 192.168.0.251 tui
sdsctl --profile home tui
sdsctl --replay tests/fixtures/replay/sds100-tui.jsonl tui
```

The initial Milestone 13.1 shell loads one scanner-information snapshot and shows:

- connection endpoint and status
- scanner model and firmware
- system, department, and site
- channel, frequency, modulation, and service type
- semantic activity, signal, hold, mute, and recording state
- availability and severity

The interface uses the same renderer-independent `ScannerPresentation`,
`ThemeRole`, and light/dark palettes as the Rich CLI. Meaning remains visible in
text labels rather than relying on color alone.

Keyboard shortcuts:

- `Q`: exit and close the scanner connection cleanly
- `T`: toggle between the built-in dark and light semantic palettes

Milestone 13.2 will add live PSI state subscriptions and reconnect display.
Milestone 13.3 will add scanner controls. Audio remains outside the TUI until the
v0.14.0 audio-integration work.
