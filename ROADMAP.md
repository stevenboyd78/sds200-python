# Roadmap

This document records ordered work planned for `sdsctl`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Active milestone

### Milestone 25.4 — generic Docker Hub image publication

Milestone 25.3 is closed after migrating the current project, product,
repository, executable, live URLs, and branding identity to `sdsctl` while
preserving the documented Python, Home Assistant, scanner, configuration, and
internal compatibility boundaries.

Milestone 25.4 adds dedicated generic image publication to Docker Hub as
`theboyd78/sdsctl`. Pull requests, pushes to `main`, and manual dispatches run a
non-publishing `linux/amd64` plus `linux/arm64` build. Only a genuine GitHub push
of a `v*` tag exactly matching `v<package-version>` may authenticate and publish
the multi-platform image, using exactly the package-version and `latest` tags.
The workflow also requires `sds200.__version__` to equal the pyproject package
version and fails closed when the configured Docker Hub username is not the
canonical owner.

The Python distribution and import package remain `sds200`, the CLI remains
`sdsctl`, and the local development image tag remains `sds200-daemon`. Home
Assistant App/GHCR publication remains separate and keeps the
`sds200-home-assistant` identity. Repository-root Compose remains source-built
with `build: .` and does not select a published image.

Keep bridge-network callback design, separate client or web containers, USB
passthrough, Windows and macOS Docker behavior, remote standalone web exposure,
and physical generic-container validation in later container slices.

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

- Milestone 21.1 completed the SDS100/200 format foundation: immutable lossless
  positional source records, tolerant preservation of observed extensions and
  unknown commands, separate Conventional and Trunk hierarchy projections,
  `f_list.cfg` catalog projection, a pure exact-filename workspace binder,
  sanitized synthetic fixtures, and explicit isolation from storage and writes.
- Milestone 21.2 completed the first read-only storage boundary: immutable exact
  catalog/document byte snapshots, safe catalog filename validation, and an
  offline copied `favorites_lists` backend with deterministic immediate HPD
  discovery, stable regular-file reads, managed-symlink rejection, and no write,
  live-scanner, credential, FTP, USB, or renderer behavior.
- Milestone 21.3 completed renderer-neutral read-only hierarchy navigation:
  immutable source-index-addressed Favorites List, Conventional/Trunk system,
  department, trunk-site, and channel nodes with explicit parent/child structure,
  exact source names and typed provenance, deterministic catalog ordering, and
  reconstruction of mixed trunk children in raw HPD source order.
- Milestone 21.4 completed renderer-neutral Favorites search and filtering:
  immutable display-name substring queries, navigation-kind filtering, and
  inclusive subtree selection over `FavoritesNavigation`, with query-local
  case-folding, deterministic navigation preorder, original-node identity,
  explicit stale-path failure, and no raw-field search semantics.
- Milestone 21.5 completed renderer-neutral Favorites schema diagnostics:
  immutable workspace validation with stable error/warning/info rules, exact
  catalog/HPD and record/field provenance, required metadata and evidence-backed
  shape checks, supported name-tag validation, scanner-observed extension
  acceptance, and compatibility-preserving treatment of unvalidated fields and
  unsupported commands.
- Milestone 21.6 completed renderer-neutral Favorites comparison and preview:
  immutable exact baseline/candidate workspace comparison, deterministic
  add/remove/replace record changes, exact source provenance, filename-based HPD
  pairing, explicit duplicate-filename ambiguity, byte-aware line-ending
  comparison, and compatibility-preserving treatment of unknown source material.
- Milestone 21.7 completed renderer-neutral Favorites import and export:
  exact `FavoritesStorageSnapshot -> FavoritesWorkspace ->
  FavoritesStorageSnapshot` round trips, public pure reverse projection,
  preserved catalog and HPD source bytes, exact document ordering and duplicate
  filenames, and compatibility-preserving export of unknown commands, positional
  extensions, blank fields, physical line endings, empty files, and unresolved
  workspace diagnostics.
- The renderer-neutral Favorites Workspace foundation is complete through
  Milestone 21.7: lossless data modeling, read-only copied storage, hierarchy
  browsing, search/filtering, schema diagnostics, comparison/preview, and exact
  native snapshot import/export all preserve unknown scanner material.
- Keep fixtures and copied storage images as the normal automated-test substrate;
  physical scanner storage is not required for renderer-neutral planning work.

### Milestone 22 — Verified Favorites writes and storage backends

