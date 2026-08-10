# Project Vision

This document preserves the broader direction of the `sds200-python` project and
the `sdsctl` user-facing tool. It records architectural decisions, safety and
security constraints, deferred capabilities, and product ideas that are not yet
ready for a scheduled milestone.

For ordered implementation work, see [the roadmap](../ROADMAP.md).

## Purpose

The project is intended to provide one reliable, model-neutral platform for
controlling, observing, recording, and integrating supported Uniden SDS-series
scanners.

The same typed protocol, immutable state, lifecycle services, and semantic
presentation layers should support:

- the Python API;
- the `sdsctl` command-line interface;
- the Textual terminal interface;
- future daemon and web interfaces;
- Home Assistant and other local integrations;
- possible future desktop interfaces.

Interfaces should consume shared services rather than independently implementing
scanner protocols or opening competing scanner sessions.

## Naming and compatibility

`SDSScanner` and `sdsctl` are the preferred model-neutral user-facing names.

Layered application configuration now uses the `sdsctl` namespace. Future
services, state, cache, daemon, and integration names should use the same
namespace:

- system configuration: `/etc/sdsctl/`;
- user configuration: `~/.config/sdsctl/`;
- persistent user state: `~/.local/state/sdsctl/`;
- user cache: `~/.cache/sdsctl/`;
- service naming: forms such as `sdsctl.service`.

The existing distribution and Python import package remain named `sds200`, and
the repository remains named `sds200-python`, until a separate compatibility and
migration plan justifies changing them.

Legacy `sds200` configuration must not be silently abandoned. Milestone 19.1
adds read-only detection of known legacy profile locations while preserving their
existing defaults. It does not move or rewrite user data; any future migration
requires an explicit compatibility plan.

## Architectural principles

### Model-neutral domain services

Scanner protocol parsing, state, presentation, recording metadata, audio fanout,
and remote destinations should remain independent of a particular renderer.

Raw scanner values should be preserved even when a semantic classifier provides a
normalized interpretation. Unknown commands, nodes, modes, and fields should fail
safely or remain available for future support.

### Control and audio isolation

Scanner control and network audio are separate subsystems.

An audio startup, codec, sink, encoder, playback-device, recording, or remote
destination failure must not close, replace, or interrupt the active scanner
control transport.

One decoded audio stream should fan out to independent destinations. Slow disk,
device, encoder, or network operations must not block RTP reception.

### Single-owner daemon direction

Milestone 19.3 provides the renderer-neutral ownership runtime for long-lived
scanner control, PSI, one audio fanout, and dynamic decoded-PCM destinations.
Milestones 19.4 through 19.7 host that runtime in `sdsctl daemon` and add private
versioned API, ordered-event, and accepted-PCMU services. Milestone 19.8 adds
typed bounded scanner controls. Milestones 19.9 and 19.10 add explicit
daemon-backed CLI and TUI clients. Milestone 20.1 begins the loopback web client,
and Milestones 20.2 through 20.5 add the responsive dashboard, ordered live
updates, explicit PCMU playback, and daemon-owned recording workflows including
a fourth private finalized-recording service.

The SDS200 accepts only one network-audio client at a time. The ownership runtime
holds that single RTSP/RTP session, publishes each accepted PCMU packet once, and
decodes it once. The shared decoded-PCM router already fans that decode out to
daemon-owned destinations and browser recording without opening another scanner
audio session. Independent decoded-PCM client subscriptions may be added later;
a slow or failed subscriber must never block RTP reception or another subscriber.

The web dashboard and explicit daemon-backed CLI and TUI modes consume local
daemon services instead of opening duplicate scanner connections. Standalone CLI
and TUI operation remains available where practical. Future automation clients,
including Home Assistant, should follow the same single-owner boundary.

### Deterministic lifecycle behavior

Startup, reconnect, cancellation, teardown, and partial failure paths are first
class behavior.

Workers, timers, callbacks, sockets, files, subprocesses, and audio devices must
have deterministic ownership and shutdown. Repeated requests should be idempotent
or rejected clearly.

