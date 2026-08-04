# Local daemon API

Milestone 19.5 adds a versioned, renderer-neutral, read-only API to the
foreground `sdsctl daemon` process. The API is served only through a local
Unix-domain stream socket. It does not expose TCP, remote authentication,
scanner controls, event subscriptions, or audio subscriptions.

CLI and TUI daemon-client workflows remain follow-on work. The protocol can be
used directly by local integrations that implement the documented framing and
version contract.

## Starting the API

The local API starts automatically with the foreground daemon:

```bash
sdsctl --log-level INFO --host 192.168.0.251 daemon
```

Socket-path resolution uses this precedence:

1. an explicit absolute `--socket-path`;
2. `$XDG_RUNTIME_DIR/sdsctl/daemon.sock`; or
3. the resolved user state directory followed by `daemon.sock`, normally
   `$XDG_STATE_HOME/sdsctl/daemon.sock` or
   `~/.local/state/sdsctl/daemon.sock`.

An explicit path overrides the environment:

```bash
sdsctl --host 192.168.0.251 daemon \
  --socket-path /run/user/1000/sdsctl-custom/daemon.sock
```

The parent directory for an explicit path must already exist and remains
caller-managed. For default runtime or user-state locations, the daemon creates
the final `sdsctl` directory when needed and sets its mode to `0700`. The socket
entry is set to `0600`.

The daemon refuses:

- a symlink as the final parent directory;
- a symlink or non-socket entry at the socket path;
- an existing socket that accepts a connection; and
- a socket whose filesystem identity changes during stale-socket probing.

An unresponsive socket is removed only after connection refusal and a matching
device and inode check. The daemon never replaces an active endpoint.

## Transport and framing

The transport is an `AF_UNIX`, `SOCK_STREAM` socket. Requests and responses are
UTF-8 JSON Lines: each JSON value is terminated by one newline.

One connection may submit multiple requests. Responses remain ordered for that
connection. Separate admitted clients are handled independently outside scanner
control, PSI, RTP reception, and destination worker paths.

Default server limits are:

| Limit | Default |
| --- | ---: |
| Concurrent clients | 8 |
| Request size | 65,536 bytes |
| Response size | 1,048,576 bytes |
| Idle client timeout | 5 seconds |
| Worker shutdown deadline | 2 seconds |

The corresponding daemon options are:

```text
--api-max-clients COUNT
--api-max-request-bytes BYTES
--api-max-response-bytes BYTES
--api-client-timeout SECONDS
--api-shutdown-timeout SECONDS
```

An excess connection is closed without admitting a worker. An idle client is
closed after its timeout. An oversized request receives a
`request_too_large` response when possible and that connection is then closed.
Shutdown closes the listener and connected clients before waiting for bounded
worker completion.

## Request envelope

Every request is a JSON object with these required fields:

- `protocol`: the exact string `sdsctl.daemon`;
- `version`: protocol version `1`;
- `request_id`: a non-empty client correlation string; and
- `operation`: the requested operation name.

`params` is optional and defaults to an empty object. All Milestone 19.5
operations reject non-empty parameters.

A request identifier is limited to 128 characters and must not contain control
characters. Request objects are strict: missing fields and unexpected fields are
rejected.

Example request:

```json
{"operation":"hello","params":{},"protocol":"sdsctl.daemon","request_id":"example-1","version":1}
```

## Response envelope

A successful response contains `result`:

```json
{"ok":true,"protocol":"sdsctl.daemon","request_id":"example-1","result":{"pong":true},"version":1}
```

A failed response contains `error` with a stable machine-readable `code` and a
human-readable `message`:

```json
{"error":{"code":"unknown_operation","message":"Unknown daemon API operation: 'scanner.delete'."},"ok":false,"protocol":"sdsctl.daemon","request_id":"example-1","version":1}
```

A response contains exactly one of `result` or `error`. When malformed JSON,
invalid UTF-8, or an invalid request identifier prevents safe correlation,
`request_id` is `null`.

Internal runtime exceptions are redacted. Clients receive `internal_error`
without the underlying exception message.

## Read-only operations

| Operation | Result |
| --- | --- |
| `hello` | Protocol, supported versions, operations, read-only status, and selected version |
| `daemon.capabilities` | Protocol, supported versions, operations, and read-only status |
| `ping` | `{"pong": true}` |
| `runtime.snapshot` | The complete authoritative `DaemonRuntimeSnapshot.as_dict()` payload |
| `scanner.state` | Scanner endpoint, connection state, PSI settings and state, and current radio state |
| `audio.health` | Audio-session and decoded-PCM router snapshots |

`hello`, `daemon.capabilities`, and `ping` do not read the runtime snapshot.
The remaining operations obtain one authoritative snapshot for that request.

## Error codes

The version 1 protocol defines these error codes:

- `invalid_request`
- `unsupported_protocol`
- `unsupported_version`
- `unknown_operation`
- `invalid_parameters`
- `request_too_large`
- `internal_error`

Clients should branch on `error.code`, not the human-readable message.

## Minimal Python client

This example uses the default XDG runtime socket and sends one `ping` request:

```python
import json
import os
import socket
from pathlib import Path

path = Path(os.environ["XDG_RUNTIME_DIR"]) / "sdsctl" / "daemon.sock"
request = {
    "protocol": "sdsctl.daemon",
    "version": 1,
    "request_id": "ping-1",
    "operation": "ping",
    "params": {},
}

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect(str(path))
    client.sendall((json.dumps(request) + "\n").encode("utf-8"))

    response = bytearray()
    while not response.endswith(b"\n"):
        chunk = client.recv(4096)
        if not chunk:
            raise RuntimeError("Daemon closed the connection before responding.")
        response.extend(chunk)

print(json.loads(response))
```

Use the user-state fallback path instead when `XDG_RUNTIME_DIR` is not defined,
or start the daemon with an explicit absolute `--socket-path`.

## Physical SDS200 validation

Validated on 2026-08-04 against a physical SDS200 at
`192.168.0.251`:

- the managed XDG socket directory used mode `0700` and the socket used
  mode `0600`;
- `hello`, `daemon.capabilities`, `ping`, `runtime.snapshot`,
  `scanner.state`, and `audio.health` returned successful correlated
  responses;
- the authoritative runtime reported `running`, connected scanner control,
  active PSI, active RTSP/RTP audio, and a running decoded-PCM router;
- malformed JSON returned `invalid_request`, after which the same connection
  completed another valid request;
- a second concurrent client completed capability negotiation independently;
- the daemon received seven RTP packets and 2,240 decoded samples during the
  validation run; and
- systemd-style `SIGTERM` produced exit status 0 and removed the owned socket.

## Lifecycle and current exclusions

`DaemonProcess` starts the ownership runtime before opening the local API. This
ensures every admitted read-only request observes an initialized runtime.
Shutdown reverses that relationship: the API listener and clients stop before
the scanner, PSI, audio, and router runtime.

If API startup fails, the attempted API component and the runtime are both
stopped. Cleanup continues after an individual failure, while the primary
startup or process error remains authoritative.

Milestone 19.5 intentionally excludes:

- event or transition subscriptions;
- binary PCM or PCMU delivery;
- scanner-control operations;
- TCP or remote-network exposure;
- daemon discovery and automatic client selection;
- CLI and TUI daemon-client modes; and
- destination activation or configuration reload.

Those capabilities remain assigned to later Milestone 19 work.