- Milestone 22.1 completed renderer-neutral immutable Favorites write planning:
  exact baseline and intended storage snapshots remain authoritative; existing
  comparison and schema evidence is retained; unsafe or ambiguous intended
  states produce deterministic blockers; exact snapshot inequality determines
  change state; and exact baseline equality provides the stale-target
  precondition for a future executor without performing any storage mutation.
- Milestone 22.2 completed pure renderer-neutral Favorites record editing:
  immutable exact source-provenance targets, stale and ambiguous target
  rejection, evidence-backed Name Tag replacement, conservative HPD leaf
  deletion, template-backed leaf creation, deterministic hierarchy-safe
  insertion, exact untouched-byte preservation, and intended-snapshot
  construction integrated with the existing write planner.
- Milestone 22.3 completed the first verified storage-mutating workflow against
  an offline copied Favorites tree: exact plan-bound target validation, an
  exclusive operation boundary, mandatory verified complete backup, full-tree
  staging, staged readback and exact intended comparison, a second
  concurrency/stale-baseline check before replacement, deterministic active-tree
  replacement, rollback recovery, and durable operation reporting.
- Milestone 22.4 completed read-only Linux USB mass-storage discovery and target
  qualification for already-mounted scanner storage: immutable mountinfo and
  sysfs evidence, proven USB ancestry, canonical contained Favorites targets,
  explicit read-only/writable state, ambiguity and stale/remount rejection,
  copied-tree read validation, and revalidated explicit-path qualification
  without mounting or mutating media.
- Milestone 22.5 completed the first verified USB mass-storage write executor:
  plan-bound fresh target qualification, host-side exclusive operation state,
  complete verified backup and staging away from scanner media, second exact
  mount/device/tree stale checks before activation, removable-media-specific
  activation without copied-tree sibling assumptions, exact active readback,
  rollback recovery, durable reporting, deterministic failure coverage, and
  guarded reversible physical SDS200 validation.
- Milestone 22.6 completed the first read-only FTP Favorites storage foundation:
  explicit trusted-network configuration, read-only credential references with
  no writable fallback, bounded exact binary retrieval, deterministic safe
  listings, two-pass exact snapshot stability, secret-redacted failures, a
  fakeable transport boundary, and guarded read-only SDS200 validation.
- Keep FTP access limited to trusted local networks or VPNs and preserve
  separate read-only and explicitly resolved writable credential roles.
- Keep every write operation deterministic, recoverable, auditable, and free of
  silent last-writer-wins behavior.

### Milestone 23 — External Favorites data and synchronization

- Milestone 23.1 completed the renderer-neutral external-data foundation:
  immutable provider/dataset/record identities, timezone-aware observation
  evidence, explicit value-versus-absence semantics, external/local/detached
  field ownership, deterministic add/replace/remove/unchanged/local-only/conflict
  previews, redacted fakeable source reads, explicit detach behavior, and exact
  local record provenance without provider-specific scanner mapping.
- Milestone 23.2 completed the first RadioReference-specific documented-interface
  boundary: secret-reference configuration, user/application credential
  separation, fakeable provider sessions, stable redacted failures,
  traceback-secret hardening, normalized observation validation, and documented
  SOAP/WSDL/licensing research without a production network adapter.
- Milestone 23.3 completed the documented WSDL/provider-record contract boundary:
  direct public WSDL inspection, exact metadata for 19 programming-relevant
  RPC/encoded operations, immutable provider-side conventional/trunked DTOs,
  schema-faithful scalar and array modeling, provider-record runtime hardening,
  and offline validation without a SOAP parser or production network adapter.
- Milestone 23.4 completed the dependency-free offline SOAP response decoder:
  bounded SOAP 1.1 RPC/encoded XML parsing, strict operation/response-type and
  QName validation, schema-faithful scalar decoding, SOAP-ENC arrays and named
  array containers, bounded local reference graphs, stable redacted failures,
  and fully offline regression coverage without live provider access.
- Milestone 23.5 completed the offline SOAP request serializer foundation: exact reviewed
  `authInfo` metadata, dependency-free deterministic SOAP 1.1 RPC/encoded request
  serialization for all 19 reviewed programming operations, exact request-side scalar
  validation, ephemeral secret-bearing request bytes, and fully offline coverage without
  HTTP/TLS transport or live provider access.
- Milestone 23.6 completed the offline observation mapping foundation:
  reviewed conventional-frequency and talkgroup DTOs map into immutable
  source-neutral observations with stable provider record identities, exact
  alpha-tag preservation, whole-Hz frequency conversion, and no provider
  revision, hierarchy, deletion, or scanner-record inference.
