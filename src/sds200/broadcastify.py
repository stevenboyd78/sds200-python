from __future__ import annotations

import base64
import socket
import subprocess
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Protocol, cast

from .audio_recording import PCM_CHANNELS, PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE
from .exceptions import AudioOutputError
from .reliability import ReconnectPolicy
from .remote_audio import (
    EnvironmentSecret,
    RemoteAudioConnection,
    RemoteDestinationConfig,
    RemotePcmSink,
)

BROADCASTIFY_SAMPLE_RATE = 22_050
BROADCASTIFY_MONO_BITRATE_KBPS = 16
BROADCASTIFY_ALLOWED_PORTS = frozenset({80, 8000, 8080, 8500})
BROADCASTIFY_PASSWORD_SECRET = "password"

_MAX_RESPONSE_BYTES = 8192
_PUMP_CHUNK_BYTES = 4096


class _SocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...

    def sendall(self, data: bytes) -> None: ...

    def recv(self, size: int) -> bytes: ...

    def shutdown(self, how: int) -> None: ...

    def close(self) -> None: ...


class _EncoderProcess(Protocol):
    @property
    def stdin(self) -> BinaryIO: ...

    @property
    def stdout(self) -> BinaryIO: ...

    @property
    def stderr(self) -> BinaryIO: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


_EncoderFactory = Callable[[tuple[str, ...]], _EncoderProcess]
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


class BroadcastifyConnectionFactory:
    """Create Broadcastify Icecast connections for one immutable feed config."""

    def __init__(
        self,
        config: BroadcastifyConfig,
        *,
        encoder_factory: _EncoderFactory | None = None,
        socket_factory: _SocketFactory | None = None,
    ) -> None:
        self.config = config
        self._encoder_factory = (
            _start_ffmpeg_encoder if encoder_factory is None else encoder_factory
        )
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


