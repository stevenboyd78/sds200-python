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

A future daemon should own long-lived scanner, PSI, audio, recording, and remote
destination sessions.

The SDS200 accepts only one network-audio client at a time. The daemon should own
that single RTSP/RTP session, decode accepted audio once, and expose independent
bounded PCM or PCMU subscriptions to CLI, TUI, web, recording, streaming, and
automation clients. A slow or failed subscriber must not block RTP reception or
another subscriber.

CLI, TUI, web, and automation clients should be able to consume the daemon API
instead of opening duplicate scanner connections. Standalone operation may remain
available where practical.

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
- Bind future web and API services to localhost by default.
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

A future responsive dashboard should provide scanner state, connection health,
operational logs, recordings, audio destinations, and safe controls.

It should use the daemon API, bind locally by default, and require deliberate
security configuration before remote access.

### Home Assistant

Home Assistant should integrate through the daemon API rather than opening another
scanner connection.

Potential entities and events include:

- connection and availability state;
- current system, department, site, and channel;
- scanner mode and hold state;
- signal and RSSI;
- audio, playback, and recording status;
- destination health;
- safe supported controls and scanner events.

HACS packaging should wait until the API and entity model are stable.

### Future GUI and themes

A future desktop GUI may reuse the same services and API.

An LCARS-inspired theme remains a possible presentation option. Scalable SVG
assets and responsive layouts should be preferred so the design can adapt to
terminal, web, desktop, and compact Raspberry Pi displays.

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

Future recording work includes:

- safe scanner-state-derived path components;
- configurable organization policies;
- retention previews and reporting;
- sidecar-aware file management;
- no deletion by default;
- explicit confirmation for destructive policies.

Milestone 18 delivers per-subscriber audio health events and explicit
PortAudio, PipeWire, PulseAudio, and ALSA playback adapters.

Remaining future audio work includes:

- daemon ownership and bounded local client subscriptions;
- layered saved playback configuration and automatic backend selection;
- continued separation between control and audio failures.

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
