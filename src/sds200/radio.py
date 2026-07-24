from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Self, TypeVar

from .commands import (
    Command,
    GetChargeStatus,
    GetFirmware,
    GetModel,
    GetScannerInfo,
    GetSquelch,
    GetStatus,
    GetVolume,
    SetSquelch,
    SetVolume,
    StartScannerInfoPush,
)
from .device import choose_scanner
from .events import EventBus
from .exceptions import (
    CommandTimeoutError,
    ProtocolError,
    SDS200Error,
    UnsupportedScannerFeatureError,
    UnsupportedScannerModelError,
)
from .fallback import FallbackTransport, TransportCandidate
from .models import (
    ChargeStatus,
    FirmwareResponse,
    HealthSummary,
    ModelResponse,
    Packet,
    RadioEvent,
    RadioHealth,
    ScannerInfo,
    StatusResponse,
)
from .network import (
    DEFAULT_UDP_PORT,
    DatagramSocketFactory,
    UdpTransport,
)
from .parser import PacketParser
from .profiles import ConnectionProfile, TransportPreference
from .reliability import HealthHistory, HealthThresholds, ReconnectPolicy
from .scanner import (
    ScannerCapabilities,
    ScannerModel,
    capabilities_for_model,
    normalize_model_name,
)
from .state import RadioState, RadioStateSnapshot, StateChange
from .trace import TrafficTrace
from .transport import (
    ControlTransport,
    DiagnosticControlTransport,
    SerialFactory,
    SerialTransport,
    StatisticalControlTransport,
    TransportDiagnostic,
)
from .xml_protocol import ScannerInfoParser, XmlResponseAssembler

logger = logging.getLogger(__name__)
T = TypeVar("T")


