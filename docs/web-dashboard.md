# Web dashboard

Milestone 20.1 established the optional daemon-backed HTTP service and
loopback-only command. Milestone 20.2 added the first accessible responsive,
read-only browser shell. Milestone 20.3 added live ordered browser updates over
Server-Sent Events. Milestone 20.4 adds explicit browser playback of daemon-owned
PCMU audio.

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

The web process resolves all three private daemon sockets used by the dashboard:

- `daemon.sock` supplies bounded request-response status and snapshot reads;
- `events.sock` supplies the authoritative snapshot-first ordered event stream;
- `pcmu.sock` supplies accepted RTP PCMU packets for explicit browser playback.

Their default locations are under `$XDG_RUNTIME_DIR/sdsctl`, with the existing
user-state fallback when `XDG_RUNTIME_DIR` is unavailable.

Select explicit sockets when needed:

```bash
sdsctl web \
  --daemon-socket-path /run/user/1000/sdsctl/daemon.sock \
  --daemon-event-socket-path /run/user/1000/sdsctl/events.sock \
  --daemon-pcmu-socket-path /run/user/1000/sdsctl/pcmu.sock
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
- `audio.state`; and
- `destination.health`.

The browser directly applies complete runtime snapshots, scanner connection
changes, PSI and radio-state updates, and audio snapshots. Destination-health
events trigger an authoritative reconciliation because the displayed router
summary is broader than one subscriber transition.

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
- audio and destination-router state; and
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

## Command options

```text
--daemon-socket-path PATH
--daemon-event-socket-path PATH
--daemon-pcmu-socket-path PATH
--daemon-timeout SECONDS
--daemon-max-response-bytes BYTES
--daemon-max-event-bytes BYTES
--daemon-pcmu-max-endpoint-bytes BYTES
--daemon-pcmu-max-frame-bytes BYTES
--listen-address ADDRESS
--listen-port PORT
--no-access-log
```

The daemon timeout defaults to five seconds and applies to API, event, and PCMU
connection establishment. The response, event, PCMU endpoint, and PCMU frame
limits default to the existing daemon client contracts. Browser PCMU frame size
must be at least the fixed 82-byte header and cannot exceed 131,072 bytes.

Disable the HTTP access log when a supervising service supplies request logging:

```bash
sdsctl web --no-access-log
```

## Current scope

Milestones 20.1 through 20.4 include:

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
- deterministic browser PCMU and SSE cleanup, including hidden-tab event
  suspension without stopping active audio;
- idle disconnected daemon event-client reaping;
- restrictive static-, event-, and audio-response headers; and
- parser, application, event, audio lifecycle, shell, server, packaging, and
  regression tests.

Later Milestone 20 work remains responsible for browser recording workflows,
logs, safe controls, optional LCARS-inspired and Matrix-inspired
themes, expanded SVG assets and branding, authentication and secure
remote-access planning, and Home Assistant integration.