## Configuration and secrets

Layered application configuration uses this precedence:

1. built-in defaults;
2. system configuration;
3. user configuration;
4. environment variables;
5. command-line arguments.

The versioned schema, supported settings, and path behavior are documented in
[the configuration guide](configuration.md).

Secrets should be referenced rather than embedded in ordinary profiles,
Favorites data, exported configuration, logs, traces, or API responses.

Logging and diagnostic output must redact credentials, tokens, source passwords,
private endpoints when appropriate, and other secret-bearing values.

## Security and network boundaries

SDS200 UDP control, RTSP/RTP audio, scanner discovery, and FTP access are intended
for trusted local networks or trusted VPNs.

- Do not expose scanner UDP port 50536 directly to the public internet.
- Do not expose unauthenticated scanner-control or Favorites-write interfaces.
- Keep web and daemon client services local-only by default.
- Require explicit authentication and transport-security planning before remote
  access.
- Avoid wildcard-interface binds as a default.
- Treat recordings and metadata as potentially sensitive.

The project is not a safety-critical emergency receiver or dispatch system.
Scanner users must retain appropriate independent monitoring equipment and
operational procedures.

## Validation policy

Normal automated tests must not require physical scanner hardware.

Protocol and lifecycle behavior should be covered with:

- synthetic fixtures;
- sanitized captures;
- replay tests;
- deterministic fake transports;
- fault injection;
- platform-independent unit and integration tests.

Physical validation must be documented separately with scanner model, firmware,
transport, scenario, and observed limitations.

Documentation must distinguish:

- implemented behavior;
- modeled or fixture-tested behavior;
- physically validated behavior.

SDS200 network control and audio have physical validation. SDS100 USB control has
physical validation. SDS150 support is implemented and fixture-tested but physical
validation is deferred until hardware is available.

Lack of SDS150 hardware must not block unrelated releases.

## Interface direction

### Textual TUI

The Textual interface should remain a sustained-operation workstation rather than
a thin command wrapper. It should continue to use renderer-neutral state and
services for diagnostics, controls, audio, recordings, and mode-aware screens.

### Web dashboard

The responsive loopback dashboard now provides authoritative scanner and runtime
state, connection health, ordered live updates, explicit browser audio playback,
daemon-owned recording telemetry, newest-first finalized recording inventory,
safe saved-WAV playback and download, and browser-local visual presentation.

The web process remains a daemon client and does not open scanner hardware or a
second RTSP/RTP session. The dashboard also provides capability-negotiated
semantic scanner controls and browser-local system-adaptive, LCARS-inspired,
Matrix-inspired, First Responder, and Amateur Radio themes over one shared
accessible structure. The four custom themes can become immersive full-screen
desktop workstations with renderer-specific staging and instrumentation while
compact displays reflow the same controls and state. Theme staging remains
decorative, pointer-inert, reduced-motion aware, and independent of daemon or
scanner state. Remaining dashboard work includes operational logs, additional
shared branding assets, and deliberate authentication and transport-security
design before any supported remote access.

### Home Assistant

Home Assistant must preserve the same single-owner daemon boundary rather than
opening another scanner control or RTSP/RTP session. Milestone 20.8 establishes a
generic daemon-owned MQTT publication substrate over the existing authoritative
event stream. Milestone 20.9 adds optional semantic MQTT scanner commands through
the existing daemon control boundary. Milestone 20.10 adds read-only Home
Assistant MQTT device discovery as an adapter over that generic contract,
including Home Assistant birth-triggered republication while leaving scanner
ownership and semantic state authoritative in the daemon. Milestone 20.11 packages
that existing ownership runtime and dashboard as one Home Assistant App with
Supervisor MQTT service adaptation, authenticated Ingress, persistent recordings,
and a narrowly published RTP UDP port.

The implemented sequence is:

- retain generic semantic daemon/scanner/radio/audio/recording/destination state
  and availability on MQTT without publishing every 500 ms PSI packet;
- route MQTT commands through the daemon's existing semantic scanner control
  operations rather than raw scanner keys, implemented as the opt-in Milestone
  20.9 command/response contract;
