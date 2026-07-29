# Roadmap

This document records planned work for `sds200-python`. It is intentionally
forward-looking: listed items are not available until they appear in a released
changelog. Milestone order may change as hardware validation and protocol research
produce new information.

## Active milestone

### Milestone 16.2 — TUI audio workflow and repeatable recordings

- Add immediate unmuted TUI live playback and status controls without opening a
  second RTSP/RTP session.
- Allow multiple recording sessions without restarting the TUI.
- Generate collision-safe local timestamp filenames from an output directory and
  optional filename template.
- Preserve explicit one-file output and overwrite protection.
- List compatible recordings newest first with timestamp, duration, and size.
- Play, pause, resume, and stop saved recordings while automatically suspending and
  restoring enabled live playback.
- Show completion summaries and recording history.

## Planned milestones

### Milestone 16.3 — Remote audio destinations

- Add a destination configuration model that keeps credentials out of command-line
  history and operational logs.
- Investigate and implement a Broadcastify-compatible feed sink with the required
  codec, metadata, reconnect, and backoff behavior.
- Investigate and implement an Asterisk music-on-hold stream sink or documented
  adapter suitable for a configured music-on-hold class.
- Isolate every remote destination behind its own bounded queue so a slow or failed
  service cannot delay scanner RTP reception, local playback, or recording.
- Add deterministic disconnect, retry, credential-redaction, and shutdown tests
  before hardware/service validation.

### Milestone 16.4 — Recording metadata and organization

- Add optional sidecar JSON metadata.
- Capture scanner, system, department, site, channel, and frequency state at
  recording boundaries when available.
- Add safe filename components derived from scanner state.
- Define retention and organization helpers without deleting recordings by default.

### Milestone 16.5 — SDS150 hardware validation

- Validate model detection, battery/charge reporting, navigation, PSI state, and
  documented limits on physical SDS150 hardware.
- Record tested firmware and transport evidence in the supported-models guide.

### Milestone 16.6 — v0.16.0 release preparation

- Select only completed and validated Milestone 16 work for release.
- Run the full software, package, Raspberry Pi, audio, and shutdown checklists.
- Publish a normal GitHub release and verify the clean PyPI installation.

## Longer-term ideas

- Pluggable encoder processes for destinations that do not accept native 8 kHz mono
  PCM or G.711 mu-law.
- Per-destination health events and operational metrics.
- Saved audio destination profiles with secret references rather than embedded
  passwords.
- Optional stream metadata updates synchronized with live PSI state.
- Additional local audio backends when PortAudio is unavailable.

## Completed milestone groups

- Milestones 1–10: typed core protocol, transports, discovery, profiles, reliability,
  documentation, packaging, and static quality gates.
- Milestones 11–14: SDS200 RTSP/RTP audio, native PCMU decoding, WAV recording,
  reusable recording sessions, and Textual audio controls.
- Milestone 15: deterministic TUI lifecycle hardening, operational logging,
  automatic stale-PSI recovery, Raspberry Pi fault injection, and v0.15.0.
- Milestone 16.1: decoded-PCM fanout, optional local playback, simultaneous playback
  and recording, sink reliability counters, roadmap enforcement, and SDS200 hardware
  validation.
