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
| `recording_directory` | no | `sdsctl/recordings` |

Set `scanner_host` to the SDS200 LAN hostname or IP address.

Set `recording_directory` to a relative path below Home Assistant `/media`. The
default `sdsctl/recordings` resolves to `/media/sdsctl/recordings`. Absolute
paths and traversal components are rejected.

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
- reconnect from Scanner connection plus previous/next channel controls;
- browser audio;
- daemon-owned recording;
- finalized recording inventory and playback; and
- the existing dashboard themes and API documentation.

On desktop, Scanner connection also contains daemon runtime, while Browser audio,
Capture, and Recent recordings each have their own lower dashboard panel.
Responsive layouts preserve those functional groups on narrower screens.

## Recordings

Recordings are stored under `/media/<recording_directory>` and persist across App
stop/start and container replacement. With the default option, the library is
`/media/sdsctl/recordings`.

The App maps Home Assistant media storage read/write so finalized WAV files and
their metadata sidecars can be managed through the Home Assistant media tree,
including Samba or SSH access when those services expose `/media`.

When upgrading from v0.20.0, startup migrates legacy files from
`/data/recordings` into the configured media library before launching the daemon.
The migration is recursive and refuses to overwrite a differing destination
file. Copied files are verified before their legacy sources are removed, so an
interrupted migration can be resumed safely.

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

## Bundled Lovelace card

The Home Assistant App installs its first-party read-only SDS200 card at:

```text
/homeassistant/www/sds200/sds200-card.js
```

Home Assistant serves that file to the frontend as:

```text
/local/sds200/sds200-card.js
```

Register that URL once in **Settings > Dashboards > Resources** as a
**JavaScript Module**. HACS is not required.

If the App creates Home Assistant's `www` directory for the first time, restart
Home Assistant Core once before registering the resource so `/local` becomes
available.

The automatic `/local` delivery requires the App to map Home Assistant's
configuration directory read/write. That filesystem permission is broader than
the single card file: the container can technically write elsewhere in the Home
Assistant configuration tree while it is running. The SDS200 installer
deliberately limits its own behavior to creating `www/sds200` when necessary and
creating or replacing only `www/sds200/sds200-card.js`. It does not edit Home
Assistant YAML, `.storage`, dashboards, or resource registration.

Failure to install or update the optional card is isolated from the scanner
runtime. The App logs a warning and continues starting the daemon and web
dashboard.

The card intentionally does not call the App, daemon, scanner, MQTT broker, or
Home Assistant APIs. It subscribes only to Home Assistant's supported `states`
data context through the frontend `context-request` mechanism.

After registering the resource, add **SDS200 Scanner** from the Home Assistant
card picker. The card uses Home Assistant's built-in graphical form editor.
Expand **SDS200 entities** and select the entities created by the SDS200 MQTT
Discovery device. Entity selectors are constrained to the expected `sensor` or
`binary_sensor` domain.

YAML configuration remains available as a fallback:

```yaml
type: custom:sds200-card
title: SDS200 Scanner
entities:
  scanner_connected: binary_sensor.REPLACE_ME
  system: sensor.REPLACE_ME
  department: sensor.REPLACE_ME
  channel: sensor.REPLACE_ME
  signal: sensor.REPLACE_ME
  rssi: sensor.REPLACE_ME
  audio_running: binary_sensor.REPLACE_ME
  recording_active: binary_sensor.REPLACE_ME
  recording_status: sensor.REPLACE_ME
  daemon_state: sensor.REPLACE_ME
```

Use the actual entity IDs created by the SDS200 MQTT Discovery device. The first
card slice is deliberately read-only. Scanner controls remain a separate Home
Assistant control-adapter milestone.

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

If recording packets advance but live audio is silent, first verify saved
recording playback plus browser, tab, and system audio output. Live Browser Audio
uses Web Audio, while finalized recordings use the browser's native media
playback path, so a browser audio-service problem can affect only the live path.

### Recordings are not visible through Samba or SSH

The default recording library is `/media/sdsctl/recordings`, not
`/data/recordings`. A custom `recording_directory` is also relative to `/media`.
Confirm the Samba or SSH service being used exposes Home Assistant media storage.

### MQTT Discovery is missing

Confirm the Home Assistant MQTT integration is active and check the App Logs tab
for MQTT service errors.

### Logs

App stdout and stderr are available from the Home Assistant App Logs tab.
