# Roadmap

This document records ordered work planned for `sds200-python`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Active milestone

### Milestone 21.1 — lossless Favorites format foundation

- Begin from the completed and physically validated v0.20.2 release boundary.
- Base the initial Favorites implementation on the SDS100/200 File Specification
  v1.08 plus observed read-only files copied from an SDS200 running firmware
  1.26.01.
- Parse `favorites_lists/f_list.cfg` and Favorites `.hpd` content into immutable,
  lossless positional records before projecting a typed hierarchy.
- Preserve source record order, blank fields, duplicate values, trailing empty
  fields, undocumented extra positional fields, and unknown commands without
  normalization or silent data loss.
- Model conventional and trunked programming separately where their record
  structures differ while exposing renderer-neutral Favorites List, system,
  department, site, and channel hierarchy.
- Use sanitized synthetic fixtures derived from observed structures; do not
  check private scanner programming data into the repository.
- Keep Milestone 21.1 independent of live scanner storage, FTP, USB mass storage,
  scanner control, renderers, and every write operation.

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

## Current and future milestone groups

These milestone groups preserve the current product sequence and intended future
work. Numbering and release assignment may change before an unstarted slice
begins.

### Milestone 20 — Web dashboard, themes, and Home Assistant

- Milestone 20.1 completed the optional FastAPI and Uvicorn service foundation,
  loopback-only `sdsctl web` command, versioned health, daemon-status,
  authoritative-snapshot, and OpenAPI routes, redacted daemon errors, package
  extras, documentation, and host-independent regression coverage.
- Milestone 20.2 completed the first accessible responsive read-only browser
  shell, packaged HTML, CSS, and JavaScript assets, two-second daemon-status
  polling, scanner and runtime summaries, restrictive browser response headers,
  light and dark presentation, compact layouts, reduced-motion behavior, and
  host-independent shell and static-asset tests.
- Milestone 20.3 completed same-origin Server-Sent Events over the existing
  ordered daemon event socket, authoritative snapshot-first delivery, browser
  incremental updates and reconnect behavior, polling fallback, periodic
  reconciliation, redacted failures, and event-client lifecycle tests.
- Milestone 20.4 completed explicit browser playback of daemon-owned PCMU with
  same-origin binary streaming, manual Play and Stop controls, AudioWorklet
  mu-law decoding, bounded buffering and resampling, queue-loss and RTP-loss
  telemetry, hidden-tab playback continuity, deterministic PCMU and SSE client
  cleanup, idle daemon event-client reaping, bounded web-server graceful
  shutdown, and physical SDS200 validation.
- Milestone 20.5 completed daemon-owned browser recording workflows over the
  existing decoded-PCM router, recording status/start/stop/inventory API
  operations, ordered `recording.state` events, a private `recordings.sock`
  finalized-file service, safe inventory-relative WAV playback and download,
  recording survival across browser and web-process disconnects, daemon-shutdown
  finalization, regression coverage, packaging validation, and physical SDS200
  validation.
- Milestone 20.6 completed capability-negotiated browser semantic scanner
  controls, bounded reconnect, stable redacted failures, authoritative
  reconciliation, self-hosted interactive API documentation, regression
  coverage, and physical SDS200 validation.
- Milestone 20.7 completed browser-local system-adaptive, LCARS-inspired,
  Matrix-inspired, First Responder, and Amateur Radio themes over one shared
  accessible dashboard, deterministic documentation captures, packaging
  coverage, and CodeQL hardening.
- Milestone 20.8 completed the native daemon MQTT publication substrate:
  strict optional configuration, optional Paho packaging, retained availability,
  canonical semantic state, non-retained semantic events, PSI suppression,
  worker-owned retry/backoff, and daemon lifecycle ownership.
- Milestone 20.9 completed opt-in semantic MQTT scanner commands through the
  daemon's existing control dispatcher, including bounded transport input,
  correlated responses, retained-command rejection, manual acknowledgement, and
  request-ID deduplication without unrestricted raw scanner keys.
- Milestone 20.10 completed read-only Home Assistant MQTT device Discovery over
  the generic daemon state contract, with ten entities, namespace-derived device
  identity, birth-triggered republication, and no Home Assistant-specific scanner
  owner or command path.
- Milestone 20.11 completed Home Assistant App packaging around the existing
  daemon and web dashboard, Supervisor MQTT service adaptation, Ingress path
  portability and peer enforcement, persistent recordings, fixed UDP 50000 RTP
  publication without host networking, multi-architecture image automation, and
  physical HAOS validation of scanner control, live audio, recording persistence,
  App restart, and all ten MQTT Discovery entities.
- The v0.20.1 corrective release hardened Milestone 20.11 by moving recordings
  into writable Home Assistant `/media`, safely migrating legacy
  `/data/recordings`, improving the dashboard layout, and validating
  repository-managed upgrade and reinstall behavior on physical HAOS.
- Milestone 20.12.1 completed Home Assistant configuration translations and
  post-v0.20.1 roadmap synchronization without changing runtime semantics.
  Repository-managed rendering was physically validated on HAOS in the v0.20.2
  acceptance run.
