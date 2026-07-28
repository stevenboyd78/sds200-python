# Textual TUI

Version 0.13 introduces an optional full-screen Textual interface for
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

Launch the interface with the same USB, network, profile, or replay selectors used
by other `sdsctl` commands:

```bash
sdsctl tui
sdsctl --host 192.168.0.251 tui
sdsctl --profile home tui
sdsctl --replay tests/fixtures/replay/sds100-tui-live.jsonl tui
```

The TUI starts continuous PSI scanner-information updates after loading the model,
firmware, and initial GSI snapshot. The default 500 ms update interval and 3 second
freshness threshold may be adjusted independently:

```bash
sdsctl --host 192.168.0.251 tui --interval 250 --stale-after 2
```

The interface shows:

- connection endpoint and connected, degraded, or disconnected status
- scanner model and firmware
- system, department, and site
- channel, frequency, modulation, and service type
- semantic activity, signal, hold, mute, and recording state
- live PSI, reconnect, diagnostic, and stale-data status
- availability and severity derived from the shared presentation model

Radio callbacks originate on control-transport threads. The adapter marshals every
widget update into Textual's event loop, unsubscribes callbacks on shutdown, and
stops PSI before the scanner connection closes. Reconnects retain the last known
state while making its disconnected or stale status explicit.

The interface uses the same renderer-independent `ScannerPresentation`,
`ThemeRole`, and light/dark palettes as the Rich CLI. Meaning remains visible in
text labels rather than relying on color alone.

Keyboard shortcuts:

- `Q`: exit, stop PSI, unsubscribe callbacks, and close the connection
- `T`: toggle between the built-in dark and light semantic palettes
- `?`: show or hide the complete keyboard reference
- `H`: hold the current indexed channel
- `S`: hold the current indexed system
- `D`: hold the current indexed department
- `I`: hold the current indexed site
- `N`: move to the next indexed channel
- `P`: move to the previous indexed channel
- `+` / `-`: raise or lower volume
- `]` / `[`: raise or lower squelch

Scanner commands execute sequentially on a background worker so a slow command
round trip does not block the Textual event loop. Hold controls use the documented
system, department, site, TGID, or conventional-frequency indexes from live GSI/PSI
state. Channel next/previous controls require a documented `TGID` or conventional
frequency index. The status panel reports queued, completed, unavailable, and
failed controls without relying on color alone.

The deterministic `sds100-tui-controls.jsonl` replay is a strict, one-shot command
script rather than a scanner simulator. For a manual control pass, start a fresh TUI
session and tap `H`, `S`, `D`, `I`, `N`, `P`, `+`, and `]` exactly once in that
order. Quit and restart the replay to reset its command cursor after any deviation.

## Responsive Raspberry Pi layout

The interface adapts automatically to terminal dimensions; no compact-mode flag is
required. Terminals narrower than 80 columns remove decorative borders and spacing.
At fewer than 32 rows, the dedicated identity panel is hidden while the model remains
in the title and the endpoint and firmware remain in the header subtitle. The main
content stays vertically scrollable, so no scanner state is discarded.

At 120 columns or wider, panels switch to a two-column dashboard. An 80 by 24
terminal is the recommended Raspberry Pi starting size, while a 64 by 20 terminal is
supported as the compact regression-test target. Press `?` when the compact footer
only shows the essential quit, theme, and key-reference actions.

The same responsive classes are exercised in headless Textual tests at compact and
wide terminal sizes.

Audio remains outside the TUI until the v0.14.0 audio-integration work.

Project authorship and AI-assisted development are documented in [Acknowledgments](../ACKNOWLEDGMENTS.md).
