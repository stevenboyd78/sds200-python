# Home Assistant App

Milestone 20.11 packages the existing `sdsctl daemon` ownership runtime and
daemon-backed web dashboard as one Home Assistant App. The App is an adapter and
process supervisor around those existing services; it does not create another
scanner control connection, PSI stream, RTSP/RTP session, recording owner, or
Home Assistant-specific scanner state machine.

## Architecture

The App starts two child processes:

1. the existing foreground `sdsctl daemon`, which remains the only scanner owner;
2. the existing `sdsctl web` service in explicit Home Assistant Ingress mode.

The App supervisor starts the daemon first, probes the private daemon API until it
is ready, and only then starts the web child. Failure of either child fails the
App and stops the sibling. Shutdown stops the web child before the daemon so
active browser streams close before daemon-owned recordings, audio, MQTT, and
scanner ownership are finalized.

Private runtime files live under `/run/sdsctl`:

- `daemon.sock`
- `events.sock`
- `pcmu.sock`
- `recordings.sock`
- generated `daemon-mqtt.toml`

These remain container-private Unix-domain interfaces. The Home Assistant App
does not expose them as LAN TCP services.

## Requirements

The App requires:

- Home Assistant OS with Apps/Supervisor support;
- a LAN-connected Uniden SDS200 reachable from the Home Assistant host;
- an available Home Assistant MQTT service;
- host UDP port `50000` available for SDS200 RTP audio; and
- browser access to the Home Assistant frontend for Ingress.

Home Assistant OS is the physically validated release target. Other historical
or development Supervisor-based installation types are not part of the v0.20.0
release validation matrix.

The current Home Assistant MQTT adapter supports the non-TLS MQTT service shape
used by the tested deployment. If Supervisor reports the selected MQTT service
with TLS enabled, App startup rejects that service instead of silently weakening
or misconfiguring transport security.

## Configuration

The App exposes three options:

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `scanner_host` | yes | none | SDS200 LAN hostname or IPv4 address |
| `mqtt_topic_prefix` | no | `sdsctl` | Generic daemon MQTT topic root |
| `recording_directory` | no | `sdsctl/recordings` | Path below `/media` |

Home Assistant writes these values to `/data/options.json`. The App reads that
file at startup and converts the Supervisor MQTT service response into the
existing strict daemon MQTT configuration.

`recording_directory` is always interpreted relative to Home Assistant `/media`.
The default resolves to `/media/sdsctl/recordings`. Absolute paths, empty path
components, `.` and `..` traversal components, backslashes, and malformed values
are rejected before the daemon is started.

The MQTT password is never written into the generated TOML file. It is supplied
to the daemon through a dedicated environment variable. The Supervisor token is
used only by the App supervisor while resolving the MQTT service and is removed
from both daemon and web child environments.

Home Assistant Discovery is enabled by the App adapter. Semantic MQTT scanner
commands remain disabled.

## Networking

### Scanner RTP audio

SDS200 network audio is negotiated over RTSP, but the scanner sends RTP audio
back to a UDP port selected by the client. A container-local ephemeral port is
not sufficient because the physical scanner must be able to route packets back
through the Home Assistant host.

The App therefore fixes the daemon RTP receive port at UDP `50000`:

```text
sdsctl --host <scanner_host> daemon --rtp-bind-port 50000 ...
```

and publishes the same port through Supervisor:

```yaml
ports:
  50000/udp: 50000
ports_description:
  50000/udp: "SDS200 RTP audio"
```

No host network is required. If recording elapsed time advances but packet and
sample counters remain zero, verify that the App's Network configuration shows
UDP `50000`, that the port is not already in use, and that the scanner can route
UDP traffic to the Home Assistant host.

### Ingress

The web service listens on container port `8099` only for Home Assistant Ingress.
Ingress mode is explicit and separate from the normal standalone loopback mode.

The Ingress application guard admits only the actual Supervisor proxy peer
`172.30.32.2` and returns `403` to other peers. Uvicorn proxy-header processing
is disabled so an untrusted forwarded address cannot replace the real ASGI peer.

Home Assistant performs user authentication before forwarding the request. The
App does not publish the dashboard port directly to the LAN.

