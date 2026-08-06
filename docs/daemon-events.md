# Local daemon event stream

Milestone 19.6 adds a versioned, renderer-neutral stream of ordered daemon state
events to the foreground `sdsctl daemon` process. It is served through a separate
private Unix-domain socket and does not change the Milestone 19.5
request-response API.

The stream publishes state and lifecycle information only. It does not publish
packet-rate PCM or PCMU audio, scanner controls, remote TCP traffic, or
renderer-specific output. Accepted PCMU packets use the separate
[local daemon PCMU stream](daemon-pcmu.md).

## Starting the stream

The event service starts automatically with the foreground daemon:

```bash
sdsctl --log-level INFO --host 192.168.0.251 daemon
```

Event-socket resolution uses this precedence:

1. an explicit absolute `--event-socket-path`;
2. `$XDG_RUNTIME_DIR/sdsctl/events.sock`; or
3. the resolved user state directory followed by `events.sock`, normally
   `$XDG_STATE_HOME/sdsctl/events.sock` or
   `~/.local/state/sdsctl/events.sock`.

An explicit path overrides the environment:

```bash
sdsctl --host 192.168.0.251 daemon \
  --event-socket-path /run/user/1000/sdsctl-custom/events.sock
```

The parent directory for an explicit path must already exist and remains
caller-managed. Default runtime and user-state locations use the same private
directory and socket rules as the local API: the managed directory uses mode
`0700`, the socket uses mode `0600`, active endpoints are never replaced, and
stale removal requires a refused connection plus matching filesystem identity.

## Transport and framing

The transport is an `AF_UNIX`, `SOCK_STREAM` socket. The server writes UTF-8 JSON
Lines and does not require a client request or subscription message. Each JSON
object ends with one newline.

A connection owns exactly one event subscription. The first line is always an
authoritative `stream.snapshot` event. Later lines contain only events published
after that snapshot's sequence boundary.

Default limits are:

| Limit | Default |
| --- | ---: |
| Concurrent event clients | 8 |
| Queued events per subscriber | 64 |
| Encoded event line | 1,048,576 bytes |
| Client send timeout | 5 seconds |
| Worker shutdown deadline | 2 seconds |

The corresponding daemon options are:

```text
--event-socket-path PATH
--event-queue-capacity COUNT
--event-max-clients COUNT
--event-max-bytes BYTES
--event-send-timeout SECONDS
--event-shutdown-timeout SECONDS
```

An excess connection is closed without a subscription worker. A disconnected or
slow client affects only its own worker. The publisher validates encoded size
before advancing the global sequence or enqueueing an event, and the socket
server performs a second defensive size check before sending.

## Event envelope

Every line contains these fields:

| Field | Meaning |
| --- | --- |
| `protocol` | Exact string `sdsctl.daemon.events` |
| `version` | Protocol version `1` |
| `sequence` | Non-negative global stream checkpoint or event sequence |
| `observed_at` | Timezone-aware ISO 8601 observation timestamp |
| `kind` | Stable event-kind string |
| `payload` | Immutable JSON-compatible event data |

Example:

```json
{"kind":"scanner.connection","observed_at":"2026-08-05T06:30:00+00:00","payload":{"connected":true,"endpoint":"udp://192.0.2.25:50536"},"protocol":"sdsctl.daemon.events","sequence":42,"version":1}
```

Object keys are serialized deterministically. Payload field names are strings,
numbers must be finite, and payload values are limited to JSON-compatible
mappings, lists, strings, booleans, integers, finite floats, and null.

## Snapshot boundary and ordering

The publisher owns one global sequence counter.

When a client subscribes:

1. the publisher captures the current global sequence;
2. it obtains one authoritative `DaemonRuntimeSnapshot.as_dict()` payload;
3. it emits that payload as `stream.snapshot` using the captured sequence; and
4. the subscription then receives only events with later sequence values.

The snapshot does not increment the sequence. The first later event uses the
next global value. A client connecting after sequence 25 therefore receives a
snapshot at sequence 25, followed by event 26 or later.

Current snapshots include optional `scanner_model` and `scanner_firmware`
identity values. Either may be `null` when its daemon startup probe failed.
Version 1 event clients remain compatible with older snapshot checkpoints
that omit these additive fields.

All source callbacks are serialized through the composed event stream before
publication, so every healthy subscriber observes the same global ordering.

## Event kinds

| Kind | Payload |
| --- | --- |
| `stream.snapshot` | Complete authoritative daemon runtime snapshot |
| `daemon.transition` | `DaemonRuntimeTransition.as_dict()` |
| `scanner.connection` | Scanner endpoint and connected state |
| `scanner.psi` | PSI command, receive timestamp, and current radio-state snapshot |
| `radio.state` | Changed fields plus previous and current radio-state snapshots |
| `audio.state` | Immutable audio-fanout lifecycle snapshot |
| `destination.health` | Decoded-PCM subscriber transition and health data |

PSI events represent parsed scanner-information updates. Radio-state events are
published only for actual state changes. Audio packets and decoded samples are
not events.