class SDSScanner:
    def __init__(
        self,
        port: str | Path | None = None,
        *,
        transport: ControlTransport | None = None,
        baudrate: int = 115200,
        reconnect: bool = True,
        reconnect_policy: ReconnectPolicy | None = None,
        serial_factory: SerialFactory | None = None,
        trace_path: str | Path | None = None,
        health_history_limit: int = 100,
        health_thresholds: HealthThresholds | None = None,
        expected_model: ScannerModel | str | None = None,
    ) -> None:
        if port is not None and transport is not None:
            raise ValueError("Supply either port or transport, not both.")
        if port is None and transport is None:
            raise ValueError("A serial port or control transport is required.")
        if transport is not None and serial_factory is not None:
            raise ValueError("serial_factory cannot be used with a custom transport.")

        normalized_expected = (
            normalize_model_name(expected_model) if expected_model is not None else None
        )
        if expected_model is not None and normalized_expected is None:
            raise ValueError(f"Unsupported SDS-series scanner model: {expected_model!r}")
        self.expected_model = normalized_expected

        self.transport: ControlTransport
        if transport is not None:
            self.transport = transport
        elif serial_factory is None:
            assert port is not None
            self.transport = SerialTransport(
                port,
                baudrate=baudrate,
                reconnect=reconnect,
                reconnect_policy=reconnect_policy,
            )
        else:
            assert port is not None
            self.transport = SerialTransport(
                port,
                baudrate=baudrate,
                reconnect=reconnect,
                reconnect_policy=reconnect_policy,
                serial_factory=serial_factory,
            )

        self.parser = PacketParser()
        self.xml_parser = ScannerInfoParser()
        self.xml_assembler = XmlResponseAssembler()
        self.events = EventBus()
        if isinstance(self.transport, DiagnosticControlTransport):
            self.transport.set_diagnostic_handler(self._transport_diagnostic)
        self.state = RadioState()
        self.trace = TrafficTrace(trace_path)
        self._responses: dict[str, queue.Queue[object]] = {}
        self._response_lock = threading.RLock()
        self._closed = threading.Event()
        self._closed.set()
        self._psi_interval_ms: int | None = None
        self._health_lock = threading.RLock()
        self._connection_events = 0
        self._last_connection_state: bool | None = None
        self._last_connected_at: datetime | None = None
        self._last_disconnected_at: datetime | None = None
        self._last_response_at: datetime | None = None
        self._last_state_at: datetime | None = None
        self._model: ScannerModel | None = None
        self._firmware: str | None = None
        self.health_history = HealthHistory(health_history_limit)
        self.health_thresholds = health_thresholds or HealthThresholds()

    @classmethod
    def auto(
        cls,
        *,
        baudrate: int = 115200,
        reconnect: bool = True,
        reconnect_policy: ReconnectPolicy | None = None,
        serial_factory: SerialFactory | None = None,
        trace_path: str | Path | None = None,
        health_history_limit: int = 100,
        model: ScannerModel | str | None = None,
    ) -> Self:
        return cls(
            choose_scanner(model=model),
            baudrate=baudrate,
            reconnect=reconnect,
            reconnect_policy=reconnect_policy,
            serial_factory=serial_factory,
            trace_path=trace_path,
            health_history_limit=health_history_limit,
            expected_model=model,
        )

    @classmethod
    def network(
        cls,
        host: str,
        *,
        remote_port: int = DEFAULT_UDP_PORT,
        local_host: str = "",
        local_port: int = 0,
        reconnect: bool = True,
        reconnect_policy: ReconnectPolicy | None = None,
        socket_factory: DatagramSocketFactory | None = None,
        max_xml_retries: int = 2,
        trace_path: str | Path | None = None,
        health_history_limit: int = 100,
    ) -> Self:
        if socket_factory is None:
            transport = UdpTransport(
                host,
                remote_port=remote_port,
                local_host=local_host,
                local_port=local_port,
                reconnect=reconnect,
                reconnect_policy=reconnect_policy,
                max_xml_retries=max_xml_retries,
            )
        else:
            transport = UdpTransport(
                host,
                remote_port=remote_port,
                local_host=local_host,
                local_port=local_port,
                reconnect=reconnect,
                reconnect_policy=reconnect_policy,
                max_xml_retries=max_xml_retries,
                socket_factory=socket_factory,
            )
        return cls.from_transport(
            transport,
            trace_path=trace_path,
            health_history_limit=health_history_limit,
            expected_model="SDS200",
        )

    @classmethod
    def from_profile(
        cls,
        profile: ConnectionProfile,
        *,
        preference: TransportPreference | None = None,
        baudrate: int = 115200,
        serial_factory: SerialFactory | None = None,
        socket_factory: DatagramSocketFactory | None = None,
        max_xml_retries: int = 2,
        reconnect_policy: ReconnectPolicy | None = None,
        trace_path: str | Path | None = None,
        health_history_limit: int = 100,
    ) -> Self:
        if profile.kind == "serial":
            if preference is not None:
                raise ValueError("--prefer only applies to fallback profiles.")
            serial_port = profile.port
            assert serial_port is not None
            return cls(
                serial_port,
                baudrate=baudrate,
                reconnect_policy=reconnect_policy,
                serial_factory=serial_factory,
                trace_path=trace_path,
                health_history_limit=health_history_limit,
                expected_model=profile.model,
            )
        if profile.kind == "network":
            if preference is not None:
                raise ValueError("--prefer only applies to fallback profiles.")
            network_host = profile.host
            assert network_host is not None
            return cls.network(
                network_host,
                remote_port=profile.udp_port,
                local_host=profile.bind_address,
                local_port=profile.bind_port,
                socket_factory=socket_factory,
                max_xml_retries=max_xml_retries,
                reconnect_policy=reconnect_policy,
                trace_path=trace_path,
                health_history_limit=health_history_limit,
            )

        serial_port = profile.port
        network_host = profile.host
        assert serial_port is not None
        assert network_host is not None
        resolved_preference = preference or profile.preference

        def serial_transport() -> ControlTransport:
            if serial_factory is None:
                return SerialTransport(
                    serial_port,
                    baudrate=baudrate,
                    reconnect=False,
                )
            return SerialTransport(
                serial_port,
                baudrate=baudrate,
                reconnect=False,
                serial_factory=serial_factory,
            )

        def network_transport() -> ControlTransport:
            if socket_factory is None:
                return UdpTransport(
                    network_host,
                    remote_port=profile.udp_port,
                    local_host=profile.bind_address,
                    local_port=profile.bind_port,
                    reconnect=False,
                    max_xml_retries=max_xml_retries,
                )
            return UdpTransport(
                network_host,
                remote_port=profile.udp_port,
                local_host=profile.bind_address,
                local_port=profile.bind_port,
                reconnect=False,
                max_xml_retries=max_xml_retries,
                socket_factory=socket_factory,
            )

        candidates = {
            "serial": TransportCandidate(
                name="serial",
                endpoint=serial_port,
                factory=serial_transport,
            ),
            "network": TransportCandidate(
                name="network",
                endpoint=f"udp://{network_host}:{profile.udp_port}",
                factory=network_transport,
            ),
        }
        alternate: TransportPreference = (
            "network" if resolved_preference == "serial" else "serial"
        )
        transport = FallbackTransport(
            (candidates[resolved_preference], candidates[alternate]),
            reconnect_policy=reconnect_policy,
        )
        return cls.from_transport(
            transport,
            trace_path=trace_path,
            health_history_limit=health_history_limit,
            expected_model=profile.model,
        )

    @classmethod
    def from_transport(
        cls,
        transport: ControlTransport,
        *,
        trace_path: str | Path | None = None,
        health_history_limit: int = 100,
        health_thresholds: HealthThresholds | None = None,
        expected_model: ScannerModel | str | None = None,
    ) -> Self:
        return cls(
            transport=transport,
            trace_path=trace_path,
            health_history_limit=health_history_limit,
            health_thresholds=health_thresholds,
            expected_model=expected_model,
        )

    @property
    def model(self) -> ScannerModel | None:
        return self._model

    @property
    def capabilities(self) -> ScannerCapabilities | None:
        return capabilities_for_model(self._model) if self._model is not None else None

    @property
    def endpoint(self) -> str:
        return self.transport.endpoint

    @property
    def port(self) -> str:
        """Backward-compatible alias for the active transport endpoint."""
        return self.endpoint

    @property
    def connected(self) -> bool:
        return self.transport.connected

    @property
    def psi_active(self) -> bool:
        return self._psi_interval_ms is not None

    @property
    def psi_interval_ms(self) -> int | None:
        return self._psi_interval_ms

    def connect(self) -> None:
        self._closed.clear()
        try:
            self.transport.start(self._receive_line, self._connection_changed)
        except Exception:
            self._closed.set()
            raise

    def close(self) -> None:
        if self.psi_active and self.connected:
            with suppress(SDS200Error, OSError, ValueError):
                self.stop_scanner_info_push()
        self._psi_interval_ms = None
        self.transport.stop()
        self._closed.set()

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def wait(self) -> None:
        try:
            while not self._closed.wait(3600):
                pass
        except KeyboardInterrupt:
            return

    def on_packet(self, callback: Callable[[Packet], None]) -> Callable[[], None]:
        return self.events.subscribe("packet", callback)

    def on_response(self, callback: Callable[[object], None]) -> Callable[[], None]:
        return self.events.subscribe("response", callback)

    def on_state(
        self,
        callback: Callable[[RadioStateSnapshot], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("state", callback)

    def on_state_change(
        self,
        callback: Callable[[StateChange], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("state_change", callback)

    def on_connection(self, callback: Callable[[bool], None]) -> Callable[[], None]:
        return self.events.subscribe("connection", callback)

    def on_diagnostic(
        self,
        callback: Callable[[TransportDiagnostic], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("diagnostic", callback)

    def on_event(
        self,
        callback: Callable[[RadioEvent], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("event", callback)

    def health_summary(self) -> HealthSummary:
        return self.health_history.summary()

    def send(self, command: str) -> None:
        self.trace.tx(command)
        self.transport.write_command(command)

    def command(self, command: str, *, timeout: float = 2.0) -> object:
        return self._wait_for_response(
            command.split(",", 1)[0].strip().upper(),
            command,
            timeout,
        )

    def execute(self, command: Command[T], *, timeout: float = 2.0) -> T:
        response = self._wait_for_response(
            command.response_command,
            command.wire,
            timeout,
        )
        return command.parse_response(response)

    def get_model(self, *, timeout: float = 2.0) -> ScannerModel:
        reported_model = self.execute(GetModel(), timeout=timeout)
        model = normalize_model_name(reported_model)
        if model is None:
            raise UnsupportedScannerModelError(
                f"Scanner reported unsupported model {reported_model!r}."
            )
        if self.expected_model is not None and model != self.expected_model:
            raise UnsupportedScannerModelError(
                f"Expected {self.expected_model}, but connected scanner reported {model}."
            )
        with self._health_lock:
            self._model = model
        return model

    def get_firmware(self, *, timeout: float = 2.0) -> str:
        return self.execute(GetFirmware(), timeout=timeout)

    def get_volume(self, *, timeout: float = 2.0) -> int:
        return self.execute(GetVolume(), timeout=timeout)

    def _model_capabilities(self, *, timeout: float) -> ScannerCapabilities:
        model = self._model or self.get_model(timeout=timeout)
        return capabilities_for_model(model)

    def set_volume(self, level: int, *, timeout: float = 2.0) -> None:
        SetVolume(level)
        capabilities = self._model_capabilities(timeout=timeout)
        self.execute(
            SetVolume(level, maximum=capabilities.maximum_volume),
            timeout=timeout,
        )

    def get_squelch(self, *, timeout: float = 2.0) -> int:
        return self.execute(GetSquelch(), timeout=timeout)

    def set_squelch(self, level: int, *, timeout: float = 2.0) -> None:
        SetSquelch(level)
        capabilities = self._model_capabilities(timeout=timeout)
        self.execute(
            SetSquelch(level, maximum=capabilities.maximum_squelch),
            timeout=timeout,
        )

    def get_charge_status(self, *, timeout: float = 2.0) -> ChargeStatus:
        capabilities = self._model_capabilities(timeout=timeout)
        if not capabilities.charge_status:
            raise UnsupportedScannerFeatureError(
                f"{capabilities.model} does not provide handheld charge status."
            )
        return self.execute(GetChargeStatus(), timeout=timeout)

    def get_status(self, *, timeout: float = 2.0) -> StatusResponse:
        return self.execute(GetStatus(), timeout=timeout)

    def get_scanner_info(self, *, timeout: float = 3.0) -> ScannerInfo:
        return self.execute(GetScannerInfo(), timeout=timeout)

    def _create_health(
        self,
        *,
        latency_ms: float | None,
        error: str | None = None,
        model: str | None = None,
        firmware: str | None = None,
    ) -> RadioHealth:
        statistics = (
            self.transport.statistics
            if isinstance(self.transport, StatisticalControlTransport)
            else None
        )
        with self._health_lock:
            health = RadioHealth.create(
                endpoint=self.endpoint,
                connected=self.connected,
                model=model if model is not None else self._model,
                firmware=firmware if firmware is not None else self._firmware,
                latency_ms=latency_ms,
                status=self.health_thresholds.classify(
                    connected=self.connected,
                    latency_ms=latency_ms,
                    error=error,
                ),
                connection_events=self._connection_events,
                last_connected_at=self._last_connected_at,
                last_disconnected_at=self._last_disconnected_at,
                last_response_at=self._last_response_at,
                last_state_at=self._last_state_at,
                psi_active=self.psi_active,
                psi_interval_ms=self.psi_interval_ms,
                error=error,
                statistics=statistics,
            )
        return self.health_history.record(health)

    def health_snapshot(self, *, error: str | None = None) -> RadioHealth:
        return self._create_health(latency_ms=None, error=error)

    def health_check(self, *, timeout: float = 2.0) -> RadioHealth:
        started = monotonic()
        model = self.get_model(timeout=timeout)
        latency_ms = (monotonic() - started) * 1000.0
        firmware = self.get_firmware(timeout=timeout)
        return self._create_health(
            latency_ms=latency_ms,
            model=model,
            firmware=firmware,
        )

    def start_scanner_info_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> ScannerInfo:
        if self.psi_active:
            raise RuntimeError("PSI scanner information push is already active.")

        first_updates: queue.Queue[ScannerInfo] = queue.Queue(maxsize=1)

        def capture_first_update(response: object) -> None:
            if not isinstance(response, ScannerInfo) or response.command != "PSI":
                return
            with suppress(queue.Full):
                first_updates.put_nowait(response)

        unsubscribe = self.events.subscribe("psi", capture_first_update)
        command = StartScannerInfoPush(interval_ms)
        deadline = monotonic() + timeout
        self._psi_interval_ms = interval_ms
        try:
            initial = self.execute(
                command,
                timeout=max(0.0, deadline - monotonic()),
            )
            if initial is not None:
                return initial

            try:
                return first_updates.get(
                    timeout=max(0.0, deadline - monotonic()),
                )
            except queue.Empty as exc:
                raise CommandTimeoutError(
                    "Timed out waiting for the first PSI scanner information update."
                ) from exc
        except Exception:
            self._psi_interval_ms = None
            if self.connected:
                with suppress(SDS200Error, OSError, ValueError):
                    self.send("PSI,0")
            raise
        finally:
            unsubscribe()

    def stop_scanner_info_push(self) -> None:
        if not self.psi_active:
            return
        self._psi_interval_ms = None
        if self.connected:
            self.send("PSI,0")

    @contextmanager
    def scanner_info_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> Iterator[ScannerInfo]:
        first = self.start_scanner_info_push(interval_ms, timeout=timeout)
        try:
            yield first
        finally:
            self.stop_scanner_info_push()

    def _wait_for_response(
        self,
        response_command: str,
        wire_command: str,
        timeout: float,
    ) -> object:
        response_queue: queue.Queue[object] = queue.Queue(maxsize=1)
        with self._response_lock:
            if response_command in self._responses:
                raise RuntimeError(f"A {response_command} command is already pending.")
            self._responses[response_command] = response_queue
        try:
            self.send(wire_command)
            try:
                return response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise CommandTimeoutError(
                    f"Timed out waiting for {response_command} response."
                ) from exc
        finally:
            with self._response_lock:
                self._responses.pop(response_command, None)

    def _receive_line(self, raw: str) -> None:
        self.trace.rx(raw)

        assembled = self.xml_assembler.feed(raw)
        if assembled is not None:
            command, xml = assembled
            try:
                info = self.xml_parser.parse(command, xml)
            except ProtocolError as exc:
                self.events.emit("protocol_error", exc)
                return

            with self._health_lock:
                self._last_state_at = info.received_at
            change = self.state.update(info)
            if change is not None:
                self.events.emit("state", change.current)
                self.events.emit("state_change", change)
                for field in change.fields:
                    self.events.emit(f"state.{field}", getattr(change.current, field))
                self._emit_event(
                    "state.changed",
                    "Scanner state changed",
                    data={
                        "fields": sorted(change.fields),
                        "state": asdict(change.current),
                    },
                )
            self._publish(command, info)
            return

        if self.xml_assembler.collecting or raw.startswith(("GSI,<XML>", "PSI,<XML>")):
            return

        try:
            packet = self.parser.parse_packet(raw)
            response = self.parser.parse_typed(packet)
        except ProtocolError as exc:
            self.events.emit("protocol_error", exc)
            return

        self.events.emit("packet", packet)
        self._publish(packet.command, response)

    def _publish(self, command: str, response: object) -> None:
        with self._health_lock:
            self._last_response_at = datetime.now(UTC)
            if isinstance(response, ModelResponse):
                self._model = normalize_model_name(response.model)
            elif isinstance(response, FirmwareResponse):
                self._firmware = response.version
        self.events.emit("response", response)
        self.events.emit(command.lower(), response)
        with self._response_lock:
            response_queue = self._responses.get(command)
        if response_queue is not None:
            with suppress(queue.Full):
                response_queue.put_nowait(response)

    def _transport_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        self.events.emit("diagnostic", diagnostic)
        self._emit_event(
            f"transport.{diagnostic.kind}",
            diagnostic.message,
            endpoint=diagnostic.endpoint,
            observed_at=diagnostic.observed_at,
            data=diagnostic.as_dict(),
        )

    def _connection_changed(self, connected: bool) -> None:
        observed_at = datetime.now(UTC)
        with self._health_lock:
            if self._last_connection_state != connected:
                self._connection_events += 1
                self._last_connection_state = connected
            if connected:
                self._last_connected_at = observed_at
            else:
                self._last_disconnected_at = observed_at
        self.events.emit("connection", connected)
        self._emit_event(
            "connection.connected" if connected else "connection.disconnected",
            "Scanner transport connected" if connected else "Scanner transport disconnected",
            endpoint=self.endpoint,
            observed_at=observed_at,
            data={"connected": connected},
        )
        if not connected or self._psi_interval_ms is None:
            return
        try:
            self.send(f"PSI,{self._psi_interval_ms}")
        except SDS200Error:
            logger.warning("Could not restart PSI after reconnect", exc_info=True)

    def _emit_event(
        self,
        kind: str,
        message: str,
        *,
        endpoint: str | None = None,
        observed_at: datetime | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        self.events.emit(
            "event",
            RadioEvent.create(
                kind,
                message,
                endpoint=endpoint or self.endpoint,
                observed_at=observed_at,
                data=data,
            ),
        )


# Backward-compatible public name retained for existing applications.
SDS200 = SDSScanner
