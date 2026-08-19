# Web dashboard

Milestone 20.1 established the optional daemon-backed HTTP service and
loopback-only command. Milestone 20.2 added the first accessible responsive,
read-only browser shell. Milestone 20.3 added live ordered browser updates over
Server-Sent Events. Milestone 20.4 added explicit browser playback of daemon-owned
PCMU audio. Milestone 20.5 added daemon-owned recording workflows, finalized
recording inventory, and safe saved-WAV playback and download. Milestone 20.6
added capability-negotiated scanner hold, previous/next navigation, and bounded
reconnect controls without changing daemon scanner ownership. Milestone 20.7
adds browser-local system-adaptive, LCARS-inspired, Matrix-inspired,
First Responder, and Amateur Radio themes over the same accessible dashboard
structure, including immersive full-screen desktop compositions and compact
responsive fallbacks for the four custom environments. Milestone 20.11 adds an
explicit Home Assistant Ingress mode, prefixed-path-safe assets and API requests,
Ingress framing policy, and browser-audio compatibility for non-secure Home
Assistant browser contexts without changing standalone loopback defaults.

## Architecture

The web service is a daemon client. It does not open USB serial hardware, create
an SDS200 UDP control connection, or start a second RTSP/RTP audio session.

Run the foreground daemon as the single scanner owner:

```bash
sdsctl --log-level INFO --host 192.168.0.251 daemon
```

Then start the web service in a separate terminal:

```bash
sdsctl web
```

The web process resolves all four private daemon sockets used by the dashboard:

- `daemon.sock` supplies bounded request-response status, recording operations,
  and inventory reads;
- `events.sock` supplies the authoritative snapshot-first ordered event stream;
- `pcmu.sock` supplies accepted RTP PCMU packets for explicit browser playback;
  and
- `recordings.sock` supplies finalized WAV bytes by daemon inventory-relative
  identifier.

Their default locations are under `$XDG_RUNTIME_DIR/sdsctl`, with the existing
user-state fallback when `XDG_RUNTIME_DIR` is unavailable.

Select explicit sockets when needed:

```bash
sdsctl web \
  --daemon-socket-path /run/user/1000/sdsctl/daemon.sock \
  --daemon-event-socket-path /run/user/1000/sdsctl/events.sock \
  --daemon-pcmu-socket-path /run/user/1000/sdsctl/pcmu.sock \
  --daemon-recording-file-socket-path /run/user/1000/sdsctl/recordings.sock
```

Every browser event connection creates an independent local
`DaemonEventClient`. The client validates the daemon event protocol, requires
the first event to be `stream.snapshot`, and enforces strictly increasing,
gap-free sequence delivery. Closing the browser stream closes that client
without stopping the daemon event service or scanner ownership.

Every browser audio connection creates an independent local `DaemonPcmuClient`.
The web service forwards each validated daemon PCMU v1 frame exactly as encoded;
it does not decode, re-encode, or create another scanner RTSP/RTP session.
Stopping playback or leaving the page closes that browser PCMU client without
affecting daemon audio ownership or other subscribers.

Browser recording commands use short-lived `DaemonApiClient` connections. The
daemon recording manager attaches `PcmWavSink` to the existing decoded-PCM router,
so starting a browser recording never creates another scanner RTSP/RTP session.
The recording remains daemon-owned if the page reloads or the web process exits.

Completed WAV playback and download use `recordings.sock`, not `pcmu.sock`.
The web service passes only daemon inventory-relative identifiers to
`DaemonRecordingFileClient`; it never opens recording filesystem paths itself.
The daemon recording-file service revalidates the inventory entry and securely
reopens it before streaming bytes.

## Installation

Install the optional web dependencies:

```bash
python -m pip install "sds200[web]"
```

The extra installs FastAPI and Uvicorn. Development installations also include
HTTPX2 for host-independent HTTP tests.

## Local binding and security boundary

The service binds to `127.0.0.1:8000` by default:

```bash
sdsctl web
```

Select another loopback address or local port:

```bash
sdsctl web \
  --listen-address ::1 \
  --listen-port 8123
```

The command accepts `localhost`, IPv4 loopback addresses, and IPv6 loopback
addresses. It rejects wildcard addresses, LAN addresses, public addresses, and
hostnames other than literal `localhost`.