## Overflow and resynchronization

Every subscription has an independent bounded queue. A slow subscriber cannot
delay publication or another subscriber.

If a queue fills before its initial snapshot is read, the snapshot is preserved
and the oldest later event is discarded. After the snapshot has been consumed,
the oldest queued event is discarded. The subscription's internal dropped-event
counter increases, but it is not transmitted as a separate protocol event.

Loss is visible through a sequence gap. For example, observing sequence 120
after sequence 116 means at least sequences 117 through 119 were not delivered
to that subscriber.

There is no replay buffer or in-band resynchronization operation. Disconnect and
reconnect to receive a new authoritative snapshot at the current global
sequence boundary.

## Lifecycle and isolation

`DaemonProcess` starts services in this order:

1. event listener and accept worker;
2. PCMU listener and accept worker;
3. ownership runtime; and
4. request-response API.

This allows an already connected event client to observe runtime startup
transitions, prepares PCMU subscriptions before authoritative audio starts, and
keeps API requests unavailable until runtime startup succeeds.

Shutdown occurs in this order:

1. request-response API;
2. ownership runtime;
3. PCMU service; and
4. event service.

The event service therefore remains available while the runtime emits final
shutdown transitions. Stopping the event service closes the listener, closes the
composed publisher, wakes blocked subscribers, closes clients, and waits only
until the configured worker deadline.

Listener, subscriber, client, source-unsubscribe, and cleanup failures are
isolated where possible. Public failure payloads retain redacted error types
rather than arbitrary exception messages.

## Minimal Python client

This example resolves the default runtime or user-state path and prints decoded
events until interrupted:

```python
import json
import os
import socket
from pathlib import Path

runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
if runtime_dir:
    path = Path(runtime_dir) / "sdsctl" / "events.sock"
else:
    state_home = Path(
        os.environ.get(
            "XDG_STATE_HOME",
            str(Path.home() / ".local" / "state"),
        )
    )
    path = state_home / "sdsctl" / "events.sock"

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect(str(path))
    with client.makefile("r", encoding="utf-8") as lines:
        for line in lines:
            print(json.loads(line))
```

Use the explicit path instead when the daemon was started with
`--event-socket-path`.

## CLI event client

Milestone 19.9 adds a reusable `DaemonEventClient` and an explicit CLI event
watch that never opens scanner hardware or the request-response API socket:

```bash
sdsctl daemon-client events --count 10 --json
sdsctl daemon-client events \
  --kind scanner.connection \
  --kind radio.state \
  --count 20
```

The client validates every received envelope, requires the initial authoritative
`stream.snapshot`, and enforces strictly increasing gap-free sequence delivery.
A malformed frame, incompatible protocol or version, repeated snapshot,
regression, or sequence gap closes the client connection and reports a protocol
error. Reconnect to obtain a new authoritative checkpoint after a true gap.

`--kind` is a local output filter; the server still sends the complete stream and
the client still validates every event. Therefore filtered output may skip
sequence values without indicating loss. `--count` counts matching printed
events, including the initial snapshot only when it matches the filter.

Use `--event-socket-path PATH` for an explicit event socket and
`--max-event-bytes BYTES` to bound one accepted JSON Lines event. The parent
`--timeout` bounds connection establishment; an established event watch waits
for later events until its count is reached or the user interrupts it.

## Current exclusions

The event service and Milestone 19.9 client still do not add:

- event replay or server-side filtering;
- binary PCM delivery or PCMU delivery on the event socket;
- scanner-control operations on the event socket;
- TCP or remote authentication;
- daemon discovery or automatic client selection;
- decoded-PCM CLI client workflows; or
- destination activation and configuration reload.

Milestone 19.10 uses this ordered event stream for daemon-backed TUI
radio-state and connection updates. Closing the TUI closes its event client
without stopping the daemon event service or scanner ownership.

## Physical SDS200 validation

Validated on 2026-08-05 against a physical SDS200 at `192.168.0.251`:

- the caller-managed socket directory used mode `0700`, and both local sockets
  used mode `0600`;
- two independent clients received authoritative `stream.snapshot` events at
  the same sequence 11 boundary;
- a third connection above the configured two-client limit was closed without
  receiving an event;
- the request-response API completed a correlated `ping` while event clients
  remained connected;
- one client received 76 valid events from sequence 11 through 86 without a
  sequence gap, regression, malformed line, or reader failure;
- observed event kinds included `stream.snapshot`, `scanner.psi`,
  `radio.state`, `audio.state`, `scanner.connection`, and
  `daemon.transition`;
- controlled `SIGTERM` delivered shutdown audio, scanner-connection, and daemon
  lifecycle events before the event socket closed;
- the runtime received 507 RTP packets and 162,240 decoded samples;
- the daemon returned exit status 0; and
- both `daemon.sock` and `events.sock` were removed before process exit.

Daemon-owned playback, recording, and remote-profile destinations now
participate in the shared router. Active destination lifecycle and health
changes are published through the existing `destination.health` event contract.
An empty destination set legitimately produces no destination-health events.