- Milestone 23.7 completed the offline SOAP result observation adapter:
  operation-aware mapping for three reviewed conventional-frequency operations
  and `GET_TRUNKED_TALKGROUPS`, exact immutable result validation, supported
  empty-result semantics, duplicate provider-identity rejection, provider-order
  preservation, and decoder-to-observation integration without transport access.
- Milestone 23.8 completed the offline request-plan/session composition
  foundation: immutable secret-free reviewed request plans, fakeable
  operation-aware byte exchange, serializer/decoder/observation composition,
  timezone-aware observation evidence, stable redacted failures, deterministic
  cleanup, and integration through `RadioReferenceSource` without production
  HTTP/TLS transport or retained request/response bytes.
- Milestone 23.9 completed the assisted-import provenance binding foundation:
  explicit normalized field-name to exact local source-field-index bindings,
  external/local ownership, exact-value proof before external ownership,
  source-neutral preview integration, explicit detach continuity, and a real
  RadioReference mapper regression that leaves unbound whole-Hz frequency data
  as provider-only evidence without inferring scanner representation.
- Milestone 23.10 completed the assisted-update name acceptance planning
  foundation: one pure name-only acceptance planner over existing linked
  provenance, schema-aware rename authority, ordinary write planning,
  simultaneous bound-field change rejection, refreshed intended in-memory
  provenance, and no implicit provider-frequency mapping.
- Milestone 23.11 completed name acceptance execution composition: injected
  storage-specific write execution, exact independent post-write snapshot
  readback, provenance promotion only after intended-snapshot equality, opaque
  backend success evidence, and fail-closed completion without duplicating
  copied-tree or USB safety/recovery machinery.
- Milestone 23.12 completed canonical external-provenance serialization and
  fresh-snapshot rebinding: source-neutral linked/detached state, exact local
  target locators with source-record SHA-256 evidence, strict bounded JSON,
  stable redacted errors, and fail-closed moved/changed/ambiguous target
  rejection without choosing a filesystem owner.
- Milestone 23.13 completed provenance filesystem durability and explicit
  loading: one deterministic XDG-backed state path, private current-user-owned
  publication, advisory coordination, exclusive no-follow temporary files,
  exact readback and synchronization, guarded atomic replacement, and explicit
  fresh-snapshot loading with absent-versus-empty semantics.
- Milestone 23.14 completed durable name-acceptance provenance: exact retained
  baseline, observation, and intended-state proof, complete persisted-provenance
  preflight, expected-current conditional publication, exact post-write
  verification, and a distinct failure when verified Favorites mutation is not
  followed by completed provenance publication.
- Milestone 23.15 completed startup provenance restoration lifecycle ownership:
  exactly one fresh Favorites snapshot, exact durable provenance loading and
  rebinding with absent `None` versus present-empty `()` preserved, idempotent
  active start, terminal failed/closed behavior, and redacted failures without
  partial restoration evidence.
- Milestone 23.16 completed assisted-refresh preview session composition:
  exactly one lifecycle snapshot and one source read per explicit refresh,
  immutable exact lifecycle, observation, and preview evidence, preserved
  absent-versus-empty provenance, fresh retryable uncached attempts, and no
  Favorites or provenance mutation.
- Milestone 23.17 completed assisted-refresh name-acceptance planning
  composition: exact selected preview, matched observation and linked lifecycle
  provenance evidence, delegated name-only acceptance planning, strict
  relationship validation, and no execution, publication, or lifecycle mutation.
- Milestone 23.18 completed post-acceptance lifecycle advancement: active
  lifecycle evidence adopts one already-verified durable name acceptance under
  the lifecycle lock with exact path, Favorites baseline, complete provenance
  baseline, and idempotent same-result validation.
- Milestone 23.19 completed assisted-refresh name-acceptance orchestration: one
  lifecycle critical section validates the selected refresh baseline, requires
  exact complete persisted provenance, executes the existing durable acceptance,
  conditionally publishes provenance, and advances lifecycle evidence.
- Milestone 23.20 completed assisted-refresh detach planning: explicit field or
  whole-record detach decisions reuse existing ownership transformations, retain
  exact refresh/baseline evidence, preserve local Favorites bytes, and reject
  unbound, ambiguous, local, already-detached, or no-op selections.
- Milestone 23.21 completed assisted-refresh detach orchestration: exact detach
  plans flow through complete persisted-provenance validation, conditional
  provenance publication, and lifecycle adoption under one critical section
  without changing Favorites bytes or rereading the provider.