Dashboard assets, API requests, Server-Sent Events, browser audio, scanner
controls, Swagger/ReDoc assets, saved recording playback, and recording downloads
derive their URLs from the active Ingress prefix rather than assuming `/`.

Long-lived SSE and audio responses are compatible with Home Assistant Ingress
streaming.

## Browser audio

The preferred browser renderer is AudioWorklet. Some Home Assistant installations
are opened over a non-secure HTTP browser origin where the browser can construct
an `AudioContext` but does not expose `audioWorklet`.

The dashboard feature-detects that condition. When AudioWorklet is available it
uses the normal packaged `audio-worklet.js` renderer. Otherwise it falls back to
a script-driven Web Audio processor with the same G.711 mu-law decoding, bounded
buffering, gap insertion, and linear resampling behavior.

The browser still consumes the same daemon-owned PCMU stream in both cases.

## Persistent recordings

The App maps Home Assistant media storage read/write. Daemon-owned recordings are
stored under:

```text
/media/<recording_directory>
```

The default option therefore produces:

```text
/media/sdsctl/recordings
```

Finalized WAV files and adjacent metadata sidecars remain available after App
restart and can be managed through Home Assistant media storage. Samba or SSH
tools can also manage the files when those services expose `/media`.

On upgrade from v0.20.0, the App migrates the legacy `/data/recordings` tree into
the configured media library before starting the daemon. Migration preserves
nested relative paths and sidecars, preflights all destination collisions, and
never overwrites a differing file. Missing destinations are copied with metadata
and byte-verified before the source is removed. An identical destination is
treated as an already completed copy, allowing an interrupted migration to
resume safely.

Changing `recording_directory` later selects a different media library; it does
not implicitly move files between two already existing `/media` libraries.

The web dashboard lists finalized recordings through the existing private
recording-file service rather than opening arbitrary paths. Saved recordings can
be played or downloaded through Ingress.

## Home Assistant MQTT Discovery

The App enables the daemon's Home Assistant MQTT Discovery adapter plus the
dedicated Milestone 20.12.3 Home Assistant control adapter. One SDS200 device
contains seventeen components:

| Component | Home Assistant platform |
| --- | --- |
| Daemon State | sensor |
| Scanner Connection | binary sensor |
| System | sensor |
| Department | sensor |
| Channel | sensor |
| Signal | sensor |
| RSSI | sensor |
| Audio | binary sensor |
| Recording | binary sensor |
| Recording Status | sensor |
| System Hold | switch |
| Department Hold | switch |
| Site Hold | switch |
| Channel Hold | switch |
| Previous Channel | button |
| Next Channel | button |
| Reconnect Scanner | button |

Device metadata includes Uniden as manufacturer plus scanner model and firmware
when the daemon's authoritative snapshot contains them.

The App keeps the generic daemon MQTT request-envelope command transport
disabled. Home Assistant controls instead use seven exact dedicated QoS 0,
non-retained topics below:

```text
<mqtt_topic_prefix>/home_assistant/control/
```

The four Hold switches publish `ON` or `OFF` and are non-optimistic. Their state
comes from the authoritative daemon radio-state topic, so a rejected command
does not falsely change the switch. A Hold switch is available only when the
daemon is online, the scanner is connected, and the corresponding hold state is
currently meaningful.

Previous Channel and Next Channel publish `PRESS`. They are available only for a
current trunked `TGID` or conventional `ConvFrequency` channel with a valid SDS200
channel index. The adapter reuses the existing bounded current-channel semantic
resolver and translates the action to `scanner.previous` or `scanner.next`. Its
navigation context comes only from the latest ordered daemon radio state and is
cleared on scanner disconnect or event-stream resynchronization until a fresh
authoritative state arrives.

Reconnect Scanner publishes `PRESS` to the dedicated reconnect topic. The daemon
retains the existing capability check, so unsupported transports still reject
the semantic `scanner.reconnect` operation.

Home Assistant never supplies a daemon request ID. The adapter creates a fresh
internal request ID for every accepted action and dispatches through the existing
typed daemon-control boundary. It does not expose raw scanner keys, publish to
the generic `<mqtt_topic_prefix>/commands` topic, create a response topic, or
open another scanner/control session.

