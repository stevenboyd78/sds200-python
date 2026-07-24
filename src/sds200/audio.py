from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, Self, runtime_checkable

from .events import EventBus


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """Opaque audio bytes received from an audio transport.

    Codec and packet framing intentionally remain transport-specific until
    a scanner audio protocol is implemented and validated against hardware.
    """

    data: bytes
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


AudioChunkHandler = Callable[[AudioChunk], None]


@runtime_checkable
class AudioTransport(Protocol):
    """Lifecycle contract for future USB, RTP, or RTSP audio transports."""

    @property
    def endpoint(self) -> str: ...

    @property
    def running(self) -> bool: ...

    def start(self, handler: AudioChunkHandler) -> None: ...

    def stop(self) -> None: ...


class AudioStream:
    """Transport-independent audio event stream.

    Audio is deliberately separate from :class:`sds200.SDSScanner`, so control
    failover and protocol parsing cannot be destabilized by audio work.
    """

    def __init__(self, transport: AudioTransport) -> None:
        self.transport = transport
        self.events = EventBus()

    @property
    def endpoint(self) -> str:
        return self.transport.endpoint

    @property
    def running(self) -> bool:
        return self.transport.running

    def on_chunk(self, callback: AudioChunkHandler) -> Callable[[], None]:
        return self.events.subscribe("chunk", callback)

    def start(self) -> None:
        self.transport.start(self._receive_chunk)

    def stop(self) -> None:
        self.transport.stop()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _receive_chunk(self, chunk: AudioChunk) -> None:
        self.events.emit("chunk", chunk)
