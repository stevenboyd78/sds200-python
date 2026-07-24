# Audio subsystem architecture

Network audio is not implemented in version 0.6.0. This milestone establishes a
separate public lifecycle so future RTP, RTSP, or other validated scanner audio
transports do not become entangled with scanner control.

`AudioTransport` owns audio I/O. `AudioStream` owns subscriptions and lifecycle.
`AudioChunk` intentionally contains opaque bytes until the codec and framing are
verified against real hardware.

```python
from sds200 import AudioStream

stream = AudioStream(validated_audio_transport)
stream.on_chunk(lambda chunk: process(chunk.data))
with stream:
    run_application()
```

Audio transport failures must never switch, close, or delay the control
transport. Control profiles and fallback behavior remain independent.
