# Audio subsystem architecture

Version 0.11.0 implements hardware-validated SDS200 network audio while keeping
its lifecycle independent from scanner control. Audio failures never switch,
close, or delay USB serial, UDP control, fallback profiles, or preferred recovery.

## Layers

- `NetworkAudioTransport` performs RTSP negotiation over TCP and receives RTP over
  one UDP client port. The SDS200 requires `RTP/AVP;unicast;client_port=PORT`
  rather than the conventional RTP/RTCP port pair.
- `AudioStream` owns subscriptions and lifecycle without depending on
  `SDSScanner`.
- `AudioChunk.data` contains the raw payload type 0 G.711 mu-law bytes from one
  accepted RTP packet.
- `decode_mulaw` and `PcmuWavRecorder` convert the payload to 8 kHz, mono,
  signed 16-bit PCM and stream it into a finalized WAV file.

## CLI recording

```bash
sdsctl --host 192.168.0.251 audio \
  --output scanner-audio.wav \
  --duration 30
```

Omit `--duration` to record until `Ctrl+C`. Use `--force` to replace an existing
output file. The recorder finalizes the WAV header during normal shutdown and
keyboard interruption.

## Python lifecycle

```python
from pathlib import Path

from sds200 import AudioStream, NetworkAudioTransport, PcmuWavRecorder

transport = NetworkAudioTransport("192.168.0.251")
stream = AudioStream(transport)

with PcmuWavRecorder(Path("scanner-audio.wav")) as recorder:
    unsubscribe = stream.on_chunk(recorder.write_chunk)
    try:
        with stream:
            run_application()
    finally:
        unsubscribe()
```

## Reliability statistics

`NetworkAudioTransport.statistics` returns an immutable session snapshot with
received datagrams and bytes, delivered packets and payload bytes, sequence gaps,
estimated packet loss, duplicates, late packets, malformed packets, RTP timestamp
discontinuities, missing-sample estimates, receive and callback errors,
keepalives, teardown count, sequence endpoints, final timestamp, and SSRC.

Sequence tracking begins with the first packet actually received because the
SDS200's `RTP-Info` starting sequence is not a reliable initialization value.
Synthetic fixtures exercise loss, duplicate, late, malformed, wraparound, and
backward-timestamp behavior without requiring scanner hardware.

The protocol is unauthenticated and unencrypted. Keep RTSP TCP port 554 and its
negotiated RTP UDP port on a trusted LAN or behind a secured VPN.