- Milestone 23.22 completed the first RadioReference-to-Favorites representability
  seam: immutable source-neutral field mapping evidence plus one reviewed
  conventional `C-Freq` whole-Hz frequency mapping with exact target, provider
  identity, source-field index, and scanner-compatible value validation.
- Milestone 23.23 completed source-neutral arbitrary-field acceptance planning:
  exact mapping consumption, externally owned or previously unbound selected
  fields, collision/conflict rejection, exact positional replacement or no-op
  write planning, and intended in-memory provenance without execution.
- Milestone 23.24 completed arbitrary-field acceptance execution composition:
  exact mapped-field plans flow through injected existing write executors,
  independent exact readback, opaque backend evidence retention, and fail-closed
  durability without another editor or storage backend.
- Milestone 23.25 completed the renderer-neutral assisted-synchronization
  foundation across checkpoints A–F: durable arbitrary-field acceptance and
  lifecycle orchestration; explicit template-and-binding-backed record import;
  explicit provider-removal delete versus keep-local/detach planning; exact
  provenance rebasing after structural changes; the production documented
  RadioReference stdlib HTTPS SOAP exchange and reviewed request/session/source
  chain; reviewed conventional and talkgroup observation mappings; and one
  synchronous renderer-neutral application service exposing typed refresh,
  plan, and execute choices.
- Milestone 23 is renderer-neutral foundation complete. Supported
  synchronization is user-initiated and assisted only—never automatic,
  scheduled, polling, or background behavior. Only exact reviewed mappings are
  supported: conventional `C-Freq` name and whole-Hz frequency, plus `TGID`
  name and decimal talkgroup ID. Structural import requires an explicit scanner
  template and field bindings, and provider removal requires an explicit delete
  or keep-local/detach decision.
- The production RadioReference exchange is implemented and tested with
  deterministic offline fixtures and fake HTTPS connections. No live
  credentialed RadioReference validation or live scanner-hardware validation
  was performed for this milestone.
- Keep credentials and application keys outside exported Favorites data. Keep
  MyRR, scraping, undocumented/private interfaces, broader mappings and scanner
  transformations, renderer wiring, and automatic synchronization out of this
  foundation.

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

Tentative evidence-led slicing is:

- Milestone 24.1: protocol inventory, evidence ledger, and fixture/provenance
  foundation;
- Milestone 24.2: exact `GLT,FL` retrieval and generalized bounded-XML framing;
- Milestone 24.3: completed `FQK` read/control; `QSH` remains deferred pending
  evidence for its exact `FRQ` representation;
- Milestone 24.4: ordered/repeated `ScannerInfo` preservation foundation before
  any evidence-backed richer NAC, RAN, color-code, area, activity, or quality
  modeling;
- Milestone 24.5: `URC` scanner recording control;
- Milestone 24.6: `AST`/`APR` analysis-session foundation;
- Milestone 24.7: `PWF`/`GWF`/`GW2` waterfall session and data work;
- Milestone 24.8: `MNU`/`MSI`/`MSV`/`MSB` menu operations; and
- later discovery, system-status, and RF-power work built on the analysis
  substrate.

Milestone 24.8 began with a receive-only `MSI` bounded-XML model/parser
foundation preserving the exact reviewed `<MSI ...>` root, root attributes,
ordered/repeated descendants, unknown attributes and elements, and raw XML. The
second slice added exact `MSI` retrieval through typed command/radio APIs for
CR-line serial and deterministic replay. The third added only the six indexed
`MNU` rows whose index roles are explicit in both reviewed SDS specifications,
and the fourth added a read-only documented MSI menu projection without
discarding lossless source evidence. The fifth slice promotes exact one-shot
`MSI` retrieval to the shared SDS200 UDP bounded-XML expectation, fragment
reassembly, and retry machinery with deterministic fake datagrams. Fallback
MSI remains blocked, and no `MSV`/`MSB`, unindexed `MNU`, menu lifecycle,
model/firmware applicability, or physical-scanner behavior is inferred.

Milestone 24.1 selected `GLT` as the safest leading implementation candidate
because it is bounded retrieval that complements the Favorites foundation and
requires generalized bounded-XML framing before more stateful work. Milestone
24.2 now implements only the narrow, evidence-backed `GLT,FL` slice. Broader
GLT arguments and hierarchy forms remain deferred until evidence supports their
exact request and response semantics. The reported `MSM,1` reboot behavior
remains separately gated pending stronger independent protocol and firmware
evidence; the officially documented disruptive `POF` operation is adjacent
evidence, not a substitute. Later slice numbering may move as physical evidence
is collected.

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
