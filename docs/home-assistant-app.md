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

The App exposes two options:

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `scanner_host` | yes | none | SDS200 LAN hostname or IPv4 address |
| `mqtt_topic_prefix` | no | `sdsctl` | Generic daemon MQTT topic root |

Home Assistant writes these values to `/data/options.json`. The App reads that
file at startup and converts the Supervisor MQTT service response into the
existing strict daemon MQTT configuration.

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

Daemon-owned recordings are stored under:

```text
/data/recordings
```

Home Assistant persists `/data` across App stop/start and container replacement.
Finalized WAV files and metadata therefore remain available after App restart.

The web dashboard lists finalized recordings through the existing private
recording-file service rather than opening arbitrary paths. Saved recordings can
be played or downloaded through Ingress.

## Home Assistant MQTT Discovery

The App enables the existing Milestone 20.10 read-only device Discovery adapter.
One SDS200 device contains ten components:

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

Device metadata includes Uniden as manufacturer plus scanner model and firmware
when the daemon's authoritative snapshot contains them.

Discovery adds no Home Assistant command topic and no second scanner-control
path.

## Installation from the Home Assistant App repository

For a normal published installation:

1. open **Settings > Apps** in Home Assistant;
2. open **App store**;
3. open the top-right three-dot menu and choose **Repositories**;
4. add `https://github.com/stevenboyd78/sds200-python`;
5. open the new repository and select **sds200**;
6. install the App;
7. configure `scanner_host` and, if needed, `mqtt_topic_prefix`;
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
3. start the App;
4. open **Web UI**;
5. confirm live scanner state;
6. exercise browser audio or recording as needed.

Normal routine startup is intentionally quiet. App stdout/stderr is available
from the Home Assistant App Logs tab when a failure occurs.

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
encrypted. Keep the scanner, broker, Home Assistant host, and App on trusted
networks.

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
stays at zero packets, troubleshoot UDP `50000` before investigating Ingress. If
recording receives packets but browser audio does not, investigate the
daemon-PCMU/web/Ingress stream path.

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
