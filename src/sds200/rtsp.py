from __future__ import annotations

import socket
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from ipaddress import IPv4Address, ip_address
from types import MappingProxyType
from typing import Protocol

from .exceptions import ProtocolError, ScannerConnectionError

DEFAULT_RTSP_PORT = 554
DEFAULT_AUDIO_PATH = "/au:scanner.au"
DEFAULT_USER_AGENT = "sds200-python/0.11"


class RtspProtocolError(ProtocolError):
    """An RTSP response or SDP description violates the expected protocol."""


class RtspStatusError(RtspProtocolError):
    """The RTSP server returned a non-success status."""

    def __init__(self, method: str, status_code: int, reason: str) -> None:
        self.method = method
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"RTSP {method} failed with {status_code} {reason}.")


class StreamSocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...

    def sendall(self, data: bytes) -> None: ...

    def recv(self, size: int) -> bytes: ...

    def close(self) -> None: ...


StreamConnectionFactory = Callable[[tuple[str, int], float], StreamSocketLike]


def default_stream_connection_factory(
    address: tuple[str, int],
    timeout: float,
) -> StreamSocketLike:
    return socket.create_connection(address, timeout=timeout)


@dataclass(frozen=True, slots=True)
class RtspResponse:
    status_code: int
    reason: str
    headers: Mapping[str, str]
    body: bytes = b""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


@dataclass(frozen=True, slots=True)
class SdpAudioDescription:
    control: str
    payload_types: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RtpTransportInfo:
    """RTP sender parameters negotiated by the SDS200 RTSP SETUP response."""

    source: str
    server_port: int
    ssrc: int | None = None


def parse_rtp_transport(value: str, *, client_port: int) -> RtpTransportInfo:
    """Parse and validate the scanner-specific RTSP Transport response header."""
    fields = [field.strip() for field in value.split(";") if field.strip()]
    if not fields or fields[0].upper() != "RTP/AVP":
        raise RtspProtocolError("RTSP SETUP returned an unsupported Transport profile.")

    flags: set[str] = set()
    parameters: dict[str, str] = {}
    for field in fields[1:]:
        name, separator, raw_value = field.partition("=")
        name = name.strip().lower()
        if not separator:
            flags.add(name)
            continue
        if not name or not raw_value.strip():
            raise RtspProtocolError("RTSP SETUP returned an invalid Transport parameter.")
        parameters[name] = raw_value.strip()

    if "unicast" not in flags:
        raise RtspProtocolError("RTSP SETUP did not negotiate unicast RTP.")

    negotiated_client_port = _parse_single_port(
        parameters.get("client_port"),
        name="client_port",
    )
    if negotiated_client_port != client_port:
        raise RtspProtocolError(
            "RTSP SETUP returned a client_port that does not match the bound RTP port."
        )

    source_value = parameters.get("source")
    if source_value is None:
        raise RtspProtocolError("RTSP SETUP response does not contain an RTP source.")
    try:
        source_address = ip_address(source_value)
    except ValueError as exc:
        raise RtspProtocolError("RTSP SETUP returned an invalid RTP source address.") from exc
    if not isinstance(source_address, IPv4Address):
        raise RtspProtocolError("RTSP SETUP returned a non-IPv4 RTP source address.")

    server_port = _parse_single_port(
        parameters.get("server_port"),
        name="server_port",
    )

    raw_ssrc = parameters.get("ssrc")
    ssrc: int | None = None
    if raw_ssrc is not None:
        base = 16 if raw_ssrc.lower().startswith("0x") or any(
            character in "abcdefABCDEF" for character in raw_ssrc
        ) else 10
        try:
            ssrc = int(raw_ssrc, base)
        except ValueError as exc:
            raise RtspProtocolError("RTSP SETUP returned an invalid RTP SSRC.") from exc
        if not 0 <= ssrc <= 0xFFFFFFFF:
            raise RtspProtocolError("RTSP SETUP returned an out-of-range RTP SSRC.")

    return RtpTransportInfo(
        source=str(source_address),
        server_port=server_port,
        ssrc=ssrc,
    )


def _parse_single_port(value: str | None, *, name: str) -> int:
    if value is None or "-" in value:
        raise RtspProtocolError(f"RTSP SETUP did not return a single {name} value.")
    try:
        port = int(value)
    except ValueError as exc:
        raise RtspProtocolError(f"RTSP SETUP returned an invalid {name} value.") from exc
    if not 1 <= port <= 65535:
        raise RtspProtocolError(f"RTSP SETUP returned an out-of-range {name} value.")
    return port


