from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .audio import AudioChunk, AudioChunkHandler
from .events import EventBus
from .exceptions import ScannerConnectionError
from .pcmu import PcmuPacket, PcmuPacketHandler
from .rtp import (
    RtpPacket,
    RtpProtocolError,
    RtpSequenceTracker,
    RtpTimestampTracker,
)
from .rtsp import (
    DEFAULT_AUDIO_PATH,
    DEFAULT_RTSP_PORT,
    RtpTransportInfo,
    RtspClient,
    RtspProtocolError,
)
from .socket_utils import (
    LocalAddressResolver,
    normalize_local_ipv4_bind_address,
    resolve_local_ipv4_address,
)

logger = logging.getLogger(__name__)
MAX_RTP_DATAGRAM_SIZE = 65535


class AudioDatagramSocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...

    def bind(self, address: tuple[str, int]) -> None: ...

    def getsockname(self) -> tuple[str, int]: ...

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]: ...

    def close(self) -> None: ...


class RtspSessionClientLike(Protocol):
    def start(self, client_port: int) -> RtpTransportInfo: ...

    def get_parameter(self) -> object: ...

    def teardown(self) -> object: ...

    def close(self) -> None: ...


AudioDatagramSocketFactory = Callable[[int, int], AudioDatagramSocketLike]
RtspSessionClientFactory = Callable[[str, int, str, float], RtspSessionClientLike]


@dataclass(frozen=True, slots=True)
class NetworkAudioStatistics:
    """Snapshot of one network-audio transport session."""

    sessions_started: int = 0
    datagrams_received: int = 0
    bytes_received: int = 0
    packets_delivered: int = 0
    payload_bytes_delivered: int = 0
    sequence_gaps: int = 0
    packets_lost: int = 0
    duplicate_packets: int = 0
    late_packets: int = 0
    malformed_packets: int = 0
    unexpected_source_packets: int = 0
    ssrc_mismatch_packets: int = 0
    timestamp_discontinuities: int = 0
    timestamp_samples_missing: int = 0
    timestamp_backwards: int = 0
    receive_errors: int = 0
    callback_errors: int = 0
    keepalives_sent: int = 0
    keepalive_failures: int = 0
    teardowns_sent: int = 0
    first_sequence: int | None = None
    last_sequence: int | None = None
    last_timestamp: int | None = None
    ssrc: int | None = None


@dataclass(slots=True)
class _MutableNetworkAudioStatistics:
    sessions_started: int = 0
    datagrams_received: int = 0
    bytes_received: int = 0
    packets_delivered: int = 0
    payload_bytes_delivered: int = 0
    sequence_gaps: int = 0
    packets_lost: int = 0
    duplicate_packets: int = 0
    late_packets: int = 0
    malformed_packets: int = 0
    unexpected_source_packets: int = 0
    ssrc_mismatch_packets: int = 0
    timestamp_discontinuities: int = 0
    timestamp_samples_missing: int = 0
    timestamp_backwards: int = 0
    receive_errors: int = 0
    callback_errors: int = 0
    keepalives_sent: int = 0
    keepalive_failures: int = 0
    teardowns_sent: int = 0
    first_sequence: int | None = None
    last_sequence: int | None = None
    last_timestamp: int | None = None
    ssrc: int | None = None

    def snapshot(self) -> NetworkAudioStatistics:
        return NetworkAudioStatistics(
            sessions_started=self.sessions_started,
            datagrams_received=self.datagrams_received,
            bytes_received=self.bytes_received,
            packets_delivered=self.packets_delivered,
            payload_bytes_delivered=self.payload_bytes_delivered,
            sequence_gaps=self.sequence_gaps,
            packets_lost=self.packets_lost,
            duplicate_packets=self.duplicate_packets,
            late_packets=self.late_packets,
            malformed_packets=self.malformed_packets,
            unexpected_source_packets=self.unexpected_source_packets,
            ssrc_mismatch_packets=self.ssrc_mismatch_packets,
            timestamp_discontinuities=self.timestamp_discontinuities,
            timestamp_samples_missing=self.timestamp_samples_missing,
            timestamp_backwards=self.timestamp_backwards,
            receive_errors=self.receive_errors,
            callback_errors=self.callback_errors,
            keepalives_sent=self.keepalives_sent,
            keepalive_failures=self.keepalive_failures,
            teardowns_sent=self.teardowns_sent,
            first_sequence=self.first_sequence,
            last_sequence=self.last_sequence,
            last_timestamp=self.last_timestamp,
            ssrc=self.ssrc,
        )


