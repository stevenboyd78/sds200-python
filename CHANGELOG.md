# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/) as the public API matures.

## [Unreleased]

### Planned

- Validated SDS200 network audio transport after control reliability work
- Optional preferred-transport recovery without connection flapping

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

[Unreleased]: https://github.com/stevenboyd78/sds200-python/compare/v0.8.1...HEAD
[0.8.1]: https://github.com/stevenboyd78/sds200-python/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/stevenboyd78/sds200-python/compare/v0.5.3...v0.6.0
[0.5.3]: https://github.com/stevenboyd78/sds200-python/releases/tag/v0.5.3