The current web dashboard intentionally provides no authentication, TLS
termination, trusted-proxy handling, or supported arbitrary remote/LAN exposure.
Do not expose this standalone service through a public listener or reverse
proxy. Authentication and transport-security design must be completed before
remote access becomes a supported workflow.

Generic Compose deployment has one separate explicit container mode:
`sdsctl web --container-exposure`. It binds exactly `0.0.0.0:8000` inside the
container so Docker bridge publication can reach it, but Compose publishes it
only on Docker-host loopback. The default browser URL is
`http://127.0.0.1:8000/`; set `SDSCTL_WEB_PORT` to change only that host-loopback
port, not the fixed container port 8000. LAN and public clients cannot reach the
Compose publication by default. The internal wildcard is safe only because the
publication is constrained to `127.0.0.1`; never copy `--container-exposure`
into arbitrary public or LAN publication without a separate authentication and
TLS design. Do not use host networking for the web container. Home Assistant
Ingress remains a distinct mode with its Supervisor peer guard and is not
enabled by generic container exposure.

Uvicorn proxy-header trust and its identifying server header are disabled.
Graceful shutdown is bounded to two seconds so an intentionally long-lived SSE
or audio response cannot make one `Ctrl+C` wait indefinitely. Requests still
active at that deadline are cancelled by Uvicorn before application shutdown.

## Browser dashboard

Open the local dashboard after starting the web service:

```text
http://127.0.0.1:8000/
```

Theme selection is presentation-only and browser-local. **System** follows the
browser or operating-system light/dark preference. The four custom choices are
deliberately more theatrical while preserving the same dashboard semantics:

- **LCARS-inspired** connects the six operational panels with asymmetric rails,
  segmented console bands, luminous command-deck surfaces, and layered display
  depth.
- **Matrix-inspired** turns the shared dashboard into a cinematic terminal
  workstation with varied terminal panes, technical grids, scan illumination,
  data-field staging, and perspective depth.
- **First Responder** presents the same controls as a dispatch/CAD workstation
  with application chrome, tactical/radar instrumentation, restrained emergency
  light washes, and operations-center monitor depth.
- **Amateur Radio** frames the dashboard as an SDS200-inspired rack/front panel
  with a scanner display window, tactile controls, rotary hardware, vents,
  chassis depth, and bench-equipment lighting.

At desktop-class sizes the custom themes use a dense three-by-two full-screen
workstation composition with minimal page scrolling. Intermediate widths reflow
recording telemetry to preserve readable values, while compact and phone layouts
fall back to conventional scrolling without changing the semantic interface.
Large displays can expose more of the surrounding instrumentation rather than
merely enlarging controls.

The same-origin `/assets/theme-bootstrap.js` script runs in the document head
before `/assets/dashboard.css`. It validates the stored value, applies the
corresponding `data-theme` value to the document root before first paint, and
updates `color-scheme` and `theme-color` metadata. The normal dashboard Content
Security Policy remains unchanged: inline scripts and styles are still
forbidden, and no remote fonts, scripts, styles, or theme assets are required.
If local storage is unavailable, the dashboard safely falls back to **System**.

The cinematic layer is a shared `aria-hidden` decorative stage. It is
pointer-inert and carries no scanner meaning. All themes retain the same labels,
DOM structure, ARIA state, keyboard focus treatment, responsive behavior, and
status text; scanner state is never communicated by color alone. Decorative
animation and transitions are suppressed for `prefers-reduced-motion`, and the
more expensive effects are disabled in compact layouts.

## Theme gallery

These documentation captures render the real packaged dashboard and stylesheet
through `create_web_dashboard_app()`. The screenshot helper supplies deterministic
fictional daemon, scanner, radio, recording, and reliability state; the images do
not contain live scanner identifiers, locations, or recordings.

### System — 1920x1080

![System theme at 1920x1080](assets/web-dashboard/theme-system-1920x1080.png)

### LCARS-inspired — 1920x1080

![LCARS-inspired theme at 1920x1080](assets/web-dashboard/theme-lcars-1920x1080.png)

### Matrix-inspired — 1920x1080

![Matrix-inspired theme at 1920x1080](assets/web-dashboard/theme-matrix-1920x1080.png)

### First Responder — 1920x1080

![First Responder theme at 1920x1080](assets/web-dashboard/theme-first-responder-1920x1080.png)

### Amateur Radio — 1920x1080

![Amateur Radio theme at 1920x1080](assets/web-dashboard/theme-amateur-radio-1920x1080.png)