def parse_sdp_audio(body: bytes) -> SdpAudioDescription:
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RtspProtocolError("RTSP SDP body is not ASCII.") from exc

    audio_payload_types: tuple[int, ...] | None = None
    control: str | None = None
    in_audio_section = False

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("m="):
            fields = line[2:].split()
            in_audio_section = bool(fields and fields[0] == "audio")
            if in_audio_section:
                if len(fields) < 4 or fields[2] != "RTP/AVP":
                    raise RtspProtocolError("SDS200 SDP has an unsupported audio media line.")
                try:
                    audio_payload_types = tuple(int(value) for value in fields[3:])
                except ValueError as exc:
                    raise RtspProtocolError("SDS200 SDP has an invalid payload type.") from exc
            continue
        if in_audio_section and line.startswith("a=control:"):
            control = line.removeprefix("a=control:").strip()

    if audio_payload_types is None:
        raise RtspProtocolError("SDS200 SDP does not contain an audio media section.")
    if 0 not in audio_payload_types:
        raise RtspProtocolError("SDS200 SDP does not advertise PCMU payload type 0.")
    if not control:
        raise RtspProtocolError("SDS200 SDP does not contain an audio control track.")

    return SdpAudioDescription(control=control, payload_types=audio_payload_types)


class RtspClient:
    """Minimal RTSP client for the SDS200 scanner audio service."""

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_RTSP_PORT,
        path: str = DEFAULT_AUDIO_PATH,
        timeout: float = 5.0,
        user_agent: str = DEFAULT_USER_AGENT,
        connection_factory: StreamConnectionFactory = default_stream_connection_factory,
    ) -> None:
        if not host.strip():
            raise ValueError("RTSP host must not be empty.")
        if not 1 <= port <= 65535:
            raise ValueError("RTSP port must be between 1 and 65535.")
        if not path.startswith("/"):
            raise ValueError("RTSP path must begin with '/'.")
        if timeout <= 0:
            raise ValueError("RTSP timeout must be greater than zero.")

        self.host = host
        self.port = port
        self.path = path
        self.timeout = timeout
        self.user_agent = user_agent
        self._connection_factory = connection_factory
        self._socket: StreamSocketLike | None = None
        self._buffer = bytearray()
        self._cseq = 0
        self._content_base: str | None = None
        self._audio: SdpAudioDescription | None = None
        self._session: str | None = None
        self._transport: RtpTransportInfo | None = None

    @property
    def endpoint(self) -> str:
        return self._aggregate_uri

    @property
    def session(self) -> str | None:
        return self._session

    @property
    def transport(self) -> RtpTransportInfo | None:
        return self._transport

    @property
    def _aggregate_uri(self) -> str:
        authority = self.host if self.port == DEFAULT_RTSP_PORT else f"{self.host}:{self.port}"
        return f"rtsp://{authority}{self.path}"

    def connect(self) -> None:
        if self._socket is not None:
            return
        try:
            stream = self._connection_factory((self.host, self.port), self.timeout)
            stream.settimeout(self.timeout)
        except OSError as exc:
            raise ScannerConnectionError(
                f"Could not connect to SDS200 RTSP service at {self.endpoint}."
            ) from exc
        self._socket = stream
        self._buffer.clear()
        self._cseq = 0

    def close(self) -> None:
        stream, self._socket = self._socket, None
        if stream is not None:
            with suppress(OSError):
                stream.close()
        self._buffer.clear()
        self._content_base = None
        self._audio = None
        self._session = None
        self._transport = None

    def start(self, client_port: int) -> RtpTransportInfo:
        if not 1 <= client_port <= 65535:
            raise ValueError("RTP client port must be between 1 and 65535.")
        self.connect()
        self.options()
        self.describe()
        self.setup(client_port)
        self.play()
        assert self._transport is not None
        return self._transport

    def options(self) -> RtspResponse:
        return self._request("OPTIONS", self._aggregate_uri)

    def describe(self) -> SdpAudioDescription:
        response = self._request(
            "DESCRIBE",
            self._aggregate_uri,
            headers={"Accept": "application/sdp"},
        )
        content_type = response.header("content-type")
        if content_type is None or content_type.split(";", 1)[0].strip() != "application/sdp":
            raise RtspProtocolError("RTSP DESCRIBE response is not application/sdp.")
        self._content_base = response.header("content-base") or f"{self._aggregate_uri}/"
        self._audio = parse_sdp_audio(response.body)
        return self._audio

    def setup(self, client_port: int) -> RtspResponse:
        if self._audio is None:
            raise RtspProtocolError("RTSP DESCRIBE must succeed before SETUP.")
        response = self._request(
            "SETUP",
            self._track_uri(self._audio.control),
            headers={
                "Transport": f"RTP/AVP;unicast;client_port={client_port}",
            },
        )
        session = response.header("session")
        if session is None:
            raise RtspProtocolError("RTSP SETUP response does not contain a Session header.")
        self._session = session.split(";", 1)[0].strip()
        if not self._session:
            raise RtspProtocolError("RTSP SETUP returned an empty session identifier.")
        transport = response.header("transport")
        if transport is None:
            raise RtspProtocolError("RTSP SETUP response does not contain a Transport header.")
        self._transport = parse_rtp_transport(transport, client_port=client_port)
        return response

    def play(self) -> RtspResponse:
        return self._session_request(
            "PLAY",
            headers={"Range": "npt=0.000-"},
        )

    def get_parameter(self) -> RtspResponse:
        return self._session_request("GET_PARAMETER")

    def teardown(self) -> RtspResponse:
        return self._session_request("TEARDOWN")

    def _session_request(
        self,
        method: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RtspResponse:
        if self._session is None:
            raise RtspProtocolError(f"RTSP session is not established for {method}.")
        request_headers = {"Session": self._session}
        if headers is not None:
            request_headers.update(headers)
        return self._request(method, f"{self._aggregate_uri}/", headers=request_headers)

    def _track_uri(self, control: str) -> str:
        if control.startswith("rtsp://"):
            return control
        base = self._content_base or f"{self._aggregate_uri}/"
        return f"{base.rstrip('/')}/{control.lstrip('/')}"

    def _request(
        self,
        method: str,
        uri: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RtspResponse:
        stream = self._socket
        if stream is None:
            raise ScannerConnectionError("RTSP client is not connected.")

        self._cseq += 1
        cseq = self._cseq
        request_headers = {
            "CSeq": str(cseq),
            "User-Agent": self.user_agent,
        }
        if headers is not None:
            request_headers.update(headers)
        payload = (
            f"{method} {uri} RTSP/1.0\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in request_headers.items())
            + "\r\n"
        ).encode("ascii")

        try:
            stream.sendall(payload)
            response = self._read_response(stream)
        except OSError as exc:
            raise ScannerConnectionError(
                f"RTSP {method} failed while communicating with {self.endpoint}."
            ) from exc

        response_cseq = response.header("cseq")
        if response_cseq is None or response_cseq.strip() != str(cseq):
            raise RtspProtocolError(
                f"RTSP {method} response CSeq {response_cseq!r} does not match {cseq}."
            )
        if response.status_code != 200:
            raise RtspStatusError(method, response.status_code, response.reason)
        return response

    def _read_response(self, stream: StreamSocketLike) -> RtspResponse:
        marker = b"\r\n\r\n"
        while marker not in self._buffer:
            self._receive(stream)

        header_bytes, remainder = bytes(self._buffer).split(marker, 1)
        status_code, reason, headers = self._parse_headers(header_bytes)
        raw_length = headers.get("content-length", "0")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise RtspProtocolError(
                f"RTSP response has invalid Content-Length {raw_length!r}."
            ) from exc
        if content_length < 0:
            raise RtspProtocolError("RTSP response has a negative Content-Length.")

        while len(remainder) < content_length:
            self._receive(stream)
            _, remainder = bytes(self._buffer).split(marker, 1)

        body = remainder[:content_length]
        trailing = remainder[content_length:]
        self._buffer = bytearray(trailing)
        return RtspResponse(
            status_code=status_code,
            reason=reason,
            headers=MappingProxyType(headers),
            body=body,
        )

    def _receive(self, stream: StreamSocketLike) -> None:
        chunk = stream.recv(4096)
        if not chunk:
            raise ScannerConnectionError("RTSP connection closed while reading a response.")
        self._buffer.extend(chunk)

    @staticmethod
    def _parse_headers(header_bytes: bytes) -> tuple[int, str, dict[str, str]]:
        try:
            lines = header_bytes.decode("ascii").split("\r\n")
        except UnicodeDecodeError as exc:
            raise RtspProtocolError("RTSP response headers are not ASCII.") from exc
        if not lines:
            raise RtspProtocolError("RTSP response is missing a status line.")
        status_fields = lines[0].split(" ", 2)
        if len(status_fields) < 2 or status_fields[0] != "RTSP/1.0":
            raise RtspProtocolError(f"Invalid RTSP status line {lines[0]!r}.")
        try:
            status_code = int(status_fields[1])
        except ValueError as exc:
            raise RtspProtocolError(f"Invalid RTSP status code {status_fields[1]!r}.") from exc
        reason = status_fields[2] if len(status_fields) == 3 else ""

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            name, separator, value = line.partition(":")
            if not separator or not name.strip():
                raise RtspProtocolError(f"Invalid RTSP header line {line!r}.")
            headers[name.strip().lower()] = value.strip()
        return status_code, reason, headers
