from sds200.audio import AudioChunk, AudioStream

from .fakes import FakeAudioTransport


def test_audio_stream_has_independent_lifecycle_and_events() -> None:
    transport = FakeAudioTransport()
    stream = AudioStream(transport)
    received: list[AudioChunk] = []
    stream.on_chunk(received.append)

    with stream:
        chunk = AudioChunk(b"audio")
        transport.feed(chunk)
        assert stream.running

    assert not stream.running
    assert received == [chunk]
