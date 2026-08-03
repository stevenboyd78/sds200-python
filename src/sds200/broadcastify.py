from __future__ import annotations

import base64
import socket
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol
from urllib.parse import urlencode

from .audio_recording import PCM_CHANNELS, PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE
from .exceptions import AudioOutputError
from .reliability import ReconnectPolicy
from .remote_audio import (
    EnvironmentSecret,
    RemoteAudioConnection,
    RemoteDestinationConfig,
    RemotePcmSink,
)
from .remote_audio_encoder import (
    AudioEncoderConfig,
    AudioEncoderProcessFactory,
    ManagedAudioEncoder,
)
from .remote_audio_metadata import RemoteStreamMetadata
from .remote_audio_metadata_publisher import (
    RemoteMetadataPublication,
    RemoteMetadataPublisher,
    RemoteMetadataPublisherConfig,
)

BROADCASTIFY_SAMPLE_RATE = 22_050
BROADCASTIFY_MONO_BITRATE_KBPS = 16
BROADCASTIFY_ALLOWED_PORTS = frozenset({80, 8000, 8080, 8500})
BROADCASTIFY_PASSWORD_SECRET = "password"
BROADCASTIFY_METADATA_PATH = "/admin/metadata"

_MAX_RESPONSE_BYTES = 8192
_PUMP_CHUNK_BYTES = 4096


class _SocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...

    def sendall(self, data: bytes) -> None: ...

    def recv(self, size: int) -> bytes: ...

    def shutdown(self, how: int) -> None: ...

    def close(self) -> None: ...


_SocketFactory = Callable[[tuple[str, int], float], _SocketLike]


@dataclass(frozen=True, slots=True)
class BroadcastifyConfig:
    """Broadcastify feed settings without retaining the resolved source password."""

    name: str
    server: str
    mount: str
    password: EnvironmentSecret
    port: int = 80
    stream_name: str = "SDS200 scanner feed"
    genre: str = "Scanner"
    public: bool = True
    ffmpeg_executable: str = "ffmpeg"
    connect_timeout: float = 10.0
    socket_timeout: float = 10.0
    encoder_stop_timeout: float = 2.0
    buffer_seconds: float = 5.0
    stop_timeout: float = 5.0
    reconnect_policy: ReconnectPolicy = field(default_factory=ReconnectPolicy)

    def __post_init__(self) -> None:
        _validate_header_text("Broadcastify destination name", self.name)
        _validate_server(self.server)
        _validate_mount(self.mount)
        if not isinstance(self.password, EnvironmentSecret):
            raise TypeError("Broadcastify password must be an EnvironmentSecret.")
        if self.port not in BROADCASTIFY_ALLOWED_PORTS:
            allowed = ", ".join(str(port) for port in sorted(BROADCASTIFY_ALLOWED_PORTS))
            raise ValueError(f"Broadcastify port must be one of: {allowed}.")
        _validate_header_text("Broadcastify stream name", self.stream_name)
        _validate_header_text("Broadcastify genre", self.genre)
        _validate_header_text("FFmpeg executable", self.ffmpeg_executable)
        if self.connect_timeout <= 0:
            raise ValueError("Broadcastify connect timeout must be greater than zero.")
        if self.socket_timeout <= 0:
            raise ValueError("Broadcastify socket timeout must be greater than zero.")
        if self.encoder_stop_timeout <= 0:
            raise ValueError("Broadcastify encoder stop timeout must be greater than zero.")
        if self.buffer_seconds <= 0:
            raise ValueError("Broadcastify buffer must be greater than zero seconds.")
        if self.stop_timeout <= 0:
            raise ValueError("Broadcastify stop timeout must be greater than zero.")
        if self.encoder_stop_timeout >= self.stop_timeout:
            raise ValueError(
                "Broadcastify encoder stop timeout must be shorter than the sink stop timeout."
            )

    @property
    def endpoint(self) -> str:
        return f"http://{self.server}:{self.port}{self.mount}"

    @property
    def metadata_endpoint(self) -> str:
        query = urlencode(
            {
                "mount": self.mount,
                "mode": "updinfo",
            }
        )
        return (
            f"http://{self.server}:{self.port}"
            f"{BROADCASTIFY_METADATA_PATH}?{query}"
        )

    def remote_metadata_destination(
        self,
        *,
        minimum_update_interval: float = 0.0,
        stop_timeout: float | None = None,
    ) -> RemoteMetadataPublisherConfig:
        """Build service-neutral configuration for metadata publication."""

        return RemoteMetadataPublisherConfig(
            name=self.name,
            endpoint=self.metadata_endpoint,
            secrets={BROADCASTIFY_PASSWORD_SECRET: self.password},
            minimum_update_interval=minimum_update_interval,
            stop_timeout=(
                self.stop_timeout if stop_timeout is None else stop_timeout
            ),
            reconnect_policy=self.reconnect_policy,
        )

    def remote_destination(self) -> RemoteDestinationConfig:
        return RemoteDestinationConfig(
            name=self.name,
            endpoint=self.endpoint,
            secrets={BROADCASTIFY_PASSWORD_SECRET: self.password},
            buffer_seconds=self.buffer_seconds,
            stop_timeout=self.stop_timeout,
            reconnect_policy=self.reconnect_policy,
        )

    def ffmpeg_command(self) -> tuple[str, ...]:
        """Return the fixed mono Broadcastify encoding command."""

        return (
            self.ffmpeg_executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "s16le",
            "-ar",
            str(PCMU_SAMPLE_RATE),
            "-ac",
            str(PCM_CHANNELS),
            "-i",
            "pipe:0",
            "-map_metadata",
            "-1",
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{BROADCASTIFY_MONO_BITRATE_KBPS}k",
            "-ar",
            str(BROADCASTIFY_SAMPLE_RATE),
            "-ac",
            "1",
            "-write_xing",
            "0",
            "-flush_packets",
            "1",
            "-f",
            "mp3",
            "pipe:1",
        )

    def encoder_config(self) -> AudioEncoderConfig:
        """Build the reusable lifecycle configuration for fixed MP3 encoding."""

        return AudioEncoderConfig(
            name="Broadcastify FFmpeg encoder",
            command=self.ffmpeg_command(),
            stop_timeout=self.encoder_stop_timeout,
            diagnostic_limit=_MAX_RESPONSE_BYTES,
        )


