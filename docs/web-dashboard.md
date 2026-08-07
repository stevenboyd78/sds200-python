# Web dashboard

Milestone 20.1 established the optional daemon-backed HTTP service and
loopback-only command. Milestone 20.2 added the first accessible responsive,
read-only browser shell. Milestone 20.3 added live ordered browser updates over
Server-Sent Events. Milestone 20.4 added explicit browser playback of daemon-owned
PCMU audio. Milestone 20.5 adds daemon-owned recording workflows, finalized
recording inventory, and safe saved-WAV playback and download.

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
termination, trusted-proxy handling, or supported remote-exposure mode. Do not
expose this service through a public listener or reverse proxy. Authentication
and transport-security design must be completed before remote access becomes a
supported workflow.

Uvicorn proxy-header trust and its identifying server header are disabled.
Graceful shutdown is bounded to two seconds so an intentionally long-lived SSE
or audio response cannot make one `Ctrl+C` wait indefinitely. Requests still
active at that deadline are cancelled by Uvicorn before application shutdown.

## Browser dashboard

Open the local dashboard after starting the web service:

```text
http://127.0.0.1:8000/
```

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
| `GET` | `/api/v1/recording` | Current daemon-owned recording snapshot |
| `POST` | `/api/v1/recording/start` | Start one daemon-owned WAV recording |
| `POST` | `/api/v1/recording/stop` | Stop and finalize the active recording |
| `GET` | `/api/v1/recordings` | Bounded newest-first finalized recording inventory |
| `GET` | `/api/v1/recordings/file/{identifier}` | Stream one finalized playable WAV by inventory-relative identifier |
| `GET` | `/api/v1/openapi.json` | Machine-readable API schema |

Interactive Swagger and ReDoc routes are disabled.

The service envelope uses protocol `sdsctl.web`, version `1`. Each
request-response daemon route creates a bounded local API client, negotiates the
daemon protocol, performs the requested read, and closes the client.

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

Milestones 20.1 through 20.5 include:

- the optional `web` package extra;
- a host-independent FastAPI application factory;
- versioned health, status, snapshot, metadata, event, audio, and OpenAPI routes;
- redacted daemon-unavailable responses;
- a loopback-only Uvicorn adapter with bounded graceful shutdown;
- the `sdsctl web` command;
- a packaged accessible responsive read-only browser shell;
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
- restrictive static-, event-, audio-, and recording-file response headers; and
- parser, application, event, audio, recording lifecycle, shell, server,
  packaging, and regression tests.

Later Milestone 20 work remains responsible for browser logs, safe scanner
controls, optional LCARS-inspired and Matrix-inspired themes, expanded SVG
assets and branding, authentication and secure remote-access planning, and Home
Assistant integration.