def default_audio_datagram_socket_factory(
    family: int,
    socket_type: int,
) -> AudioDatagramSocketLike:
    return socket.socket(family, socket_type)


def default_rtsp_session_client_factory(
    host: str,
    port: int,
    path: str,
    timeout: float,
) -> RtspSessionClientLike:
    return RtspClient(host, port=port, path=path, timeout=timeout)


class NetworkAudioTransport:
    """SDS200 RTSP/RTP network audio transport emitting raw PCMU payloads."""

    def __init__(
        self,
        host: str,
        *,
        rtsp_port: int = DEFAULT_RTSP_PORT,
        path: str = DEFAULT_AUDIO_PATH,
        local_host: str | None = None,
        local_port: int = 0,
        read_timeout: float = 0.2,
        rtsp_timeout: float = 5.0,
        keepalive_interval: float = 15.0,
        datagram_socket_factory: AudioDatagramSocketFactory = (
            default_audio_datagram_socket_factory
        ),
        rtsp_client_factory: RtspSessionClientFactory = (
            default_rtsp_session_client_factory
        ),
        local_address_resolver: LocalAddressResolver = resolve_local_ipv4_address,
    ) -> None:
        if not host.strip():
            raise ValueError("Audio host must not be empty.")
        if not 1 <= rtsp_port <= 65535:
            raise ValueError("RTSP port must be between 1 and 65535.")
        if not 0 <= local_port <= 65535:
            raise ValueError("Local RTP port must be between 0 and 65535.")
        normalized_local_host = normalize_local_ipv4_bind_address(
            local_host,
            description="Local RTP address",
        )
        if read_timeout <= 0:
            raise ValueError("Audio read timeout must be greater than zero.")
        if rtsp_timeout <= 0:
            raise ValueError("RTSP timeout must be greater than zero.")
        if keepalive_interval <= 0:
            raise ValueError("RTSP keepalive interval must be greater than zero.")

        self.host = host
        self.rtsp_port = rtsp_port
        self.path = path
        self.local_host = normalized_local_host
        self.local_port = local_port
        self.read_timeout = read_timeout
        self.rtsp_timeout = rtsp_timeout
        self.keepalive_interval = keepalive_interval
        self._datagram_socket_factory = datagram_socket_factory
        self._rtsp_client_factory = rtsp_client_factory
        self._local_address_resolver = local_address_resolver
        self.events = EventBus()
        self._rtp_socket: AudioDatagramSocketLike | None = None
        self._rtsp_client: RtspSessionClientLike | None = None
        self._handler: AudioChunkHandler | None = None
        self._receiver_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
        self._rtsp_lock = threading.Lock()
        self._statistics_lock = threading.RLock()
        self._statistics = _MutableNetworkAudioStatistics()
        self._sequence_tracker = RtpSequenceTracker()
        self._timestamp_tracker = RtpTimestampTracker()
        self._expected_source: tuple[str, int] | None = None
        self._expected_ssrc: int | None = None

    @property
    def endpoint(self) -> str:
        authority = self.host if self.rtsp_port == DEFAULT_RTSP_PORT else (
            f"{self.host}:{self.rtsp_port}"
        )
        return f"rtsp://{authority}{self.path}"

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._rtp_socket is not None and self._rtsp_client is not None

    @property
    def statistics(self) -> NetworkAudioStatistics:
        with self._statistics_lock:
            return self._statistics.snapshot()

    def on_packet(
        self,
        callback: PcmuPacketHandler,
    ) -> Callable[[], None]:
        """Subscribe to accepted RTP PCMU packets before PCM decoding."""

        return self.events.subscribe("packet", callback)

    def start(self, handler: AudioChunkHandler) -> None:
        with self._state_lock:
            if self._rtp_socket is not None:
                return
            self._handler = handler
            self._stop.clear()
            self._sequence_tracker.reset()
            self._timestamp_tracker.reset()
        with self._statistics_lock:
            self._statistics = _MutableNetworkAudioStatistics()

        rtp_socket: AudioDatagramSocketLike | None = None
        rtsp_client: RtspSessionClientLike | None = None
        negotiated: RtpTransportInfo | None = None
        try:
            rtp_socket = self._datagram_socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            rtp_socket.settimeout(self.read_timeout)
            bind_host = self.local_host
            if bind_host is None:
                bind_host = self._local_address_resolver(
                    self.host,
                    self.rtsp_port,
                )
            rtp_socket.bind((bind_host, self.local_port))
            client_port = rtp_socket.getsockname()[1]
            if not 1 <= client_port <= 65535:
                raise ScannerConnectionError(
                    f"Could not allocate an RTP client port for {self.endpoint}."
                )

            rtsp_client = self._rtsp_client_factory(
                self.host,
                self.rtsp_port,
                self.path,
                self.rtsp_timeout,
            )
            negotiated = rtsp_client.start(client_port)
        except (OSError, RtspProtocolError, ScannerConnectionError) as exc:
            if rtsp_client is not None:
                with suppress(Exception):
                    rtsp_client.close()
            if rtp_socket is not None:
                with suppress(OSError):
                    rtp_socket.close()
            raise ScannerConnectionError(
                f"Could not start SDS200 network audio at {self.endpoint}."
            ) from exc

        assert rtp_socket is not None
        assert rtsp_client is not None
        assert negotiated is not None
        self._expected_source = (negotiated.source, negotiated.server_port)
        self._expected_ssrc = negotiated.ssrc
        with self._statistics_lock:
            self._statistics.sessions_started += 1
            self._statistics.ssrc = negotiated.ssrc
        with self._state_lock:
            self._rtp_socket = rtp_socket
            self._rtsp_client = rtsp_client
            self._receiver_thread = threading.Thread(
                target=self._receiver_loop,
                name="sds200-audio-rtp-reader",
                daemon=True,
            )
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop,
                name="sds200-audio-rtsp-keepalive",
                daemon=True,
            )
            receiver = self._receiver_thread
            keepalive = self._keepalive_thread
        assert receiver is not None
        assert keepalive is not None
        receiver.start()
        keepalive.start()

    def stop(self) -> None:
        self._stop.set()
        with self._state_lock:
            rtp_socket, self._rtp_socket = self._rtp_socket, None
            rtsp_client, self._rtsp_client = self._rtsp_client, None
            receiver, self._receiver_thread = self._receiver_thread, None
            keepalive, self._keepalive_thread = self._keepalive_thread, None

        if rtsp_client is not None:
            with self._rtsp_lock:
                with suppress(Exception):
                    rtsp_client.teardown()
                    with self._statistics_lock:
                        self._statistics.teardowns_sent += 1
                with suppress(Exception):
                    rtsp_client.close()
        if rtp_socket is not None:
            with suppress(OSError):
                rtp_socket.close()

        current = threading.current_thread()
        for thread in (receiver, keepalive):
            if thread is not None and thread is not current:
                thread.join(timeout=max(1.0, self.read_timeout * 4))
        self._handler = None
        self._sequence_tracker.reset()
        self._timestamp_tracker.reset()
        self._expected_source = None
        self._expected_ssrc = None

    def _receiver_loop(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                rtp_socket = self._rtp_socket
            if rtp_socket is None:
                return
            try:
                datagram, source = rtp_socket.recvfrom(MAX_RTP_DATAGRAM_SIZE)
            except TimeoutError:
                continue
            except OSError:
                if not self._stop.is_set():
                    with self._statistics_lock:
                        self._statistics.receive_errors += 1
                    logger.exception("SDS200 RTP socket failed for %s", self.endpoint)
                return
            if self._stop.is_set() or not datagram:
                continue
            with self._statistics_lock:
                self._statistics.datagrams_received += 1
                self._statistics.bytes_received += len(datagram)
            if source != self._expected_source:
                with self._statistics_lock:
                    self._statistics.unexpected_source_packets += 1
                logger.warning(
                    "Discarding SDS200 RTP packet from unexpected source %s:%s",
                    source[0],
                    source[1],
                )
                continue
            try:
                packet = RtpPacket.parse(datagram)
            except RtpProtocolError:
                with self._statistics_lock:
                    self._statistics.malformed_packets += 1
                logger.warning("Discarding invalid SDS200 RTP packet", exc_info=True)
                continue

            if self._expected_ssrc is None:
                self._expected_ssrc = packet.ssrc
                with self._statistics_lock:
                    self._statistics.ssrc = packet.ssrc
            elif packet.ssrc != self._expected_ssrc:
                with self._statistics_lock:
                    self._statistics.ssrc_mismatch_packets += 1
                logger.warning(
                    "Discarding SDS200 RTP packet with unexpected SSRC %s",
                    packet.ssrc,
                )
                continue

            observation = self._sequence_tracker.observe(packet.sequence)
            if observation.missing:
                with self._statistics_lock:
                    self._statistics.sequence_gaps += 1
                    self._statistics.packets_lost += observation.missing
                logger.warning(
                    "SDS200 RTP sequence gap: expected %s, received %s (%s missing)",
                    observation.expected,
                    observation.sequence,
                    observation.missing,
                )
            elif observation.duplicate:
                with self._statistics_lock:
                    self._statistics.duplicate_packets += 1
                logger.debug("Discarding duplicate SDS200 RTP packet %s", packet.sequence)
                continue
            elif observation.out_of_order:
                with self._statistics_lock:
                    self._statistics.late_packets += 1
                logger.debug("Discarding late SDS200 RTP packet %s", packet.sequence)
                continue

            timestamp = self._timestamp_tracker.observe(
                packet.timestamp,
                len(packet.payload),
            )
            if timestamp.missing_samples:
                with self._statistics_lock:
                    self._statistics.timestamp_discontinuities += 1
                    self._statistics.timestamp_samples_missing += (
                        timestamp.missing_samples
                    )
                logger.warning(
                    "SDS200 RTP timestamp discontinuity: expected %s, received %s "
                    "(%s samples missing)",
                    timestamp.expected,
                    timestamp.timestamp,
                    timestamp.missing_samples,
                )
            elif timestamp.backwards:
                with self._statistics_lock:
                    self._statistics.timestamp_discontinuities += 1
                    self._statistics.timestamp_backwards += 1
                logger.warning(
                    "SDS200 RTP timestamp moved backwards: expected %s, received %s",
                    timestamp.expected,
                    timestamp.timestamp,
                )

            with self._statistics_lock:
                statistics = self._statistics
                statistics.packets_delivered += 1
                statistics.payload_bytes_delivered += len(packet.payload)
                if statistics.first_sequence is None:
                    statistics.first_sequence = packet.sequence
                statistics.last_sequence = packet.sequence
                statistics.last_timestamp = packet.timestamp

            observed_at = datetime.now(UTC)
            self.events.emit(
                "packet",
                PcmuPacket(
                    endpoint=self.endpoint,
                    sequence=packet.sequence,
                    timestamp=packet.timestamp,
                    ssrc=packet.ssrc,
                    payload=packet.payload,
                    observed_at=observed_at,
                    marker=packet.marker,
                    expected_sequence=observation.expected,
                    missing_packets=observation.missing,
                    expected_timestamp=timestamp.expected,
                    missing_samples=timestamp.missing_samples,
                    timestamp_backwards=timestamp.backwards,
                ),
            )

            handler = self._handler
            if handler is not None:
                try:
                    handler(
                        AudioChunk(
                            packet.payload,
                            received_at=observed_at,
                        )
                    )
                except Exception:
                    with self._statistics_lock:
                        self._statistics.callback_errors += 1
                    logger.exception("Unhandled exception in audio chunk callback")

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(self.keepalive_interval):
            with self._state_lock:
                rtsp_client = self._rtsp_client
            if rtsp_client is None:
                return
            try:
                with self._rtsp_lock:
                    rtsp_client.get_parameter()
                with self._statistics_lock:
                    self._statistics.keepalives_sent += 1
            except (OSError, RtspProtocolError, ScannerConnectionError):
                if not self._stop.is_set():
                    with self._statistics_lock:
                        self._statistics.keepalive_failures += 1
                    logger.exception("SDS200 RTSP keepalive failed for %s", self.endpoint)
                return
