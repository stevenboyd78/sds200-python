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

### Milestone 16.5 — TUI diagnostics and mode-aware screens

- **16.5.0 — TUI log panel**
  - Route package log records into a bounded, thread-safe in-app panel.
  - Show the panel by default and allow it to be toggled without losing records.
  - Preserve optional file logging and restore stderr logging after TUI shutdown.
- **16.5.1 — Screen-mode foundation**
  - Add a renderer-neutral scanner-screen classifier with an unknown-screen
    fallback.
  - Add fixture and transition coverage for mode-specific GSI/PSI state.
- **16.5.2 — Quick Search and Close Call**
  - Present search frequency, modulation, hold/hit state, signal, RSSI, and
    reported tone or digital-code details.
  - Support both `SrchFrequency` and `CcHitsChannel` scanner-state nodes.
- **16.5.3 — Weather Mode**
  - Present weather channel, frequency, scan/hold state, signal, and RSSI.
  - Capture alert and SAME details when the scanner reports them.
- **16.5.4 — Tone Out Mode**
  - Present tone-out channel/profile, monitored frequency, tone values, and
    standby/detected/hold state when reported.
- **16.5.5 — SDS200 hardware validation**
  - Capture representative physical SDS200 GSI/PSI XML for every supported
    special screen mode.
  - Validate live transitions between normal scanning, search/Close Call,
    weather, and tone-out screens.
  - Document tested firmware, observed fields, and known limitations.

### Milestone 16.6 — SDS150 hardware validation

- Validate model detection, battery/charge reporting, navigation, PSI state, and
  documented limits on physical SDS150 hardware.
- Record tested firmware and transport evidence in the supported-models guide.

### Milestone 16.7 — v0.16.0 release preparation

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