### Compact responsive example — 1366x768

![Amateur Radio theme at 1366x768](assets/web-dashboard/theme-amateur-radio-1366x768.png)

Regenerate the checked-in gallery from a repository checkout with Chrome or
Chromium and the web dependencies available:

```bash
python scripts/generate_web_dashboard_screenshots.py
```

The helper starts a loopback-only demo instance of the real application, selects
each theme through same-origin browser-local state, uses an isolated Chrome
profile per capture, enforces a bounded capture timeout, validates the written
PNG dimensions, and shuts the demo server down when capture is complete. None of
that demo behavior is part of the shipped `sdsctl web` service.

The browser performs one initial `/api/v1/status` request and opens
`/api/v1/events` with the same origin. The event response uses
`text/event-stream`. Every daemon envelope is emitted as one SSE message with:

- an `id` equal to the validated daemon sequence;
- a `data` field containing the complete daemon event JSON object; and
- a blank line terminating the message.

The first message is always the authoritative `stream.snapshot` checkpoint.
Later messages retain the existing daemon event kinds and payloads:

- `daemon.transition`;
- `scanner.connection`;
- `scanner.psi`;
- `radio.state`;
- `audio.state`;
- `recording.state`; and
- `destination.health`.

The browser directly applies complete runtime snapshots, scanner connection
changes, PSI and radio-state updates, audio snapshots, and recording snapshots.
`recording.state` updates the recording panel directly and is also committed into
the current browser snapshot so later unrelated events cannot repaint stale
recording state. Destination-health events trigger an authoritative
reconciliation because the displayed router summary is broader than one
subscriber transition.

When the event stream disconnects, the browser's `EventSource` reconnects
automatically. Two-second `/api/v1/status` polling remains active while the
event stream is unavailable. A status request also runs every 30 seconds during
healthy streaming to reconcile the incremental browser state with the
authoritative daemon snapshot.

The interface presents:

- scanner connection, model, firmware, and endpoint;
- active system, channel, mode, screen kind, signal, and RSSI when available;
- daemon lifecycle and transition sequence;
- PSI activity and interval;
- audio and destination-router state;
- daemon-owned recording state, elapsed time, packet and sample totals, audio
  duration, RTP reliability, and current file;
- a newest-first list of recent finalized recordings with Play and Download
  actions for compatible WAVs; and
- the local time of the most recent applied update.

The interface uses semantic landmarks, definition lists, a skip link, visible
keyboard focus, status text that does not rely on color alone, responsive
single-, two-, and three-column layouts, system light and dark modes, and
reduced-motion behavior. JavaScript updates text through `textContent`; it does
not render daemon-provided HTML.

The HTML, CSS, and JavaScript are package resources served with `no-store`, a
restrictive Content Security Policy, no-referrer behavior, MIME sniffing
disabled, and framing denied.

## Browser audio playback

Browser playback is explicit and never starts on page load. Press **Play audio**
from the dashboard to satisfy browser autoplay requirements and create one
same-origin `GET /api/v1/audio` stream. The route connects its independent
`DaemonPcmuClient` before returning HTTP `200`, then forwards complete
`encode_pcmu_delivery` frames as `application/octet-stream`.

The main browser thread validates arbitrary HTTP chunk boundaries, PCMU magic,
version, flags, frame and body sizes, monotonic stream sequence, cumulative
daemon queue-loss counters, and their relationship to skipped publications. It
then transfers the raw PCMU payload to the packaged AudioWorklet.

The AudioWorklet decodes G.711 mu-law at the scanner's 8 kHz rate, keeps a
bounded two-second sample buffer, waits for a 60 ms startup threshold, inserts
bounded silence for reported missing samples, and linearly resamples to the
browser output rate. Backwards RTP timestamps and large discontinuities reset
the playback buffer rather than replaying stale samples.

The panel reports the current source endpoint, browser-received packet count,
daemon subscriber queue drops and overflows, and cumulative RTP missing packets.
The source is mono; normal browser output may reproduce that mono signal through
both destination channels.

**Stop** aborts the HTTP request, cancels the reader, disconnects the
AudioWorklet, closes the `AudioContext`, and releases the daemon PCMU client.
Hiding the dashboard intentionally suspends SSE but does not stop audio. Returning
to the page reopens SSE while preserving the same audio stream. Closing or
navigating away from the page stops both event and audio streams.

## Browser recording workflows

