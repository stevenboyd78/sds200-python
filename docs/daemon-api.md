# Local daemon API

The foreground `sdsctl daemon` process exposes a versioned, renderer-neutral
request-response API through the private local `daemon.sock` Unix-domain
stream socket.

Milestone 19.5 introduced strict read-only capability and snapshot operations.
Milestone 19.8 preserves those version 1 operations and adds explicit,
capability-checked scanner controls for hold, next, previous, and reconnect.
The protocol does not expose unrestricted raw scanner-command passthrough.

Ordered events and accepted PCMU packets remain separate services:

- [local daemon event stream](daemon-events.md) through `events.sock`;
- [local daemon PCMU stream](daemon-pcmu.md) through `pcmu.sock`.

CLI and TUI daemon-client workflows remain follow-on work. Integrations may use
the documented socket framing and version contract directly.

The Python implementation retains the historical public class name
`DaemonReadOnlyApi` for compatibility even though version 1 now advertises both
read-only and control operations.

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

## Transport, framing, and limits

The transport is an `AF_UNIX`, `SOCK_STREAM` socket. Requests and responses are
UTF-8 JSON Lines: each JSON value is terminated by one newline.

One connection may submit multiple requests. Responses remain ordered for that
connection. Separate admitted clients are handled independently. Scanner
mutations are serialized by the daemon runtime rather than by a client
connection.

Default server limits are:

| Limit | Default |
| --- | ---: |
| Concurrent clients | 8 |
| Request size | 65,536 bytes |
| Response size | 1,048,576 bytes |
| Idle client timeout | 5 seconds |
| Maximum control deadline | 2 seconds |
| Worker shutdown deadline | 3 seconds |

The corresponding daemon options are:

```text
--api-max-clients COUNT
--api-max-request-bytes BYTES
--api-max-response-bytes BYTES
--api-client-timeout SECONDS
--api-shutdown-timeout SECONDS
```

The API server requires its worker shutdown deadline to be greater than the
maximum request duration. This prevents a valid bounded control request from
exceeding the configured worker-shutdown contract.

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

`params` is optional and defaults to an empty object. Allowed parameters depend
on the selected operation. Read-only operations reject non-empty parameters.
Control operations use the strict contracts documented below.

A request identifier is limited to 128 characters and must not contain control
characters. Request objects are strict: missing fields and unexpected fields
are rejected.

Example request:

```json
{"operation":"ping","params":{},"protocol":"sdsctl.daemon","request_id":"example-1","version":1}
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

Internal runtime exceptions are redacted. Clients receive stable error codes
without scanner response contents, endpoint secrets, or underlying exception
messages.

## Capability negotiation

`hello` and `daemon.capabilities` advertise:

- protocol name and supported versions;
- every supported operation;
- `read_only: false`;
- the backward-compatible `read_only_operations` set;
- the explicit `control_operations` set; and
- `max_control_timeout`, currently `2.0` seconds.

`hello` additionally returns `selected_version`.

Clients should negotiate capabilities rather than infer support from the
historical Python class name.

## Read-only operations

| Operation | Result |
| --- | --- |
| `hello` | Capabilities plus the selected protocol version |
| `daemon.capabilities` | Supported versions, operations, operation groups, and limits |
| `ping` | `{"pong": true}` |
| `runtime.snapshot` | Complete authoritative `DaemonRuntimeSnapshot.as_dict()` payload |
| `scanner.state` | Endpoint, connection and PSI state, and current radio state |
| `audio.health` | Audio-session and decoded-PCM router snapshots |

`hello`, `daemon.capabilities`, and `ping` do not read the runtime snapshot.
The remaining operations obtain one authoritative snapshot for that request.

## Scanner control operations

The API accepts only documented typed operations. It never accepts an arbitrary
scanner command string.

| Operation | Parameters |
| --- | --- |
| `scanner.hold` | Required `target`; optional `first`, `second`, and `timeout` |
| `scanner.next` | Required `target`; optional `first`, `second`, `count`, and `timeout` |
| `scanner.previous` | Required `target`; optional `first`, `second`, `count`, and `timeout` |
| `scanner.reconnect` | Optional `timeout` only |

Navigation targets are limited to:

- `SYS`
- `DEPT`
- `SITE`
- `CFREQ`
- `TGID`
- `STGID`
- `WX`
- `FTO`
- `CCHIT`
- `CS_FREQ`
- `QS_FREQ`

Targets are normalized to uppercase. `first` and `second` may be strings,
integers, or `null`, but may not contain commas or line breaks. Navigation
`count` defaults to `1` and must be an integer from `1` through `8`.

`timeout` defaults to `2.0` seconds and must be finite, greater than zero, and
no greater than the advertised maximum.

Example hold request:

```json
{"operation":"scanner.hold","params":{"first":42,"target":"SYS","timeout":1.5},"protocol":"sdsctl.daemon","request_id":"hold-1","version":1}
```

There is no `scanner.resume` operation. The documented scanner protocol used by
this project provides `HLD`, `NXT`, and `PRV`, but no verified resume or
unhold wire contract. The daemon does not invent one.

### Reconnect availability

`scanner.reconnect` is supported only when the daemon directly owns an SDS200
`UdpTransport`. Serial, fallback, replay, injected, or otherwise unbounded
transport implementations receive `unsupported_operation`.

This restriction keeps the daemon request and shutdown deadlines enforceable.
Standalone `SDSScanner.reconnect()` remains available to existing Python and
CLI/TUI workflows according to their selected transport behavior.

## Completion and concurrency semantics

A successful control response is returned only after the scanner command has
completed authoritatively:

- navigation operations require the matching scanner `OK` acknowledgement;
- reconnect must reopen the supported control transport and restore an active
  PSI interval before completion;
- the runtime increments one control sequence after success; and
- the result includes the authoritative runtime snapshot captured at
  completion.

Successful control results contain:

- `sequence`;
- `operation`;
- aware UTC `started_at`;
- aware UTC `completed_at`; and
- `snapshot`.

Only one daemon-owned scanner mutation may execute at a time. A concurrent
control is rejected immediately with `control_busy`; it is not queued behind
the in-flight request.

The caller's control deadline includes waiting for runtime lifecycle ownership
and scanner completion. Expiration produces `control_timeout`. Runtime shutdown
waits for an already executing bounded control before releasing scanner
ownership.

API workers are separate from PSI processing, RTP reception, event publication,
and PCMU delivery. The existing snapshot and ordered-event services remain the
authoritative observation paths for resulting state changes.

## Error codes

Version 1 defines these stable error codes:

- `invalid_request`
- `unsupported_protocol`
- `unsupported_version`
- `unknown_operation`
- `invalid_parameters`
- `control_busy`
- `control_unavailable`
- `unsupported_operation`
- `control_timeout`
- `control_rejected`
- `control_failed`
- `request_too_large`
- `internal_error`

Important control classifications are:

| Code | Meaning |
| --- | --- |
| `control_busy` | Another scanner mutation is already in progress |
| `control_unavailable` | Runtime or required connection state is unavailable |
| `unsupported_operation` | Scanner model, capability, or transport cannot safely perform the operation |
| `control_timeout` | The bounded control did not complete before its deadline |
| `control_rejected` | The scanner explicitly returned `NG`, `ERR`, or `ERROR` |
| `control_failed` | Another redacted scanner or transport failure occurred |

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
            raise RuntimeError(
                "Daemon closed the connection before responding."
            )
        response.extend(chunk)

print(json.loads(response))
```

