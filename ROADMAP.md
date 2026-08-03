# Roadmap

This document records ordered work planned for `sds200-python`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Active milestone

### Milestone 17.4 — Explicit retention execution

- **Milestone 17.3 inventory and retention planning — complete**
  - Merged PR #44 after all CI and CodeQL checks passed.
  - Added renderer-neutral recursive recording inventory and artifact
    classification.
  - Added deterministic non-destructive age, managed-unit, and aggregate-byte
    retention planning.
  - Preserved malformed, unsafe, orphaned, and unknown-time artifacts for explicit
    review.
- **Retention execution foundation — active**
  - Add a renderer-neutral executor that consumes an existing retention plan
    without selecting additional candidates.
  - Require an explicit confirmation boundary before any filesystem mutation.
  - Revalidate every selected managed unit against its inventory root and recorded
    state immediately before execution.
  - Handle each WAV file and adjacent metadata sidecar deterministically as one
    managed unit.
  - Return immutable attempted, completed, skipped, and failed execution results.
- **CLI preview and confirmation workflow — planned**
  - Expose inventory and retention-plan previews before execution.
  - Provide stable human-readable and JSON reports.
  - Require an explicit destructive command and confirmation value.
  - Clearly report unsatisfied limits, protected units, stale plans, and partial
    failures.
- **Safety boundaries — required**
  - Never delete by default or execute from an implicit policy.
  - Execute only `select` decisions from the exact confirmed plan.
  - Never delete retained or protected entries.
  - Refuse path escapes, symlinked managed artifacts, unexpected file types, and
    entries that changed after inventory.
  - Do not remove directories or recursively delete unknown contents.
  - Preserve deterministic results when one managed unit cannot be completed.
- **Validation — planned**
  - Cover confirmation, stale-plan, path-safety, symlink, partial-failure, and
    WAV-plus-sidecar behavior without requiring scanner hardware.
  - Keep normal CI filesystem tests isolated beneath temporary directories.

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
- Milestone 16.7: Linux PortAudio runtime diagnostics, local host-API and
  output-device inspection, Raspberry Pi default and explicit-device playback
  validation, v0.16.1 GitHub and PyPI publication, and clean Python 3.14 PyPI
  installation verification.