Browser recording is explicit and daemon-owned. Press **Record** to send
`POST /api/v1/recording/start`. The daemon recording manager allocates a
collision-safe WAV path beneath its configured recording root and attaches a WAV
sink to the already-running decoded-PCM router. No browser request supplies a
filesystem path or filename, and no second scanner RTSP/RTP stream is opened.

While recording is active, the browser reconciles `GET /api/v1/recording` once
per second so elapsed time, packet and sample totals, audio duration, sink
statistics, and RTP reliability continue to advance even when no state
transition event is emitted. While inactive, the normal 30-second reconciliation
checks recording status. `recording.state` events and visibility restoration
provide faster state recovery.

Reloading the page or stopping the web process does not stop an active recording.
The daemon continues to own the sink until **Stop**, explicit daemon shutdown, or
a recording failure. Press **Stop** to send `POST /api/v1/recording/stop`;
successful finalization closes the WAV, writes the adjacent metadata sidecar, and
refreshes `GET /api/v1/recordings`.

The recent-recording list is bounded and newest-first. Only finalized inventory
entries marked playable receive actions. **Play** assigns the same-origin
recording-file route to a native `<audio>` element. **Download** uses that same
route with a browser download filename derived from the inventory identifier.
Neither action creates a browser PCMU client or changes live scanner-audio
ownership.

`GET /api/v1/recordings/file/{identifier}` never reads a caller-selected
filesystem path. The web service sends the identifier to the daemon's private
recording-file client. The daemon accepts only canonical inventory-relative POSIX
WAV identifiers, rejects traversal and non-inventory entries, excludes active or
pending recordings, securely reopens path components without following symlinks,
requires a regular file, revalidates compatible WAV parameters, and then streams
the already-open file with an exact content length.

Daemon shutdown stops recording-file readers before closing the recording
manager. The manager finalizes any active WAV and metadata while the shared audio
runtime is still alive, then the normal destination, runtime, PCMU, and event
shutdown continues.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Accessible responsive browser dashboard shell |
| `GET` | `/api/v1` | Service metadata and endpoint links |
| `GET` | `/healthz` | Web-process health without contacting the daemon |
| `GET` | `/api/v1/status` | Negotiated daemon capabilities and runtime snapshot |
| `GET` | `/api/v1/snapshot` | Authoritative daemon runtime snapshot |
| `GET` | `/api/v1/events` | Snapshot-first ordered daemon Server-Sent Events |
| `GET` | `/api/v1/audio` | Validated daemon-owned PCMU v1 binary frame stream |
| `POST` | `/api/v1/scanner/hold/{scope}` | Set desired system, department, site, or channel hold state |
| `POST` | `/api/v1/scanner/next` | Move to the next documented current channel selection |
| `POST` | `/api/v1/scanner/previous` | Move to the previous documented current channel selection |
| `POST` | `/api/v1/scanner/reconnect` | Request one bounded daemon-owned scanner reconnect |
| `GET` | `/api/v1/recording` | Current daemon-owned recording snapshot |
| `POST` | `/api/v1/recording/start` | Start one daemon-owned WAV recording |
| `POST` | `/api/v1/recording/stop` | Stop and finalize the active recording |
| `GET` | `/api/v1/recordings` | Bounded newest-first finalized recording inventory |
| `GET` | `/api/v1/recordings/file/{identifier}` | Stream one finalized playable WAV by inventory-relative identifier |
| `GET` | `/api/v1/openapi.json` | Machine-readable API schema |
| `GET` | `/api/v1/docs` | Self-hosted interactive Swagger UI |
| `GET` | `/api/v1/redoc` | Self-hosted ReDoc API reference |

Swagger UI and ReDoc are served entirely from version-pinned assets packaged
with `sds200`. Loading either documentation page does not contact the scanner
daemon and does not require a CDN, Google Fonts, or another third-party browser
request. Both documentation UIs read the same local `/api/v1/openapi.json`
schema. Swagger UI is interactive: its **Try it out** feature can invoke
same-origin API operations, including mutating operations such as recording
start and stop, so use those controls deliberately.

The documentation pages keep `script-src 'self'` and `connect-src 'self'`.
Their docs-specific Content Security Policy allows inline styles because the
vendored Swagger UI and ReDoc runtimes generate style attributes or style
elements while rendering. Inline scripts remain prohibited, and the normal
dashboard keeps its stricter `style-src 'self'` policy without this exception.

