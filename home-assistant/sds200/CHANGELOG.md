# Changelog

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
