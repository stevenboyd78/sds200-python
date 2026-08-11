# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/) as the public API matures.

## [Unreleased]

### Added

- Lossless read-only parsing for SDS100/200 Favorites source files preserves
  physical record order, ASCII positional fields, blank and trailing fields,
  per-record line endings, unknown commands, and undocumented extra positions
  without applying hierarchy or scanner-storage semantics.
- Renderer-neutral read-only `.hpd` hierarchy projection distinguishes
  conventional and trunked systems, departments, channels, trunk sites, site
  frequencies, and band-plan records while retaining originating raw records,
  observed supplemental records, and explicitly unclassified source records.
- Lossless `f_list.cfg` catalog projection exposes ordered Favorites List display
  names and `.hpd` filenames while retaining complete positional `F-List`
  records, metadata, duplicate mappings, and unclassified source records.
- Pure in-memory Favorites workspace binding combines catalog entries with
  explicitly named `.hpd` hierarchy documents using exact filename equality and
  reports missing targets, ambiguous duplicate documents, duplicate catalog
  mappings, and orphan supplied documents without filesystem semantics.
- Immutable read-only Favorites storage snapshots carry exact `f_list.cfg` and
  ordered named document bytes through the existing parser, hierarchy, catalog,
  and workspace projections while rejecting absolute and traversing storage
  filenames without case folding, trimming, repair, or filesystem access.
- Read-only copied-tree Favorites storage loads an offline `favorites_lists`
  directory using an exact regular `f_list.cfg` plus deterministic immediate
  lowercase-`.hpd` regular files, rejects managed symbolic links, preserves exact
  bytes and filenames, and performs no format interpretation or writes.

## [0.20.2] - 2026-08-10

### Added

- Home Assistant-specific MQTT scanner controls add authoritative System,
  Department, Site, and Channel Hold switches plus Previous Channel, Next
  Channel, and Reconnect Scanner buttons. Dedicated QoS 0 non-retained command
  topics translate into fresh internal typed daemon requests without enabling
  the generic request-envelope MQTT command topic, adding raw scanner commands,
  or creating another scanner owner. Previous/Next reuse the existing bounded
  TGID/CFREQ current-channel resolver and invalidate cached navigation context
  after scanner disconnect or daemon-event resynchronization.
- First-party read-only SDS200 Lovelace card packaging and Home Assistant
  `/local/sds200/sds200-card.js` delivery without HACS or Home Assistant Core
  API access; the card uses Home Assistant's supported state context and built-in
  graphical form editor to select the Discovery entities, and exposes no scanner
  controls.
- Home Assistant App configuration translations provide user-facing names and
  descriptions for scanner host, MQTT topic prefix, and recording directory;
  the recording directory description explicitly identifies Home Assistant
  `/media` as the path root and shows the default resolved location.

## [0.20.1] - 2026-08-10

### Added

- Home Assistant App `recording_directory` option for a media-relative recording
  library under `/media`, defaulting to `sdsctl/recordings`, with a writable
  Supervisor media mapping.

### Changed

- Home Assistant App dashboard layout now groups daemon runtime with scanner
  connection, keeps scanner reconnect with connection state, separates active
  capture from finalized recordings, and gives the recording library a dedicated
  responsive grid area.
- Home Assistant App sidebar presentation now requests the `mdi:radio-tower`
  icon.

### Fixed

- Home Assistant App startup safely migrates legacy v0.20.0 recordings and
  metadata sidecars from `/data/recordings` into the configured media library.
  Migration preflights destination conflicts, never overwrites differing files,
  byte-verifies copied files before removing their sources, and can resume after
  an interrupted partial migration.

## [0.20.0] - 2026-08-09

### Added

- Project-consistent icon and logo assets for Home Assistant App presentation.
- Milestone 20.11 Home Assistant App packaging around the existing single-owner
  daemon and web dashboard, with Supervisor-managed startup, required MQTT service
  discovery, strict `/data/options.json` configuration, private runtime sockets,
  persistent `/data/recordings`, amd64/aarch64 images, release-only GHCR
  publication, and Home Assistant Ingress on port 8099 without creating another
  scanner, PSI, RTSP/RTP, or control owner.
- Home Assistant Ingress path portability for dashboard, API documentation,
  scanner controls, SSE, browser PCMU, and saved-recording requests; an explicit
  Ingress peer guard that admits only the Supervisor proxy; Ingress-compatible
  frame policy; and a browser-audio compatibility fallback when AudioWorklet is
  unavailable in a non-secure Home Assistant browser context.
- A fixed Home Assistant App RTP destination on UDP port 50000 with an explicit
  Supervisor port mapping so SDS200 RTP sent back to the HAOS host reaches the
  container-owned daemon audio socket without enabling host networking. Physical
  HAOS validation with SDS200 firmware 1.26.01 confirmed live scanner state,
  Channel Hold/release, browser audio, recording and WAV playback, recording
  persistence across App restart, clean stop/start lifecycle, and all ten
  Home Assistant MQTT Discovery entities with correct model and firmware metadata.
- Milestone 20.10 optional Home Assistant MQTT device Discovery over the existing
  generic daemon MQTT state contract. Discovery is disabled by default, derives a
  stable device identity from the configured MQTT topic prefix, publishes ten
  read-only daemon/scanner/radio/audio/recording entities after authoritative
  snapshots, and republishes after broker reconnect, event-stream
  resynchronization, or an exact configured Home Assistant birth message. The
  birth subscription uses QoS 0 without changing semantic-command manual
  acknowledgement or request-ID deduplication, and configurations reject a birth
  topic that collides with the command topic. No Home Assistant control topic,
  second scanner owner, PSI stream, or RTSP/RTP session is introduced.
- Milestone 20.9 opt-in daemon MQTT scanner controls through the existing
  semantic daemon API boundary. `commands_enabled` defaults to false; enabled
  workers subscribe only after the authoritative initial snapshot, accept only
  hold/hold-state/next/previous/reconnect operations, reject retained and
  oversized requests, publish non-retained correlated responses, use Paho manual
  acknowledgements, and keep a bounded process-local request-ID response cache so
  immediate QoS redelivery does not repeat non-idempotent controls. The Paho
  callback remains transport-only, the inbound queue is bounded, and the local
  socket server and MQTT worker share one `DaemonReadOnlyApi` dispatcher.
