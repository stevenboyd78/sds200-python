# Local daemon PCMU stream

Milestone 19.7 adds a versioned, renderer-neutral stream of accepted
SDS200 RTP PCMU packets to the foreground `sdsctl daemon` process. It
uses a third private Unix-domain socket and does not change the existing
`sdsctl.daemon` request-response or `sdsctl.daemon.events` protocols.

Packets are published from the authoritative `NetworkAudioTransport`
after RTP acceptance and before mu-law decoding. Local subscribers
therefore receive the original payload bytes and RTP continuity metadata
without opening another scanner RTSP/RTP session or re-encoding decoded
PCM.

## Starting the stream

The PCMU service starts automatically with the foreground daemon:

```bash
sdsctl --log-level INFO --host 192.168.0.251 daemon
```

PCMU-socket resolution uses this precedence:

1. an explicit absolute `--pcmu-socket-path`;
2. `$XDG_RUNTIME_DIR/sdsctl/pcmu.sock`; or
3. the resolved user state directory followed by `pcmu.sock`, normally
   `$XDG_STATE_HOME/sdsctl/pcmu.sock` or
   `~/.local/state/sdsctl/pcmu.sock`.

An explicit path overrides the environment:

```bash
sdsctl --host 192.168.0.251 daemon \
  --pcmu-socket-path /run/user/1000/sdsctl-custom/pcmu.sock
```

The parent directory for an explicit path must already exist and remains
caller-managed. Default runtime and user-state locations use the shared
private socket rules: the managed directory uses mode `0700`, the socket
uses mode `0600`, active endpoints are never replaced, and stale removal
requires a refused connection plus matching filesystem identity.

## Transport and admission

The transport is an `AF_UNIX`, `SOCK_STREAM` socket. A client sends no
request, handshake, or subscription envelope. Each admitted connection
owns one independent bounded packet subscription and receives only
packets accepted after that subscription is created.

Default limits are:

| Limit | Default |
| --- | ---: |
| Concurrent PCMU clients | 8 |
| Queued packets per subscriber | 64 |
| Accepted PCMU payload | 65,535 bytes |
| Encoded endpoint | 4,096 bytes |
| Complete encoded frame | 131,072 bytes |
| Client send timeout | 5 seconds |
| Worker shutdown deadline | 2 seconds |

The corresponding daemon options are:

```text
--pcmu-socket-path PATH
--pcmu-queue-capacity COUNT
--pcmu-max-clients COUNT
--pcmu-max-payload-bytes BYTES
--pcmu-max-endpoint-bytes BYTES
--pcmu-max-frame-bytes BYTES
--pcmu-send-timeout SECONDS
--pcmu-shutdown-timeout SECONDS
```

An excess connection is closed without obtaining a subscription. A slow
or disconnected client affects only its own queue and worker. Packet
publication never waits for socket writes.

## Binary frame

Every delivery is one complete binary frame. The fixed header is 82 bytes
and uses network byte order with this Python `struct` format:

```text
!4sBBHIQqHIIHIIIQQQHI
```

The frame begins with these fields in order:

| Field | Type | Meaning |
| --- | --- | --- |
| Magic | `4s` | Exact bytes `SDSP` |
| Version | `B` | Protocol version `1` |
| Flags | `B` | Marker and optional-field flags |
| Header size | `H` | Fixed value `82` |
| Frame size | `I` | Header, endpoint, and payload bytes |
| Stream sequence | `Q` | Global accepted-packet publication sequence |
| Observed time | `q` | Signed UTC microseconds since Unix epoch |
| RTP sequence | `H` | Accepted RTP packet sequence |
| RTP timestamp | `I` | Accepted RTP packet timestamp |
| RTP SSRC | `I` | Accepted synchronization source |
| Expected RTP sequence | `H` | Value is present when flag bit 1 is set |
| Missing RTP packets | `I` | Network discontinuity estimate |
| Expected RTP timestamp | `I` | Value is present when flag bit 2 is set |
| Missing RTP samples | `I` | Timestamp discontinuity estimate |
| Queue packets dropped | `Q` | Cumulative drops for this subscriber |
| Queue payload bytes dropped | `Q` | Cumulative dropped payload bytes |
| Queue overflows | `Q` | Cumulative overflow operations |
| Endpoint size | `H` | Following UTF-8 endpoint bytes |
| Payload size | `I` | Following raw PCMU payload bytes |

