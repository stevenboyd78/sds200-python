# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/) as the public API matures.

## [Unreleased]

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

[Unreleased]: https://github.com/stevenboyd78/sds200-python/compare/v0.12.0...HEAD
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