- Milestone 20.8 native daemon MQTT foundation with a strict optional version 1
  `daemon-mqtt.toml` broker manifest, `--mqtt-config` override, a separate
  `sds200[mqtt]` Paho MQTT 2.x extra, environment-referenced password secrets,
  validated reconnect policy, and dependency/configuration preflight before
  scanner construction only when MQTT is configured.
- A failure-isolated daemon MQTT publication worker over the existing authoritative
  event stream. It publishes retained `online`/`offline` availability with a
  retained offline last will, canonical daemon/scanner/radio/audio/recording and
  per-destination state topics, and a non-retained semantic event stream while
  suppressing packet-rate PSI. Sequence gaps force authoritative resubscription,
  broker failures use worker-owned exponential backoff, graceful local failures
  publish offline before disconnect, and MQTT shutdown precedes runtime shutdown
  without opening another scanner or RTSP/RTP session.
- Milestone 20.7 browser-local dashboard themes with a system-adaptive default
  plus immersive LCARS-inspired, Matrix-inspired, First Responder, and Amateur
  Radio environments over the existing shared accessible dashboard structure.
  Desktop-class custom themes use a dense full-screen workstation composition
  with renderer-specific console rails, terminal fields, dispatch/CAD surfaces,
  scanner chassis details, layered depth, reflections, scan effects, and other
  pointer-inert decorative staging. Compact layouts reflow telemetry for smaller
  displays, reduced-motion preferences suppress decorative animation, and no
  state is communicated by color alone. A same-origin CSP-compatible bootstrap
  script restores the saved choice before the stylesheet paints, theme selection
  is persisted only in browser local storage, browser color metadata follows the
  active appearance, and the feature adds no daemon, scanner, API-state, CLI, or
  TUI theme coupling.
- Deterministic web-dashboard documentation captures generated from the real
  packaged dashboard with fictional daemon, scanner, radio, recording, and
  reliability data. The repository helper uses native headless Chrome, isolated
  per-capture browser profiles, bounded capture timeouts, and checked dimensions
  to produce the five 1920x1080 theme references plus a 1366x768 compact-layout
  reference without adding screenshot behavior to the shipped web service.
- Milestone 20.6 loopback browser scanner controls for semantic system,
  department, site, and channel hold/release, previous/next channel navigation,
  and bounded reconnect. The web layer negotiates daemon-advertised
  capabilities, sends explicit hold desired state without raw scanner targets
  or keys, preserves documented snapshot-based selection resolution for channel
  navigation, prevents overlapping browser control mutations, maps stable
  redacted failures, and renders authoritative completion snapshots without
  disturbing ordered SSE reconciliation or daemon scanner ownership.
- Foreground-daemon PSI silence recovery using every successfully parsed PSI
  frame as the liveness signal, with default-on bounded recovery after 10 seconds,
  a 60-second retry cooldown, command-line policy controls, and the existing
  daemon mutation lock so automatic reconnect cannot overlap browser or local
  scanner controls. A busy mutation defers recovery without consuming cooldown.
- Physical SDS200 validation of the daemon PSI watchdog by dropping only inbound
  UDP control/PSI datagrams to the daemon's original local control port. The same
  daemon process detected 10.1 seconds of PSI silence, reopened the scanner
  transport on a new local port, resumed ordered PSI events, and kept independent
  RTSP/RTP audio advancing while the web process remained running.
- Self-hosted interactive Swagger UI and ReDoc at `/api/v1/docs` and
  `/api/v1/redoc`, backed by the existing local OpenAPI schema and version-pinned
  packaged Swagger UI 5.32.11 and ReDoc 2.5.3 assets with upstream license and
  notice material. Documentation pages keep same-origin scripts and connections,
  make no daemon request merely by loading, and use a narrowly scoped
  style-only CSP exception without weakening the normal dashboard policy.
- Milestone 20.5 daemon-owned browser recording workflows over the existing
  decoded-PCM router, with repeatable collision-safe WAV capture, adjacent
  metadata sidecars, bounded newest-first finalized inventory, stable recording
  status/start/stop/list API operations, ordered `recording.state` events, and
  daemon-shutdown finalization without opening a second scanner RTSP/RTP stream.
- A private versioned `recordings.sock` recording-file service with bounded local
  clients, canonical inventory-relative identifiers, component-by-component
  `O_NOFOLLOW` reopening, regular-file and WAV revalidation, exact-length
  streaming, redacted failures, and deterministic process cleanup.
- Browser Record and Stop controls with live elapsed, packet, sample, duration,
  and RTP reliability telemetry; automatic active-recording reconciliation after
  reload or reconnect; recent finalized recordings; and same-origin Play and
  Download actions that use the recording-file service without creating browser
  PCMU or scanner-audio ownership.
- Physical SDS200 browser-recording validation confirmed live daemon-owned
  capture, loss-free observed telemetry, finalized WAV and metadata inventory,
  saved-file playback and download, active recording survival across web-process
  shutdown and restart, and SIGTERM finalization before daemon audio shutdown.
  The shutdown-finalized recording was rediscovered as playable after daemon
  restart, and the full browser acceptance workflow passed.
- Milestone 20.4 explicit same-origin browser audio over the daemon-owned PCMU
  socket with one independent validated client per playback stream, exact v1
  frame forwarding without re-encoding, manual Play and Stop controls,
  AudioWorklet G.711 mu-law decoding, bounded buffering and resampling, daemon
  queue-loss and RTP-loss telemetry, hidden-tab playback continuity, and
  deterministic Stop and page-close cleanup. The milestone also reaps idle
  disconnected daemon event clients and bounds Uvicorn graceful shutdown to two
  seconds so long-lived browser streams cannot make one `SIGINT` wait forever.
