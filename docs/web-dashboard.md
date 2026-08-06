# Web dashboard foundation

Milestone 20.1 establishes the optional daemon-backed HTTP service and
loopback-only command that later dashboard milestones will build on. It does not
yet provide the finished visual browser dashboard.

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

Milestone 20.1 intentionally provides no authentication, TLS termination,
trusted-proxy handling, or supported remote-exposure mode. Do not expose this
service through a public listener or reverse proxy. Authentication and
transport-security design must be completed before remote access becomes a
supported workflow.

Uvicorn proxy-header trust and its identifying server header are disabled.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Service metadata and endpoint links |
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

Milestone 20.1 includes:

- the optional `web` package extra;
- a host-independent FastAPI application factory;
- versioned health, status, snapshot, and OpenAPI routes;
- redacted daemon-unavailable responses;
- a loopback-only Uvicorn adapter;
- the `sdsctl web` command;
- parser, application, server, packaging, and regression tests.

Later Milestone 20 work remains responsible for the responsive browser
interface, accessible design system, conventional, LCARS-inspired, and
Matrix-inspired themes, SVG assets and branding, event-driven live updates,
audio and recording workflows, logs, safe controls, authentication and secure
remote-access planning, and Home Assistant integration.