The variable body contains the UTF-8 endpoint followed immediately by the
raw PCMU payload. The endpoint and payload sizes must exactly account for
the encoded frame size.

Flag bits are:

| Bit | Meaning |
| ---: | --- |
| 0 | RTP marker is set |
| 1 | Expected RTP sequence is present, including value zero |
| 2 | Expected RTP timestamp is present, including value zero |
| 3 | RTP timestamp moved backwards |

Unknown flags, magic, versions, inconsistent lengths, invalid UTF-8, and
values outside the configured bounds are protocol errors.

## Ordering, loss, and discontinuity

`stream_sequence` advances once for every accepted PCMU packet published
by the authoritative transport. Duplicate, late, malformed, unexpected-
source, and rejected RTP packets are not published.

RTP continuity fields describe scanner-network reception. In contrast,
`packets_dropped`, `payload_bytes_dropped`, and `overflows` describe
local loss in one subscriber's bounded queue. Those queue counters are
cumulative and are repeated on each later delivery. They do not change
another subscriber's frames.

A client should track both the global stream sequence and the cumulative
queue counters. The first delivered stream sequence may be greater than one
because publication began before the client subscribed. Within one
uninterrupted connection, a later stream-sequence gap means that the
subscriber's bounded queue dropped one or more publications; the cumulative
queue counters quantify that local loss.

There is no replay buffer or authoritative audio snapshot. Reconnecting
creates a new empty subscription at the current publication boundary, so
comparing sequences across separate connections also includes packets
published while the client was disconnected.

## Lifecycle and isolation

`DaemonProcess` starts services in this order:

1. event listener and accept worker;
2. PCMU listener and accept worker;
3. ownership runtime; and
4. request-response API.

The event and PCMU sockets therefore exist before the authoritative runtime
opens scanner control, PSI, and RTSP/RTP audio. The API opens only after
runtime startup succeeds.

Shutdown occurs in this order:

1. request-response API;
2. ownership runtime;
3. PCMU service; and
4. event service.

Stopping the PCMU service closes the listener, closes the publisher and
source subscription, wakes blocked subscribers, closes clients, and waits
only until the configured worker deadline. It does not independently stop
the shared `NetworkAudioTransport`; runtime ownership remains authoritative.

Listener, client, subscription, encoding, unsubscribe, and cleanup failures
are isolated where possible. Operational snapshots expose counts and
redacted error types without transmitting exception messages.

## CLI audio client

Milestone 19.9 adds the reusable `DaemonPcmuClient` and an explicit CLI consumer
for daemon-owned audio. It connects directly to `pcmu.sock`; it does not open the
request-response API socket, scanner control, PSI, or another scanner RTSP/RTP
session.

Play through the local default output device:

```bash
sdsctl daemon-client audio --play
```

Record daemon-owned audio as an 8 kHz mono signed 16-bit PCM WAV file:

```bash
sdsctl daemon-client audio \
  --output scanner-audio.wav \
  --duration 30
```

Playback and recording may share the same PCMU connection:

```bash
sdsctl daemon-client --timeout 2 audio \
  --pcmu-socket-path /run/user/1000/sdsctl/pcmu.sock \
  --play \
  --device 2 \
  --output scanner-audio.wav
```

The parent `--timeout` option bounds connection establishment and must precede
the `audio` action. `--pcmu-socket-path`, `--max-endpoint-bytes`, and
`--max-frame-bytes` belong to the audio action. Omit `--duration` to run until
`Ctrl+C`. Existing files are protected unless `--force` is explicit.

Every complete frame is decoded through the public PCMU codec before its payload
is converted once to PCM and submitted to the existing bounded playback and WAV
sinks. The client rejects incompatible framing, non-monotonic stream sequences,
and regressing cumulative queue-loss counters. Stream-sequence gaps are counted
rather than rejected because they represent the per-client bounded queue loss
described by the frame counters.

The completion summary reports received packets and samples, first and last
stream sequences, skipped publications, cumulative queue drops and overflows,
RTP missing-packet and missing-sample observations, backwards RTP timestamps,
playback statistics when selected, and the WAV path when recorded.

## Minimal Python client

This example resolves the default socket, reads complete frames, and uses
the public decoder:

```python
import os
import socket
import struct
from pathlib import Path

from sds200 import decode_pcmu_delivery

HEADER = struct.Struct("!4sBBHIQqHIIHIIIQQQHI")


def receive_exact(client: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = client.recv(size - len(data))
        if not chunk:
            raise EOFError("daemon closed the PCMU stream")
        data.extend(chunk)
    return bytes(data)


runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
if runtime_dir:
    path = Path(runtime_dir) / "sdsctl" / "pcmu.sock"
else:
    state_home = Path(
        os.environ.get(
            "XDG_STATE_HOME",
            str(Path.home() / ".local" / "state"),
        )
    )
    path = state_home / "sdsctl" / "pcmu.sock"

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect(str(path))
    while True:
        header = receive_exact(client, HEADER.size)
        frame_size = HEADER.unpack(header)[4]
        body = receive_exact(client, frame_size - HEADER.size)
        delivery = decode_pcmu_delivery(header + body)
        print(
            delivery.stream_sequence,
            delivery.packet.sequence,
            len(delivery.packet.payload),
            delivery.packets_dropped,
        )
```

Use the explicit path instead when the daemon was started with
`--pcmu-socket-path`.

## Current exclusions

The PCMU service still does not add:

- daemon-side decoded-PCM subscriptions or decoded-PCM CLI workflows;
- client negotiation, filtering, replay, or seek operations;
- audio delivery through `daemon.sock` or `events.sock`;
- scanner-control operations;
- TCP transport or remote authentication;
- daemon discovery or automatic client selection;
- TUI daemon-audio client workflows; or
- destination activation and configuration reload.

## Physical SDS200 validation

Validated on 2026-08-05 with `sdsctl daemon-client audio` and a physical
SDS200:

- an explicit private `pcmu.sock` supplied simultaneous default-device
  playback and WAV recording without opening another scanner RTSP/RTP
  session;
- the client received 258 consecutive frames from stream sequence 16 through
  273 and 82,560 samples without a stream gap, daemon PCMU queue drop,
  daemon PCMU overflow, RTP missing packet, RTP missing sample, or backwards
  timestamp;
- the local PortAudio sink wrote 159,942 PCM bytes, reported zero underflows
  and zero callback statuses, and recorded six bounded-queue overflows that
  dropped 2,088 PCM bytes;
- the WAV sink finalized one-channel signed 16-bit PCM at 8 kHz with 82,560
  frames and a duration of 10.320 seconds;
- a subsequent daemon API status request still reported a running runtime,
  connected scanner, active PSI and audio, and a running router; and
- controlled `SIGTERM` returned exit status 0 and removed `daemon.sock`,
  `events.sock`, and `pcmu.sock`.

Validated on 2026-08-05 against a physical SDS200 at `192.168.0.251`
with `scripts/validate_daemon_pcmu.py`:

- the caller-managed validation directory used mode `0700`, and
  `daemon.sock`, `events.sock`, and `pcmu.sock` each used mode `0600`;
- one API client completed 61 correlated `ping` operations while the
  runtime remained `running` with connected scanner control, active PSI,
  active RTSP/RTP audio, and a running decoded-PCM router;
- one event client received 231 continuous ordered events from sequence 1
  through 231 without a gap or reader failure;
- observed event traffic included authoritative startup state, live PSI
  and radio-state updates, and final audio, scanner-connection, and daemon
  lifecycle transitions;
- two independent PCMU clients each received 1,503 ordered frames from
  stream sequence 1 through 1,503 and 480,960 raw payload bytes;
- all 1,503 overlapping frames had identical decoded metadata and payload
  fingerprints for both clients;
- neither client reported a stream-sequence gap, packet drop, payload-byte
  drop, queue overflow, missing RTP packet, missing RTP sample, or
  backwards timestamp;
- a third PCMU connection above the configured two-client limit was
  closed without obtaining a subscription;
- decoded audio advanced by 1,500 packets and 480,000 samples during the
  60-second simultaneous-client interval;
- controlled `SIGTERM` returned exit status 0; and
- all three owned sockets were removed before process exit.

The same validator now accepts `--exercise-controls` for an opt-in Milestone
19.8 hardware sequence. On 2026-08-05 it completed TGID hold, next, previous,
hold release, and bounded reconnect while one API client, one event client, and
two PCMU clients remained connected. The run completed 16 API pings, 82 ordered
events without a gap, and 410 identical loss-free frames per PCMU client before
clean `SIGTERM` shutdown and removal of all three sockets. The control path
requires an initially unheld controllable channel and binds reversible navigation
to the actual PSI-reported held selection.