- Physical SDS200 browser validation with firmware 1.26.01 confirmed audible
  scanner audio in both output channels, zero browser queue and RTP loss during
  the observed run, PCMU cleanup on Stop, continued audio while the dashboard
  was hidden, repeated SSE visibility-cycle cleanup with no retained accepted
  event sockets, and controlled active-SSE web shutdown after one `SIGINT` in
  2.368 seconds with the daemon remaining healthy.
- Milestone 20.3 same-origin Server-Sent Events bridge over the existing ordered
  daemon event socket, authoritative snapshot-first delivery, validated SSE
  identifiers and JSON envelopes, redacted initial failures, deterministic
  event-client cleanup, browser-side incremental updates, automatic reconnect,
  two-second polling fallback, and periodic authoritative reconciliation.
- Milestone 20.2 accessible responsive browser shell with packaged HTML, CSS,
  and JavaScript assets; read-only polling of the existing daemon-status API;
  scanner, activity, PSI, audio, and destination summaries; progressive
  no-JavaScript messaging; light, dark, compact, keyboard-focus, and
  reduced-motion behavior; strict browser response headers; and
  host-independent shell and static-asset regression tests.
- Milestone 20.1 optional web-service foundation with FastAPI and Uvicorn
  packaging, a loopback-only `sdsctl web` command, versioned process-health,
  daemon-status, authoritative-snapshot, and OpenAPI endpoints, redacted daemon
  failures, disabled interactive documentation, and host-independent application,
  listener, CLI, packaging, and regression tests.
- A web dashboard foundation guide covering installation, daemon-client
  architecture, localhost-only binding, the current HTTP contract, security
  boundaries, command options, and explicitly deferred browser-dashboard work.

## [0.19.0] - 2026-08-06

### Added

- Immutable renderer-neutral application configuration values with validated
  reconnect, reliability, presentation, and logging settings plus per-field
  source provenance.
- Versioned system and user TOML loading, supported `SDSCTL_*` environment
  overrides, explicit CLI precedence, deterministic `sdsctl` configuration,
  state, and cache paths, and read-only legacy configuration discovery.
- Renderer-neutral `DaemonRuntime` ownership of scanner control, PSI, one
  RTSP/RTP decoded-PCM fanout, and dynamic destinations, with immutable snapshots,
  ordered transitions, partial-start cleanup, reverse-order shutdown, isolated
  listeners, and redacted failures.
- Additive daemon snapshot identity fields for scanner model and firmware,
  populated by independent nonfatal startup probes and accepted as optional by
  version 1 API and event clients for backward compatibility.
- Public `DaemonSignalController`, `DaemonProcess`, and immutable
  `DaemonProcessResult` contracts for foreground process ownership, SIGINT and
  SIGTERM stop requests, handler restoration, and deterministic cleanup that
  preserves primary failures.
- Validated `SIGHUP` destination-manifest reload using the startup path,
  transactional activation, stop-request priority, redacted failure isolation,
  and committed replacement results that retain post-commit cleanup reporting.
- Foreground `sdsctl daemon` construction of one scanner, PSI, RTSP/RTP audio,
  decoded-PCM router, and `DaemonRuntime` from an explicit SDS200 host or
  network-capable saved profile.
- Versioned `sdsctl.daemon` protocol with backward-compatible snapshot
  operations plus capability-checked hold, next, previous, and reconnect
  controls, strict JSON Lines envelopes, correlation identifiers, capability
  negotiation, and structured redacted errors.
- Private Unix-domain socket resolution through an explicit path,
  `XDG_RUNTIME_DIR`, or the user state directory, with private permissions,
  active-daemon refusal, safe stale-socket replacement, and filesystem identity
  checks.
- Bounded local API client handling with request and response limits, idle
  timeouts, isolated connection workers, server health snapshots, CLI limit
  options, and process lifecycle integration that stops API clients before the
  ownership runtime.
- Public `DaemonApiClient` and explicit `sdsctl daemon-client` status, snapshot,
  hold, next, previous, and reconnect workflows with capability negotiation,
  authoritative result validation, distinct control deadlines, clear absent or
  incompatible daemon diagnostics, and preserved standalone scanner commands.
- Single-owner daemon mutation execution with immediate concurrent-request
  rejection, scanner-acknowledged completion, ordered immutable control results,
  authoritative completion snapshots, and stable redacted control error codes.
- One maximum two-second daemon control budget covering lifecycle-lock waits and
  scanner completion, plus bounded reconnect that is advertised only for the
  directly owned SDS200 UDP control transport.
- Versioned `sdsctl.daemon.events` JSON Lines envelopes with immutable payloads,
  authoritative snapshot checkpoints, global sequence numbers, observation
  timestamps, stable event kinds, and encoded-size enforcement.
- One serialized daemon event stream aggregating runtime lifecycle, scanner
  connection, PSI, radio-state, audio lifecycle, and decoded-PCM destination
  health without publishing packet-rate audio data.
- A separate private `events.sock` Unix-domain endpoint with one bounded
  subscription per admitted client, independent overflow, explicit sequence-gap
  resynchronization, slow-client isolation, deterministic cleanup, and process
  lifecycle integration.
- Public `DaemonEventClient` and `sdsctl daemon-client events` workflows with
  validated envelope, protocol, version, snapshot, framing, size, and sequence
  validation; clear disconnect diagnostics; bounded matching counts; and
  optional client-side event-kind filtering.
- Public `DaemonPcmuClient` with bounded binary-frame reads, strict magic,
  version, header, endpoint, frame, stream-order, and cumulative-loss validation,
  plus immutable delivery, duration, RTP-continuity, and client-loss snapshots.
- Explicit `sdsctl daemon-client audio` playback and WAV-recording workflows that
  consume daemon-owned PCMU without opening scanner hardware or the daemon API,
  reuse existing bounded PCM sinks, support optional duration and output-device
  selection, and report stream, queue-loss, RTP, playback, and output summaries.
