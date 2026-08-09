# sds200

The sds200 Home Assistant App runs the existing single-owner SDS200 daemon and
web dashboard under Home Assistant Supervisor.

It provides:

- the full scanner dashboard through authenticated Home Assistant Ingress;
- live scanner status and semantic scanner controls;
- browser audio from the daemon-owned SDS200 RTP stream;
- daemon-owned recordings persisted under `/data/recordings`;
- automatic use of the Supervisor-provided MQTT service; and
- ten read-only Home Assistant MQTT Discovery entities.

The App requires a LAN-connected SDS200 and publishes UDP port `50000` for the
scanner's inbound RTP audio. It does not enable host networking or expose the
daemon's private Unix-domain client sockets.

See the App Documentation tab for configuration, networking, storage, security,
and troubleshooting details.