- Milestone 20.12.2 completed the first-party Lovelace SDS200 card, including
  safe `/local` delivery, Home Assistant's graphical card form, supported state
  subscription, deterministic package validation, and explicit isolation of
  optional card-installation failures. Resource delivery, manual registration,
  picker/editor behavior, and live read-only rendering were physically validated
  on HAOS in the v0.20.2 acceptance run.
- Milestone 20.12.3 completed the deliberate Home Assistant control adapter:
  four authoritative non-optimistic Hold switches plus Previous Channel, Next
  Channel, and Reconnect Scanner buttons over seven dedicated QoS 0 non-retained
  topics. The adapter generates fresh internal daemon request IDs, reuses the
  existing typed semantic controls and bounded current-channel resolver, clears
  navigation context on disconnect/resynchronization, keeps generic daemon MQTT
  commands disabled for the App, and preserves the daemon as sole scanner owner.
  All seven controls, generic-command isolation, and the single-owner boundary
  were physically validated on HAOS in the v0.20.2 acceptance run.
- v0.20.2 completed Milestone 20 release closure with reviewed wiki publication,
  PyPI publication, amd64/aarch64 Home Assistant image publication,
  repository-managed HAOS acceptance, and a normal GitHub Release.
- Keep authenticated/TLS LAN access, a network transport for remote daemon-backed
  CLI/TUI/GUI clients, and any optional host-network App variant as a separate
  later security boundary. The current daemon client interfaces remain private
  Unix-domain sockets, so host networking alone would not expose them remotely.

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

### Milestone 25 — Portability, containers, and additional interfaces

- Continue prioritizing Linux and Raspberry Pi operation.
- Add a supported container image and Docker Compose examples for daemon,
  daemon-client, and web-dashboard workflows.
- Make network-connected SDS100, SDS150, and SDS200 operation the primary
  container deployment path.
- Document scanner host addressing, configuration, state, cache, recording, log,
  destination-manifest, and private-socket mounts; health checks; restart policy;
  signal handling; and orderly container shutdown.
- Document Linux USB serial passthrough through stable
  `/dev/serial/by-id/...` paths, narrowly scoped device access, and required host
  group permissions without recommending broadly privileged containers.
- Document host networking versus explicit socket or port exposure and identify
  platform-specific Docker limitations.
- Add host-independent container integration tests, followed by separate physical
  network and USB validation.
- Preserve native systemd deployment as the preferred production option when
  direct host-device, local-audio, or operating-system integration is important.
- Validate Windows and macOS behavior without blocking current releases.
- Consider a future desktop GUI over the same renderer-neutral services.
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
- Milestone 19.8: capability-checked hold, next, previous, and bounded reconnect
  controls; serialized mutation ownership; scanner-acknowledged completion;
  stable redacted failures; regression coverage; and physical SDS200 validation.
- Milestone 19.9: explicit daemon CLI status, snapshots, safe controls, ordered
  event watching, PCMU playback and WAV recording, protocol compatibility, and
  physical SDS200 validation.
- Milestone 19.10: explicit daemon-backed TUI state, events, controls, playback,
  recording, and saved-recording workflows without opening scanner hardware or
  stopping daemon ownership, plus physical SDS200 validation.
- Milestone 19.11: validated playback, recording, and remote-profile destination
  manifests; deterministic activation resources; transactional replacement;
  failure-isolated reload; daemon lifecycle ownership; `SIGHUP`; regression
  coverage; and physical SDS200 validation.
- Milestone 19.12: compatibility, migration, deployment, and systemd
  documentation; adversarial client and shutdown validation; full Python
  3.11–3.14 CI and CodeQL validation; physical daemon-owned CLI and TUI
  validation; v0.19.0 trusted PyPI and GitHub publication; and clean Python 3.14
  installation verification from public PyPI.
- Milestone 20.1: optional loopback-only FastAPI/Uvicorn web-service foundation,
  versioned health/status/snapshot/OpenAPI routes, redacted failures, package
  extras, documentation, and host-independent regression coverage.
- Milestone 20.2: accessible responsive browser shell, packaged web assets,
  daemon-status polling, scanner/runtime summaries, restrictive response
  headers, light/dark presentation, compact layouts, and accessibility coverage.
- Milestone 20.3: same-origin Server-Sent Events over the ordered daemon event
  service with snapshot-first delivery, incremental updates, reconnect, polling
  fallback, authoritative reconciliation, and lifecycle coverage.
- Milestone 20.4: explicit browser playback over daemon-owned PCMU with
  AudioWorklet decoding, bounded buffering/resampling, loss telemetry,
  deterministic client cleanup, and physical SDS200 validation.
- Milestone 20.5: daemon-owned browser recording, recording API and ordered
  events, private finalized-WAV service, newest-first inventory, safe saved
  playback/download, disconnect survival, shutdown finalization, packaging and
  regression coverage, and physical SDS200 validation.