class BroadcastifyConnectionFactory:
    """Create Broadcastify Icecast connections for one immutable feed config."""

    def __init__(
        self,
        config: BroadcastifyConfig,
        *,
        encoder_factory: AudioEncoderProcessFactory | None = None,
        socket_factory: _SocketFactory | None = None,
    ) -> None:
        self.config = config
        self._encoder_factory = encoder_factory
        self._socket_factory = _open_socket if socket_factory is None else socket_factory

    def __call__(
        self,
        config: RemoteDestinationConfig,
        secrets: Mapping[str, str],
    ) -> RemoteAudioConnection:
        if config.endpoint != self.config.endpoint:
            raise AudioOutputError(
                "Broadcastify connection factory received an unexpected endpoint."
            )
        password = secrets.get(BROADCASTIFY_PASSWORD_SECRET)
        if not password:
            raise AudioOutputError("Broadcastify source password was not resolved.")
        return BroadcastifyConnection(
            self.config,
            password,
            encoder_factory=self._encoder_factory,
            socket_factory=self._socket_factory,
        )


class BroadcastifyMetadataPublicationFactory:
    """Create one short-lived authenticated Icecast metadata update."""

    def __init__(
        self,
        config: BroadcastifyConfig,
        *,
        socket_factory: _SocketFactory | None = None,
    ) -> None:
        self.config = config
        self._socket_factory = (
            _open_socket if socket_factory is None else socket_factory
        )

    def __call__(
        self,
        config: RemoteMetadataPublisherConfig,
        secrets: Mapping[str, str],
        metadata: RemoteStreamMetadata,
    ) -> RemoteMetadataPublication:
        if config.endpoint != self.config.metadata_endpoint:
            raise AudioOutputError(
                "Broadcastify metadata factory received an unexpected endpoint."
            )
        password = secrets.get(BROADCASTIFY_PASSWORD_SECRET)
        if not password:
            raise AudioOutputError(
                "Broadcastify source password was not resolved for metadata."
            )
        return BroadcastifyMetadataPublication(
            self.config,
            password,
            metadata,
            socket_factory=self._socket_factory,
        )


