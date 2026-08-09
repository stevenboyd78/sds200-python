# sds200 Home Assistant App

## Requirements

- Home Assistant OS with Apps/Supervisor support
- LAN-connected Uniden SDS200
- Home Assistant MQTT service
- UDP port `50000` available on the Home Assistant host

## Installation

For normal installation:

1. open **Settings > Apps > App store**;
2. open the top-right three-dot menu and choose **Repositories**;
3. add `https://github.com/stevenboyd78/sds200-python`;
4. select the **sds200** App from the repository;
5. install it, then configure the scanner host.

Published versions use the release image from GHCR. Local `/addons` staging is
only for development builds.

## Configuration

| Option | Required | Default |
| --- | --- | --- |
| `scanner_host` | yes | none |
| `mqtt_topic_prefix` | no | `sdsctl` |

Set `scanner_host` to the SDS200 LAN hostname or IP address.

The App automatically obtains the selected MQTT service from Supervisor and
enables the existing read-only Home Assistant MQTT Discovery adapter. Semantic
MQTT scanner commands remain disabled.

## Network audio

The SDS200 sends RTP audio back to the client over UDP. The App fixes that
destination at UDP `50000` and Supervisor maps host UDP `50000` to the same
container port.

If scanner status works but recordings show zero packets and zero samples, verify
the App Network configuration contains:

```text
SDS200 RTP audio
50000/udp -> 50000
```

Host networking is not required.

## Web UI

Use **Open Web UI** from the App page.

The dashboard runs through authenticated Home Assistant Ingress and supports:

- live scanner state;
- System, Department, Site, and Channel Hold/release;
- previous/next channel and reconnect controls;
- browser audio;
- daemon-owned recording;
- finalized recording inventory and playback; and
- the existing dashboard themes and API documentation.

## Recordings

Recordings are stored under `/data/recordings` and persist across App stop/start
and container replacement.

Stopping a recording finalizes the WAV before it is added to the recent-recording
inventory.

## MQTT entities

The discovered SDS200 device contains ten read-only entities:

- Daemon State
- Scanner Connection
- System
- Department
- Channel
- Signal
- RSSI
- Audio
- Recording
- Recording Status

Scanner model and firmware are included in device metadata when available.

## Security

The App does not enable `host_network`.

The dashboard port is not published directly to the LAN; Home Assistant Ingress
is the browser access boundary. Only UDP `50000` is published for SDS200 RTP.

The daemon API, event, PCMU, and recording-file services remain private
Unix-domain sockets inside the App container.

The current MQTT adapter does not configure TLS. Keep Home Assistant, the MQTT
broker, and the scanner on trusted networks.

## Troubleshooting

### Repository does not appear in the App store

Refresh the browser after adding the repository. If the repository still does
not appear, inspect the Home Assistant Supervisor log for repository or App
configuration errors.

### Local App changes do not appear

Refresh the Home Assistant page and Local Apps repository so Supervisor rereads
the updated `config.yaml`.

### Browser audio stays on Buffering

Start a recording and inspect its packet counter. If recording also stays at
zero, verify UDP `50000` before troubleshooting Ingress.

### MQTT Discovery is missing

Confirm the Home Assistant MQTT integration is active and check the App Logs tab
for MQTT service errors.

### Logs

App stdout and stderr are available from the Home Assistant App Logs tab.
