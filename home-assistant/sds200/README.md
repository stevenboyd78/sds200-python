# sds200

The sds200 Home Assistant App runs the existing single-owner SDS200 daemon and
web dashboard under Home Assistant Supervisor.

For a published release, add
`https://github.com/stevenboyd78/sds200-python` as a third-party repository from
**Settings > Apps > App store > Repositories**, then install **sds200** from that
repository. Local `/addons` staging is intended only for development.

It provides:

- the full scanner dashboard through authenticated Home Assistant Ingress;
- live scanner status and semantic scanner controls;
- browser audio from the daemon-owned SDS200 RTP stream;
- daemon-owned recordings stored in Home Assistant media, defaulting to
  `/media/sdsctl/recordings`;
- automatic use of the Supervisor-provided MQTT service; and
- ten read-only Home Assistant MQTT Discovery entities.

The App requires a LAN-connected SDS200 and publishes UDP port `50000` for the
scanner's inbound RTP audio. It does not enable host networking or expose the
daemon's private Unix-domain client sockets.

See the App Documentation tab for configuration, networking, storage, security,
and troubleshooting details.