The service envelope uses protocol `sdsctl.web`, version `1`. Each
request-response daemon route creates a bounded local API client, negotiates the
daemon protocol, performs the requested operation, and closes the client.

Scanner controls are deliberately narrow browser operations rather than a raw
scanner-command passthrough. Browser hold requests never provide raw `HLD` or
`KEY` values: `POST /api/v1/scanner/hold/{scope}` accepts exactly one JSON field,
`{"held": true}` or `{"held": false}`, and forwards the semantic scope plus
desired state to daemon `scanner.hold_state`. The daemon performs an
authoritative `GSI` read before deciding whether a verified key gesture is
needed. Channel next/previous still resolve the current documented selection
from the daemon snapshot and never accept raw `NXT` or `PRV` targets from the
browser.

`GET /api/v1/status` already carries the negotiated daemon `hello` result. The
browser enables semantic hold controls only when the daemon advertises
`scanner.hold_state`; channel navigation and reconnect continue to negotiate
`scanner.next`, `scanner.previous`, and `scanner.reconnect` independently.
Controls additionally require a running runtime and connected scanner. A new
hold requires a usable current selection, while release remains available when
the authoritative hold field is `On` even if scanning has already exposed the
SDS200 unsigned-32 no-current-selection sentinel (`4294967295`). Next/previous
continue to reject that sentinel.

Hold controls reflect authoritative PSI/GSI hold fields. An unheld scope renders
`Hold system`, `Hold department`, `Hold site`, or `Hold channel`; an active scope
keeps its separate `Held` indicator and changes the button to the corresponding
`Release` action with `aria-pressed="true"`. The daemon no-ops an already
satisfied desired state, otherwise executes the complete verified SDS200 gesture
under the mutation lock and polls authoritative `GSI` until only the requested
hold field converges. This target-field convergence deliberately tolerates the
temporary unrelated field inconsistencies observed after the Site Hold gesture.
Reconnect remains available while the scanner is disconnected when the running
daemon advertises bounded reconnect support.

The browser allows only one scanner-control mutation at a time. The daemon
runtime independently retains its own nonblocking control lock, so another local
client still receives the daemon's stable busy response rather than overlapping
scanner commands. Successful HTTP control responses contain the daemon control
sequence, operation timestamps, and authoritative completion `snapshot`. The
browser renders that snapshot immediately when no ordered SSE event arrived
during the request. If an event did arrive while the control was in flight, the
browser does not compare the independent control and event sequence spaces or
use wall-clock timestamps as an ordering surrogate. Instead it performs a fresh
authoritative status read. If the event stream remains continuously active
through the bounded reconciliation attempts, its ordered projection is preserved
and the normal periodic status refresh provides the next complete snapshot
boundary. A temporarily unavailable reconciliation status read likewise preserves
the event-derived projection; because the scanner control has already completed,
that read failure is not reported as a scanner-control failure, and normal status
refresh supplies a later full snapshot boundary.

Control failures are mapped to stable redacted HTTP errors. Busy, unavailable,
unsupported, rejected, timeout, invalid-parameter, connection, and generic
control failures do not expose private daemon paths or low-level exception
messages.

Physical Milestone 20.6 validation on SDS200 firmware 1.26.01 exercised
semantic release and re-hold for System, Department, Site, and Channel through
the loopback web HTTP routes. Channel release authoritatively returned `Off`
while the current channel index was the SDS200 `4294967295` no-selection
sentinel; the route later restored Channel Hold after a new TGID became usable.
A representative real-browser Channel cycle also confirmed the visible
`Release channel` plus `Held` state, successful Release completion, transition
to enabled `Hold channel` with the indicator hidden, successful Hold completion,
and restoration of the Release/`Held` presentation. The daemon and web process
IDs remained unchanged, PSI stayed active, and daemon-owned audio continued
advancing throughout the browser validation.

The event route receives its first validated daemon event before starting the
HTTP response. An absent, refused, inaccessible, incompatible, or malformed
initial stream therefore returns HTTP `503` with the same stable redacted
message as other daemon-backed endpoints. Private socket paths and low-level
exception details are not included.

After HTTP streaming begins, a later daemon disconnect or protocol failure ends
that SSE response and closes the local event client. The browser reconnects to
obtain a new authoritative snapshot boundary. The web service does not invent
event replay, skip daemon sequence validation, or translate a gap into partial
browser state.

