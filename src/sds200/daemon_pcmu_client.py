from __future__ import annotations

import errno
import os
import socket as socket_module
import struct
import threading
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite

from .audio_recording import PCMU_SAMPLE_RATE
from .daemon_ipc import DaemonSocketLocation
from .exceptions import (
    DaemonDisconnectedError,
    DaemonProtocolError,
    DaemonUnavailableError,
)
from .pcmu_protocol import (
    PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
    PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
    PCMU_STREAM_HEADER_BYTES,
    PCMU_STREAM_MAGIC,
    PCMU_STREAM_SUPPORTED_VERSIONS,
    PcmuProtocolError,
    decode_pcmu_delivery,
)
from .pcmu_subscriptions import PcmuPacketDelivery

DAEMON_PCMU_CLIENT_DEFAULT_TIMEOUT = 5.0

_FRAME_PREFIX = struct.Struct("!4sBBHI")


@dataclass(frozen=True, slots=True)
class DaemonPcmuClientSnapshot:
    """Immutable delivery and loss state for one daemon PCMU connection."""

    connected: bool
    packets_received: int
    payload_bytes_received: int
    samples_received: int
    first_stream_sequence: int | None
    last_stream_sequence: int | None
    stream_packets_skipped: int
    packets_dropped: int
    payload_bytes_dropped: int
    overflows: int
    rtp_missing_packets: int
    rtp_missing_samples: int
    rtp_timestamp_backwards: int
    endpoint: str | None

    @property
    def audio_duration_seconds(self) -> float:
        return self.samples_received / PCMU_SAMPLE_RATE