class BroadcastifyConnection:
    """One blocking FFmpeg-to-Icecast source connection used by RemotePcmSink."""

    def __init__(
        self,
        config: BroadcastifyConfig,
        password: str,
        *,
        encoder_factory: _EncoderFactory | None = None,
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
        self._encoder_failure_reported = False
        self._encoder_factory = (
            _start_ffmpeg_encoder if encoder_factory is None else encoder_factory
        )
        selected_socket_factory = _open_socket if socket_factory is None else socket_factory

        source_socket = selected_socket_factory(
            (config.server, config.port),
            config.connect_timeout,
        )
        try:
            source_socket.settimeout(config.socket_timeout)
            _authenticate_source(source_socket, config, password)
            process = self._encoder_factory(config.ffmpeg_command())
        except Exception:
            _close_socket(source_socket)
            raise

        self._socket = source_socket
        self._process = process
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
            process = self._process
            returncode = process.poll()
            if returncode is not None:
                self._encoder_failure_reported = True
                raise AudioOutputError(
                    "Broadcastify FFmpeg encoder exited unexpectedly "
                    f"with status {returncode}."
                )

        try:
            process.stdin.write(data)
            process.stdin.flush()
        except Exception as error:
            with self._lock:
                interrupted = self._interrupted or self._closing
                if process.poll() is not None:
                    self._encoder_failure_reported = True
            if interrupted:
                raise AudioOutputError("Broadcastify encoder input was interrupted.") from error
            raise AudioOutputError(
                f"Broadcastify encoder input failed: {type(error).__name__}: {error}"
            ) from error

        with self._lock:
            self._raise_if_unusable_locked()
            returncode = process.poll()
            if returncode is not None:
                self._encoder_failure_reported = True
                raise AudioOutputError(
                    "Broadcastify FFmpeg encoder exited unexpectedly "
                    f"with status {returncode}."
                )

    def interrupt(self) -> None:
        with self._lock:
            if self._closed or self._interrupted:
                return
            self._interrupted = True
            process = self._process
            source_socket = self._socket

        _close_socket(source_socket)
        _close_binary_stream(process.stdin)
        if process.poll() is None:
            with suppress(OSError):
                process.terminate()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            interrupted = self._interrupted
            encoder_failure_reported = self._encoder_failure_reported
            process = self._process
            source_socket = self._socket
            pump_thread = self._pump_thread

        deadline = monotonic() + self.config.encoder_stop_timeout
        if not interrupted:
            _close_binary_stream(process.stdin)

        returncode: int | None = None
        cleanup_error: AudioOutputError | None = None
        try:
            returncode = _terminate_encoder(process, deadline)
        except AudioOutputError as error:
            cleanup_error = error

        detail = ""
        if (
            cleanup_error is None
            and not interrupted
            and returncode is not None
            and returncode != 0
        ):
            detail = _read_encoder_error(process.stderr)

        if cleanup_error is None:
            pump_thread.join(timeout=_remaining_timeout(deadline))
        if pump_thread.is_alive():
            _close_socket(source_socket)
            if process.poll() is None:
                with suppress(OSError):
                    process.kill()
            _close_binary_stream(process.stdout)
            pump_thread.join(timeout=_remaining_timeout(deadline))
        pump_alive = pump_thread.is_alive()

        _close_binary_stream(process.stdout)
        _close_binary_stream(process.stderr)
        _close_socket(source_socket)

        with self._lock:
            self._closed = True
            self._closing = False

        if pump_alive:
            raise AudioOutputError("Broadcastify encoded-audio worker did not stop.")
        if cleanup_error is not None:
            raise cleanup_error
        if interrupted:
            return
        if returncode is not None and returncode != 0 and not encoder_failure_reported:
            suffix = "" if not detail else f": {detail}"
            raise AudioOutputError(
                f"Broadcastify FFmpeg encoder exited with status {returncode}{suffix}."
            )

    def _pump_encoded_audio(self) -> None:
        try:
            while True:
                chunk = self._process.stdout.read(_PUMP_CHUNK_BYTES)
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


def create_broadcastify_sink(
    config: BroadcastifyConfig,
    *,
    environ: Mapping[str, str] | None = None,
    encoder_factory: _EncoderFactory | None = None,
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


class _PopenEncoder:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        stdin = process.stdin
        stdout = process.stdout
        stderr = process.stderr
        if stdin is None or stdout is None or stderr is None:
            process.kill()
            raise AudioOutputError("FFmpeg did not expose all required pipe streams.")
        self._process = process
        self._stdin = cast(BinaryIO, stdin)
        self._stdout = cast(BinaryIO, stdout)
        self._stderr = cast(BinaryIO, stderr)

    @property
    def stdin(self) -> BinaryIO:
        return self._stdin

    @property
    def stdout(self) -> BinaryIO:
        return self._stdout

    @property
    def stderr(self) -> BinaryIO:
        return self._stderr

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()


def _start_ffmpeg_encoder(command: tuple[str, ...]) -> _EncoderProcess:
    try:
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except FileNotFoundError as error:
        executable = Path(command[0]).name
        raise AudioOutputError(
            f"FFmpeg executable {executable!r} was not found."
        ) from error
    except OSError as error:
        raise AudioOutputError(
            f"Unable to start the Broadcastify FFmpeg encoder: {error}"
        ) from error
    return _PopenEncoder(process)


def _open_socket(address: tuple[str, int], timeout: float) -> _SocketLike:
    return socket.create_connection(address, timeout=timeout)


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


def _terminate_encoder(process: _EncoderProcess, deadline: float) -> int:
    try:
        return process.wait(timeout=_cleanup_stage_timeout(deadline))
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.terminate()
    try:
        return process.wait(timeout=_cleanup_stage_timeout(deadline))
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        try:
            return process.wait(timeout=_cleanup_stage_timeout(deadline))
        except subprocess.TimeoutExpired as error:
            raise AudioOutputError("Broadcastify FFmpeg encoder did not stop.") from error


def _cleanup_stage_timeout(deadline: float) -> float:
    return _remaining_timeout(deadline) / 2.0


def _remaining_timeout(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def _read_encoder_error(stream: BinaryIO) -> str:
    try:
        data = stream.read(_MAX_RESPONSE_BYTES)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def _close_binary_stream(stream: BinaryIO) -> None:
    with suppress(OSError, ValueError):
        stream.close()


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
