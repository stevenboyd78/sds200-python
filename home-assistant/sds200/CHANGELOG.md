# Changelog

## Unreleased

## 0.20.1

- Recordings now use writable Home Assistant media storage, defaulting to
  `/media/sdsctl/recordings`, with a configurable media-relative
  `recording_directory`.
- Existing v0.20.0 files under `/data/recordings`, including metadata sidecars
  and nested library paths, migrate safely without overwriting destination
  conflicts.
- The dashboard groups daemon runtime with scanner connection, moves scanner
  reconnect into that panel, separates active capture from recent recordings,
  and gives the finalized library more vertical room.
- The Home Assistant sidebar panel requests the `mdi:radio-tower` icon.

## 0.20.0

- Project-consistent Home Assistant App icon and logo presentation assets.
- Initial Home Assistant App packaging for the existing SDS200 daemon and web
  dashboard.
- Supervisor MQTT service adaptation with ten read-only MQTT Discovery entities.
- Authenticated Ingress dashboard with live scanner state, controls, browser
  audio, recording, and saved-recording playback.
- Persistent recordings under `/data/recordings`.
- Fixed UDP `50000` publication for inbound SDS200 RTP without host networking.
- amd64 and aarch64 image build/publishing workflow.
