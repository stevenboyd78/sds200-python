# Web dashboard

Milestone 20.1 established the optional daemon-backed HTTP service and
loopback-only command. Milestone 20.2 adds the first accessible responsive,
read-only browser shell over that service.

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

The web process resolves the same private daemon API socket used by
`sdsctl daemon-client`. The default location is
`$XDG_RUNTIME_DIR/sdsctl/daemon.sock`, with the existing user-state fallback
when `XDG_RUNTIME_DIR` is unavailable.

Select an explicit daemon socket when needed:

```bash
sdsctl web \
  --daemon-socket-path /run/user/1000/sdsctl/daemon.sock
```

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

## Browser dashboard

Open the local dashboard after starting the web service:

```text
http://127.0.0.1:8000/
```

The shell polls `/api/v1/status` every two seconds while its browser tab is
visible. It presents:

- scanner connection, model, firmware, and endpoint;
- active system, channel, mode, screen kind, signal, and RSSI when available;
- daemon lifecycle and transition sequence;
- PSI activity and interval;
- audio and destination-router state; and
- the local time of the most recent successful update.

The interface uses semantic landmarks, definition lists, a skip link, visible
keyboard focus, status text that does not rely on color alone, responsive
single-, two-, and three-column layouts, system light and dark modes, and
reduced-motion behavior. JavaScript updates text through `textContent`; it does
not render daemon-provided HTML.

The HTML, CSS, and JavaScript are package resources served with `no-store`,
a restrictive Content Security Policy, no-referrer behavior, MIME sniffing
disabled, and framing denied.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Accessible responsive browser dashboard shell |
| `GET` | `/api/v1` | Service metadata and endpoint links |
| `GET` | `/healthz` | Web-process health without contacting the daemon |
| `GET` | `/api/v1/status` | Negotiated daemon capabilities and runtime snapshot |
| `GET` | `/api/v1/snapshot` | Authoritative daemon runtime snapshot |
| `GET` | `/api/v1/openapi.json` | Machine-readable API schema |

Interactive Swagger and ReDoc routes are disabled.

The service envelope uses protocol `sdsctl.web`, version `1`. Each daemon-backed
request creates a bounded local daemon client, negotiates the daemon protocol,
performs the requested read, and closes the client.

Daemon transport and protocol failures return HTTP `503` with a stable,
redacted message. Private socket paths and low-level exception details are not
included in the HTTP response.

## Command options

```text
--daemon-socket-path PATH
--daemon-timeout SECONDS
--daemon-max-response-bytes BYTES
--listen-address ADDRESS
--listen-port PORT
--no-access-log
```

The daemon timeout defaults to five seconds. The response-size default matches
the existing daemon API client limit.

Disable the HTTP access log when a supervising service supplies request logging:

```bash
sdsctl web --no-access-log
```

## Current scope

Milestones 20.1 and 20.2 include:

- the optional `web` package extra;
- a host-independent FastAPI application factory;
- versioned health, status, snapshot, metadata, and OpenAPI routes;
- redacted daemon-unavailable responses;
- a loopback-only Uvicorn adapter;
- the `sdsctl web` command;
- a packaged accessible responsive read-only browser shell;
- two-second status polling while the browser tab is visible;
- scanner, radio-activity, daemon, PSI, audio, and router summaries;
- restrictive static-response security headers; and
- parser, application, shell, server, packaging, and regression tests.

Later Milestone 20 work remains responsible for event-driven live updates,
audio and recording workflows, logs, safe controls, optional LCARS-inspired and
Matrix-inspired themes, expanded SVG assets and branding, authentication and
secure remote-access planning, and Home Assistant integration.
