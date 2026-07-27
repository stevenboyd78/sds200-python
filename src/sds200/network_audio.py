from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol

from .audio import AudioChunk, AudioChunkHandler
from .exceptions import ScannerConnectionError
from .rtp import RtpPacket, RtpProtocolError, RtpSequenceTracker
from .rtsp import DEFAULT_AUDIO_PATH, DEFAULT_RTSP_PORT, RtspClient, RtspProtocolError

logger = logging.getLogger(__name__)
MAX_RTP_DATAGRAM_SIZE = 65535


class AudioDatagramSocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...

    def bind(self, address: tuple[str, int]) -> None: ...

    def getsockname(self) -> tuple[str, int]: ...

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]: ...

    def close(self) -> None: ...


class RtspSessionClientLike(Protocol):
    def start(self, client_port: int) -> None: ...

    def get_parameter(self) -> object: ...

    def teardown(self) -> object: ...

    def close(self) -> None: ...


AudioDatagramSocketFactory = Callable[[int, int], AudioDatagramSocketLike]
RtspSessionClientFactory = Callable[[str, int, str, float], RtspSessionClientLike]


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
        local_host: str = "",
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
    ) -> None:
        if not host.strip():
            raise ValueError("Audio host must not be empty.")
        if not 1 <= rtsp_port <= 65535:
            raise ValueError("RTSP port must be between 1 and 65535.")
        if not 0 <= local_port <= 65535:
            raise ValueError("Local RTP port must be between 0 and 65535.")
        if read_timeout <= 0:
            raise ValueError("Audio read timeout must be greater than zero.")
        if rtsp_timeout <= 0:
            raise ValueError("RTSP timeout must be greater than zero.")
        if keepalive_interval <= 0:
            raise ValueError("RTSP keepalive interval must be greater than zero.")

        self.host = host
        self.rtsp_port = rtsp_port
        self.path = path
        self.local_host = local_host
        self.local_port = local_port
        self.read_timeout = read_timeout
        self.rtsp_timeout = rtsp_timeout
        self.keepalive_interval = keepalive_interval
        self._datagram_socket_factory = datagram_socket_factory
        self._rtsp_client_factory = rtsp_client_factory
        self._rtp_socket: AudioDatagramSocketLike | None = None
        self._rtsp_client: RtspSessionClientLike | None = None
        self._handler: AudioChunkHandler | None = None
        self._receiver_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
        self._rtsp_lock = threading.Lock()
        self._sequence_tracker = RtpSequenceTracker()

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

    def start(self, handler: AudioChunkHandler) -> None:
        with self._state_lock:
            if self._rtp_socket is not None:
                return
            self._handler = handler
            self._stop.clear()
            self._sequence_tracker.reset()

        rtp_socket: AudioDatagramSocketLike | None = None
        rtsp_client: RtspSessionClientLike | None = None
        try:
            rtp_socket = self._datagram_socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            rtp_socket.settimeout(self.read_timeout)
            rtp_socket.bind((self.local_host, self.local_port))
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
            rtsp_client.start(client_port)
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

    def _receiver_loop(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                rtp_socket = self._rtp_socket
            if rtp_socket is None:
                return
            try:
                datagram, _source = rtp_socket.recvfrom(MAX_RTP_DATAGRAM_SIZE)
            except TimeoutError:
                continue
            except OSError:
                if not self._stop.is_set():
                    logger.exception("SDS200 RTP socket failed for %s", self.endpoint)
                return
            if self._stop.is_set() or not datagram:
                continue
            try:
                packet = RtpPacket.parse(datagram)
            except RtpProtocolError:
                logger.warning("Discarding invalid SDS200 RTP packet", exc_info=True)
                continue

            observation = self._sequence_tracker.observe(packet.sequence)
            if observation.missing:
                logger.warning(
                    "SDS200 RTP sequence gap: expected %s, received %s (%s missing)",
                    observation.expected,
                    observation.sequence,
                    observation.missing,
                )
            elif observation.duplicate:
                logger.debug("Discarding duplicate SDS200 RTP packet %s", packet.sequence)
                continue
            elif observation.out_of_order:
                logger.debug("Discarding out-of-order SDS200 RTP packet %s", packet.sequence)
                continue

            handler = self._handler
            if handler is not None:
                try:
                    handler(AudioChunk(packet.payload))
                except Exception:
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
            except (OSError, RtspProtocolError, ScannerConnectionError):
                if not self._stop.is_set():
                    logger.exception("SDS200 RTSP keepalive failed for %s", self.endpoint)
                return