class BroadcastifyMetadataPublication:
    """One interruptible Broadcastify-compatible Icecast metadata request."""

    def __init__(
        self,
        config: BroadcastifyConfig,
        password: str,
        metadata: RemoteStreamMetadata,
        *,
        socket_factory: _SocketFactory | None = None,
    ) -> None:
        if not password:
            raise AudioOutputError(
                "Broadcastify metadata password must not be empty."
            )
        if not isinstance(metadata, RemoteStreamMetadata):
            raise TypeError(
                "Broadcastify metadata publication requires "
                "RemoteStreamMetadata."
            )

        self.config = config
        self.metadata = metadata
        self._password = password
        self._socket_factory = (
            _open_socket if socket_factory is None else socket_factory
        )
        self._lock = threading.RLock()
        self._socket: _SocketLike | None = None
        self._interrupted = False
        self._closed = False

    def publish(self) -> None:
        with self._lock:
            if self._closed:
                raise AudioOutputError(
                    "Broadcastify metadata publication is closed."
                )
            if self._interrupted:
                raise AudioOutputError(
                    "Broadcastify metadata publication was interrupted."
                )

        source_socket = self._socket_factory(
            (self.config.server, self.config.port),
            self.config.connect_timeout,
        )
        with self._lock:
            closed = self._closed
            interrupted = self._interrupted
            if not closed and not interrupted:
                self._socket = source_socket

        if closed or interrupted:
            _close_socket(source_socket)
            raise AudioOutputError(
                "Broadcastify metadata publication was interrupted."
            )

        try:
            source_socket.settimeout(self.config.socket_timeout)
            source_socket.sendall(
                _metadata_request(
                    self.config,
                    self._password,
                    self.metadata.render_title(),
                )
            )
            response = _read_response_headers(source_socket)
            _validate_metadata_response(response)
        except Exception as error:
            with self._lock:
                interrupted = self._interrupted
            if interrupted:
                raise AudioOutputError(
                    "Broadcastify metadata publication was interrupted."
                ) from error
            if isinstance(error, AudioOutputError):
                raise
            raise AudioOutputError(
                "Broadcastify metadata request failed: "
                f"{type(error).__name__}: {error}"
            ) from error
        finally:
            with self._lock:
                if self._socket is source_socket:
                    self._socket = None
            _close_socket(source_socket)

    def interrupt(self) -> None:
        with self._lock:
            if self._closed or self._interrupted:
                return
            self._interrupted = True
            source_socket = self._socket

        if source_socket is not None:
            _close_socket(source_socket)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            source_socket = self._socket
            self._socket = None

        if source_socket is not None:
            _close_socket(source_socket)


