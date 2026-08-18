from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Self, TypeVar

from .analysis_subscriptions import (
    AnalysisPublisher,
    AnalysisPublisherSnapshot,
    AnalysisSubscription,
)
from .commands import (
    Command,
    GetChargeStatus,
    GetFavoritesQuickKeys,
    GetFirmware,
    GetGltFavorites,
    GetModel,
    GetMsi,
    GetScannerInfo,
    GetScannerRecordingStatus,
    GetSquelch,
    GetStatus,
    GetVolume,
    HoldSelection,
    IndexedMenuId,
    NavigationTarget,
    NextSelection,
    OpenIndexedMenu,
    PauseResumeAnalysis,
    PressKey,
    PreviousSelection,
    SetFavoritesQuickKeys,
    SetScannerRecordingStatus,
    SetSquelch,
    SetVolume,
    StartCurrentActivityAnalysis,
    StartLcnMonitorAnalysis,
    StartScannerInfoPush,
)
from .device import choose_scanner
from .events import EventBus
from .exceptions import (
    CommandRejectedError,
    CommandTimeoutError,
    ProtocolError,
    SDS200Error,
    UnsupportedScannerFeatureError,
    UnsupportedScannerModelError,
)
from .fallback import FallbackTransport, TransportCandidate
from .models import (
    AnalysisMode,
    AnalysisResponse,
    ChargeStatus,
    FavoritesQuickKeys,
    FavoritesQuickKeyState,
    FirmwareResponse,
    GltResponse,
    GwfResponse,
    HealthSummary,
    ModelResponse,
    MsiResponse,
    Packet,
    PwfResponse,
    RadioEvent,
    RadioHealth,
    ScannerInfo,
    ScannerRecordingStatus,
    ScannerRecordingStatusResponse,
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
from .replay import RecordingTransport, ReplayTransport
from .scanner import (
    ScannerCapabilities,
    ScannerModel,
    capabilities_for_model,
    normalize_model_name,
)
from .state import (
    RadioState,
    RadioStateSnapshot,
    StateChange,
    snapshot_from_scanner_info,
)
from .trace import TrafficTrace
from .transport import (
    ControlTransport,
    DiagnosticControlTransport,
    SerialFactory,
    SerialTransport,
    StatisticalControlTransport,
    TransportDiagnostic,
)
from .waterfall_subscriptions import (
    WaterfallPublisher,
    WaterfallPublisherSnapshot,
    WaterfallSubscription,
)
from .xml_protocol import (
    AnalysisParser,
    GltParser,
    MsiParser,
    ScannerInfoParser,
    XmlResponseAssembler,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Physical SDS200 1.26.01 testing observed that an otherwise healthy network PSI
# push stops after roughly 184 seconds. Refresh the active push conservatively
# before that observed boundary without reopening scanner control.
_PSI_RENEWAL_INTERVAL_SECONDS = 120.0
_PSI_RENEWAL_DEFER_SECONDS = 1.0
_PSI_RENEWAL_TIMEOUT_SECONDS = 2.0


@dataclass(slots=True)
class _PendingResponse:
    command: str
    queue: queue.Queue[object]


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
        capture_path: str | Path | None = None,
        capture_redactions: tuple[str, ...] = (),
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

        fallback_transport = (
            self.transport if isinstance(self.transport, FallbackTransport) else None
        )
        direct_udp_msi_supported = isinstance(self.transport, UdpTransport)
        if capture_path is not None:
            self.transport = RecordingTransport(
                self.transport,
                capture_path,
                redactions=capture_redactions,
            )

        self.parser = PacketParser()
        self.xml_parser = ScannerInfoParser()
        self.glt_parser = GltParser()
        self.analysis_parser = AnalysisParser()
        self.msi_parser = MsiParser()
        self.xml_assembler = XmlResponseAssembler()
        self.events = EventBus()
        self._analysis_publisher = AnalysisPublisher()
        self._waterfall_publisher = WaterfallPublisher()
        if isinstance(self.transport, DiagnosticControlTransport):
            self.transport.set_diagnostic_handler(self._transport_diagnostic)
        self.state = RadioState()
        self.trace = TrafficTrace(trace_path)
        self._responses: dict[str, _PendingResponse] = {}
        self._response_lock = threading.RLock()
        self._command_lock = threading.RLock()
        self._fallback_transport = fallback_transport
        self._direct_udp_msi_supported = direct_udp_msi_supported
        if self._fallback_transport is not None:
            self._fallback_transport.set_recovery_guard(self._recovery_idle)
        self._closed = threading.Event()
        self._closed.set()
        self._psi_interval_ms: int | None = None
        self._psi_renewal_supported = (
            self.endpoint.startswith("udp://") or self._fallback_transport is not None
        )
        self._psi_renewal_interval = _PSI_RENEWAL_INTERVAL_SECONDS
        self._psi_renewal_defer = _PSI_RENEWAL_DEFER_SECONDS
        self._psi_renewal_timeout = _PSI_RENEWAL_TIMEOUT_SECONDS
        self._psi_renewal_stop = threading.Event()
        self._psi_renewal_thread: threading.Thread | None = None
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
        capture_path: str | Path | None = None,
        capture_redactions: tuple[str, ...] = (),
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
            capture_path=capture_path,
            capture_redactions=capture_redactions,
        )

    @classmethod
    def network(
        cls,
        host: str,
        *,
        remote_port: int = DEFAULT_UDP_PORT,
        local_host: str | None = None,
        local_port: int = 0,
        reconnect: bool = True,
        reconnect_policy: ReconnectPolicy | None = None,
        socket_factory: DatagramSocketFactory | None = None,
        max_xml_retries: int = 2,
        trace_path: str | Path | None = None,
        health_history_limit: int = 100,
        capture_path: str | Path | None = None,
        capture_redactions: tuple[str, ...] = (),
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
            capture_path=capture_path,
            capture_redactions=capture_redactions,
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
        capture_path: str | Path | None = None,
        capture_redactions: tuple[str, ...] = (),
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
                capture_path=capture_path,
                capture_redactions=capture_redactions,
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
                capture_path=capture_path,
                capture_redactions=capture_redactions,
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
        expected_model = profile.model or "SDS200"

        def validate_model_probe(line: str) -> bool:
            command, separator, value = line.strip().partition(",")
            return (
                command.upper() == "MDL"
                and bool(separator)
                and normalize_model_name(value) == expected_model
            )

        transport = FallbackTransport(
            (candidates[resolved_preference], candidates[alternate]),
            reconnect_policy=reconnect_policy,
            preferred_recovery_policy=profile.preferred_recovery_policy,
            recovery_probe_validator=validate_model_probe,
        )
        return cls.from_transport(
            transport,
            trace_path=trace_path,
            health_history_limit=health_history_limit,
            expected_model=profile.model,
            capture_path=capture_path,
            capture_redactions=capture_redactions,
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
        capture_path: str | Path | None = None,
        capture_redactions: tuple[str, ...] = (),
    ) -> Self:
        return cls(
            transport=transport,
            trace_path=trace_path,
            health_history_limit=health_history_limit,
            health_thresholds=health_thresholds,
            expected_model=expected_model,
            capture_path=capture_path,
            capture_redactions=capture_redactions,
        )

    @classmethod
    def replay(
        cls,
        path: str | Path,
        *,
        speed: float = 0.0,
        strict: bool = True,
        expected_model: ScannerModel | str | None = None,
        trace_path: str | Path | None = None,
        health_history_limit: int = 100,
    ) -> Self:
        return cls.from_transport(
            ReplayTransport.from_file(path, speed=speed, strict=strict),
            expected_model=expected_model,
            trace_path=trace_path,
            health_history_limit=health_history_limit,
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

    @property
    def supports_bounded_reconnect(self) -> bool:
        """Whether reconnect uses the directly owned bounded UDP transport."""
        return isinstance(self.transport, UdpTransport)

    def connect(self) -> None:
        self._closed.clear()
        try:
            self.transport.start(self._receive_line, self._connection_changed)
        except Exception:
            self._closed.set()
            raise

    def reconnect(self, *, timeout: float = 2.0) -> None:
        """Apply one deadline to restart response waits and PSI restoration."""
        normalized_timeout = _require_positive_timeout(
            timeout,
            label="Scanner reconnect timeout",
        )
        deadline = monotonic() + normalized_timeout
        remaining = deadline - monotonic()
        if remaining <= 0 or not self._command_lock.acquire(timeout=remaining):
            raise CommandTimeoutError(
                "Scanner reconnect timed out waiting for scanner command activity."
            )

        try:
            interval_ms = self._psi_interval_ms
            logger.info(
                "scanner reconnect starting endpoint=%s psi_interval_ms=%s",
                self.endpoint,
                interval_ms,
            )
            self._stop_psi_renewal()
            self._psi_interval_ms = None
            self.transport.stop()
            self._closed.set()
            try:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise CommandTimeoutError(
                        "Scanner reconnect timed out while stopping the control "
                        "transport."
                    )

                self.connect()

                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise CommandTimeoutError(
                        "Scanner reconnect timed out while opening the control "
                        "transport."
                    )
                if interval_ms is not None:
                    self.start_scanner_info_push(
                        interval_ms,
                        timeout=remaining,
                    )
            except Exception:
                self._psi_interval_ms = interval_ms
                raise
            logger.info(
                "scanner reconnect completed endpoint=%s psi_interval_ms=%s",
                self.endpoint,
                interval_ms,
            )
        finally:
            self._command_lock.release()

    def close(self) -> None:
        self._analysis_publisher.close()
        self._waterfall_publisher.close()
        if self.psi_active:
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

    def subscribe_analysis(self) -> AnalysisSubscription:
        return self._analysis_publisher.subscribe()

    def analysis_snapshot(self) -> AnalysisPublisherSnapshot:
        return self._analysis_publisher.snapshot()

    def subscribe_waterfall(self) -> WaterfallSubscription:
        return self._waterfall_publisher.subscribe()

    def waterfall_snapshot(self) -> WaterfallPublisherSnapshot:
        return self._waterfall_publisher.snapshot()

    def on_response(self, callback: Callable[[object], None]) -> Callable[[], None]:
        return self.events.subscribe("response", callback)

    def on_psi(
        self,
        callback: Callable[[ScannerInfo], None],
    ) -> Callable[[], None]:
        return self.events.subscribe("psi", callback)

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
        with self._command_lock:
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

    def press_hold_key(
        self,
        key_code: str,
        *,
        timeout: float = 2.0,
    ) -> None:
        capabilities = self._model_capabilities(timeout=timeout)
        if not capabilities.hold_key_control:
            raise UnsupportedScannerFeatureError(
                f"{capabilities.model} does not provide hold-related key control."
            )
        self.execute(PressKey(key_code), timeout=timeout)

    def hold(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        timeout: float = 2.0,
    ) -> None:
        capabilities = self._model_capabilities(timeout=timeout)
        if not capabilities.navigation_control:
            raise UnsupportedScannerFeatureError(
                f"{capabilities.model} does not provide navigation control."
            )
        self.execute(HoldSelection(str(target), first, second), timeout=timeout)

    def next(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        capabilities = self._model_capabilities(timeout=timeout)
        if not capabilities.navigation_control:
            raise UnsupportedScannerFeatureError(
                f"{capabilities.model} does not provide navigation control."
            )
        self.execute(
            NextSelection(str(target), first, second, count),
            timeout=timeout,
        )

    def previous(
        self,
        target: NavigationTarget | str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> None:
        capabilities = self._model_capabilities(timeout=timeout)
        if not capabilities.navigation_control:
            raise UnsupportedScannerFeatureError(
                f"{capabilities.model} does not provide navigation control."
            )
        self.execute(
            PreviousSelection(str(target), first, second, count),
            timeout=timeout,
        )

    def get_battery_level(self, *, timeout: float = 3.0) -> float | None:
        """Return optional raw battery telemetry from the scanner information XML."""
        capabilities = self._model_capabilities(timeout=timeout)
        if not capabilities.battery_level:
            raise UnsupportedScannerFeatureError(
                f"{capabilities.model} does not provide GSI battery information."
            )
        return self.get_scanner_info(timeout=timeout).battery

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

    def get_glt_favorites(self, *, timeout: float = 3.0) -> GltResponse:
        return self.execute(GetGltFavorites(), timeout=timeout)

    def _require_msi_retrieval_supported(self) -> None:
        if self._fallback_transport is not None or (
            self.endpoint.startswith("udp://")
            and not self._direct_udp_msi_supported
        ):
            raise UnsupportedScannerFeatureError(
                "MSI retrieval is unavailable on unverified UDP-like and "
                "fallback control transports."
            )

    def get_msi(self, *, timeout: float = 3.0) -> MsiResponse:
        return self.execute(GetMsi(), timeout=timeout)

    def open_indexed_menu(
        self,
        menu_id: IndexedMenuId,
        index: str,
        *,
        timeout: float = 2.0,
    ) -> None:
        self.execute(OpenIndexedMenu(menu_id, index), timeout=timeout)

    def open_indexed_menu_snapshot(
        self,
        menu_id: IndexedMenuId,
        index: str,
        *,
        timeout: float = 3.0,
    ) -> MsiResponse:
        # Compose existing indexed-MNU and MSI operations under one host lock.
        menu_command = OpenIndexedMenu(menu_id, index)
        normalized_timeout = _require_positive_timeout(
            timeout,
            label="Indexed menu snapshot timeout",
        )
        deadline = monotonic() + normalized_timeout
        remaining = deadline - monotonic()
        if remaining <= 0 or not self._command_lock.acquire(timeout=remaining):
            raise CommandTimeoutError(
                "Indexed menu snapshot timed out waiting for scanner command activity."
            )

        try:
            self._require_msi_retrieval_supported()

            remaining = deadline - monotonic()
            if remaining <= 0:
                raise CommandTimeoutError(
                    "Indexed menu snapshot timed out before MNU request."
                )
            self.execute(menu_command, timeout=remaining)

            remaining = deadline - monotonic()
            if remaining <= 0:
                raise CommandTimeoutError(
                    "Indexed menu snapshot timed out before MSI request."
                )
            return self.execute(GetMsi(), timeout=remaining)
        finally:
            self._command_lock.release()

    def start_current_activity_analysis(
        self, site_index: int, *, timeout: float = 2.0
    ) -> AnalysisResponse:
        return self.execute(StartCurrentActivityAnalysis(site_index), timeout=timeout)

    def start_lcn_monitor_analysis(
        self, site_index: int, *, timeout: float = 2.0
    ) -> AnalysisResponse:
        return self.execute(StartLcnMonitorAnalysis(site_index), timeout=timeout)

    def pause_resume_analysis(
        self, mode: AnalysisMode, *, timeout: float = 2.0
    ) -> None:
        self.execute(PauseResumeAnalysis(mode), timeout=timeout)

    def get_favorites_quick_keys(
        self, *, timeout: float = 2.0
    ) -> FavoritesQuickKeys:
        return self.execute(GetFavoritesQuickKeys(), timeout=timeout)

    def set_favorites_quick_keys(
        self,
        states: Sequence[int | FavoritesQuickKeyState],
        *,
        timeout: float = 2.0,
    ) -> None:
        self.execute(SetFavoritesQuickKeys(states), timeout=timeout)

    def get_scanner_recording_status(
        self, *, timeout: float = 2.0
    ) -> ScannerRecordingStatusResponse:
        return self.execute(GetScannerRecordingStatus(), timeout=timeout)

    def set_scanner_recording_status(
        self,
        status: int | ScannerRecordingStatus,
        *,
        timeout: float = 2.0,
    ) -> None:
        self.execute(SetScannerRecordingStatus(status), timeout=timeout)

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

    def _renew_psi_if_idle(self) -> bool:
        """Refresh an active network PSI push without overlapping a command."""

        if (
            not self._psi_renewal_supported
            or not self.endpoint.startswith("udp://")
            or self._psi_renewal_stop.is_set()
            or not self._command_lock.acquire(blocking=False)
        ):
            return False

        try:
            interval_ms = self._psi_interval_ms
            if (
                interval_ms is None
                or not self.connected
                or self._psi_renewal_stop.is_set()
            ):
                return False

            first_updates: queue.Queue[ScannerInfo] = queue.Queue(maxsize=1)

            def capture_update(response: object) -> None:
                if isinstance(response, Packet) and response.command == "PSI":
                    with suppress(queue.Empty):
                        first_updates.get_nowait()
                    return
                if not isinstance(response, ScannerInfo) or response.command != "PSI":
                    return
                with suppress(queue.Full):
                    first_updates.put_nowait(response)

            unsubscribe = self.events.subscribe("psi", capture_update)
            deadline = monotonic() + self._psi_renewal_timeout
            try:
                initial = self.execute(
                    StartScannerInfoPush(interval_ms),
                    timeout=max(0.0, deadline - monotonic()),
                )

                # A ScannerInfo response itself confirms the renewed stream. If
                # the scanner acknowledges first instead, keep serialization
                # until the first PSI frame arrives. That frame may already be
                # queued by the time execute() returns from the acknowledgement.
                if initial is None:
                    try:
                        first_updates.get(
                            timeout=max(0.0, deadline - monotonic()),
                        )
                    except queue.Empty as exc:
                        raise CommandTimeoutError(
                            "Timed out waiting for renewed PSI scanner information "
                            "update."
                        ) from exc
            except SDS200Error:
                logger.warning(
                    "Could not renew active PSI stream endpoint=%s",
                    self.endpoint,
                    exc_info=True,
                )
                return False
            finally:
                unsubscribe()
        finally:
            self._command_lock.release()

        logger.debug(
            "PSI stream renewed endpoint=%s interval_ms=%d",
            self.endpoint,
            interval_ms,
        )
        return True

    def _psi_renewal_loop(self) -> None:
        wait_seconds = self._psi_renewal_interval
        while not self._psi_renewal_stop.wait(wait_seconds):
            if not self.endpoint.startswith("udp://") or self._renew_psi_if_idle():
                wait_seconds = self._psi_renewal_interval
            else:
                wait_seconds = min(
                    self._psi_renewal_defer,
                    self._psi_renewal_interval,
                )

    def _start_psi_renewal(self) -> None:
        if not self._psi_renewal_supported:
            return
        self._stop_psi_renewal()
        self._psi_renewal_stop.clear()
        thread = threading.Thread(
            target=self._psi_renewal_loop,
            name="sds200-psi-renewal",
            daemon=True,
        )
        self._psi_renewal_thread = thread
        thread.start()

    def _stop_psi_renewal(self) -> None:
        self._psi_renewal_stop.set()
        thread = self._psi_renewal_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._psi_renewal_thread = None

    def start_scanner_info_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> ScannerInfo:
        if self.psi_active:
            raise RuntimeError("PSI scanner information push is already active.")

        deadline = monotonic() + timeout
        remaining = deadline - monotonic()
        if remaining <= 0 or not self._command_lock.acquire(timeout=remaining):
            raise CommandTimeoutError(
                "Timed out waiting for the first PSI scanner information update."
            )

        try:
            logger.info(
                "PSI stream starting endpoint=%s interval_ms=%d",
                self.endpoint,
                interval_ms,
            )
            first_updates: queue.Queue[ScannerInfo] = queue.Queue(maxsize=1)

            def capture_first_update(response: object) -> None:
                if isinstance(response, Packet) and response.command == "PSI":
                    with suppress(queue.Empty):
                        first_updates.get_nowait()
                    return
                if not isinstance(response, ScannerInfo) or response.command != "PSI":
                    return
                with suppress(queue.Full):
                    first_updates.put_nowait(response)

            unsubscribe = self.events.subscribe("psi", capture_first_update)
            command = StartScannerInfoPush(interval_ms)
            self._psi_interval_ms = interval_ms
            try:
                initial = self.execute(
                    command,
                    timeout=max(0.0, deadline - monotonic()),
                )
                if initial is not None:
                    self._start_psi_renewal()
                    logger.info("PSI stream started endpoint=%s", self.endpoint)
                    return initial

                # An acknowledgement is not the first streamed scanner-info
                # frame. Keep command serialization until that first PSI frame
                # arrives; it may already be queued when execute() returns.
                try:
                    first = first_updates.get(
                        timeout=max(0.0, deadline - monotonic()),
                    )
                    self._start_psi_renewal()
                    logger.info("PSI stream started endpoint=%s", self.endpoint)
                    return first
                except queue.Empty as exc:
                    raise CommandTimeoutError(
                        "Timed out waiting for the first PSI scanner information update."
                    ) from exc
            except Exception:
                self._stop_psi_renewal()
                self._psi_interval_ms = None
                if self.connected:
                    with suppress(SDS200Error, OSError, ValueError):
                        self.send("PSI,0")
                raise
            finally:
                unsubscribe()
        finally:
            self._command_lock.release()

    def stop_scanner_info_push(self) -> None:
        with self._command_lock:
            self._stop_psi_renewal()
            if not self.psi_active:
                return
            logger.info("PSI stream stopping endpoint=%s", self.endpoint)
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

    @contextmanager
    def radio_state_push(
        self,
        interval_ms: int = 500,
        *,
        timeout: float = 3.0,
    ) -> Iterator[RadioStateSnapshot]:
        """Yield the first renderer-neutral state while PSI remains active."""

        with self.scanner_info_push(interval_ms, timeout=timeout) as first:
            yield snapshot_from_scanner_info(first)

    def _recovery_idle(self) -> bool:
        with self._response_lock:
            return not self._responses

    def _wait_for_response(
        self,
        response_command: str,
        wire_command: str,
        timeout: float,
    ) -> object:
        deadline = monotonic() + timeout
        remaining = deadline - monotonic()
        if remaining <= 0 or not self._command_lock.acquire(timeout=remaining):
            raise CommandTimeoutError(
                f"Timed out waiting for {response_command} response."
            )

        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise CommandTimeoutError(
                    f"Timed out waiting for {response_command} response."
                )

            if response_command == "MSI":
                self._require_msi_retrieval_supported()

            response_queue: queue.Queue[object] = queue.Queue(maxsize=1)
            pending = _PendingResponse(command=response_command, queue=response_queue)
            with self._response_lock:
                if response_command in self._responses:
                    raise RuntimeError(
                        f"A {response_command} command is already pending."
                    )
                self._responses[response_command] = pending
            try:
                self.send(wire_command)
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise CommandTimeoutError(
                        f"Timed out waiting for {response_command} response."
                    )
                try:
                    response = response_queue.get(timeout=remaining)
                    if isinstance(response, CommandRejectedError):
                        raise response
                    return response
                except queue.Empty as exc:
                    raise CommandTimeoutError(
                        f"Timed out waiting for {response_command} response."
                    ) from exc
            finally:
                with self._response_lock:
                    self._responses.pop(response_command, None)
        finally:
            self._command_lock.release()

    def _receive_line(self, raw: str) -> None:
        self.trace.rx(raw)

        assembled = self.xml_assembler.feed(raw)
        if assembled is not None:
            command, xml = assembled
            try:
                xml_response: GltResponse | AnalysisResponse | MsiResponse | ScannerInfo
                if command == "GLT":
                    xml_response = self.glt_parser.parse(command, xml)
                elif command == "AST":
                    xml_response = self.analysis_parser.parse(command, xml)
                elif command == "MSI":
                    xml_response = self.msi_parser.parse(command, xml)
                else:
                    xml_response = self.xml_parser.parse(command, xml)
            except ProtocolError as exc:
                self.events.emit("protocol_error", exc)
                return

            if isinstance(xml_response, (GltResponse, AnalysisResponse, MsiResponse)):
                self._publish(command, xml_response)
                return

            info = xml_response
            with self._health_lock:
                self._last_state_at = info.received_at
            change = self.state.update(info)
            current = self.state.snapshot
            self.events.emit("state", current)
            if change is not None:
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

        if self.xml_assembler.collecting or self.xml_assembler.recognizes_header(raw):
            return

        try:
            packet = self.parser.parse_packet(raw)
            response = self.parser.parse_typed(packet)
        except ProtocolError as exc:
            self.events.emit("protocol_error", exc)
            return

        self.events.emit("packet", packet)
        if packet.command in {"ERR", "NG"}:
            self._reject_pending(packet)
        self._publish(packet.command, response)

    def _reject_pending(self, packet: Packet) -> None:
        """Associate a generic ERR/NG response when exactly one command is pending."""
        with self._response_lock:
            if len(self._responses) != 1:
                return
            pending = next(iter(self._responses.values()))
        rejection = CommandRejectedError(
            f"Scanner rejected {pending.command} command: {packet.raw}"
        )
        with suppress(queue.Full):
            pending.queue.put_nowait(rejection)

    def _publish(self, command: str, response: object) -> None:
        with self._health_lock:
            self._last_response_at = datetime.now(UTC)
            if isinstance(response, ModelResponse):
                self._model = normalize_model_name(response.model)
            elif isinstance(response, FirmwareResponse):
                self._firmware = response.version
        if isinstance(response, AnalysisResponse):
            self._analysis_publisher.publish(response)
        if isinstance(response, (PwfResponse, GwfResponse)):
            self._waterfall_publisher.publish(response)
        self.events.emit("response", response)
        self.events.emit(command.lower(), response)
        with self._response_lock:
            pending = self._responses.get(command)
        if pending is not None:
            with suppress(queue.Full):
                pending.queue.put_nowait(response)

    def _transport_diagnostic(self, diagnostic: TransportDiagnostic) -> None:
        if (
            diagnostic.kind == "preferred_recovery_succeeded"
            and self._psi_interval_ms is not None
        ):
            try:
                self.send(f"PSI,{self._psi_interval_ms}")
            except SDS200Error:
                logger.warning(
                    "Could not restart PSI after preferred transport recovery",
                    exc_info=True,
                )
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


def _require_positive_timeout(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return normalized