- Public `DaemonPcmuAudioTransport` adaptation of daemon-owned PCMU to the
  renderer-neutral audio-stream contract, preserving observation timestamps,
  daemon queue and RTP continuity statistics, bounded lifecycle, and isolated
  receive and callback failures.
- Explicit `sdsctl tui --daemon-client` operation using authoritative daemon
  snapshots, ordered events, safe controls, and daemon-owned PCMU audio without
  opening scanner hardware or a second RTSP/RTP session. Standalone TUI
  ownership remains the default, and closing the TUI leaves the daemon running.
- Foreground daemon options for event socket location, subscriber queue depth,
  concurrent event clients, maximum encoded event size, send timeout, and worker
  shutdown deadline.
- Immutable accepted-PCMU packet publication before decoding, preserving RTP
  sequence, timestamp, SSRC, expected continuity values, missing packet and sample
  estimates, observation time, marker state, and authoritative audio endpoint.
- Independent bounded PCMU subscriptions with global publication ordering,
  drop-oldest queues, cumulative packet, byte, and overflow loss counters,
  immutable health snapshots, subscriber limits, and deterministic close behavior.
- Versioned `sdsctl.daemon.pcmu` binary framing with strict magic, version, flags,
  complete-frame lengths, UTF-8 endpoint encoding, payload and frame bounds, and
  public encode and decode helpers.
- A third private `pcmu.sock` endpoint with one isolated subscription per admitted
  client, bounded send waits and shutdown, excess and disconnected-client
  isolation, server health snapshots, and foreground daemon lifecycle integration.
- Foreground daemon options for PCMU socket location, subscriber queue depth,
  concurrent clients, payload, endpoint, and frame sizes, send timeout, and worker
  shutdown deadline.
- Physical SDS200 validation of foreground startup, live PSI and RTSP/RTP
  reception, private API socket permissions, all read-only operations,
  malformed-request recovery, an independent second client, controlled
  `SIGINT` and systemd-style `SIGTERM` shutdown, socket removal, reverse-order
  cleanup, and successful process exit.
- Physical SDS200 validation of the private local event endpoint with two
  independent snapshot-first clients, excess-client rejection, uninterrupted
  API ping, 76 continuous ordered events from sequence 11 through 86, live PSI
  and radio-state updates, shutdown lifecycle events, 507 received RTP packets,
  162,240 decoded samples, clean `SIGTERM`, and removal of both owned sockets.
- Physical SDS200 validation of simultaneous API, event, and PCMU clients with
  private `0700` directory and `0600` socket permissions, 61 successful API
  pings, 231 continuous ordered events, and two independent PCMU clients that
  each received 1,503 frames and 480,960 payload bytes without sequence gaps,
  queue drops, overflows, RTP loss, timestamp reversal, or mismatched overlapping
  frames. An excess PCMU client was rejected, decoded audio advanced by 1,500
  packets and 480,000 samples, and controlled `SIGTERM` removed all three sockets
  with exit status 0.
- Physical SDS200 validation of capability negotiation and the complete safe
  daemon-control sequence: TGID hold, next, previous, hold release, and bounded
  reconnect. All five scanner-acknowledged operations completed in order, next
  changed the held selection, previous returned to it, hold was restored to
  `Off`, reconnect produced both connection transitions, and API, PSI, event,
  RTSP/RTP, decoded-audio, and PCMU activity remained healthy. The run completed
  16 API pings, 82 ordered events without a gap, and two matching loss-free PCMU
  streams of 410 frames and 131,200 payload bytes each. Controlled `SIGTERM`
  returned exit status 0 and removed all three sockets.
- Added semantic desired-state `scanner.hold_state` control for system,
  department, site, and channel hold/release without changing the compatibility
  indexed `scanner.hold` operation. The daemon performs an authoritative `GSI`
  read before deciding whether a gesture is needed, no-ops when the requested
  state already matches, executes the complete verified gesture under one
  mutation lock, and polls authoritative `GSI` until the target hold field
  converges.
- Physical SDS200 firmware 1.26.01 validation confirmed one `KEY,A,P` toggles
  System Hold, one `KEY,B,P` toggles Department Hold, `KEY,F,P` followed by
  `KEY,B,P` toggles Site Hold, and one `KEY,C,P` toggles Channel Hold in both
  directions. Browser hold controls now send explicit `held: true|false`
  desired state, render held scopes as actionable Release controls, and never
  expose a generic raw `KEY` operation.
- Physical end-to-end Milestone 20.6 web validation exercised semantic
  release/re-hold for all four scopes through the loopback HTTP routes,
  including Channel release across the `4294967295` no-selection interval. A
  real browser then confirmed the Channel Release/Held -> Hold -> Release/Held
  UI cycle and completion messages while daemon/web process IDs remained
  unchanged and PSI plus daemon-owned audio stayed healthy.
- Physical SDS200 validation of `sdsctl daemon-client audio` with simultaneous
  default-device playback and WAV recording through the private PCMU socket.
  The client received 258 consecutive frames from stream sequence 16 through
  273 and 82,560 samples without stream gaps, PCMU queue loss, RTP loss,
  missing samples, timestamp reversal, or callback status. It finalized an
  8 kHz mono signed 16-bit WAV containing 10.320 seconds of audio, preserved
  API health, and completed clean `SIGTERM` removal of all three sockets. The
  bounded local playback queue wrote 159,942 bytes and reported six overflows
  dropping 2,088 PCM bytes without underflow.
- Physical SDS200 validation of `sdsctl tui --daemon-client` using explicit
  API, event, and PCMU sockets. The TUI rendered cleanly, followed live state,
  completed a safe scanner control, automatically started playback, toggled
  playback with `A`, and finalized a 53.120-second 8 kHz mono WAV plus metadata.
  Quitting the TUI left scanner, PSI, RTSP/RTP audio, router, and daemon
  ownership running. Controlled `SIGTERM` then removed all three sockets.

- A daemon deployment and upgrade guide covering preserved naming
  compatibility, dedicated service accounts, systemd supervision, private socket
  access, destination manifests, transactional reload, migration, clean
  installation, upgrades, and rollback.
