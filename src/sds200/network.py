from __future__ import annotations

import logging
import socket
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol

from .exceptions import ScannerConnectionError
from .transport import (
    ConnectionHandler,
    DiagnosticHandler,
    LineHandler,
    TransportDiagnostic,
)

logger = logging.getLogger(__name__)
DEFAULT_UDP_PORT = 50536
MAX_DATAGRAM_SIZE = 65535
_XML_MARKER = ",<XML>,"
_FOOTER_TAGS = {"Foot", "Footer"}
_RETRYABLE_DIAGNOSTICS = {"invalid_footer", "missing_first", "sequence_gap"}


class DatagramSocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...
    def bind(self, address: tuple[str, int]) -> None: ...
    def connect(self, address: tuple[str, int]) -> None: ...
    def send(self, data: bytes) -> int: ...
    def recv(self, size: int) -> bytes: ...
    def close(self) -> None: ...


DatagramSocketFactory = Callable[[int, int], DatagramSocketLike]


def default_datagram_socket_factory(
    family: int,
    socket_type: int,
) -> DatagramSocketLike:
    return socket.socket(family, socket_type)


@dataclass(slots=True)
class _XmlSequence:
    root_tag: str
    attributes: dict[str, str]
    children: list[ET.Element] = field(default_factory=list)
    next_number: int = 1


@dataclass(slots=True)
class _MutableNetworkStatistics:
    commands_sent: int = 0
    retries_sent: int = 0
    datagrams_received: int = 0
    bytes_received: int = 0
    receive_timeouts: int = 0
    receive_errors: int = 0
    socket_opens: int = 0
    socket_reopens: int = 0
    xml_documents_completed: int = 0
    xml_fragments_dropped: int = 0
    last_receive_at: datetime | None = None
    last_diagnostic: str | None = None

    def mapping(self) -> Mapping[str, object]:
        values: dict[str, object] = {
            "commands_sent": self.commands_sent,
            "retries_sent": self.retries_sent,
            "datagrams_received": self.datagrams_received,
            "bytes_received": self.bytes_received,
            "receive_timeouts": self.receive_timeouts,
            "receive_errors": self.receive_errors,
            "socket_opens": self.socket_opens,
            "socket_reopens": self.socket_reopens,
            "xml_documents_completed": self.xml_documents_completed,
            "xml_fragments_dropped": self.xml_fragments_dropped,
            "last_receive_at": (
                self.last_receive_at.isoformat()
                if self.last_receive_at is not None
                else None
            ),
            "last_diagnostic": self.last_diagnostic,
        }
        return MappingProxyType(values)


