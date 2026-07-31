# Roadmap

This document records ordered work planned for `sds200-python`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Active milestone

### Milestone 16.5 — TUI diagnostics and mode-aware screens

- **16.5.0 — TUI log panel — complete**
  - Route package log records into a bounded, thread-safe in-app panel.
  - Show the panel by default and allow it to be toggled without losing records.
  - Preserve optional file logging and restore stderr logging after TUI shutdown.
  - Stop periodic polling before widget teardown and suppress late refreshes.
- **16.5.1 — Screen-mode foundation — complete**
  - Add a renderer-neutral scanner-screen classifier with an unknown-screen
    fallback.
  - Preserve the raw scanner `Mode` and `V_Screen` values.
  - Add fixture and transition coverage for mode-specific GSI/PSI state.
- **16.5.2 — Quick Search and Close Call — complete**
  - Present search frequency, modulation, hold/hit state, signal, RSSI, and
    reported tone or digital-code details.
  - Support both `SrchFrequency` and `CcHitsChannel` scanner-state nodes.
- **16.5.3 — Weather Mode — complete**
  - Present weather channel, frequency, scan/hold state, signal, and RSSI.
  - Capture alert and SAME details when the scanner reports them.
- **16.5.4 — Tone Out Mode — active**
  - Present tone-out channel/profile, monitored frequency, tone values, and
    standby/detected/hold state when reported.
- **16.5.5 — SDS200 hardware validation**
  - Capture representative physical SDS200 GSI/PSI XML for every supported
    special screen mode.
  - Validate live transitions between normal scanning, search/Close Call,
    weather, and tone-out screens.
  - Document tested firmware, observed fields, and known limitations.

### Milestone 16.6 — v0.16.0 release preparation

- Select only completed and validated Milestone 16 work for release.
- Run the full software, package, Raspberry Pi, audio, and shutdown checklists.
- Confirm documentation distinguishes fixture coverage from physical validation.
- Publish a normal GitHub release and verify a clean PyPI installation.

## Deferred hardware validation

### SDS150 physical validation

Physical SDS150 validation is deferred until representative hardware is available.
It does not block v0.16.0.

When hardware becomes available:

- validate model detection and USB serial control;
- validate battery and charge reporting;
- validate navigation and PSI state;
- record tested firmware and transport evidence;
- document any limits that differ from modeled or fixture-tested behavior.

Until then, documentation must describe SDS150 support as implemented or
fixture-tested, not hardware-validated.

## Post-v0.16 milestone candidates

These milestone groups preserve intended future work. Their numbering and release
assignment may change before implementation begins.

### Milestone 17 — Recording organization and retention

- Add safe filename components derived from scanner state.
- Organize recordings by configurable scanner, date, system, department, site, or
  channel components.
- Add retention planning and reporting helpers.
- Do not delete recordings by default.
- Require explicit policy and preview before destructive retention actions.
- Preserve recording sidecars and audio files as one managed unit.

### Milestone 18 — Remote-audio operations

- Add per-destination health events and operational metrics.
- Add saved destination profiles that reference secrets rather than embedding
  credentials.
- Synchronize optional stream metadata with live PSI state.
- Support pluggable encoder processes for destinations that do not accept native
  8 kHz mono PCM or G.711 mu-law.
- Evaluate local playback backends for systems where PortAudio is unavailable.
- Preserve the rule that audio failures never interrupt scanner control.

### Milestone 19 — Layered configuration, daemon, and local API

- Adopt `sdsctl` as the user-facing configuration and service namespace.
- Support layered configuration with precedence:
  defaults, system configuration, user configuration, environment, then CLI.
- Use `/etc/sdsctl/` for system configuration.
- Use `~/.config/sdsctl/` for user configuration.
- Use `~/.local/state/sdsctl/` for persistent user state.
- Use `~/.cache/sdsctl/` for disposable cached data.
- Detect legacy `sds200` configuration and provide a safe migration path.
- Add a long-running daemon that owns scanner, PSI, audio, recording, and remote
  destination sessions.
- Add a local API and event stream suitable for CLI, TUI, web, and integrations.
- Keep the existing Python import package compatible until a separate migration
  plan justifies a rename.

### Milestone 20 — Web dashboard and Home Assistant

- Add a responsive browser dashboard backed by the daemon API.
- Bind to localhost by default.
- Require explicit authentication and transport-security planning before remote
  exposure.
- Expose scanner state, connection health, audio state, recordings, logs, and safe
  controls.
- Add Home Assistant integration through the daemon API rather than opening a
  second scanner connection.