- Validated the repository's existing GitHub CodeQL default setup for Actions
  and Python against the release pull-request head with no analysis errors.

### Changed

- `sdsctl` now resolves application settings from built-in defaults,
  `/etc/sdsctl/config.toml`, the XDG user configuration file, environment
  variables, and explicit CLI options while preserving existing behavior when
  optional files are absent.
- Kept `--config PATH` dedicated to the legacy scanner connection-profile file;
  no profile or remote-audio configuration is moved or rewritten automatically.
- Raised the default daemon API worker shutdown deadline from two to three
  seconds and added rejection of API server configurations whose shutdown
  deadline cannot outlast the maximum request duration.
- Short TUI layouts now use dense borderless panels, four-line audio and PSI
  health summaries, and a one-line essential-controls footer so the operational
  view fits Raspberry Pi-class displays while tall layouts retain full detail.
- Physically validated the compact TUI on a Raspberry Pi 4 with an 800 by 480
  display at 100 by 30 terminal cells, including live playback, recording,
  library navigation, scrolling, controls, live PSI, and clean shutdown.

### Fixed

- Renew active SDS200 network PSI pushes in place every 120 seconds after
  physical firmware 1.26.01 testing showed otherwise healthy push delivery
  stopping at roughly 184 seconds. Renewal is serialized with scanner command
  transactions, the acknowledgement path remains serialized through the first
  subsequent PSI frame, command and bounded-reconnect timeout budgets are
  preserved, capture wrapping and UDP fallback remain supported, and stale-PSI
  reconnect recovery remains the second-line failure path.
- Added the package author email to the PEP 621 project metadata so future
  distributions expose the expected `Author-email` value.

## [0.18.0] - 2026-08-03

### Added

- Renderer-neutral `RemoteSinkHealth` classification, serializable
  `RemotePcmSinkSnapshot` metrics, ordered `RemotePcmSinkTransition` events,
  timezone-aware lifecycle timestamps, and isolated `on_transition()`
  subscriptions for future CLI, TUI, daemon, and integration consumers.
- Versioned `BroadcastifyDestinationProfile` and `RemoteAudioProfileStore`
  APIs that retain environment-variable secret references, preserve adapter and
  reconnect settings, and convert into validated configuration without storing
  resolved credentials.
- Renderer-neutral `RemoteStreamMetadata`, worker-backed
  `RemoteMetadataPublisher` metrics and retry isolation, and optional
  Broadcastify-compatible Icecast alpha-tag updates synchronized with live
  scanner state without blocking PSI or PCM delivery.
- Public renderer-neutral audio encoder process contracts, immutable command and
  lifecycle settings, reusable pipe-backed subprocess management, bounded
  interruption and finalization, stderr diagnostics, and migration of the fixed
  Broadcastify FFmpeg MP3 profile onto the shared lifecycle.
- A renderer-neutral local playback lifecycle with bounded newest-audio buffering,
  warm mute behavior, underflow and overflow metrics, preserved PortAudio
  compatibility, and explicit PipeWire, PulseAudio, and ALSA command adapters with
  injectable factories and bounded process cleanup.
- Reusable dynamic PCM subscriber routing with immutable per-subscriber health
  snapshots, ordered transition events, lifecycle and submission counters,
  timezone-aware timestamps, redacted failure state, listener isolation, and
  startup, submission, and shutdown failure isolation.

## [0.17.0] - 2026-08-03

### Added

- Renderer-neutral `RecordingIdentity` derivation and portable filename-component
  normalization for future recording organization policies.
- Configurable TUI recording directories organized by ordered scanner, date,
  system, department, site, or channel identity components.
- Renderer-neutral, read-only recording inventory with WAV and metadata-sidecar
  classification, deterministic ordering, issue reporting, and aggregate totals.
- Deterministic non-destructive recording-retention previews with age, managed-unit,
  and aggregate-byte limits plus protected-artifact reporting.
- Explicit plan-bound retention execution with stale-state revalidation,
  path-and-symlink refusal, deterministic WAV-plus-sidecar deletion, and immutable
  partial-failure reporting.
- Local `sdsctl recordings retention` previews with stable JSON, exact plan-bound
  execution tokens, fixed age-policy planning boundaries, and meaningful exit
  statuses for unsatisfied limits or incomplete execution.

## [0.16.1] - 2026-08-02

### Added

- `sdsctl audio-devices` reporting the local PortAudio version, host APIs, default
  output, and output-capable devices without opening a scanner connection.

### Fixed

- Missing Linux PortAudio runtimes now produce an actionable Debian and Raspberry
  Pi OS `sudo apt install libportaudio2` diagnostic instead of the raw
  `PortAudio library not found` import failure.

### Changed

- Documented the Linux system dependency behind the optional `sounddevice`
  playback extra and clarified how PortAudio may be exposed through ALSA,
  PipeWire or PulseAudio compatibility, or JACK.
- Recorded direct PipeWire, PulseAudio, and ALSA adapters as planned Milestone 18
  work and daemon-owned single-session audio fanout as planned Milestone 19 work.

## [0.16.0] - 2026-08-02

### Added

- Transport-independent decoded-PCM fanout sessions with independently buffered
  sink destinations.
- Optional live playback through the local default or selected PortAudio output
  device, including queue, underflow, overflow, and dropped-audio counters.
- A maintained project roadmap covering active, planned, and exploratory work.
- A consolidated project-vision document preserving product direction,
  architectural constraints, security boundaries, hardware-validation policy,
  Favorites Workspace plans, daemon and integration ideas, and advanced protocol
  research.
- Version-controlled GitHub Wiki source with task-oriented home, installation,
  troubleshooting, navigation, and publishing guidance.
- Repeatable TUI recordings with collision-safe local timestamp filenames, a
  newest-first recording library, and saved-recording playback controls.
- Immediate unmuted TUI live playback through the default or selected PortAudio
  device.

- A service-neutral remote PCM destination core with environment-backed secret
  references, bounded worker queues, reconnect backoff, redacted failures, and
  immutable operational snapshots.
