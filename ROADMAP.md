# Roadmap

This document records ordered work planned for `sds200-python`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Active milestone

### Milestone 16.7 — v0.16.1 Linux audio support

- **v0.16.0 publication — complete**
  - Merged the release-preparation change after CI and CodeQL passed.
  - Tagged `v0.16.0` from the validated `main` commit.
  - Published the normal GitHub release and PyPI distributions.
  - Verified a clean Python 3.14 installation of `sds200[tui,playback]==0.16.0`
    with no broken requirements.
- **Linux playback maintenance — complete**
  - Documented the PortAudio runtime required by `sounddevice` on Debian and
    Raspberry Pi OS.
  - Added an actionable `libportaudio2` installation diagnostic when the shared
    library is missing.
  - Added local PortAudio host-API, default-output, and output-device inspection
    without requiring scanner hardware.
  - Preserved deferred output-device opening and scanner-control isolation.
- **Validation — complete**
  - Passed Ruff, MyPy across 42 source files, 444 tests, and documentation checks
    across 28 Markdown files.
  - Built the `0.16.1` source and wheel distributions and passed Twine checks.
  - Validated default and explicit-device playback on Raspberry Pi OS through
    PortAudio and ALSA.
  - Confirmed clean live audio with no RTP loss, duplicates, late packets,
    malformed packets, source mismatches, timestamp discontinuities, playback
    drops, overflows, or callback-status errors.
  - Advanced package and CLI metadata to `0.16.1`.
- **Publication — pending**
  - Merge the maintenance change after CI and CodeQL pass.
  - Tag `v0.16.1` from the validated `main` commit.
  - Publish the GitHub release and PyPI distributions.
  - Verify a clean PyPI installation of `sds200[tui,playback]==0.16.1`.

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
- Add pluggable local playback adapters for PortAudio, PipeWire, PulseAudio, and
  ALSA while preserving bounded nonblocking sink behavior.
- Add per-subscriber health events and metrics for future daemon audio clients.
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
- Add a long-running daemon that owns scanner, PSI, the SDS200's single
  RTSP/RTP audio session, recording, and remote destination sessions.
- Add bounded PCM and PCMU subscriptions so multiple local clients share one
  scanner audio connection.
- Add a local API and event stream suitable for CLI, TUI, web, and integrations.
- Allow CLI and TUI clients to use daemon-owned sessions while retaining an
  explicit standalone fallback.
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
- Milestone 16.5.4: mode-aware Tone Out panels with profile and channel number,
  monitored frequency, modulation, Tone A and Tone B values, hold state, signal,
  and RSSI.
- Milestone 16.5.5: physical SDS200 firmware 1.26.01 validation of normal,
  Quick Search, Close Call, Weather, and Tone Out GSI/PSI states and live
  transitions, with observed protocol differences and unvalidated limits
  documented.
- Milestone 16.6: v0.16.0 release preparation, full CI and CodeQL validation,
  GitHub and PyPI publication, and clean Python 3.14 installation verification.
