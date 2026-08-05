# Roadmap

This document records ordered work planned for `sds200-python`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Recently completed milestone

### Milestone 19.8 — Safe daemon controls

- **Capability-checked control operations — implemented**
  - Added explicit version 1 daemon operations for hold, next, previous, and
    reconnect without unrestricted raw scanner-command passthrough.
  - Reused typed scanner navigation contracts, model capability checks, strict
    targets, bounded counts, and structured scanner acknowledgements.
  - Preserved every read-only operation and standalone CLI/TUI behavior.
  - Did not add resume because no documented or verified resume/unhold scanner
    wire contract exists.
- **Serialized execution and authoritative completion — implemented**
  - Added one nonblocking daemon mutation slot so conflicting clients receive
    `control_busy` instead of interleaving or building a queue.
  - Successful responses follow scanner acknowledgement and include an ordered
    `DaemonControlResult` with timestamps and an authoritative runtime snapshot.
  - Applied one maximum two-second request budget to runtime-lock acquisition and
    scanner completion.
  - Raised the default API worker shutdown deadline to three seconds and added
    rejection of configurations that cannot outlast the maximum request duration.
  - Limited daemon reconnect to the directly owned bounded SDS200 UDP transport;
    serial, fallback, replay, and injected transports return
    `unsupported_operation`.
- **Safety, isolation, and observability — implemented**
  - Added structured redacted `control_busy`, `control_unavailable`,
    `unsupported_operation`, `control_timeout`, `control_rejected`, and
    `control_failed` responses.
  - Kept resulting scanner and runtime changes observable through authoritative
    snapshots and the existing ordered event stream.
  - Preserved private socket permissions, bounded clients, worker isolation, and
    the single-owner scanner lifecycle.
- **Regression, documentation, and hardware validation — complete**
  - Added regression coverage for success, strict parameters, scanner rejection,
    timeouts, unsupported transports, concurrent controls, shutdown, and
    unchanged read-only behavior.
  - Documented envelopes, completion semantics, deadlines, exclusions,
    compatibility, and client responsibilities.
  - Physically validated TGID hold, next, previous, hold release, and bounded
    reconnect against an SDS200 while API, event, PSI, RTSP/RTP, decoded-audio,
    and two PCMU clients remained healthy.
  - Confirmed ordered control sequences, reversible hold state, reconnect
    connection transitions, loss-free matching PCMU delivery, controlled
    `SIGTERM`, successful process exit, and removal of all owned sockets.

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

## Future milestone candidates

These milestone groups preserve intended future work. Their numbering and release
assignment may change before implementation begins.

### Milestone 19.9 — CLI daemon client

- Add daemon status, snapshot, event-watch, safe-control, and optional audio
  client workflows.
- Preserve explicit daemon and standalone selection with clear absent,
  incompatible, and disconnected daemon diagnostics.
- Use the CLI migration to validate protocol compatibility before TUI adoption.

### Milestone 19.10 — TUI daemon client

- Consume daemon snapshots, ordered events, controls, and daemon-owned audio.
- Preserve an explicit standalone mode and show daemon protocol, connection,
  reconnect, and degraded-state information.
- Ensure closing or reconnecting the TUI never stops the daemon-owned scanner
  session.

### Milestone 19.11 — Destination activation and reload

- Activate saved playback, recording, and remote-stream destinations under daemon
  ownership.
- Define validated, previewable configuration replacement and failure-isolated
  destination updates.
- Assign `SIGHUP` reload behavior only after deterministic reload and rollback
  contracts exist.

### Milestone 19.12 — v0.19.0 release

- Complete compatibility, migration, deployment, and systemd documentation.
- Validate multiple clients, slow and malformed clients, shutdown fault
  injection, clean installation, and upgrade behavior.
- Run full Python 3.11–3.14 CI and CodeQL validation plus physical SDS200
  daemon-owned CLI and TUI client testing.

Keep the existing Python import package compatible until a separate migration
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
- a guarded SDS200 reboot-recovery operation based on the reported `MSM,1`
  behavior, which places the scanner in mass-storage mode briefly before reboot;
  require protocol and firmware validation, explicit operator intent, bounded
  outage handling, and post-reboot control, PSI, and RTSP recovery checks before
  exposing it, and do not treat it as ordinary reconnect;
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
- Milestones 17.1–17.4: renderer-neutral recording identities, configurable
  recording organization, recursive inventory and artifact classification,
  deterministic retention planning, explicit inventory-bound execution, local CLI
  preview and confirmation workflows, and stale-state and filesystem safety
  validation.