- A Broadcastify-compatible Icecast source adapter with a fixed 22.05 kHz,
  16 kbps constant-bit-rate mono MP3 profile, FFmpeg process isolation, static
  source metadata, injected test seams, and interruptible shutdown.
- An Asterisk custom Music-on-Hold bridge with direct 8 kHz signed-linear PCM,
  a bounded nonblocking stdout worker, network-profile support, clean pipe-close
  handling, and orderly `SIGHUP`, `SIGTERM`, and `SIGINT` shutdown.

- Physical SDS200 validation tools and evidence for Broadcastify-compatible local
  Icecast streaming, forced-disconnect recovery, and assigned production-feed
  authorization and routing, including sanitized counters, MP3 profile and signal
  checks, reconnect state, credential exclusion, and orphan-process detection.
- A versioned recording-metadata model with scanner boundary state, audio and
  reliability statistics, deterministic JSON serialization, and collision-safe
  atomic sidecar writes.
- Optional TUI recording sidecars enabled by `--audio-metadata`, including live
  scanner state captured at successful recording start and stop boundaries.
- A bounded, thread-safe TUI operational log panel that is visible by default,
  toggles with `G`, retains records while hidden, and preserves optional file
  logging.
- Descriptive border titles for the standard and wide TUI panels.
- Reproducible native SVG screenshots of the real Textual TUI populated with
  fictional demonstration scanner, recording, audio, and log data.
- A renderer-neutral scanner-screen classifier for normal scanning, Quick
  Search, Close Call, weather, Tone Out, and unknown screens while preserving
  the scanner's raw `Mode` and `V_Screen` values, with synthetic GSI/PSI
  fixtures and transition coverage.
- Mode-aware Quick Search and Close Call TUI panels showing the reported state
  node, frequency or hit name, modulation, hold state, signal, RSSI, and
  detected tone or digital-code value from the scanner's `SAD` attribute.
- Mode-aware Weather TUI panels showing the reported weather channel and number,
  frequency, modulation, monitor or alert mode, hold state, signal, RSSI, and
  SAME selection when supplied by the scanner.
- Mode-aware Tone Out TUI panels showing the reported profile and channel number,
  monitored frequency, modulation, Tone A and Tone B values, hold state, signal,
  and RSSI.
- Physical SDS200 firmware 1.26.01 UDP validation for normal scanning, Quick
  Search, Close Call, Weather, and Tone Out GSI/PSI states and live transitions,
  including hardware-aligned fixtures and documented unobserved protocol
  variants.

### Changed

- Package and CLI version advanced to `0.16.0`.
- Reconciled the roadmap with completed Milestone 16 work, made screen-mode
  foundation the active slice, moved v0.16.0 preparation to Milestone 16.6, and
  deferred SDS150 physical validation until hardware is available.
- Adopted `sdsctl` as the namespace for future configuration, services, state,
  cache, daemon, API, and integration work while preserving existing Python package
  compatibility.
- `sdsctl audio` now supports playback-only, recording-only, or simultaneous
  playback and WAV recording from one SDS200 RTSP/RTP session.
- PCM WAV writes used by the fanout pipeline now run on a dedicated worker instead
  of the RTP receive callback.
- TUI playback and repeatable recording now share one long-lived RTSP/RTP stream;
  playing a saved recording temporarily suspends local live playback without
  interrupting scanner reception or an active WAV recording.
- SDS200 host TUI sessions now expose live playback controls even without
  `--audio-playback`; the flag requests automatic startup after connected live PSI.
- Full-screen TUI sessions now redirect package stderr logging into the in-app
  panel and restore the original stderr handler after shutdown.

### Fixed

- Network control and RTP audio transports now represent an unset local bind
  address with `None`, reject legacy numeric aliases for `0.0.0.0`, and use
  explicit resolver fallbacks so CodeQL can verify that wildcard binds are not
  reachable.

- Prevented the final TUI status detail row from being clipped when the
  operational log panel is hidden in a wide layout.
- Stopped TUI polling timers before widget teardown and suppressed late rendering
  callbacks after shutdown begins.
- Deferred PortAudio startup until the first connected live PSI refresh, preventing
  playback initialization from leaving stale startup panels in wide terminals.
- Live playback toggles now keep a prepared output device warm and muted until TUI
  shutdown, without counting intentional muted callbacks as underflows.

## [0.15.0] - 2026-07-28

### Added

- Deterministic fault-injection coverage for concurrent audio start/stop,
  repeated TUI recording requests, shutdown during audio startup, and scanner
  reconnects while recording.
- Configurable operational logging with explicit levels, optional persistent
  files, logrotate compatibility, and systemd/journald guidance.
- Automatic rate-limited TUI recovery when a connected UDP control transport
  stops delivering PSI updates.

### Changed

- Package and CLI version advanced to `0.15.0`.
- Updated the README project status to describe the v0.14.0 TUI audio,
  reliability, and lifecycle improvements alongside the v0.15.0 operational
  hardening.

### Fixed

- Suppressed background TUI callback dispatch after shutdown begins so in-flight
  audio and scanner-control workers can terminate without callback/join contention.
- Reconnect stale PSI streams automatically after a configurable sustained-stale
  interval while leaving an active SDS200 RTP audio recording uninterrupted.
- Preserved the configured PSI interval after a reconnect timeout so later
  automatic recovery attempts continue restarting the scanner-information stream.

## [0.14.0] - 2026-07-28

### Added

- Reusable `AudioRecordingSession` service with immutable lifecycle and reliability
  snapshots for renderer-independent integrations
- Opt-in SDS200 network-audio recording in the Textual TUI with `R` start/stop
  controls and automatic WAV finalization during shutdown
- Dedicated TUI audio worker so RTSP startup and teardown cannot block scanner
  controls or Textual's event loop
- Live TUI audio panel for elapsed time, output path, packet and sample totals, audio
  duration, and RTP reliability counters
- Local `since HH:MM:SS` transition timestamps for connection, availability, and
  severity states
- Nonblocking TUI reconnect action that preserves the active PSI interval