Use the user-state fallback path instead when `XDG_RUNTIME_DIR` is not defined,
or start the daemon with an explicit absolute `--socket-path`.

## Physical SDS200 validation

The read-only Milestone 19.5 API was validated on 2026-08-04 against a physical
SDS200 at `192.168.0.251`:

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

The Milestone 19.8 safe controls were physically validated on 2026-08-05
against the same SDS200:

- capability negotiation advertised hold, next, previous, and reconnect with the
  two-second maximum deadline;
- TGID hold activated, next changed the held selection, previous returned to the
  held selection, and the second hold restored the scanner to `Off`;
- all five scanner-acknowledged operations returned increasing control sequences
  and authoritative snapshots reporting a running runtime, connected scanner,
  active PSI and audio, and a running router;
- bounded reconnect emitted both scanner connection transitions while the API
  connection remained usable;
- the simultaneous-client run completed 16 correlated API pings and 82 ordered
  events without a sequence gap;
- two PCMU clients each received the same 410 frames and 131,200 payload bytes
  without local loss, RTP loss, timestamp reversal, or mismatched overlap; and
- controlled `SIGTERM` returned exit status 0 and removed `daemon.sock`,
  `events.sock`, and `pcmu.sock`.

## Lifecycle and current exclusions

`DaemonProcess` starts the event listener, starts the PCMU listener, starts the
ownership runtime, and finally opens the request-response API. Every admitted
API request therefore observes an initialized runtime, while event subscribers
may observe startup transitions and PCMU subscribers are ready before
authoritative audio begins.

Shutdown closes the API listener and clients before stopping scanner, PSI,
audio, and router ownership. The PCMU service stops after the runtime, and the
separate event service remains available for final lifecycle transitions and
stops last.

If service startup fails, every attempted component is cleaned up. Cleanup
continues after an individual failure, while the primary startup or process
error remains authoritative.

The `daemon.sock` protocol intentionally excludes:

- unrestricted raw scanner-command passthrough;
- undocumented resume or unhold semantics;
- streaming event responses on an API connection;
- binary PCM or PCMU delivery on the API connection;
- TCP or remote-network exposure;
- daemon discovery and automatic client selection;
- CLI and TUI daemon-client modes; and
- destination activation or configuration reload.

Ordered events and PCMU audio are available through their dedicated sockets.
Decoded-PCM subscription, discovery, client migration, and destination
activation remain assigned to later Milestone 19 work.