class BroadcastifyConnection:
    """One blocking encoded-audio Icecast connection used by RemotePcmSink."""

    def __init__(
        self,
        config: BroadcastifyConfig,
        password: str,
        *,
        encoder_factory: AudioEncoderProcessFactory | None = None,
        socket_factory: _SocketFactory | None = None,
    ) -> None:
        if not password:
            raise AudioOutputError("Broadcastify source password must not be empty.")

        self.config = config
        self._lock = threading.RLock()
        self._interrupted = False
        self._closing = False
        self._closed = False
        self._pump_error: BaseException | None = None

        selected_socket_factory = (
            _open_socket if socket_factory is None else socket_factory
        )
        source_socket = selected_socket_factory(
            (config.server, config.port),
            config.connect_timeout,
        )

        try:
            source_socket.settimeout(config.socket_timeout)
            _authenticate_source(source_socket, config, password)
            encoder = ManagedAudioEncoder(
                config.encoder_config(),
                process_factory=encoder_factory,
            )
        except Exception:
            _close_socket(source_socket)
            raise

        self._socket = source_socket
        self._encoder = encoder
        self._pump_thread = threading.Thread(
            target=self._pump_encoded_audio,
            name=f"sds200-broadcastify-{config.name}",
            daemon=True,
        )
        self._pump_thread.start()

    def write_pcm(self, data: bytes) -> None:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples.")
        if not data:
            return

        with self._lock:
            self._raise_if_unusable_locked()
            encoder = self._encoder

        encoder.write_pcm(data)

        with self._lock:
            self._raise_if_unusable_locked()

    def interrupt(self) -> None:
        with self._lock:
            if self._closed or self._interrupted:
                return
            self._interrupted = True
            encoder = self._encoder
            source_socket = self._socket

        _close_socket(source_socket)
        encoder.interrupt()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            encoder = self._encoder
            source_socket = self._socket
            pump_thread = self._pump_thread

        result = None
        cleanup_error: AudioOutputError | None = None
        output_wait_count = 0

        def wait_for_output(timeout: float) -> bool:
            nonlocal output_wait_count
            output_wait_count += 1
            if output_wait_count > 1:
                _close_socket(source_socket)
            pump_thread.join(timeout=timeout)
            return not pump_thread.is_alive()

        try:
            result = encoder.finalize(output_waiter=wait_for_output)
        except AudioOutputError as error:
            cleanup_error = error

        pump_alive = pump_thread.is_alive()
        _close_socket(source_socket)

        with self._lock:
            self._closed = True
            self._closing = False

        if pump_alive:
            raise AudioOutputError(
                "Broadcastify encoded-audio worker did not stop."
            )
        if cleanup_error is not None:
            raise cleanup_error
        assert result is not None
        if result.interrupted:
            return

        if result.returncode != 0 and not result.exit_reported:
            suffix = (
                ""
                if not result.diagnostic
                else f": {result.diagnostic}"
            )
            raise AudioOutputError(
                "Broadcastify FFmpeg encoder exited with status "
                f"{result.returncode}{suffix}."
            )

    def _pump_encoded_audio(self) -> None:
        try:
            while True:
                chunk = self._encoder.read_encoded(_PUMP_CHUNK_BYTES)
                if not chunk:
                    return
                self._socket.sendall(chunk)
        except Exception as error:
            with self._lock:
                if not self._interrupted and not self._closing:
                    self._pump_error = error

    def _raise_if_unusable_locked(self) -> None:
        if self._closed:
            raise AudioOutputError("Broadcastify connection is closed.")
        if self._interrupted or self._closing:
            raise AudioOutputError("Broadcastify connection is stopping.")
        if self._pump_error is not None:
            error = self._pump_error
            raise AudioOutputError(
                "Broadcastify Icecast stream failed: "
                f"{type(error).__name__}: {error}"
            ) from error

def create_broadcastify_metadata_publisher(
    config: BroadcastifyConfig,
    *,
    environ: Mapping[str, str] | None = None,
    minimum_update_interval: float = 0.0,
    stop_timeout: float | None = None,
    socket_factory: _SocketFactory | None = None,
) -> RemoteMetadataPublisher:
    """Create an isolated worker-backed Broadcastify metadata publisher."""

    factory = BroadcastifyMetadataPublicationFactory(
        config,
        socket_factory=socket_factory,
    )
    return RemoteMetadataPublisher(
        config.remote_metadata_destination(
            minimum_update_interval=minimum_update_interval,
            stop_timeout=stop_timeout,
        ),
        factory,
        environ=environ,
    )


def create_broadcastify_sink(
    config: BroadcastifyConfig,
    *,
    environ: Mapping[str, str] | None = None,
    encoder_factory: AudioEncoderProcessFactory | None = None,
    socket_factory: _SocketFactory | None = None,
) -> RemotePcmSink:
    """Create a worker-backed Broadcastify sink with injectable test seams."""

    factory = BroadcastifyConnectionFactory(
        config,
        encoder_factory=encoder_factory,
        socket_factory=socket_factory,
    )
    return RemotePcmSink(
        config.remote_destination(),
        factory,
        environ=environ,
    )


def _open_socket(address: tuple[str, int], timeout: float) -> _SocketLike:
    return socket.create_connection(address, timeout=timeout)


