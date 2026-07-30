# Audio subsystem architecture

Version 0.11.0 introduced hardware-validated SDS200 network audio while keeping
its lifecycle independent from scanner control. Milestone 16.1 adds a decoded-PCM
fanout layer for local playback and simultaneous recording. Audio failures never
switch, close, or delay USB serial, UDP control, fallback profiles, or preferred
recovery.

## Layers

- `NetworkAudioTransport` performs RTSP negotiation over TCP and receives RTP over
  one UDP client port. The SDS200 requires `RTP/AVP;unicast;client_port=PORT`
  rather than the conventional RTP/RTCP port pair.
- `AudioStream` owns subscriptions and lifecycle without depending on
  `SDSScanner`.
- `AudioChunk.data` contains the raw payload type 0 G.711 mu-law bytes from one
  accepted RTP packet.
- `AudioFanoutSession` decodes each accepted packet once and submits 8 kHz mono
  signed 16-bit PCM to one or more independently buffered `PcmSink` destinations.
- `SoundDevicePlaybackSink` sends PCM to the selected PortAudio output device.
- `PcmWavSink` moves WAV writes to a worker thread and finalizes the
  `PcmuWavRecorder` during shutdown.

A sink's `submit_pcm()` method must not block on device, disk, encoder, or network
I/O. Each sink owns its buffering and failure behavior so one destination cannot
hold up RTP reception or another destination. This contract is also the extension
point for future remote streaming adapters listed in [the roadmap](../ROADMAP.md).

## CLI playback and recording

Install the optional local-playback backend:

```bash
python -m pip install "sds200[playback]"
```

Listen through the operating system's default output device:

```bash
sdsctl --host 192.168.0.251 audio --play
```

Play and record from the same RTSP/RTP session:

```bash
sdsctl --host 192.168.0.251 audio \
  --play \
  --output scanner-audio.wav \
  --duration 30
```

Use `--device DEVICE` to select a PortAudio output device and `--buffer-ms` to
change the bounded playback queue. Omit `--duration` to run until `Ctrl+C`. Use
`--force` to replace an existing output file. At least one of `--play` or
`--output` is required.

Overflow drops the oldest queued playback audio to preserve live latency.
Underflow fills the device request with silence. Both conditions are counted in
the command summary. Recording uses its own bounded worker queue and does not
perform disk writes in the RTP callback.

## Python lifecycle

```python
from pathlib import Path

from sds200 import (
    AudioFanoutSession,
    AudioStream,
    NetworkAudioTransport,
    PcmWavSink,
    PcmuWavRecorder,
    SoundDevicePlaybackSink,
)

transport = NetworkAudioTransport("192.168.0.251")
stream = AudioStream(transport)
sinks = (
    SoundDevicePlaybackSink(),
    PcmWavSink(PcmuWavRecorder(Path("scanner-audio.wav"))),
)

with AudioFanoutSession(stream, sinks):
    run_application()
```

The direct one-shot `AudioRecordingSession` API remains available for callers that
want one stream and one recorder. The TUI uses `TuiAudioSession` with a dynamic PCM
sink router: one long-lived fanout owns RTSP/RTP reception while live playback,
repeatable WAV sinks, and saved-recording playback are attached or detached without
opening a second scanner audio session.

## Reliability statistics

`NetworkAudioTransport.statistics` returns an immutable session snapshot with
received datagrams and bytes, delivered packets and payload bytes, sequence gaps,
estimated packet loss, duplicates, late packets, malformed packets, unexpected
source and SSRC rejections, RTP timestamp discontinuities, missing-sample
estimates, receive and callback errors, keepalives, teardown count, sequence
endpoints, final timestamp, and SSRC.

Each PCM sink also exposes a `PcmSinkStatistics` snapshot containing submitted,
written, dropped, and queued bytes plus underflow, overflow, and callback-status
counters where applicable.

Sequence tracking begins with the first packet actually received because the
SDS200's `RTP-Info` starting sequence is not a reliable initialization value.
Synthetic fixtures exercise loss, duplicate, late, malformed, wraparound, and
backward-timestamp behavior without requiring scanner hardware.

The RTP socket binds to the local IPv4 interface selected by the route to the
scanner. Packets are accepted only from the source address, server port, and SSRC
negotiated during RTSP `SETUP`; unexpected senders are counted and discarded.
Explicit `0.0.0.0` RTP binds are rejected.

## Remote destination core

Milestone 16.3 introduces a service-neutral `RemotePcmSink` foundation before any
Broadcastify or Asterisk adapter is enabled. The sink owns a bounded newest-audio
queue and performs connection creation, blocking writes, reconnect backoff, and
connection shutdown on its worker thread. Scanner RTP reception and all other PCM
destinations therefore remain independent from a slow or failed remote service.

`RemoteDestinationConfig` rejects credentials embedded in endpoint URLs.
Credentials are represented by named `EnvironmentSecret` references and resolved
only when the worker opens an adapter connection. Resolved values are excluded from
configuration representations, snapshots, and log messages; connection exceptions
are redacted before they are retained or reported.

`RemotePcmSinkSnapshot` reports immutable queue, throughput, connection-attempt,
reconnect, failure, retry, and last-error state. Service adapters receive the
configuration and resolved secret mapping through an injected
`RemoteConnectionFactory`. No production remote service adapter or command-line
configuration is available yet.

The protocol is unauthenticated and unencrypted. Keep RTSP TCP port 554 and its
negotiated RTP UDP port on a trusted LAN or behind a secured VPN. Remote streaming
credentials must not be passed through command-line arguments or written to logs.