### Changed

- `sdsctl audio` now delegates recording lifecycle and cleanup to the shared audio
  session service
- Every valid GSI/PSI frame now refreshes state observers while field-change events
  remain limited to actual value changes
- Package and CLI version advanced to `0.14.0`

### Fixed

- Stable channels with repeated unchanged PSI frames no longer age into a false
  stale state
- Textual's footer now renders the command-palette binding as `^p Command Palette`
  without duplicated wording

## [0.13.0] - 2026-07-27

### Added

- Optional Textual 8 full-screen application shell with USB, network, profile,
  and replay launch support
- Renderer-neutral scanner-information presentation shared by the Rich CLI and
  Textual adapters
- Dark/light TUI palette switching, explicit quit binding, and headless shell
  regression tests
- Live PSI subscriptions that marshal radio callback updates safely into the
  Textual event loop
- Connected, degraded, reconnecting, disconnected, and stale-data presentation
  with configurable PSI and freshness intervals
- Deterministic callback unsubscription, PSI shutdown, and replay-driven live-state
  regression coverage
- Serialized background TUI command execution for hold, next, previous, volume,
  and squelch controls without blocking Textual's event loop
- PSI/GSI navigation-index retention with capability-aware channel controls and
  explicit unavailable, success, and failure feedback
- `sdsctl -V` and `sdsctl --version` flags for installed version information
- Deterministic TUI control-worker and replay-command regression coverage
- Automatic compact, standard, and wide TUI layouts for Raspberry Pi and terminal
  displays, including short-screen identity consolidation
- In-app `?` keyboard reference with a compact footer that keeps essential actions
  visible without crowding small terminals
- Project acknowledgment documentation for substantial ChatGPT-assisted development

### Changed

- Package and CLI version advanced to `0.13.0`

## [0.12.0] - 2026-07-27

### Added

- Renderer-independent semantic presentation types for connection, activity,
  signal, hold, availability, and severity states
- Pure snapshot classification with normalized mute, recording, service-type,
  and raw-signal values for future CLI and Textual renderers
- Rich terminal adapter that renders scanner information from semantic presentation
  roles while preserving plain-text output for redirected and captured streams
- Stable semantic theme roles that map scanner presentation states without
  introducing renderer dependencies
- Complete immutable light and dark palettes with generic color and emphasis
  tokens for future Rich and Textual adapters
- Explicit `--color`, `--no-color`, and `--theme` CLI controls with `NO_COLOR`
  and `FORCE_COLOR` environment handling
- Accessibility regression coverage proving semantic scanner information remains
  identical when ANSI styling is disabled or palettes are changed

### Changed

- Package version advanced to 0.12.0

## [0.11.1] - 2026-07-27

### Security

- Avoided wildcard-interface binds for default SDS200 UDP control and RTP audio
  sockets by using operating-system route selection
- Restricted RTP audio ingestion to the source address, server port, and SSRC
  negotiated by the scanner's RTSP `SETUP` response
- Rejected explicit `0.0.0.0` control and RTP bind addresses

### Added

- Typed parsing for scanner RTSP `Transport` response parameters
- Audio reliability counters for unexpected RTP sources and SSRC mismatches

### Changed

- Package version advanced to 0.11.1

## [0.11.0] - 2026-07-27

### Added

- Hardware-validated SDS200 network audio transport using the scanner's strict
  single-port RTSP/RTP negotiation
- Typed RTSP response and SDP handling plus RTP version 2 packet parsing for
  payload type 0 PCMU audio
- Native G.711 mu-law decoding to 8 kHz mono signed 16-bit PCM
- Streaming WAV recording through `sdsctl --host HOST audio`, including duration,
  overwrite, RTP bind, RTSP port, and keepalive options
- Per-session RTP reliability statistics for packet loss, sequence gaps,
  duplicates, late and malformed packets, timestamp discontinuities, receive and
  callback errors, keepalives, and orderly teardown
- Sanitized synthetic PCMU/RTP fixtures and deterministic transport reliability
  regression tests

### Changed

- Network audio remains independent from USB serial and UDP scanner-control
  transports while using the existing `AudioStream` lifecycle and subscriptions
- Package version advanced to 0.11.0

## [0.10.0] - 2026-07-24

### Added

- Opt-in preferred-transport recovery for SDS200 fallback profiles
- Validated `MDL` probes, stability windows, cooldowns, and command-idle promotion guards
- Preferred-recovery diagnostics, counters, timestamps, health history totals, and CLI overrides
- Manual fallback profile creation with simultaneous `--port` and `--host` options
- Persistent preferred-recovery settings in version 4 profile documents

### Changed

- Fallback profiles can now return from a healthy alternate transport to the configured preferred endpoint without reporting a connection interruption
- Continuous PSI updates restart automatically after a preferred transport recovery
- Package version advanced to 0.10.0

## [0.9.0] - 2026-07-24

### Added

- Deterministic `ReplayTransport` for running real parser, radio, and CLI flows from JSON Lines captures
- `RecordingTransport` and `--capture` support for USB, UDP, and fallback sessions
- Repeatable literal redaction for captures before fixtures are shared
- `sdsctl --replay`, replay timing control, and strict command-sequence mismatch errors
- `sdsctl capabilities` with model limits, feature flags, and hardware-validation status
- Typed `HLD`, `NXT`, and `PRV` navigation commands with CLI and Python APIs
- Hardware-derived, sanitized SDS100 replay fixture and replay regression tests

### Changed

- Model capabilities now identify scanner-info, PSI, navigation, and validation status
- README installation instructions now use the published PyPI package
- Trusted Publishing workflow uses current Node 24-based checkout and Python setup actions
- Package version advanced to 0.9.0

## [0.8.2] - 2026-07-24

### Added

- Optional SDS100 battery telemetry through the documented `GSI`/`PSI` `Property.Battery` attribute
- Immediate `CommandRejectedError` handling for generic scanner `ERR` and `NG` replies
- Extended `scanner-info` output for RSSI, optional battery, recording, and mute state
- Opt-in Uniden SDS-series udev rule for desktop ACLs and ModemManager exclusion

