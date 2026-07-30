# Roadmap

This document records planned work for `sds200-python`. It is intentionally
forward-looking: listed items are not available until they appear in a released
changelog. Milestone order may change as hardware validation and protocol research
produce new information.

## Active milestone

### Milestone 16.4 — Recording metadata and organization

- Add optional sidecar JSON metadata.
- Capture scanner, system, department, site, channel, and frequency state at
  recording boundaries when available.
- Add safe filename components derived from scanner state.
- Define retention and organization helpers without deleting recordings by default.

## Planned milestones

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
- Milestone 16.2: immediate TUI live playback, repeatable recordings, a newest-first
  recording library, saved-recording playback, one shared RTSP/RTP stream, deferred
  PortAudio startup, warm mute and resume behavior, and SDS200 hardware validation.
- Milestone 16.3: service-neutral remote PCM destinations, a Broadcastify-compatible
  Icecast source adapter, an Asterisk Music-on-Hold bridge, deterministic reconnect
  and shutdown validation, physical SDS200 testing, and assigned production-feed
  authorization and routing validation.
