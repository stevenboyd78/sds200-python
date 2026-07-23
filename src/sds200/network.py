from __future__ import annotations

import logging
import socket
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol

from .exceptions import ScannerConnectionError
from .transport import ConnectionHandler, LineHandler

logger = logging.getLogger(__name__)
DEFAULT_UDP_PORT = 50536
MAX_DATAGRAM_SIZE = 65535
_XML_MARKER = ",<XML>,"
_FOOTER_TAGS = {"Foot", "Footer"}


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


class UdpDatagramDecoder:
    """Convert SDS200 UDP datagrams into serial-compatible protocol lines.

    Normal command responses are emitted immediately. Network XML responses can
    be split across self-contained XML datagrams. Those fragments are combined
    using their Footer ``No`` and ``EOT`` attributes before being emitted to the
    package's existing XML stream parser.
    """

    def __init__(self) -> None:
        self._sequences: dict[str, _XmlSequence] = {}
        self._expected_xml_command: str | None = None
        self._stream_xml_command: str | None = None
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
                xml_command = (
                    self._expected_xml_command or self._stream_xml_command
                )
                if xml_command is not None:
                    result = self._feed_xml(xml_command, stripped)
                    self._complete_expected(xml_command, result)
                    return result

            return self._split_lines(text)

    def _complete_expected(self, command: str, result: tuple[str, ...]) -> None:
        if result and self._expected_xml_command == command:
            self._expected_xml_command = None

    @staticmethod
    def _looks_like_xml(text: str) -> bool:
        return text.startswith("<?xml") or text.startswith("<ScannerInfo")

    def _feed_xml(self, command: str, payload: str) -> tuple[str, ...]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            # Some firmware responses do not use the network Footer extension.
            # Passing their lines through preserves the serial protocol behavior
            # and lets XmlResponseAssembler collect the document normally.
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
            logger.warning(
                "Discarding %s XML fragment %d because fragment 1 was not received",
                command,
                number,
            )
            return ()

        assert sequence is not None
        if sequence.root_tag != root.tag or number != sequence.next_number:
            logger.warning(
                "Discarding incomplete %s XML response: expected fragment %d, got %d",
                command,
                sequence.next_number,
                number,
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

    @staticmethod
    def _parse_sequence_number(
        command: str,
        footer: ET.Element,
    ) -> int | None:
        raw_number = footer.attrib.get("No")
        try:
            number = int(raw_number) if raw_number is not None else None
        except ValueError:
            number = None
        if number is None or number <= 0:
            logger.warning(
                "Discarding %s XML fragment with invalid Footer No=%r",
                command,
                raw_number,
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

        self.host = host
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self.read_timeout = read_timeout
        self.reconnect = reconnect
        self.reconnect_interval = reconnect_interval
        self._socket_factory = socket_factory
        self._socket: DatagramSocketLike | None = None
        self._handler: LineHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._socket_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._decoder = UdpDatagramDecoder()

    @property
    def endpoint(self) -> str:
        return f"udp://{self.host}:{self.remote_port}"

    @property
    def connected(self) -> bool:
        with self._socket_lock:
            return self._socket is not None

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

    def write_command(self, command: str) -> None:
        normalized = command.rstrip("\r\n")
        if not normalized:
            raise ValueError("Command must not be empty.")
        data = (normalized + "\r").encode("ascii")

        with self._write_lock:
            self._decoder.expect_command(normalized)
            with self._socket_lock:
                udp_socket = self._socket
            if udp_socket is None:
                self._decoder.reset()
                raise ScannerConnectionError(
                    f"Scanner network transport is not open for {self.endpoint}."
                )
            try:
                sent = udp_socket.send(data)
            except OSError as exc:
                self._decoder.reset()
                self._close()
                raise ScannerConnectionError(
                    f"Failed to send command to scanner at {self.endpoint}."
                ) from exc
            if sent != len(data):
                self._decoder.reset()
                raise ScannerConnectionError(
                    f"Incomplete UDP write to scanner at {self.endpoint}."
                )
        logger.debug("TX %s", normalized)

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
            raise ScannerConnectionError(
                f"Could not open scanner UDP transport to {self.endpoint}."
            ) from exc

        assert udp_socket is not None
        with self._socket_lock:
            self._socket = udp_socket
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
                continue
            except OSError:
                if self._stop.is_set():
                    return
                logger.warning("Scanner UDP socket failed for %s", self.endpoint)
                self._close()
                self._decoder.reset()
                continue

            if self._stop.is_set():
                return
            if not datagram:
                continue

            logger.debug("RX UDP datagram %r", datagram)
            for line in self._decoder.feed(datagram):
                logger.debug("RX %s", line)
                if self._handler is not None:
                    self._handler(line)