- Evaluate HACS distribution after the integration contract stabilizes.

### Milestone 21 — Favorites Workspace foundation

- Add a renderer-neutral Favorites data model.
- Add read-only hierarchy browsing for Favorites Lists, systems, departments,
  sites, and channels.
- Add search, filtering, validation, and comparison views.
- Add import and export formats that preserve unknown scanner fields.
- Keep the first implementation read-only against scanner storage.
- Use fixtures and copied storage images for normal automated tests.

### Milestone 22 — Verified Favorites writes and storage backends

- Add create, edit, and delete workflows with explicit previews.
- Support USB mass-storage discovery and safe device handling.
- Support FTP only on trusted local networks or VPNs.
- Require a complete backup before every write operation.
- Stage writes away from the active data set.
- Read back and verify staged content before replacement.
- Record rollback manifests and restore instructions.
- Detect concurrent changes and refuse ambiguous overwrites.
- Keep write operations deterministic, recoverable, and auditable.

### Milestone 23 — External Favorites data and synchronization

- Add RadioReference-assisted import with update previews.
- Preserve provenance and field ownership for externally sourced data.
- Support merge decisions and detaching local records from an external source.
- Store credentials and application keys outside exported Favorites data.
- Investigate MyRR synchronization only through an approved and documented
  interface.
- Do not scrape or depend on undocumented private interfaces.

### Milestone 24 — Advanced protocol and analysis modes

Research and fixture work must precede public support for:

- Favorites and hierarchy retrieval such as `GLT`;
- Favorites quick keys such as `FQK`;
- Quick Search control such as `QSH`;
- scanner recording control such as `URC`;
- analysis controls such as `AST` and `APR`;
- waterfall data such as `PWF` and `GWF`;
- menu operations such as `MNU`, `MSI`, `MSV`, and `MSB`;
- additional NAC, RAN, color-code, area, activity, and quality details;
- conventional and trunking discovery modes;
- system-status and RF-power plot screens.

Each feature must preserve unknown fields and include captured or synthetic
fixtures before renderer-specific implementation.

### Milestone 25 — Portability and additional interfaces

- Continue prioritizing Linux and Raspberry Pi operation.
- Validate Windows and macOS behavior without blocking current releases.
- Consider a future desktop GUI over the same renderer-neutral services.
- Preserve the option for an LCARS-inspired interface theme.
- Prefer scalable SVG assets and responsive layouts.
- Treat the Raspberry Pi 7-inch 800 by 480 display as a compact reference layout,
  not a universal fixed resolution.

## Completed milestone groups

- Milestones 1–10: typed core protocol, transports, discovery, profiles,
  reliability, documentation, packaging, and static quality gates.
- Milestones 11–14: SDS200 RTSP/RTP audio, native PCMU decoding, WAV recording,
  reusable recording sessions, and Textual audio controls.
- Milestone 15: deterministic TUI lifecycle hardening, operational logging,
  automatic stale-PSI recovery, Raspberry Pi fault injection, and v0.15.0.
- Milestone 16.1: decoded-PCM fanout, optional local playback, simultaneous
  playback and recording, sink reliability counters, roadmap enforcement, and
  SDS200 hardware validation.
- Milestone 16.2: immediate TUI live playback, repeatable recordings, a
  newest-first recording library, saved-recording playback, one shared RTSP/RTP
  stream, deferred PortAudio startup, warm mute and resume behavior, and SDS200
  hardware validation.
- Milestone 16.3: service-neutral remote PCM destinations, a
  Broadcastify-compatible Icecast source adapter, an Asterisk Music-on-Hold
  bridge, deterministic reconnect and shutdown validation, physical SDS200
  testing, and assigned production-feed authorization and routing validation.
- Milestone 16.4: renderer-neutral recording metadata, atomic JSON sidecars, and
  optional TUI lifecycle integration.
- Milestone 16.5.0: a bounded operational log panel, preserved file logging,
  descriptive panel titles, wide-layout corrections, and shutdown-safe polling.
- Milestone 16.5.1: renderer-neutral scanner-screen classification, preserved raw
  `Mode` and `V_Screen` values, an unknown-screen fallback, and synthetic GSI/PSI
  fixture and transition coverage.
- Milestone 16.5.2: mode-aware Quick Search and Close Call TUI panels with
  frequency or hit details, modulation, hold state, signal, RSSI, and detected
  `SAD` tone or digital-code reporting.
- Milestone 16.5.3: mode-aware Weather panels with channel number, frequency,
  modulation, monitor or alert state, hold, signal, RSSI, and scanner-reported
  SAME selection.