The bundled Lovelace card remains read-only and transport-free. Scanner controls
are standard Home Assistant switch and button entities rather than direct card,
App HTTP, scanner, or MQTT calls.

## Installation from the Home Assistant App repository

For a normal published installation:

1. open **Settings > Apps** in Home Assistant;
2. open **App store**;
3. open the top-right three-dot menu and choose **Repositories**;
4. add `https://github.com/stevenboyd78/sdsctl`;
5. open the new repository and select **sds200**;
6. install the App;
7. configure `scanner_host` and, if needed, `mqtt_topic_prefix` or
   `recording_directory`;
8. start the App and open **Web UI**.

Published releases use the image configured in
`home-assistant/sds200/config.yaml`. The App version and package version match
the release tag, and the release workflow publishes amd64 and aarch64 images
plus the generic multi-architecture GHCR image.

The repository installation path is the normal distribution mechanism.
Copying files into `/addons` is not required for a published release.

## Local HAOS development

For development against physical hardware, Home Assistant supports local Apps
under `/addons/<slug>`. The repository Dockerfile requires the project source
context, so create a staging directory containing the App manifest/Dockerfile
plus the Python project files:

```bash
STAGE="${HOME}/sds200-ha-app-dev"

rm -rf "${STAGE}"
mkdir -p "${STAGE}"

cp home-assistant/sds200/Dockerfile "${STAGE}/"
cp home-assistant/sds200/config.yaml "${STAGE}/"
cp .dockerignore pyproject.toml README.md LICENSE "${STAGE}/"
cp -a src "${STAGE}/"

sed -i   's|^image: "ghcr.io/stevenboyd78/sds200-home-assistant"$|# image: "ghcr.io/stevenboyd78/sds200-home-assistant"|'   "${STAGE}/config.yaml"
```

Copy that staged directory to `/addons/sds200` through the Home Assistant Samba
or SSH App, refresh the Local Apps repository, then install/rebuild the local
`sds200` App. Commenting out `image:` is development-only; the committed
production manifest retains the GHCR image reference.

Python bytecode is excluded from App build contexts through `.dockerignore`.

Useful Home Assistant developer references:

- https://developers.home-assistant.io/docs/apps/configuration/
- https://developers.home-assistant.io/docs/apps/testing/
- https://developers.home-assistant.io/docs/apps/presentation/
- https://developers.home-assistant.io/docs/apps/security/

## Operation

After installation:

1. set `scanner_host`;
2. leave `mqtt_topic_prefix` at `sdsctl` unless a different namespace is needed;
3. leave `recording_directory` at `sdsctl/recordings` unless another Home
   Assistant media subdirectory is preferred;
4. start the App;
5. open **Web UI**;
6. confirm live scanner state;
7. exercise browser audio or recording as needed.

Normal routine startup is intentionally quiet. App stdout/stderr is available
from the Home Assistant App Logs tab when a failure occurs.

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

Use the actual entity IDs created by the SDS200 MQTT Discovery device. The card
remains deliberately read-only. Milestone 20.12.3 scanner controls are separate
standard Home Assistant switch and button entities, so the card does not acquire
a scanner, daemon, MQTT, or Home Assistant service-call transport.

## Security boundary

The default App deliberately avoids `host_network`.

Only SDS200 RTP UDP `50000` is published. The web dashboard remains behind
authenticated Home Assistant Ingress, and the daemon API/event/PCMU/recording
interfaces remain private Unix-domain sockets.

Enabling host networking alone would not make remote `sdsctl daemon-client`, TUI,
or future GUI clients work because those clients currently consume Unix-domain
sockets rather than LAN TCP services. A network daemon-client transport,
authentication/access policy, and any optional host-network App variant belong
to a separate future security boundary.

The SDS200's own LAN protocols and the current non-TLS MQTT adapter are not
encrypted. The App keeps generic daemon MQTT commands disabled, but its seven
dedicated Home Assistant control topics still make authorized broker publishers
scanner-control principals through the bounded semantic adapter. Keep the
scanner, broker, Home Assistant host, and App on trusted networks and restrict
publish authority for the dedicated control namespace accordingly.

