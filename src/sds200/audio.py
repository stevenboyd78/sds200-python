from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, Self, runtime_checkable

from .events import EventBus


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """Audio bytes received from an audio transport.

    Transport-specific framing is removed before delivery. The SDS200 network
    audio transport emits raw G.711 mu-law payload bytes.
    """

    data: bytes
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


AudioChunkHandler = Callable[[AudioChunk], None]


@runtime_checkable
class AudioTransport(Protocol):
    """Lifecycle contract for scanner audio transports."""

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