class DaemonPcmuClient:
    """Receive and validate one local daemon binary PCMU stream."""

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float = DAEMON_PCMU_CLIENT_DEFAULT_TIMEOUT,
        max_endpoint_bytes: int = PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
        max_frame_bytes: int = PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        if not isinstance(location, DaemonSocketLocation):
            raise TypeError(
                "Daemon PCMU client location must be a DaemonSocketLocation."
            )
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("Daemon PCMU connect timeout must be a number.")
        normalized_timeout = float(timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError(
                "Daemon PCMU connect timeout must be finite and greater than zero."
            )
        if type(max_endpoint_bytes) is not int:
            raise TypeError(
                "Maximum daemon PCMU endpoint size must be an integer."
            )
        if max_endpoint_bytes <= 0:
            raise ValueError(
                "Maximum daemon PCMU endpoint size must be greater than zero."
            )
        if type(max_frame_bytes) is not int:
            raise TypeError(
                "Maximum daemon PCMU frame size must be an integer."
            )
        if max_frame_bytes < PCMU_STREAM_HEADER_BYTES:
            raise ValueError(
                "Maximum daemon PCMU frame size must be at least "
                f"{PCMU_STREAM_HEADER_BYTES} bytes."
            )

        self.location = location
        self.timeout = normalized_timeout
        self.max_endpoint_bytes = max_endpoint_bytes
        self.max_frame_bytes = max_frame_bytes
        self._state_lock = threading.RLock()
        self._receive_lock = threading.Lock()
        self._socket: socket_module.socket | None = None
        self._packets_received: int = 0
        self._payload_bytes_received: int = 0
        self._samples_received: int = 0
        self._first_stream_sequence: int | None = None
        self._last_stream_sequence: int | None = None
        self._stream_packets_skipped: int = 0
        self._packets_dropped: int = 0
        self._payload_bytes_dropped: int = 0
        self._overflows: int = 0
        self._rtp_missing_packets: int = 0
        self._rtp_missing_samples: int = 0
        self._rtp_timestamp_backwards: int = 0
        self._endpoint: str | None = None

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._socket is not None

    @property
    def last_stream_sequence(self) -> int | None:
        with self._state_lock:
            return self._last_stream_sequence

    def snapshot(self) -> DaemonPcmuClientSnapshot:
        with self._state_lock:
            return DaemonPcmuClientSnapshot(
                connected=self._socket is not None,
                packets_received=self._packets_received,
                payload_bytes_received=self._payload_bytes_received,
                samples_received=self._samples_received,
                first_stream_sequence=self._first_stream_sequence,
                last_stream_sequence=self._last_stream_sequence,
                stream_packets_skipped=self._stream_packets_skipped,
                packets_dropped=self._packets_dropped,
                payload_bytes_dropped=self._payload_bytes_dropped,
                overflows=self._overflows,
                rtp_missing_packets=self._rtp_missing_packets,
                rtp_missing_samples=self._rtp_missing_samples,
                rtp_timestamp_backwards=self._rtp_timestamp_backwards,
                endpoint=self._endpoint,
            )

    def connect(self) -> socket_module.socket:
        with self._state_lock:
            if self._socket is not None:
                return self._socket

            client = socket_module.socket(
                socket_module.AF_UNIX,
                socket_module.SOCK_STREAM,
            )
            client.settimeout(self.timeout)
            try:
                client.connect(os.fspath(self.location.path))
                client.settimeout(None)
            except OSError as error:
                _close_socket(client)
                self._raise_connect_error(error)

            self._socket = client
            self._reset_statistics_locked()
            return client

    def close(self) -> None:
        with self._state_lock:
            client = self._socket
            self._socket = None
        if client is not None:
            _close_socket(client)

    def __enter__(self) -> DaemonPcmuClient:
        self.connect()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def receive(self) -> PcmuPacketDelivery:
        """Receive one complete validated PCMU delivery."""

        with self._receive_lock:
            client = self.connect()
            try:
                frame = self._read_frame(client)
                try:
                    delivery = decode_pcmu_delivery(
                        frame,
                        max_endpoint_bytes=self.max_endpoint_bytes,
                        max_frame_bytes=self.max_frame_bytes,
                    )
                except PcmuProtocolError as error:
                    raise DaemonProtocolError(
                        f"Invalid daemon PCMU frame: {error}"
                    ) from error
                self._record_delivery(delivery)
            except (DaemonDisconnectedError, DaemonProtocolError):
                self._invalidate(client)
                raise
            return delivery

    def _read_frame(self, client: socket_module.socket) -> bytes:
        prefix = self._receive_exact(
            client,
            _FRAME_PREFIX.size,
            description="frame prefix",
            clean_eof=True,
        )
        (
            magic,
            version,
            _flags,
            header_size,
            frame_size,
        ) = _FRAME_PREFIX.unpack(prefix)

        if magic != PCMU_STREAM_MAGIC:
            raise DaemonProtocolError(
                "The daemon PCMU frame magic is incompatible."
            )
        if version not in PCMU_STREAM_SUPPORTED_VERSIONS:
            raise DaemonProtocolError(
                "The daemon PCMU frame version is incompatible: "
                f"{version}; "
                f"supported={list(PCMU_STREAM_SUPPORTED_VERSIONS)!r}."
            )
        if header_size != PCMU_STREAM_HEADER_BYTES:
            raise DaemonProtocolError(
                "The daemon PCMU frame header size is invalid."
            )
        if frame_size < PCMU_STREAM_HEADER_BYTES:
            raise DaemonProtocolError(
                "The daemon PCMU frame is shorter than its fixed header."
            )
        if frame_size > self.max_frame_bytes:
            raise DaemonProtocolError(
                "The daemon PCMU frame exceeds the maximum accepted size "
                f"of {self.max_frame_bytes} bytes."
            )

        body = self._receive_exact(
            client,
            frame_size - len(prefix),
            description="frame body",
            clean_eof=False,
        )
        return prefix + body

    def _receive_exact(
        self,
        client: socket_module.socket,
        size: int,
        *,
        description: str,
        clean_eof: bool,
    ) -> bytes:
        payload = bytearray()
        while len(payload) < size:
            try:
                chunk = client.recv(size - len(payload))
            except OSError as error:
                raise DaemonDisconnectedError(
                    "The daemon PCMU stream disconnected while receiving data."
                ) from error

            if not chunk:
                if clean_eof and not payload:
                    raise DaemonDisconnectedError(
                        "The daemon PCMU stream disconnected."
                    )
                raise DaemonProtocolError(
                    "The daemon PCMU stream closed with an incomplete "
                    f"{description}."
                )
            payload.extend(chunk)
        return bytes(payload)

    def _record_delivery(self, delivery: PcmuPacketDelivery) -> None:
        with self._state_lock:
            previous_sequence = self._last_stream_sequence
            if (
                previous_sequence is not None
                and delivery.stream_sequence <= previous_sequence
            ):
                raise DaemonProtocolError(
                    "The daemon PCMU stream sequence did not advance "
                    "monotonically: "
                    f"previous={previous_sequence}, "
                    f"received={delivery.stream_sequence}."
                )

            previous_loss = (
                self._packets_dropped,
                self._payload_bytes_dropped,
                self._overflows,
            )
            current_loss = (
                delivery.packets_dropped,
                delivery.payload_bytes_dropped,
                delivery.overflows,
            )
            if any(
                current < previous
                for current, previous in zip(
                    current_loss,
                    previous_loss,
                    strict=True,
                )
            ):
                raise DaemonProtocolError(
                    "The daemon PCMU cumulative queue-loss counters regressed."
                )

            if previous_sequence is None:
                self._first_stream_sequence = delivery.stream_sequence
            else:
                self._stream_packets_skipped += (
                    delivery.stream_sequence - previous_sequence - 1
                )

            packet = delivery.packet
            self._last_stream_sequence = delivery.stream_sequence
            self._packets_received += 1
            self._payload_bytes_received += len(packet.payload)
            self._samples_received += packet.sample_count
            self._packets_dropped = delivery.packets_dropped
            self._payload_bytes_dropped = delivery.payload_bytes_dropped
            self._overflows = delivery.overflows
            self._rtp_missing_packets += packet.missing_packets
            self._rtp_missing_samples += packet.missing_samples
            if packet.timestamp_backwards:
                self._rtp_timestamp_backwards += 1
            self._endpoint = packet.endpoint

    def _reset_statistics_locked(self) -> None:
        self._packets_received = 0
        self._payload_bytes_received = 0
        self._samples_received = 0
        self._first_stream_sequence = None
        self._last_stream_sequence = None
        self._stream_packets_skipped = 0
        self._packets_dropped = 0
        self._payload_bytes_dropped = 0
        self._overflows = 0
        self._rtp_missing_packets = 0
        self._rtp_missing_samples = 0
        self._rtp_timestamp_backwards = 0
        self._endpoint = None

    def _invalidate(self, expected: socket_module.socket) -> None:
        with self._state_lock:
            if self._socket is expected:
                self._socket = None
        _close_socket(expected)

    def _raise_connect_error(self, error: OSError) -> None:
        path = self.location.path
        if error.errno == errno.ENOENT:
            raise DaemonUnavailableError(
                f"Daemon PCMU socket was not found: {path}"
            ) from error
        if error.errno == errno.ECONNREFUSED:
            raise DaemonUnavailableError(
                "Daemon PCMU socket is present but not accepting "
                f"connections: {path}"
            ) from error
        if error.errno in {errno.EACCES, errno.EPERM}:
            raise DaemonUnavailableError(
                "Permission denied while connecting to daemon PCMU "
                f"socket: {path}"
            ) from error
        if isinstance(error, TimeoutError):
            raise DaemonUnavailableError(
                f"Timed out connecting to daemon PCMU socket: {path}"
            ) from error

        detail = error.strerror or error.__class__.__name__
        raise DaemonUnavailableError(
            f"Could not connect to daemon PCMU socket {path}: {detail}"
        ) from error


def _close_socket(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