- Milestone 17.5: v0.17.0 release preparation, full CI and CodeQL validation,
  trusted PyPI publication, GitHub release publication, and clean Python 3.14
  installation verification from public PyPI.
- Milestone 18.1: renderer-neutral remote-destination health classification,
  serializable operational snapshots, ordered lifecycle transition events,
  timezone-aware timestamps, listener isolation, and shutdown-safe concurrency.
- Milestone 18.2: immutable saved Broadcastify destination profiles, dedicated
  versioned TOML persistence, environment-variable secret references, strict
  schema validation, deterministic atomic writes, and validated adapter conversion.
- Milestone 18.3: renderer-neutral live stream metadata, deterministic bounded
  titles, newest-value worker publication, duplicate suppression, rate limiting,
  retry and redaction metrics, and a Broadcastify-compatible Icecast metadata
  adapter isolated from PCM delivery.
- Milestone 18.4: public renderer-neutral encoder process contracts, immutable
  command and lifecycle configuration, reusable pipe-backed subprocess ownership,
  bounded interruption and terminate/kill finalization, continuously drained
  diagnostics, and Broadcastify migration without changing its fixed FFmpeg MP3
  profile or Icecast transport behavior.
- Milestone 18.5: renderer-neutral buffered local playback, preserved PortAudio
  compatibility, explicit PipeWire, PulseAudio, and ALSA command adapters,
  reusable dynamic PCM subscriber routing, immutable subscriber health snapshots
  and ordered transitions, isolated lifecycle failures, redacted diagnostics, and
  preserved separation from RTP reception and scanner control.
- Milestone 18.6: v0.18.0 release preparation, full static, test,
  documentation, distribution, clean-install, CI, and CodeQL validation, trusted
  PyPI publication, GitHub release publication, and clean Python 3.14 installation
  verification from public PyPI.
- Milestone 19.1: immutable layered application configuration with fixed
  default, system, user, environment, and CLI precedence; versioned strict TOML
  loading; deterministic `sdsctl` paths; source-aware diagnostics and provenance;
  read-only legacy discovery; CLI integration; and host-independent regression
  coverage.
- Milestone 19.2: compact Raspberry Pi TUI composition with dense borderless
  short-screen panels, concise audio and PSI summaries, an essential-controls
  footer, deterministic responsive and resize coverage, refreshed SVG evidence,
  and physical 800 by 480 Raspberry Pi validation.
- Milestone 19.3: renderer-neutral ownership of scanner control, PSI, one
  RTSP/RTP decoded-PCM fanout, and dynamic destinations; immutable runtime
  snapshots and ordered transitions; serialized startup, reverse-order cleanup,
  concurrent idempotent stop, listener isolation, redacted failures, and
  lifecycle regression coverage.
- Milestone 19.4: foreground `sdsctl daemon` ownership of scanner control, PSI,
  one RTSP/RTP audio session, and one decoded-PCM router; signal-safe SIGINT and
  SIGTERM shutdown, restored handlers, preserved primary failures, documented
  systemd `Type=simple` operation, regression coverage, and physical SDS200
  validation.
- Milestone 19.5: strict versioned read-only local API envelopes, capability
  negotiation, authoritative runtime, scanner, audio, and router snapshots,
  private Unix-domain socket ownership, safe stale-socket handling, bounded and
  isolated clients, deterministic process integration, CLI server limits, and
  host-independent regression coverage.
- Milestone 19.6: immutable versioned daemon event envelopes, authoritative
  snapshot-first subscriptions, one serialized renderer-neutral source
  aggregator, independent bounded queues with explicit sequence-gap recovery, a
  separate private `events.sock` endpoint, bounded clients and encoded event
  sizes, deterministic process lifecycle integration, CLI configuration,
  documentation, regression coverage, and physical SDS200 validation.
- Milestone 19.7: authoritative accepted-PCMU publication before decode,
  immutable RTP continuity metadata, independent bounded per-client queues with
  cumulative local-loss accounting, a strict versioned binary frame protocol,
  a third private `pcmu.sock` endpoint, bounded clients, payloads, frames, waits,
  and shutdown, deterministic daemon lifecycle integration, public decoding
  helpers, documentation, extensive regression coverage, a reusable hardware
  validator, and physical SDS200 validation with simultaneous API, event, and
  dual-PCMU clients.