- expose Home Assistant MQTT device Discovery over the stable generic state and
  availability contract, implemented as read-only Milestone 20.10 entities with
  deterministic namespace-derived device identity and birth-triggered
  republication; and
- package one Home Assistant App around the existing daemon and web dashboard,
  implemented in Milestone 20.11 with Supervisor MQTT service adaptation,
  Ingress, writable Home Assistant `/media` recording storage, safe migration
  from legacy `/data/recordings`, and fixed UDP RTP publication without enabling
  host networking.

Milestone 20.12.1 completed Home Assistant configuration translations so the App
Configuration page can give the scanner host, MQTT topic prefix, and recording
directory user-facing names and descriptions. The recording directory description
explicitly identifies `/media` as its root without changing the existing strict
media-relative path contract. Automated validation is complete; repository-managed
physical rendering is assigned to the v0.20.2 tagged App acceptance run.

Milestone 20.12.2 completed the first-party SDS200 Lovelace card without requiring
HACS. The App safely installs the resource under Home Assistant `www`, the card
uses Home Assistant's supported state context and graphical form schema, and the
card remains read-only with respect to scanner control. Physical HAOS rendering
is assigned to the v0.20.2 tagged App acceptance run.

Milestone 20.12.3 completed the deliberate Home Assistant control adapter over the
existing semantic daemon-control boundary. Discovery now adds four authoritative
Hold switches and Previous Channel, Next Channel, and Reconnect Scanner buttons
using seven dedicated QoS 0 non-retained topics. The adapter creates fresh
internal daemon request IDs, reuses existing typed control operations, derives
navigation only from ordered daemon-owned radio state, and clears navigation
context on scanner disconnect or event-stream resynchronization. The Home
Assistant App continues to disable the generic daemon MQTT request-envelope
command topic, and the daemon remains the sole scanner owner.

Milestone 20 release closure is now limited to v0.20.2 packaging, documentation,
reviewed wiki publication, tagged image publication, and repository-managed HAOS
acceptance. No additional Home Assistant runtime capability is required before
that release.

Current Home Assistant Discovery contains seventeen components: the original ten
daemon/scanner/radio/audio/recording state and diagnostic components plus the
seven bounded scanner controls. Site identity as an additional read-only state
entity, scanner mode, destination health, richer scanner events, authenticated or
TLS LAN gateways, remote daemon-backed CLI/TUI/GUI transports, and an optional
host-network deployment variant remain separate future considerations.

HACS may still be evaluated later as an optional distribution channel, but it is
not a dependency of the primary Home Assistant App repository distribution path.

### Future GUI and themes

A future desktop GUI may reuse the same services and API.

The web dashboard now provides optional LCARS-inspired, Matrix-inspired, First
Responder, and Amateur Radio environments over one shared accessible structure
rather than separate interfaces. They use renderer-specific structural tokens
and an ARIA-hidden decorative stage for cinematic depth, console geometry,
terminal fields, dispatch instrumentation, and SDS200-inspired physical details.
Their selection remains browser-local presentation state and does not alter the
renderer-neutral terminal palettes, daemon state, scanner ownership, or API
behavior. Future desktop interfaces may reuse the same semantic services while
choosing their own renderer-specific design tokens.

Scalable, theme-aware vector effects and responsive layouts should continue to
be preferred as shared visual work expands so the design can adapt to web,
terminal, desktop, documentation, 4K workstations, and compact Raspberry Pi
presentation surfaces without requiring separate semantic interfaces.

## Favorites Workspace

The Favorites Workspace is a future product area for browsing, validating,
editing, importing, exporting, and synchronizing scanner programming data.

### Read-only foundation

The first storage-facing implementation should be read-only and support:

- Favorites Lists;
- systems;
- departments;
- sites;
- channels;
- hierarchy navigation;
- search and filtering;
- schema validation;
- comparison and preview;
- preservation of unknown fields.

Automated tests should use fixtures and copied storage images rather than a live
scanner volume.