class UdpDatagramDecoder:
    """Convert SDS200 UDP datagrams into serial-compatible protocol lines."""

    def __init__(
        self,
        *,
        diagnostic_handler: DiagnosticHandler | None = None,
        completion_handler: Callable[[str], None] | None = None,
    ) -> None:
        self._sequences: dict[str, _XmlSequence] = {}
        self._expected_xml_command: str | None = None
        self._stream_xml_command: str | None = None
        self._diagnostic_handler = diagnostic_handler
        self._completion_handler = completion_handler
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._sequences.clear()
            self._expected_xml_command = None
            self._stream_xml_command = None

    def expect_command(self, command: str) -> None:
        """Record commands whose UDP response may be a bare XML document."""
        normalized = command.rstrip("\r\n").strip()
        name, _, argument = normalized.partition(",")
        name = name.upper()

        with self._lock:
            if name == "GSI":
                self._expected_xml_command = "GSI"
            elif name == "PSI":
                if argument.strip() == "0":
                    self._stream_xml_command = None
                    if self._expected_xml_command == "PSI":
                        self._expected_xml_command = None
                else:
                    self._expected_xml_command = "PSI"
                    self._stream_xml_command = "PSI"

    def feed(self, data: bytes) -> tuple[str, ...]:
        text = data.decode("utf-8", errors="replace").strip("\x00")
        if not text:
            return ()

        with self._lock:
            upper_text = text.upper()
            marker_index = upper_text.find(_XML_MARKER)
            if marker_index > 0:
                command = text[:marker_index].strip().upper()
                payload = text[marker_index + len(_XML_MARKER) :].lstrip(
                    "\x00\r\n "
                )
                if command and payload:
                    result = self._feed_xml(command, payload)
                    self._complete_expected(command, result)
                    return result
                if command:
                    return (f"{command}{_XML_MARKER}",)

            stripped = text.lstrip("\x00\r\n ")
            if self._looks_like_xml(stripped):
                xml_command = self._expected_xml_command or self._stream_xml_command
                if xml_command is not None:
                    result = self._feed_xml(xml_command, stripped)
                    self._complete_expected(xml_command, result)
                    return result

            return self._split_lines(text)

    def _complete_expected(self, command: str, result: tuple[str, ...]) -> None:
        if not result:
            return
        if self._expected_xml_command == command:
            self._expected_xml_command = None
        if self._completion_handler is not None:
            self._completion_handler(command)

    def _diagnose(
        self,
        kind: str,
        message: str,
        *,
        command: str,
        expected_fragment: int | None = None,
        received_fragment: int | None = None,
    ) -> None:
        logger.warning("%s", message)
        if self._diagnostic_handler is None:
            return
        self._diagnostic_handler(
            TransportDiagnostic(
                kind=kind,
                message=message,
                command=command,
                expected_fragment=expected_fragment,
                received_fragment=received_fragment,
            )
        )

    @staticmethod
    def _looks_like_xml(text: str) -> bool:
        return text.startswith("<?xml") or text.startswith("<ScannerInfo")

    def _feed_xml(self, command: str, payload: str) -> tuple[str, ...]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return (f"{command}{_XML_MARKER}", *self._split_lines(payload))

        footer = self._remove_footer(root)
        if footer is None:
            return (f"{command}{_XML_MARKER}", *self._split_lines(payload))

        number = self._parse_sequence_number(command, footer)
        if number is None:
            self._sequences.pop(command, None)
            return ()

        end_of_transmission = footer.attrib.get("EOT") == "1"
        sequence = self._sequences.get(command)

        if number == 1:
            sequence = _XmlSequence(
                root_tag=root.tag,
                attributes=dict(root.attrib),
            )
            self._sequences[command] = sequence
        elif sequence is None:
            self._diagnose(
                "missing_first",
                f"Discarding {command} XML fragment {number}: fragment 1 was not received",
                command=command,
                expected_fragment=1,
                received_fragment=number,
            )
            return ()

        assert sequence is not None
        if sequence.root_tag != root.tag or number != sequence.next_number:
            self._diagnose(
                "sequence_gap",
                f"Discarding incomplete {command} XML response: expected fragment "
                f"{sequence.next_number}, got {number}",
                command=command,
                expected_fragment=sequence.next_number,
                received_fragment=number,
            )
            self._sequences.pop(command, None)
            return ()

        sequence.children.extend(list(root))
        sequence.next_number = number + 1
        if not end_of_transmission:
            return ()

        merged = ET.Element(sequence.root_tag, sequence.attributes)
        merged.extend(sequence.children)
        self._sequences.pop(command, None)
        xml = ET.tostring(merged, encoding="unicode")
        return (f"{command}{_XML_MARKER}", xml)

    @staticmethod
    def _remove_footer(root: ET.Element) -> ET.Element | None:
        for child in list(root):
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name in _FOOTER_TAGS:
                root.remove(child)
                return child
        return None

    def _parse_sequence_number(
        self,
        command: str,
        footer: ET.Element,
    ) -> int | None:
        raw_number = footer.attrib.get("No")
        try:
            number = int(raw_number) if raw_number is not None else None
        except ValueError:
            number = None
        if number is None or number <= 0:
            self._diagnose(
                "invalid_footer",
                f"Discarding {command} XML fragment with invalid Footer No={raw_number!r}",
                command=command,
                received_fragment=number,
            )
            return None
        return number

    @staticmethod
    def _split_lines(text: str) -> tuple[str, ...]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return tuple(line for line in normalized.split("\n") if line)