def _metadata_request(
    config: BroadcastifyConfig,
    password: str,
    title: str,
) -> bytes:
    credentials = base64.b64encode(
        f"source:{password}".encode()
    ).decode("ascii")
    query = urlencode(
        {
            "mount": config.mount,
            "mode": "updinfo",
            "song": title,
        }
    )
    request_lines = (
        f"GET {BROADCASTIFY_METADATA_PATH}?{query} HTTP/1.0",
        f"Host: {config.server}:{config.port}",
        f"Authorization: Basic {credentials}",
        "User-Agent: sds200-python",
        "Connection: close",
        "",
        "",
    )
    return "\r\n".join(request_lines).encode("utf-8")


def _validate_metadata_response(response: bytes) -> None:
    first_line = response.splitlines()[0].decode(
        "iso-8859-1",
        errors="replace",
    )
    parts = first_line.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise AudioOutputError(
            "Broadcastify returned an invalid Icecast metadata response."
        )
    status = int(parts[1])
    if not 200 <= status < 300:
        raise AudioOutputError(
            "Broadcastify rejected the Icecast metadata update "
            f"with status {status}."
        )


def _authenticate_source(
    source_socket: _SocketLike,
    config: BroadcastifyConfig,
    password: str,
) -> None:
    credentials = base64.b64encode(f"source:{password}".encode()).decode("ascii")
    request_lines = (
        f"SOURCE {config.mount} ICE/1.0",
        f"Host: {config.server}:{config.port}",
        f"Authorization: Basic {credentials}",
        "Content-Type: audio/mpeg",
        f"Ice-Name: {config.stream_name}",
        f"Ice-Genre: {config.genre}",
        "Ice-URL: https://www.broadcastify.com/",
        f"Ice-Public: {1 if config.public else 0}",
        f"Ice-Bitrate: {BROADCASTIFY_MONO_BITRATE_KBPS}",
        "Ice-Audio-Info: "
        f"bitrate={BROADCASTIFY_MONO_BITRATE_KBPS};"
        f"samplerate={BROADCASTIFY_SAMPLE_RATE};channels=1",
        "User-Agent: sds200-python",
        "",
        "",
    )
    source_socket.sendall("\r\n".join(request_lines).encode("utf-8"))
    response = _read_response_headers(source_socket)
    first_line = response.splitlines()[0].decode("iso-8859-1", errors="replace")
    parts = first_line.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise AudioOutputError("Broadcastify returned an invalid Icecast response.")
    status = int(parts[1])
    if not 200 <= status < 300:
        raise AudioOutputError(
            f"Broadcastify rejected the Icecast source connection with status {status}."
        )


def _read_response_headers(source_socket: _SocketLike) -> bytes:
    response = bytearray()
    while b"\r\n\r\n" not in response and b"\n\n" not in response:
        chunk = source_socket.recv(1024)
        if not chunk:
            raise AudioOutputError(
                "Broadcastify closed the connection before completing the Icecast response."
            )
        response.extend(chunk)
        if len(response) > _MAX_RESPONSE_BYTES:
            raise AudioOutputError("Broadcastify Icecast response headers were too large.")
    return bytes(response)


def _remaining_timeout(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def _close_socket(source_socket: _SocketLike) -> None:
    with suppress(OSError):
        source_socket.shutdown(socket.SHUT_RDWR)
    with suppress(OSError):
        source_socket.close()


def _validate_header_text(label: str, value: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{label} must not contain line breaks.")


def _validate_server(server: str) -> None:
    _validate_header_text("Broadcastify server", server)
    if any(character.isspace() for character in server):
        raise ValueError("Broadcastify server must not contain whitespace.")
    if any(character in server for character in "/\\?#@"):
        raise ValueError("Broadcastify server must be a bare hostname.")
    if ":" in server:
        raise ValueError("Broadcastify server port must be supplied separately.")


def _validate_mount(mount: str) -> None:
    _validate_header_text("Broadcastify mount", mount)
    if not mount.startswith("/") or mount == "/":
        raise ValueError("Broadcastify mount must start with '/' and include a path.")
    if any(character.isspace() for character in mount):
        raise ValueError("Broadcastify mount must not contain whitespace.")
    if "?" in mount or "#" in mount:
        raise ValueError("Broadcastify mount must not contain a query or fragment.")