The audio route connects to `pcmu.sock` before starting its HTTP response. An
absent, refused, inaccessible, incompatible, or malformed initial PCMU
connection therefore returns the same redacted HTTP `503`. The route does not
pre-read a PCMU frame before returning `200`, because audio reception may be
idle. After streaming begins, daemon disconnect or protocol failure ends the
response and closes the PCMU client.

Recording status, mutation, and inventory routes use the daemon API and map the
stable `recording_busy`, `recording_unavailable`, and `recording_failed` codes to
redacted HTTP responses. The finalized-file route uses only `recordings.sock`;
invalid identifiers return `400`, missing entries `404`, unavailable or
non-playable entries `409`, and local service failures `503`. Successful WAV
responses use `audio/wav`, exact `Content-Length`, `Cache-Control: no-store`,
and `X-Content-Type-Options: nosniff`.

## Command options

```text
--daemon-socket-path PATH
--daemon-event-socket-path PATH
--daemon-pcmu-socket-path PATH
--daemon-recording-file-socket-path PATH
--daemon-timeout SECONDS
--daemon-max-response-bytes BYTES
--daemon-max-event-bytes BYTES
--daemon-pcmu-max-endpoint-bytes BYTES
--daemon-pcmu-max-frame-bytes BYTES
--daemon-recording-file-max-content-bytes BYTES
--listen-address ADDRESS
--listen-port PORT
--no-access-log
```

The daemon timeout defaults to five seconds and applies to API, event, PCMU, and
recording-file connection establishment. The response, event, PCMU endpoint,
PCMU frame, and recording-file content limits default to the existing daemon
client contracts. Browser PCMU frame size must be at least the fixed 82-byte
header and cannot exceed 131,072 bytes.

Disable the HTTP access log when a supervising service supplies request logging:

```bash
sdsctl web --no-access-log
```

## Current scope

Milestones 20.1 through 20.7 include:

- the optional `web` package extra;
- a host-independent FastAPI application factory;
- versioned health, status, snapshot, metadata, event, audio, and OpenAPI routes;
- redacted daemon-unavailable responses;
- capability-negotiated browser scanner hold, previous/next channel navigation,
  and bounded reconnect controls with authoritative selection resolution,
  mutation serialization, completion reconciliation, and stable redacted errors;
- a loopback-only Uvicorn adapter with bounded graceful shutdown;
- the `sdsctl web` command;
- a packaged accessible responsive browser shell;
- snapshot-first same-origin Server-Sent Events;
- validated daemon sequence identifiers and complete JSON event envelopes;
- direct incremental scanner, radio, PSI, audio, and daemon updates;
- automatic browser reconnect, two-second polling fallback, and periodic
  authoritative reconciliation;
- scanner, radio-activity, daemon, PSI, audio, and router summaries;
- explicit Play and Stop browser audio over daemon-owned PCMU with AudioWorklet
  mu-law decoding, bounded buffering, resampling, and loss telemetry;
- daemon-owned Record and Stop workflows over the existing decoded-PCM router,
  with live recording and RTP reliability telemetry;
- ordered `recording.state` browser updates plus active-recording polling and
  reload/reconnect reconciliation;
- bounded newest-first finalized recording inventory with safe same-origin Play
  and Download actions through the private recording-file service;
- deterministic browser PCMU and SSE cleanup, including hidden-tab event
  suspension without stopping active audio or daemon-owned recording;
- active recording survival across browser or web-process disconnects and
  daemon-shutdown finalization before audio runtime teardown;
- idle disconnected daemon event-client reaping;
- restrictive static-, event-, audio-, and recording-file response headers;
- browser-local system-adaptive, LCARS-inspired, Matrix-inspired, First
  Responder, and Amateur Radio themes over one shared accessible dashboard
  structure, with immersive full-screen custom-theme staging, compact responsive
  reflow, reduced-motion handling, CSP-safe pre-paint restoration, and no daemon
  or scanner state coupling;
- deterministic documentation screenshots generated from the real packaged
  dashboard with fictional demo state and bounded native-Chrome capture; and
- parser, application, event, audio, recording lifecycle, shell, server,
  packaging, and regression tests.

Later work remains responsible for browser logs, additional shared branding
assets, and authentication and secure remote-access planning for standalone
network exposure. Home Assistant App operation is documented separately in
[Home Assistant App](home-assistant-app.md); it uses Supervisor Ingress rather
than exposing the standalone web listener to the LAN.