### Mandatory backup-before-write rule

Every Favorites write operation must create a complete backup before modifying the
active data set.

This is a project constraint, not an optional convenience.

A write workflow should:

1. identify and validate the target;
2. acquire an exclusive operation boundary where possible;
3. create and verify a complete backup;
4. write changes to a staging area;
5. parse and read back staged data;
6. compare staged data with the intended result;
7. replace the active data only after verification;
8. record a rollback manifest and operation report;
9. preserve the backup until explicitly removed under a separate policy.

The tool should refuse a write when backup, staging, verification, target
identity, or conflict checks cannot be completed safely.

### Storage backends

Potential backends include:

- USB mass storage with device discovery and safe handling;
- FTP on trusted local networks or VPNs;
- local copied images for testing and offline work.

FTP credentials must be stored through secret references and must never be
embedded in exported Favorites files.

### Synchronization and conflicts

Synchronization must detect concurrent changes and avoid silent last-writer-wins
behavior.

The UI should show:

- source and target revisions;
- proposed additions, changes, and removals;
- conflicts;
- validation warnings;
- backup and rollback locations;
- the exact write plan before confirmation.

### RadioReference and MyRR

RadioReference-assisted import may provide previewable updates with provenance and
field ownership.

Users should be able to:

- review imported changes;
- choose local or external values during a conflict;
- retain local-only annotations;
- detach a record from its external source;
- identify when externally sourced data was last updated.

MyRR synchronization should be investigated only through an approved and
documented interface. The project should not scrape or rely on undocumented
private endpoints.

## Recording and audio direction

The recording stack now includes renderer-neutral identities and path policy,
configurable organization, metadata sidecars, recursive inventory, deterministic
retention planning and execution, local TUI and CLI workflows, daemon-owned
recording over the shared decoded-PCM router, and browser recording plus safe
finalized-WAV playback and download. Destructive file-management behavior must
remain explicit, inventory-bound, recoverable where practical, and disabled by
default unless the user requests it.

The audio stack includes one daemon-owned SDS200 RTSP/RTP session, accepted-PCMU
publication, single-pass decoding, decoded-PCM fanout, per-subscriber health,
explicit PortAudio, PipeWire, PulseAudio, and ALSA playback adapters, remote
destinations, daemon-backed CLI/TUI audio, and browser PCMU playback.

Remaining future audio work includes:

- bounded local decoded-PCM client subscriptions and consumption adapters;
- layered saved-playback configuration and automatic backend selection; and
- continued separation between control, recording, playback, encoder, and
  transport failures.

## Advanced scanner capabilities

The remote-command specification contains additional capabilities that require
research, fixtures, and physical evidence before support is promised.

Exploratory areas include:

- `GLT` Favorites and hierarchy retrieval;
- `FQK` Favorites quick keys;
- `QSH` Quick Search control;
- `URC` scanner recording control;
- `AST` and `APR` analysis controls;
- `PWF` and `GWF` waterfall data;
- `MNU`, `MSI`, `MSV`, and `MSB` menu operations;
- NAC, RAN, color code, area, activity, and quality details;
- conventional and trunking discovery modes;
- system-status and RF-power plot screens.

Protocol research should preserve raw evidence and avoid inventing semantics for
fields that have not been observed or documented.

## Platform direction

Linux and Raspberry Pi remain the current operational priority.

The project should preserve portable Python design and later validate Windows and
macOS behavior, but native packaging for every platform does not need to block
current milestones.

The Raspberry Pi 7-inch 800 by 480 display is a useful compact reference target.
Layouts must remain responsive rather than assuming one fixed screen size.

## Release principles

- Release only completed and validated milestone slices.
- Keep experimental or hardware-dependent claims out of stable documentation.
- Use hardware-independent CI for normal pull requests.
- Record physical validation evidence separately.
- Preserve compatibility or provide an explicit migration plan.
- Prefer small, reviewable milestone branches.
- Update the roadmap when scope is deferred, completed, split, or reordered.
- Keep the project vision broad enough that unscheduled ideas are not lost.