## Troubleshooting

### App does not appear after copying a local build

Refresh the Home Assistant page and the Local Apps repository. Supervisor must
reread `config.yaml` before newly added options or port mappings appear.

### Supervisor pulls an image instead of building local source

For local development only, comment out the `image:` line in the staged
`config.yaml`.

### Scanner state works but audio and recording stay at zero packets

Confirm the App Network configuration shows UDP `50000` mapped to host port
`50000`. The daemon can report its audio runtime as running after RTSP setup even
when no RTP datagrams are reaching the container.

### Browser audio remains on Buffering

First check whether a daemon-owned recording receives packets. If recording also
stays at zero packets, troubleshoot UDP `50000` before investigating Ingress.

If recording receives packets but live Browser Audio remains silent, verify
saved-recording playback and browser, tab, and system audio output before
changing the App. Live Browser Audio uses Web Audio while finalized recordings
use the browser's native media playback path. If browser audio output is healthy
but live packets still do not reach the renderer, investigate the
daemon-PCMU/web/Ingress stream path.

### Recordings are not visible through Samba or SSH

The default recording library is `/media/sdsctl/recordings`, not the legacy
`/data/recordings` path. A custom `recording_directory` is relative to `/media`.
Confirm the Samba or SSH service being used exposes Home Assistant media storage.

### MQTT service startup fails

Check the App Logs tab and the configured Home Assistant MQTT service. The
current App adapter rejects an MQTT service that requires TLS.

### MQTT device is missing

Confirm Home Assistant's MQTT integration is active and the App remains running.
The App publishes Discovery after an authoritative daemon snapshot and
republishes it after the configured Home Assistant birth message.

## Physical validation

Milestone 20.11 was validated on August 9, 2026, on Home Assistant OS with a
physical Uniden SDS200 running firmware 1.26.01.

The validation covered:

- local App discovery, build, installation, configuration, and startup;
- live scanner connection and ordered scanning updates through Ingress;
- Channel Hold and release through the browser control API;
- live browser audio through Ingress;
- UDP `50000` RTP delivery through the Supervisor container mapping;
- daemon-owned recording with advancing packet/sample telemetry;
- WAV finalization, inventory, and saved playback;
- recording persistence and playback across App stop/start;
- clean App restart with scanner and audio recovery; and
- all ten Home Assistant MQTT Discovery entities with correct SDS200 model and
  firmware metadata.

That evidence is the historical Milestone 20.11 baseline. The three post-v0.20.1
Home Assistant slices require one tagged repository-managed acceptance run before
v0.20.2 release completion.

### v0.20.2 release acceptance

After the v0.20.2 tag publishes the amd64, aarch64, and generic
multi-architecture App images, validate the repository-managed App rather than a
previous Local App installation:

- confirm the App reports v0.20.2 and loads the matching documentation;
- confirm the 20.12.1 Configuration-page names and descriptions render for
  `scanner_host`, `mqtt_topic_prefix`, and `recording_directory`;
- confirm the recording-directory description identifies `/media` as its root
  and the default resolves to `/media/sdsctl/recordings`;
- confirm `/local/sds200/sds200-card.js` is installed and can be registered as a
  JavaScript Module;
- confirm **SDS200 Scanner** appears in the card picker, its graphical editor
  works, and the read-only card renders the selected Discovery state entities;
- confirm the SDS200 MQTT device exposes seventeen total components;
- exercise System, Department, Site, and Channel Hold in both meaningful
  desired-state directions and confirm authoritative Home Assistant state;
- exercise Previous Channel and Next Channel while a valid current TGID or
  conventional-frequency selection is available;
- exercise Reconnect Scanner and confirm bounded scanner recovery;
- confirm the App still does not subscribe to the generic
  `<mqtt_topic_prefix>/commands` request-envelope input;
- confirm Ingress scanner state, live browser audio, recording, finalized WAV
  playback, and App restart remain healthy;
- confirm repository-managed recordings persist across App restart or upgrade;
- confirm no second scanner, PSI, RTSP/RTP, or control owner appears; and
- record the Home Assistant OS/Supervisor version and SDS200 firmware used for
  the acceptance evidence.