class UdpTransport:
    """SDS200 virtual serial control over its built-in Ethernet interface."""

    def __init__(
        self,
        host: str,
        *,
        remote_port: int = DEFAULT_UDP_PORT,
        local_host: str = "",
        local_port: int = 0,
        read_timeout: float = 0.2,
        reconnect: bool = True,
        reconnect_interval: float = 2.0,
        max_xml_retries: int = 2,
        socket_factory: DatagramSocketFactory = default_datagram_socket_factory,
    ) -> None:
        if not host.strip():
            raise ValueError("Network host must not be empty.")
        if not 1 <= remote_port <= 65535:
            raise ValueError("Remote UDP port must be between 1 and 65535.")
        if not 0 <= local_port <= 65535:
            raise ValueError("Local UDP port must be between 0 and 65535.")
        if read_timeout <= 0:
            raise ValueError("Read timeout must be greater than zero.")
        if reconnect_interval <= 0:
            raise ValueError("Reconnect interval must be greater than zero.")
        if max_xml_retries < 0:
            raise ValueError("Maximum XML retries must not be negative.")

        self.host = host
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self.read_timeout = read_timeout
        self.reconnect = reconnect
        self.reconnect_interval = reconnect_interval
        self.max_xml_retries = max_xml_retries
        self._socket_factory = socket_factory
        self._socket: DatagramSocketLike | None = None
        self._handler: LineHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._diagnostic_handler: DiagnosticHandler | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._socket_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._statistics_lock = threading.RLock()
        self._mutable_statistics = _MutableNetworkStatistics()
        self._last_xml_commands: dict[str, str] = {}
        self._xml_retry_counts: dict[str, int] = {}
        self._decoder = UdpDatagramDecoder(
            diagnostic_handler=self._handle_decoder_diagnostic,
            completion_handler=self._xml_completed,
        )

    @property
    def endpoint(self) -> str:
        return f"udp://{self.host}:{self.remote_port}"

    @property
    def connected(self) -> bool:
        with self._socket_lock:
            return self._socket is not None

    @property
    def statistics(self) -> Mapping[str, object]:
        with self._statistics_lock:
            return self._mutable_statistics.mapping()

    def set_diagnostic_handler(
        self,
        handler: DiagnosticHandler | None,
    ) -> None:
        self._diagnostic_handler = handler

    def start(
        self,
        handler: LineHandler,
        connection_handler: ConnectionHandler | None = None,
    ) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._handler = handler
        self._connection_handler = connection_handler
        self._stop.clear()
        self._open()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="sds200-udp-reader",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.read_timeout * 4))
        self._thread = None
        self._decoder.reset()
        self._last_xml_commands.clear()
        self._xml_retry_counts.clear()

    def write_command(self, command: str) -> None:
        normalized = command.rstrip("\r\n")
        if not normalized:
            raise ValueError("Command must not be empty.")
        self._remember_xml_command(normalized)
        self._send_normalized(normalized, retry=False)

    def _remember_xml_command(self, normalized: str) -> None:
        command, _, argument = normalized.partition(",")
        command = command.upper()
        self._decoder.expect_command(normalized)
        if command == "GSI":
            self._last_xml_commands[command] = normalized
            self._xml_retry_counts[command] = 0
        elif command == "PSI":
            if argument.strip() == "0":
                self._last_xml_commands.pop(command, None)
                self._xml_retry_counts.pop(command, None)
            else:
                self._last_xml_commands[command] = normalized
                self._xml_retry_counts[command] = 0

    def _send_normalized(self, normalized: str, *, retry: bool) -> None:
        data = (normalized + "\r").encode("ascii")
        with self._write_lock:
            with self._socket_lock:
                udp_socket = self._socket
            if udp_socket is None:
                raise ScannerConnectionError(
                    f"Scanner network transport is not open for {self.endpoint}."
                )
            try:
                sent = udp_socket.send(data)
            except OSError as exc:
                self._close()
                raise ScannerConnectionError(
                    f"Failed to send command to scanner at {self.endpoint}."
                ) from exc
            if sent != len(data):
                raise ScannerConnectionError(
                    f"Incomplete UDP write to scanner at {self.endpoint}."
                )
        with self._statistics_lock:
            self._mutable_statistics.commands_sent += 1
            if retry:
                self._mutable_statistics.retries_sent += 1
        logger.debug("TX%s %s", " RETRY" if retry else "", normalized)

    def _open(self) -> None:
        udp_socket: DatagramSocketLike | None = None
        try:
            udp_socket = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.settimeout(self.read_timeout)
            udp_socket.bind((self.local_host, self.local_port))
            udp_socket.connect((self.host, self.remote_port))
        except OSError as exc:
            if udp_socket is not None:
                with suppress(OSError):
                    udp_socket.close()
            with self._socket_lock:
                self._socket = None
            with self._statistics_lock:
                self._mutable_statistics.receive_errors += 1
            raise ScannerConnectionError(
                f"Could not open scanner UDP transport to {self.endpoint}."
            ) from exc

        assert udp_socket is not None
        with self._socket_lock:
            self._socket = udp_socket
        with self._statistics_lock:
            was_reopen = self._mutable_statistics.socket_opens > 0
            self._mutable_statistics.socket_opens += 1
            if was_reopen:
                self._mutable_statistics.socket_reopens += 1
        logger.info("Opened scanner network transport to %s", self.endpoint)
        self._notify_connection(True)

    def _close(self) -> None:
        with self._socket_lock:
            udp_socket, self._socket = self._socket, None
        if udp_socket is None:
            return
        try:
            udp_socket.close()
        except OSError:
            logger.debug("Error while closing UDP socket", exc_info=True)
        self._notify_connection(False)

    def _notify_connection(self, connected: bool) -> None:
        if self._connection_handler is None:
            return
        try:
            self._connection_handler(connected)
        except Exception:
            logger.exception("Unhandled exception in connection callback")

    def _emit_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        if self._diagnostic_handler is None:
            return
        try:
            self._diagnostic_handler(diagnostic)
        except Exception:
            logger.exception("Unhandled exception in transport diagnostic callback")

    def _handle_decoder_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        with self._statistics_lock:
            self._mutable_statistics.xml_fragments_dropped += 1
            self._mutable_statistics.last_diagnostic = diagnostic.message
        self._emit_diagnostic(diagnostic)

        command = diagnostic.command
        if command is None or diagnostic.kind not in _RETRYABLE_DIAGNOSTICS:
            return
        normalized = self._last_xml_commands.get(command)
        if normalized is None:
            return
        attempts = self._xml_retry_counts.get(command, 0)
        if attempts >= self.max_xml_retries:
            exhausted = TransportDiagnostic(
                kind="retry_exhausted",
                command=command,
                message=(
                    f"No more automatic {command} retries after {attempts} attempts"
                ),
            )
            with self._statistics_lock:
                self._mutable_statistics.last_diagnostic = exhausted.message
            self._emit_diagnostic(exhausted)
            return

        self._xml_retry_counts[command] = attempts + 1
        self._decoder.expect_command(normalized)
        try:
            self._send_normalized(normalized, retry=True)
        except ScannerConnectionError as exc:
            self._emit_diagnostic(
                TransportDiagnostic(
                    kind="retry_failed",
                    command=command,
                    message=f"Automatic {command} retry failed: {exc}",
                )
            )

    def _xml_completed(self, command: str) -> None:
        self._xml_retry_counts[command] = 0
        with self._statistics_lock:
            self._mutable_statistics.xml_documents_completed += 1

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            if not self.connected:
                if not self.reconnect:
                    return
                try:
                    self._open()
                except ScannerConnectionError:
                    logger.warning(
                        "UDP reopen failed for %s; retrying in %.1f seconds",
                        self.endpoint,
                        self.reconnect_interval,
                    )
                    self._stop.wait(self.reconnect_interval)
                    continue

            with self._socket_lock:
                udp_socket = self._socket
            assert udp_socket is not None
            try:
                datagram = udp_socket.recv(MAX_DATAGRAM_SIZE)
            except TimeoutError:
                with self._statistics_lock:
                    self._mutable_statistics.receive_timeouts += 1
                continue
            except OSError:
                if self._stop.is_set():
                    return
                with self._statistics_lock:
                    self._mutable_statistics.receive_errors += 1
                logger.warning("Scanner UDP socket failed for %s", self.endpoint)
                self._close()
                self._decoder.reset()
                continue

            if self._stop.is_set():
                return
            if not datagram:
                continue

            with self._statistics_lock:
                self._mutable_statistics.datagrams_received += 1
                self._mutable_statistics.bytes_received += len(datagram)
                self._mutable_statistics.last_receive_at = datetime.now(UTC)
            logger.debug("RX UDP datagram %r", datagram)
            for line in self._decoder.feed(datagram):
                logger.debug("RX %s", line)
                if self._handler is not None:
                    self._handler(line)