### Changed

- Corrected SDS100 capabilities after firmware 1.26.01 hardware testing showed
  that `GCS` returns `ERR`
- Kept SDS150 detailed `GCS` charge status as specification-based and hardware unverified
- Package version advanced to 0.8.2

## [0.8.1] - 2026-07-24

### Changed

- Replaced the model-specific `sds200` executable with the model-neutral `sdsctl` command
- Updated CLI help, shell completion, documentation, support guidance, and tests for `sdsctl`
- Kept the distribution, Python import package, configuration directory, and repository named `sds200`
- Package version advanced to 0.8.1

## [0.8.0] - 2026-07-24

### Added

- USB serial control support for the Uniden SDS100 and SDS150
- Model-neutral `SDSScanner` API while retaining the historical `SDS200` alias
- Scanner capability metadata and model-specific volume and squelch limits
- SDS100/SDS150 `GCS` battery and charge-status parsing and CLI output
- Model-aware USB discovery, selection, profiles, repair, and completions
- SDS150 `SDS150GBT` and Uniden internal model-name normalization
- LF, CR, and CRLF serial response framing for shared SDS-series commands
- Multi-model protocol, profile, discovery, and command regression tests

### Changed

- Network discovery and UDP profiles are explicitly restricted to the SDS200
- Profile documents advance to version 3 and can retain the scanner model
- Documentation distinguishes protocol support from physical-hardware validation
- Package version advanced to 0.8.0

## [0.7.0] - 2026-07-23

### Added

- Configurable exponential reconnect backoff with finite or unlimited attempts
- Structured `RadioEvent` notifications and `events --json` JSON Lines output
- Bounded health history with latency, error-rate, reconnect, and failover summaries
- Health thresholds for healthy, degraded, unhealthy, and disconnected states
- Discovery-based profile repair for stale USB paths and changed network addresses
- Detailed failover telemetry including previous and active endpoints
- Reliability regression tests for backoff, history, events, and profile repair

### Changed

- Serial, UDP, and fallback reconnect loops now share one recovery policy
- `health --history` can include historical metrics in human or JSON output
- Network audio remains deferred and documented as future work
- Package version advanced to 0.7.0

## [0.6.0] - 2026-07-23

### Added

- Discovery-driven serial, network, or fallback profile creation
- Configurable serial/network preference with runtime transport failover
- One-time command retry after a successful failover
- Continuous `health --watch` output and JSON health reports
- Connection, response, state, serial, network, and failover diagnostics
- Independent `AudioTransport`, `AudioStream`, and `AudioChunk` API groundwork

### Changed

- Profile files now use version 2 and can store both control endpoints
- Package version advanced to 0.6.0

## [0.5.3] - 2026-07-23

First planned GitHub prerelease.

### Added

- Reliable active LAN discovery with isolated per-host UDP sockets
- Bounded discovery parallelism and configurable worker count
- USB and network connection profiles stored as TOML
- Network health checks, statistics, diagnostics, and XML retry limits
- User-focused README and project documentation
- Contribution, support, security, and conduct guidance
- GitHub issue forms, pull-request template, and Dependabot configuration
- Package metadata, typed-package marker, build verification, and release checklist

### Changed

- CI now uses Node 24-based GitHub Actions majors
- CI verifies documentation links and built distribution metadata
- LAN discovery uses per-host timeouts and bounded concurrency

### Fixed

- `/24` discovery could miss a scanner because unrelated UDP errors, ARP delays,
  and shared-socket behavior interfered with valid replies
- Network XML handling now supports bare and fragmented `GSI`/`PSI` responses
- Strict MyPy narrowing in network XML decoding

## [0.5.2]

- Continued discovery after transient UDP refusal, reset, host-unreachable, and
  network-unreachable errors.

## [0.5.1]

- Improved discovery timeout placement, batching, and response draining.

## [0.5.0]

- Added LAN discovery, profiles, health checks, UDP counters, diagnostics, and
  bounded XML retries.

## [0.4.2]

- Completed strict typing for bare network XML response handling.

## [0.4.1]

- Added command-aware handling for bare `ScannerInfo` XML over UDP.

## [0.4.0]

- Added native SDS200 UDP control, multi-datagram XML reassembly, and network
  support across the existing command, state, trace, and monitor APIs.

## [0.3.1]

- Correctly handled the SDS200 `PSI` acknowledgment followed by streamed XML.

## [0.3.0]

- Added continuous `PSI` monitoring, state-difference events, live terminal
  display, traffic timestamps, and the public transport abstraction.

## [0.2.4]

- Added Ruff- and MyPy-clean shell completion integration.

## [0.2.3]

- Added Bash and Zsh completion for commands, flags, ports, profiles, and common
  scanner protocol commands.

## [0.2.2]

- Completed strict PySerial factory and write-return typing.

## [0.2.1]

- Fixed a serial-reader shutdown race and added regression coverage.

## [0.2.0]

- Added typed command objects, structured scanner XML, synchronized radio state,
  state events, traffic tracing, and the `scanner-info` command.

## [0.1.2]

- Established a Ruff-, MyPy-, and Pytest-clean transport baseline.

## [0.1.0]

- Added serial discovery, transport, packet framing, core responses, CLI tools,
  examples, tests, and CI.

[Unreleased]: https://github.com/stevenboyd78/sds200-python/compare/v0.20.2...HEAD
[0.20.2]: https://github.com/stevenboyd78/sds200-python/compare/v0.20.1...v0.20.2
[0.20.1]: https://github.com/stevenboyd78/sds200-python/compare/v0.20.0...v0.20.1
[0.20.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.16.1...v0.17.0
[0.16.1]: https://github.com/stevenboyd78/sds200-python/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/stevenboyd78/sds200-python/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/stevenboyd78/sds200-python/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/stevenboyd78/sds200-python/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.5.3...v0.6.0
[0.5.3]: https://github.com/stevenboyd78/sds200-python/releases/tag/v0.5.3
