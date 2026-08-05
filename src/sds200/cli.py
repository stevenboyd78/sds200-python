# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from typing import BinaryIO, Protocol, cast

from . import __version__
from .asterisk_moh import AsteriskMohSignalController, PcmStreamSink
from .audio import AudioStream
from .audio_recording import PcmuWavRecorder
from .audio_sinks import (
    AudioFanoutSession,
    PcmSink,
    PcmSinkRouter,
    PcmWavSink,
    SoundDevicePlaybackSink,
    inspect_audio_backend,
)
from .commands import NAVIGATION_TARGETS
from .completion import (
    SUPPORTED_SHELLS,
    command_completer,
    completion_script,
    enable_tab_completion,
    port_completer,
    profile_completer,
)
from .configuration import (
    APPLICATION_CONFIGURATION_FIELDS,
    ConfigurationPaths,
    ResolvedApplicationConfiguration,
    load_application_configuration,
)
from .daemon_api import DaemonApiOperation, DaemonReadOnlyApi
from .daemon_client import (
    DAEMON_API_CLIENT_DEFAULT_TIMEOUT,
    DaemonApiClient,
)
from .daemon_event_server import (
    DAEMON_EVENT_DEFAULT_MAX_CLIENTS,
    DAEMON_EVENT_DEFAULT_SEND_TIMEOUT,
    DAEMON_EVENT_DEFAULT_SHUTDOWN_TIMEOUT,
    DaemonEventServer,
)
from .daemon_event_stream import DaemonEventStream
from .daemon_events import (
    DAEMON_EVENT_DEFAULT_MAX_BYTES,
    DAEMON_EVENT_DEFAULT_QUEUE_CAPACITY,
)
from .daemon_ipc import (
    DaemonSocketListener,
    resolve_daemon_event_socket_location,
    resolve_daemon_pcmu_socket_location,
    resolve_daemon_socket_location,
)
from .daemon_pcmu_server import (
    DAEMON_PCMU_DEFAULT_MAX_CLIENTS,
    DAEMON_PCMU_DEFAULT_SEND_TIMEOUT,
    DAEMON_PCMU_DEFAULT_SHUTDOWN_TIMEOUT,
    DaemonPcmuServer,
)
from .daemon_process import DaemonProcess
from .daemon_runtime import DaemonRuntime
from .daemon_server import (
    DAEMON_API_DEFAULT_CLIENT_TIMEOUT,
    DAEMON_API_DEFAULT_MAX_CLIENTS,
    DAEMON_API_DEFAULT_MAX_REQUEST_BYTES,
    DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES,
    DAEMON_API_DEFAULT_SHUTDOWN_TIMEOUT,
    DaemonApiServer,
)
from .device import choose_scanner, discover_scanners
from .discovery import (
    DEFAULT_DISCOVERY_TIMEOUT,
    DEFAULT_DISCOVERY_WORKERS,
    DEFAULT_MAX_DISCOVERY_HOSTS,
    discover_network_scanners,
)
from .exceptions import DaemonProtocolError, SDS200Error
from .logging_config import LOG_LEVEL_NAMES, configure_logging
from .models import HealthSummary, RadioEvent, RadioHealth, StatusResponse
from .monitor import TerminalMonitor
from .network import DEFAULT_UDP_PORT
from .network_audio import NetworkAudioTransport
from .pcmu_protocol import (
    PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
    PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
)
from .pcmu_stream import PcmuStream
from .pcmu_subscriptions import (
    PCMU_DEFAULT_MAX_PAYLOAD_BYTES,
    PCMU_DEFAULT_QUEUE_CAPACITY,
)
from .profiles import (
    TRANSPORT_PREFERENCES,
    ConnectionProfile,
    ProfileRepairResult,
    ProfileStore,
    TransportPreference,
    profile_from_discovery,
    repair_profile,
)
from .radio import SDSScanner
from .recording_inventory import scan_recording_inventory
from .recording_organization import RecordingOrganizationPolicy
from .recording_retention import (
    RecordingRetentionPlan,
    RecordingRetentionPolicy,
    plan_recording_retention,
)
from .recording_retention_execution import (
    RecordingRetentionExecutionResult,
    execute_recording_retention,
    recording_retention_confirmation_token,
)
from .reliability import ReconnectPolicy
from .rich_cli import (
    COLOR_MODES,
    THEME_NAMES,
    RichCliRenderer,
    palette_for_name,
)
from .rtsp import DEFAULT_RTSP_PORT
from .scanner import SUPPORTED_SCANNER_MODELS, ScannerModel, normalize_model_name
from .tui_audio import (
    DEFAULT_RECORDING_TEMPLATE,
    RecordingPathPolicy,
    TuiAudioSession,
)
from .tui_logging import TuiLogBuffer, capture_package_logs

logger = logging.getLogger(__name__)


class _CompletableAction(Protocol):
    completer: Callable[..., object]


def _set_completer(
    action: argparse.Action,
    completer: Callable[..., object],
) -> None:
    cast(_CompletableAction, action).completer = completer


def _configuration_parser_default(
    value: object,
    *,
    suppress: bool,
) -> object:
    return argparse.SUPPRESS if suppress else value


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _non_negative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must not be negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must not be negative")
    return parsed


def _timezone_aware_datetime(value: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "value must be an ISO 8601 date and time"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("value must include a UTC offset")
    return parsed.astimezone(UTC)


def _remote_port(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return parsed


def _local_port(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return parsed


def _audio_device(value: str) -> str | int:
    try:
        return int(value)
    except ValueError:
        return value


def _recording_organization(value: str) -> RecordingOrganizationPolicy:
    try:
        return RecordingOrganizationPolicy.from_csv(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _scanner_model(value: str) -> ScannerModel:
    model = normalize_model_name(value)
    if model is None:
        choices = ", ".join(SUPPORTED_SCANNER_MODELS)
        raise argparse.ArgumentTypeError(f"model must be one of: {choices}")
    return model


def _add_network_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--udp-port",
        type=_remote_port,
        metavar="PORT",
        help=f"Scanner UDP control port (default: {DEFAULT_UDP_PORT})",
    )
    parser.add_argument(
        "--bind-address",
        default="",
        metavar="ADDRESS",
        help="Local address for the UDP socket (requires --host)",
    )
    parser.add_argument(
        "--bind-port",
        type=_local_port,
        default=0,
        metavar="PORT",
        help="Local UDP port; 0 selects an ephemeral port (requires --host)",
    )


def _add_profile_recovery_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--recover-preferred",
        action=argparse.BooleanOptionalAction,
        default=False,
        dest="profile_recover_preferred",
        help="Return to the preferred endpoint after validated recovery",
    )
    parser.add_argument(
        "--recovery-probe-interval",
        type=_positive_float,
        default=30.0,
        dest="profile_recovery_probe_interval",
        metavar="SECONDS",
    )
    parser.add_argument(
        "--recovery-probe-timeout",
        type=_positive_float,
        default=2.0,
        dest="profile_recovery_probe_timeout",
        metavar="SECONDS",
    )
    parser.add_argument(
        "--recovery-stability-window",
        type=_non_negative_float,
        default=5.0,
        dest="profile_recovery_stability_window",
        metavar="SECONDS",
    )
    parser.add_argument(
        "--recovery-cooldown",
        type=_non_negative_float,
        default=30.0,
        dest="profile_recovery_cooldown",
        metavar="SECONDS",
    )


def build_parser(
    *,
    suppress_configuration_defaults: bool = False,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sdsctl")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Connection profile file (default: XDG config directory)",
    )
    parser.add_argument(
        "--model",
        type=_scanner_model,
        metavar="MODEL",
        help="Expected USB scanner model: SDS100, SDS150, or SDS200",
    )
    connection = parser.add_mutually_exclusive_group()
    port_action = connection.add_argument(
        "--port",
        type=Path,
        help="Serial port or stable by-id path",
    )
    _set_completer(port_action, port_completer)
    connection.add_argument(
        "--host",
        help="SDS200 LAN hostname or IP address (SDS200 only)",
    )
    connection.add_argument(
        "--replay",
        type=Path,
        help="Replay a JSON Lines scanner capture instead of using hardware",
    )
    profile_action = connection.add_argument(
        "--profile",
        help="Use a saved serial or network connection profile",
    )
    _set_completer(profile_action, profile_completer)
    parser.add_argument(
        "--prefer",
        dest="connection_preference",
        choices=TRANSPORT_PREFERENCES,
        help="Override a fallback profile transport preference",
    )
    parser.add_argument(
        "--recover-preferred",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override preferred recovery for a fallback profile",
    )
    parser.add_argument(
        "--recovery-probe-interval",
        type=_positive_float,
        default=None,
        metavar="SECONDS",
        help="Preferred endpoint probe interval for a fallback profile",
    )
    parser.add_argument(
        "--recovery-probe-timeout",
        type=_positive_float,
        default=None,
        metavar="SECONDS",
        help="Timeout for each preferred endpoint MDL probe",
    )
    parser.add_argument(
        "--recovery-stability-window",
        type=_non_negative_float,
        default=None,
        metavar="SECONDS",
        help="Required preferred endpoint stability before promotion",
    )
    parser.add_argument(
        "--recovery-cooldown",
        type=_non_negative_float,
        default=None,
        metavar="SECONDS",
        help="Minimum fallback time before probing the preferred endpoint",
    )
    _add_network_options(parser)
    parser.add_argument(
        "--max-xml-retries",
        type=_non_negative_integer,
        default=_configuration_parser_default(
            2,
            suppress=suppress_configuration_defaults,
        ),
        metavar="COUNT",
        help="Automatic retries after a lost UDP XML fragment (default: 2)",
    )
    parser.add_argument(
        "--reconnect-attempts",
        type=_non_negative_integer,
        default=_configuration_parser_default(
            0,
            suppress=suppress_configuration_defaults,
        ),
        metavar="COUNT",
        help="Reconnect attempts after a disconnect; 0 retries forever (default: 0)",
    )
    parser.add_argument(
        "--reconnect-initial-delay",
        type=_positive_float,
        default=_configuration_parser_default(
            1.0,
            suppress=suppress_configuration_defaults,
        ),
        metavar="SECONDS",
        help="Initial reconnect delay (default: 1.0)",
    )
    parser.add_argument(
        "--reconnect-multiplier",
        type=_positive_float,
        default=_configuration_parser_default(
            2.0,
            suppress=suppress_configuration_defaults,
        ),
        metavar="FACTOR",
        help="Reconnect backoff multiplier (default: 2.0)",
    )
    parser.add_argument(
        "--reconnect-max-delay",
        type=_positive_float,
        default=_configuration_parser_default(
            30.0,
            suppress=suppress_configuration_defaults,
        ),
        metavar="SECONDS",
        help="Maximum reconnect delay (default: 30.0)",
    )
    parser.add_argument(
        "--health-history-limit",
        type=_positive_integer,
        default=_configuration_parser_default(
            100,
            suppress=suppress_configuration_defaults,
        ),
        metavar="COUNT",
        help="Maximum in-memory health observations (default: 100)",
    )
    color = parser.add_mutually_exclusive_group()
    color.add_argument(
        "--color",
        choices=COLOR_MODES,
        default=_configuration_parser_default(
            "auto",
            suppress=suppress_configuration_defaults,
        ),
        help="ANSI styling policy: auto, always, or never (default: auto)",
    )
    color.add_argument(
        "--no-color",
        action="store_const",
        const="never",
        dest="color",
        default=argparse.SUPPRESS,
        help="Disable ANSI styling (alias for --color never)",
    )
    parser.add_argument(
        "--theme",
        choices=THEME_NAMES,
        default=_configuration_parser_default(
            "dark",
            suppress=suppress_configuration_defaults,
        ),
        help="Semantic CLI palette: dark or light (default: dark)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=_configuration_parser_default(
            0,
            suppress=suppress_configuration_defaults,
        ),
        help="Increase logging verbosity; repeat for DEBUG",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVEL_NAMES,
        default=_configuration_parser_default(
            None,
            suppress=suppress_configuration_defaults,
        ),
        help="Operational log level; overrides -v/--verbose",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=_configuration_parser_default(
            None,
            suppress=suppress_configuration_defaults,
        ),
        help="Append operational logs to a watched file in addition to stderr",
    )
    parser.add_argument("--trace", type=Path, help="Append raw traffic to a trace file")
    parser.add_argument(
        "--capture",
        type=Path,
        help="Record a replayable JSON Lines transport session",
    )
    parser.add_argument(
        "--redact",
        action="append",
        default=[],
        metavar="TEXT",
        help="Replace literal text in --capture output; repeat as needed",
    )
    parser.add_argument(
        "--replay-speed",
        type=_non_negative_float,
        default=0.0,
        metavar="FACTOR",
        help="Replay timing factor; 0 runs immediately, 1 uses captured timing",
    )

    subparsers = parser.add_subparsers(dest="action", required=True)

    discover = subparsers.add_parser(
        "discover",
        help="Find USB SDS-series scanners and LAN-connected SDS200 scanners",
    )
    discover.add_argument(
        "--network",
        action="append",
        metavar="CIDR",
        help="IPv4 network to probe; repeat for multiple networks",
    )
    discover.add_argument(
        "--timeout",
        type=_positive_float,
        default=DEFAULT_DISCOVERY_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Per-host LAN probe timeout "
            f"(default: {DEFAULT_DISCOVERY_TIMEOUT})"
        ),
    )
    discover.add_argument(
        "--workers",
        type=_positive_integer,
        default=DEFAULT_DISCOVERY_WORKERS,
        metavar="COUNT",
        help=(
            "Maximum concurrent LAN probes "
            f"(default: {DEFAULT_DISCOVERY_WORKERS})"
        ),
    )
    discover.add_argument(
        "--max-hosts",
        type=_positive_integer,
        default=DEFAULT_MAX_DISCOVERY_HOSTS,
        metavar="COUNT",
        help="Safety limit for active LAN probes",
    )
    discovery_mode = discover.add_mutually_exclusive_group()
    discovery_mode.add_argument(
        "--usb-only",
        action="store_true",
        help="Only list locally attached USB scanners",
    )
    discovery_mode.add_argument(
        "--network-only",
        action="store_true",
        help="Only probe the local network",
    )

    subparsers.add_parser("info", help="Show model, firmware, volume, and squelch")
    subparsers.add_parser(
        "battery",
        help="Show available handheld battery information",
    )
    health = subparsers.add_parser(
        "health", help="Run or continuously watch connection health"
    )
    health.add_argument(
        "--watch",
        type=_positive_float,
        metavar="SECONDS",
        help="Repeat health checks until interrupted",
    )
    health.add_argument("--json", action="store_true", help="Print JSON output")
    health.add_argument(
        "--history",
        action="store_true",
        help="Include the bounded health-history summary",
    )
    events = subparsers.add_parser(
        "events",
        help="Stream structured connection, retry, failover, and state events",
    )
    events.add_argument("--json", action="store_true", help="Print JSON Lines output")
    events.add_argument(
        "--interval",
        type=_positive_integer,
        default=500,
        metavar="MS",
        help="PSI update interval used for state events (default: 500)",
    )
    subparsers.add_parser("raw", help="Print packets until interrupted")
    subparsers.add_parser("scanner-info", help="Get structured GSI scanner information")

    daemon = subparsers.add_parser(
        "daemon",
        help="Run the single-owner scanner and audio runtime in the foreground",
    )
    daemon.add_argument(
        "--interval",
        type=_positive_integer,
        default=500,
        metavar="MS",
        help="PSI update interval in milliseconds (default: 500)",
    )
    daemon.add_argument(
        "--psi-timeout",
        type=_positive_float,
        default=3.0,
        metavar="SECONDS",
        help="Timeout for the initial PSI response (default: 3.0)",
    )
    daemon.add_argument(
        "--rtsp-port",
        type=_remote_port,
        default=DEFAULT_RTSP_PORT,
        metavar="PORT",
        help=f"Scanner audio RTSP port (default: {DEFAULT_RTSP_PORT})",
    )
    daemon.add_argument(
        "--rtsp-timeout",
        type=_positive_float,
        default=5.0,
        metavar="SECONDS",
        help="RTSP operation timeout (default: 5.0)",
    )
    daemon.add_argument(
        "--rtp-bind-address",
        default="",
        metavar="ADDRESS",
        help="Local address for the daemon RTP socket",
    )
    daemon.add_argument(
        "--rtp-bind-port",
        type=_local_port,
        default=0,
        metavar="PORT",
        help="Local daemon RTP port; 0 selects an ephemeral port",
    )
    daemon.add_argument(
        "--keepalive-interval",
        type=_positive_float,
        default=15.0,
        metavar="SECONDS",
        help="RTSP GET_PARAMETER interval (default: 15.0)",
    )
    daemon.add_argument(
        "--socket-path",
        type=Path,
        metavar="PATH",
        help=(
            "Explicit absolute Unix socket path; otherwise use "
            "XDG_RUNTIME_DIR or the user state directory"
        ),
    )
    daemon.add_argument(
        "--api-max-clients",
        type=_positive_integer,
        default=DAEMON_API_DEFAULT_MAX_CLIENTS,
        metavar="COUNT",
        help=(
            "Maximum concurrent local API clients "
            f"(default: {DAEMON_API_DEFAULT_MAX_CLIENTS})"
        ),
    )
    daemon.add_argument(
        "--api-max-request-bytes",
        type=_positive_integer,
        default=DAEMON_API_DEFAULT_MAX_REQUEST_BYTES,
        metavar="BYTES",
        help=(
            "Maximum local API request size "
            f"(default: {DAEMON_API_DEFAULT_MAX_REQUEST_BYTES})"
        ),
    )
    daemon.add_argument(
        "--api-max-response-bytes",
        type=_positive_integer,
        default=DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES,
        metavar="BYTES",
        help=(
            "Maximum local API response size "
            f"(default: {DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES})"
        ),
    )
    daemon.add_argument(
        "--api-client-timeout",
        type=_positive_float,
        default=DAEMON_API_DEFAULT_CLIENT_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Idle local API client timeout "
            f"(default: {DAEMON_API_DEFAULT_CLIENT_TIMEOUT})"
        ),
    )
    daemon.add_argument(
        "--api-shutdown-timeout",
        type=_positive_float,
        default=DAEMON_API_DEFAULT_SHUTDOWN_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Local API worker shutdown deadline "
            f"(default: {DAEMON_API_DEFAULT_SHUTDOWN_TIMEOUT})"
        ),
    )
    daemon.add_argument(
        "--event-socket-path",
        type=Path,
        metavar="PATH",
        help=(
            "Explicit absolute Unix event socket path; otherwise use "
            "XDG_RUNTIME_DIR or the user state directory"
        ),
    )
    daemon.add_argument(
        "--event-queue-capacity",
        type=_positive_integer,
        default=DAEMON_EVENT_DEFAULT_QUEUE_CAPACITY,
        metavar="COUNT",
        help=(
            "Maximum queued events per local subscriber "
            f"(default: {DAEMON_EVENT_DEFAULT_QUEUE_CAPACITY})"
        ),
    )
    daemon.add_argument(
        "--event-max-clients",
        type=_positive_integer,
        default=DAEMON_EVENT_DEFAULT_MAX_CLIENTS,
        metavar="COUNT",
        help=(
            "Maximum concurrent local event clients "
            f"(default: {DAEMON_EVENT_DEFAULT_MAX_CLIENTS})"
        ),
    )
    daemon.add_argument(
        "--event-max-bytes",
        type=_positive_integer,
        default=DAEMON_EVENT_DEFAULT_MAX_BYTES,
        metavar="BYTES",
        help=(
            "Maximum encoded local event size "
            f"(default: {DAEMON_EVENT_DEFAULT_MAX_BYTES})"
        ),
    )
    daemon.add_argument(
        "--event-send-timeout",
        type=_positive_float,
        default=DAEMON_EVENT_DEFAULT_SEND_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Local event client send timeout "
            f"(default: {DAEMON_EVENT_DEFAULT_SEND_TIMEOUT})"
        ),
    )
    daemon.add_argument(
        "--event-shutdown-timeout",
        type=_positive_float,
        default=DAEMON_EVENT_DEFAULT_SHUTDOWN_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Local event worker shutdown deadline "
            f"(default: {DAEMON_EVENT_DEFAULT_SHUTDOWN_TIMEOUT})"
        ),
    )
    daemon.add_argument(
        "--pcmu-socket-path",
        type=Path,
        metavar="PATH",
        help=(
            "Explicit absolute Unix PCMU socket path; otherwise use "
            "XDG_RUNTIME_DIR or the user state directory"
        ),
    )
    daemon.add_argument(
        "--pcmu-queue-capacity",
        type=_positive_integer,
        default=PCMU_DEFAULT_QUEUE_CAPACITY,
        metavar="COUNT",
        help=(
            "Maximum queued PCMU packets per local subscriber "
            f"(default: {PCMU_DEFAULT_QUEUE_CAPACITY})"
        ),
    )
    daemon.add_argument(
        "--pcmu-max-clients",
        type=_positive_integer,
        default=DAEMON_PCMU_DEFAULT_MAX_CLIENTS,
        metavar="COUNT",
        help=(
            "Maximum concurrent local PCMU clients "
            f"(default: {DAEMON_PCMU_DEFAULT_MAX_CLIENTS})"
        ),
    )
    daemon.add_argument(
        "--pcmu-max-payload-bytes",
        type=_positive_integer,
        default=PCMU_DEFAULT_MAX_PAYLOAD_BYTES,
        metavar="BYTES",
        help=(
            "Maximum accepted PCMU packet payload size "
            f"(default: {PCMU_DEFAULT_MAX_PAYLOAD_BYTES})"
        ),
    )
    daemon.add_argument(
        "--pcmu-max-endpoint-bytes",
        type=_positive_integer,
        default=PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
        metavar="BYTES",
        help=(
            "Maximum encoded PCMU endpoint size "
            f"(default: {PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES})"
        ),
    )
    daemon.add_argument(
        "--pcmu-max-frame-bytes",
        type=_positive_integer,
        default=PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
        metavar="BYTES",
        help=(
            "Maximum encoded PCMU frame size "
            f"(default: {PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES})"
        ),
    )
    daemon.add_argument(
        "--pcmu-send-timeout",
        type=_positive_float,
        default=DAEMON_PCMU_DEFAULT_SEND_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Local PCMU client send timeout "
            f"(default: {DAEMON_PCMU_DEFAULT_SEND_TIMEOUT})"
        ),
    )
    daemon.add_argument(
        "--pcmu-shutdown-timeout",
        type=_positive_float,
        default=DAEMON_PCMU_DEFAULT_SHUTDOWN_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Local PCMU worker shutdown deadline "
            f"(default: {DAEMON_PCMU_DEFAULT_SHUTDOWN_TIMEOUT})"
        ),
    )

    daemon_client = subparsers.add_parser(
        "daemon-client",
        help="Query a running local daemon without opening scanner hardware",
    )
    daemon_client.add_argument(
        "--socket-path",
        type=Path,
        metavar="PATH",
        help=(
            "Explicit absolute daemon API socket path; otherwise use "
            "XDG_RUNTIME_DIR or the user state directory"
        ),
    )
    daemon_client.add_argument(
        "--timeout",
        type=_positive_float,
        default=DAEMON_API_CLIENT_DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=(
            "Daemon connect and response timeout "
            f"(default: {DAEMON_API_CLIENT_DEFAULT_TIMEOUT})"
        ),
    )
    daemon_client.add_argument(
        "--max-response-bytes",
        type=_positive_integer,
        default=DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES,
        metavar="BYTES",
        help=(
            "Maximum accepted daemon API response size "
            f"(default: {DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES})"
        ),
    )
    daemon_client_commands = daemon_client.add_subparsers(
        dest="daemon_client_action",
        required=True,
    )
    daemon_status = daemon_client_commands.add_parser(
        "status",
        help="Show negotiated protocol and current daemon health",
    )
    daemon_status.add_argument(
        "--json",
        action="store_true",
        help="Print the negotiated status as JSON",
    )
    daemon_client_commands.add_parser(
        "snapshot",
        help="Print the complete authoritative runtime snapshot as JSON",
    )

    tui = subparsers.add_parser(
        "tui",
        help="Launch the optional full-screen Textual interface",
    )
    tui.add_argument(
        "--interval",
        type=_positive_integer,
        default=500,
        metavar="MS",
        help="PSI update interval in milliseconds (default: 500)",
    )
    tui.add_argument(
        "--stale-after",
        type=_positive_float,
        default=3.0,
        metavar="SECONDS",
        help="Mark live scanner state stale after this age (default: 3.0)",
    )
    tui.add_argument(
        "--psi-auto-recover",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reconnect automatically after sustained stale PSI (default: enabled)",
    )
    tui.add_argument(
        "--psi-recover-after",
        type=_positive_float,
        default=10.0,
        metavar="SECONDS",
        help="Reconnect after PSI remains stale this long (default: 10.0)",
    )
    tui.add_argument(
        "--psi-recovery-cooldown",
        type=_non_negative_float,
        default=60.0,
        metavar="SECONDS",
        help="Minimum interval between automatic PSI reconnects (default: 60.0)",
    )
    tui_audio_destination = tui.add_mutually_exclusive_group()
    tui_audio_destination.add_argument(
        "--audio-output",
        type=Path,
        metavar="FILE",
        help="Use one explicit R-key PCM WAV destination",
    )
    tui_audio_destination.add_argument(
        "--audio-directory",
        type=Path,
        metavar="DIRECTORY",
        help="Store repeatable timestamped recordings and build the TUI library",
    )
    tui.add_argument(
        "--audio-template",
        metavar="TEMPLATE",
        help="Recording filename template using {timestamp} (requires --audio-directory)",
    )
    tui.add_argument(
        "--audio-organize-by",
        type=_recording_organization,
        metavar="COMPONENTS",
        help=(
            "Comma-separated recording directories from scanner,date,system,"
            "department,site,channel (requires --audio-directory)"
        ),
    )
    tui.add_argument(
        "--audio-force",
        action="store_true",
        help="Overwrite an existing explicit --audio-output file",
    )
    tui.add_argument(
        "--audio-metadata",
        action="store_true",
        help="Write an adjacent JSON sidecar for each completed TUI recording",
    )
    tui.add_argument(
        "--audio-playback",
        action="store_true",
        help="Start live playback after the first connected live PSI update",
    )
    tui.add_argument(
        "--audio-device",
        type=_audio_device,
        metavar="DEVICE",
        help="PortAudio output device name or index for TUI playback",
    )
    tui.add_argument(
        "--audio-buffer-ms",
        type=_positive_integer,
        default=250,
        metavar="MS",
        help="Bounded TUI playback queue in milliseconds (default: 250)",
    )
    tui.add_argument(
        "--audio-history-limit",
        type=_positive_integer,
        default=100,
        metavar="COUNT",
        help="Newest compatible recordings retained in the TUI library (default: 100)",
    )
    tui.add_argument(
        "--audio-rtsp-port",
        type=_remote_port,
        default=DEFAULT_RTSP_PORT,
        metavar="PORT",
        help=f"Scanner audio RTSP port (default: {DEFAULT_RTSP_PORT})",
    )
    tui.add_argument(
        "--audio-rtp-bind-address",
        default="",
        metavar="ADDRESS",
        help="Local address for the TUI audio RTP socket",
    )
    tui.add_argument(
        "--audio-rtp-bind-port",
        type=_local_port,
        default=0,
        metavar="PORT",
        help="Local TUI audio RTP port; 0 selects an ephemeral port",
    )
    tui.add_argument(
        "--audio-keepalive-interval",
        type=_positive_float,
        default=15.0,
        metavar="SECONDS",
        help="TUI audio RTSP GET_PARAMETER interval (default: 15.0)",
    )
    subparsers.add_parser(
        "capabilities",
        help="Show model limits, validation status, and supported control features",
    )
    subparsers.add_parser(
        "audio-devices",
        help="List local PortAudio host APIs and output devices",
    )

    audio = subparsers.add_parser(
        "audio",
        help="Play and/or record SDS200 network audio",
    )
    audio.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Destination 8 kHz mono signed 16-bit PCM WAV file",
    )
    audio.add_argument(
        "--play",
        action="store_true",
        help="Play live audio through a local output device",
    )
    audio.add_argument(
        "--device",
        type=_audio_device,
        metavar="DEVICE",
        help="PortAudio output device name or index (default: system output)",
    )
    audio.add_argument(
        "--buffer-ms",
        type=_positive_integer,
        default=250,
        metavar="MS",
        help="Bounded local-playback queue in milliseconds (default: 250)",
    )
    audio.add_argument(
        "--duration",
        type=_positive_float,
        metavar="SECONDS",
        help="Stop after this many seconds; otherwise record until interrupted",
    )
    audio.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing output file",
    )
    audio.add_argument(
        "--rtsp-port",
        type=_remote_port,
        default=DEFAULT_RTSP_PORT,
        metavar="PORT",
        help=f"Scanner RTSP port (default: {DEFAULT_RTSP_PORT})",
    )
    audio.add_argument(
        "--rtp-bind-address",
        default="",
        metavar="ADDRESS",
        help="Local address for the RTP UDP socket",
    )
    audio.add_argument(
        "--rtp-bind-port",
        type=_local_port,
        default=0,
        metavar="PORT",
        help="Local RTP UDP port; 0 selects an ephemeral port",
    )
    audio.add_argument(
        "--keepalive-interval",
        type=_positive_float,
        default=15.0,
        metavar="SECONDS",
        help="RTSP GET_PARAMETER interval (default: 15.0)",
    )

    recordings = subparsers.add_parser(
        "recordings",
        help="Inspect and safely manage local recording files",
    )
    recording_commands = recordings.add_subparsers(
        dest="recordings_action",
        required=True,
    )
    retention = recording_commands.add_parser(
        "retention",
        help="Preview or explicitly execute a recording-retention policy",
    )
    retention.add_argument(
        "root",
        type=Path,
        metavar="DIRECTORY",
        help="Recording inventory root",
    )
    retention.add_argument(
        "--maximum-age-days",
        type=_positive_float,
        metavar="DAYS",
        help="Select eligible recordings older than this many days",
    )
    retention.add_argument(
        "--maximum-units",
        type=_non_negative_integer,
        metavar="COUNT",
        help="Retain at most this many managed recording units",
    )
    retention.add_argument(
        "--maximum-total-bytes",
        type=_non_negative_integer,
        metavar="BYTES",
        help="Retain at most this many managed bytes",
    )
    retention.add_argument(
        "--planned-at",
        type=_timezone_aware_datetime,
        metavar="TIMESTAMP",
        help=(
            "Fixed timezone-aware ISO 8601 planning boundary; required when "
            "executing an age policy"
        ),
    )
    retention.add_argument(
        "--json",
        action="store_true",
        help="Print stable JSON output",
    )
    retention.add_argument(
        "--execute",
        metavar="CONFIRMATION",
        help=(
            "Execute the exact plan only when this value matches its displayed "
            "confirmation token"
        ),
    )

    asterisk_moh = subparsers.add_parser(
        "asterisk-moh",
        help="Stream SDS200 network audio to Asterisk custom Music on Hold",
    )
    asterisk_moh.add_argument(
        "--rtsp-port",
        type=_remote_port,
        default=DEFAULT_RTSP_PORT,
        metavar="PORT",
        help=f"Scanner RTSP port (default: {DEFAULT_RTSP_PORT})",
    )
    asterisk_moh.add_argument(
        "--rtsp-timeout",
        type=_positive_float,
        default=2.0,
        metavar="SECONDS",
        help="RTSP operation timeout (default: 2.0)",
    )
    asterisk_moh.add_argument(
        "--rtp-bind-address",
        default="",
        metavar="ADDRESS",
        help="Local address for the RTP UDP socket",
    )
    asterisk_moh.add_argument(
        "--rtp-bind-port",
        type=_local_port,
        default=0,
        metavar="PORT",
        help="Local RTP UDP port; 0 selects an ephemeral port",
    )
    asterisk_moh.add_argument(
        "--keepalive-interval",
        type=_positive_float,
        default=15.0,
        metavar="SECONDS",
        help="RTSP GET_PARAMETER interval (default: 15.0)",
    )
    asterisk_moh.add_argument(
        "--buffer-seconds",
        type=_positive_float,
        default=1.0,
        metavar="SECONDS",
        help="Bounded newest-audio stdout queue (default: 1.0)",
    )
    asterisk_moh.add_argument(
        "--stop-timeout",
        type=_positive_float,
        default=1.0,
        metavar="SECONDS",
        help="Maximum stdout-worker shutdown time (default: 1.0)",
    )

    for action_name, action_help in (
        ("hold", "Hold a documented scanner selection"),
        ("next", "Move forward through a documented scanner selection list"),
        ("previous", "Move backward through a documented scanner selection list"),
    ):
        navigation = subparsers.add_parser(action_name, help=action_help)
        navigation.add_argument("target", type=str.upper, choices=NAVIGATION_TARGETS)
        navigation.add_argument(
            "first",
            nargs="?",
            help="Primary protocol index or frequency; omit when the target allows it",
        )
        navigation.add_argument(
            "second",
            nargs="?",
            help="Optional parent index required by some targets",
        )
        if action_name != "hold":
            navigation.add_argument(
                "--count",
                type=_positive_integer,
                default=1,
                choices=range(1, 9),
                help="Number of selections to move (1-8)",
            )
        navigation.add_argument("--timeout", type=_positive_float, default=2.0)

    monitor = subparsers.add_parser(
        "monitor",
        help="Continuously display live PSI scanner state",
    )
    monitor.add_argument(
        "--interval",
        type=_positive_integer,
        default=500,
        metavar="MS",
        help="PSI update interval in milliseconds (default: 500)",
    )
    monitor.add_argument(
        "--no-clear",
        action="store_true",
        help="Print each changed state instead of refreshing the screen",
    )

    command = subparsers.add_parser("command", help="Send one raw command")
    command_action = command.add_argument(
        "value",
        help="Command without the terminating carriage return",
    )
    _set_completer(command_action, command_completer)
    command.add_argument("--timeout", type=_positive_float, default=2.0)

    completion = subparsers.add_parser(
        "completion",
        help="Print a shell tab-completion activation script",
    )
    completion.add_argument("shell", choices=SUPPORTED_SHELLS)

    profile = subparsers.add_parser(
        "profile",
        help="Manage saved scanner connection profiles",
    )
    profile_commands = profile.add_subparsers(dest="profile_action", required=True)
    profile_commands.add_parser("list", help="List saved profiles")
    profile_show = profile_commands.add_parser("show", help="Show one saved profile")
    profile_show.add_argument("name")
    profile_remove = profile_commands.add_parser("remove", help="Delete a saved profile")
    profile_remove.add_argument("name")
    profile_add = profile_commands.add_parser("add", help="Create or replace a profile")
    profile_add.add_argument("name")
    profile_add.add_argument("--port", dest="profile_port", type=Path)
    profile_add.add_argument("--host", dest="profile_host")
    profile_add.add_argument(
        "--model",
        dest="profile_model",
        type=_scanner_model,
        metavar="MODEL",
        help="Scanner model for a serial profile",
    )
    profile_add.add_argument(
        "--udp-port",
        dest="profile_udp_port",
        type=_remote_port,
        default=DEFAULT_UDP_PORT,
    )
    profile_add.add_argument(
        "--bind-address",
        dest="profile_bind_address",
        default="",
    )
    profile_add.add_argument(
        "--bind-port",
        dest="profile_bind_port",
        type=_local_port,
        default=0,
    )
    profile_add.add_argument(
        "--prefer",
        dest="profile_preference",
        choices=TRANSPORT_PREFERENCES,
        default="serial",
        help="Preferred endpoint when both --port and --host are supplied",
    )
    _add_profile_recovery_options(profile_add)
    profile_discover = profile_commands.add_parser(
        "discover",
        help="Discover a scanner and save a serial, network, or fallback profile",
    )
    profile_discover.add_argument("name")
    profile_discover.add_argument(
        "--model",
        dest="profile_model",
        type=_scanner_model,
        metavar="MODEL",
        help="Only discover this scanner model",
    )
    profile_discover.add_argument(
        "--network",
        dest="profile_networks",
        action="append",
        metavar="CIDR",
        help="IPv4 network to probe; repeat for multiple networks",
    )
    profile_discover.add_argument(
        "--timeout",
        dest="profile_timeout",
        type=_positive_float,
        default=DEFAULT_DISCOVERY_TIMEOUT,
    )
    profile_discover.add_argument(
        "--workers",
        dest="profile_workers",
        type=_positive_integer,
        default=DEFAULT_DISCOVERY_WORKERS,
    )
    profile_discover.add_argument(
        "--max-hosts",
        dest="profile_max_hosts",
        type=_positive_integer,
        default=DEFAULT_MAX_DISCOVERY_HOSTS,
    )
    profile_discover.add_argument(
        "--prefer",
        dest="profile_preference",
        choices=TRANSPORT_PREFERENCES,
        default="serial",
    )
    _add_profile_recovery_options(profile_discover)

    profile_discovery_mode = profile_discover.add_mutually_exclusive_group()
    profile_discovery_mode.add_argument("--usb-only", action="store_true")
    profile_discovery_mode.add_argument("--network-only", action="store_true")

    profile_repair = profile_commands.add_parser(
        "repair",
        help="Refresh stale USB and network endpoints using discovery",
    )
    profile_repair.add_argument("name")
    profile_repair.add_argument(
        "--network",
        dest="profile_networks",
        action="append",
        metavar="CIDR",
        help="IPv4 network to probe; repeat for multiple networks",
    )
    profile_repair.add_argument(
        "--timeout",
        dest="profile_timeout",
        type=_positive_float,
        default=DEFAULT_DISCOVERY_TIMEOUT,
    )
    profile_repair.add_argument(
        "--workers",
        dest="profile_workers",
        type=_positive_integer,
        default=DEFAULT_DISCOVERY_WORKERS,
    )
    profile_repair.add_argument(
        "--max-hosts",
        dest="profile_max_hosts",
        type=_positive_integer,
        default=DEFAULT_MAX_DISCOVERY_HOSTS,
    )
    profile_repair.add_argument(
        "--dry-run",
        action="store_true",
        help="Show repairs without writing the profile file",
    )
    return parser


def selected_port(
    explicit: Path | None,
    model: ScannerModel | None = None,
) -> Path:
    return choose_scanner(explicit, model=model)


def _apply_cli_configuration(
    args: argparse.Namespace,
    *,
    paths: ConfigurationPaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedApplicationConfiguration:
    command_line_values = {
        field: getattr(args, field)
        for field in APPLICATION_CONFIGURATION_FIELDS
        if hasattr(args, field)
    }

    if hasattr(args, "verbose") and "log_level" not in command_line_values:
        command_line_values["log_level"] = (
            "INFO" if args.verbose == 1 else "DEBUG"
        )

    resolved = load_application_configuration(
        paths=paths,
        environ=environ,
        command_line_values=command_line_values,
    )
    configuration = resolved.configuration

    for field in APPLICATION_CONFIGURATION_FIELDS:
        setattr(args, field, getattr(configuration, field))

    if not hasattr(args, "verbose"):
        args.verbose = 0

    return resolved


def _reconnect_policy_from_args(args: argparse.Namespace) -> ReconnectPolicy:
    max_attempts = args.reconnect_attempts or None
    return ReconnectPolicy(
        initial_delay=args.reconnect_initial_delay,
        multiplier=args.reconnect_multiplier,
        max_delay=args.reconnect_max_delay,
        max_attempts=max_attempts,
    )


def _profile_with_recovery_overrides(
    profile: ConnectionProfile,
    args: argparse.Namespace,
) -> ConnectionProfile:
    overrides = (
        args.recover_preferred,
        args.recovery_probe_interval,
        args.recovery_probe_timeout,
        args.recovery_stability_window,
        args.recovery_cooldown,
    )
    if all(value is None for value in overrides):
        return profile
    if profile.kind != "fallback":
        raise ValueError(
            "Preferred recovery options require a fallback --profile"
        )

    numeric_override = any(value is not None for value in overrides[1:])
    recover_preferred = (
        args.recover_preferred
        if args.recover_preferred is not None
        else profile.recover_preferred or numeric_override
    )
    return replace(
        profile,
        recover_preferred=recover_preferred,
        recovery_probe_interval=(
            args.recovery_probe_interval
            if args.recovery_probe_interval is not None
            else profile.recovery_probe_interval
        ),
        recovery_probe_timeout=(
            args.recovery_probe_timeout
            if args.recovery_probe_timeout is not None
            else profile.recovery_probe_timeout
        ),
        recovery_stability_window=(
            args.recovery_stability_window
            if args.recovery_stability_window is not None
            else profile.recovery_stability_window
        ),
        recovery_cooldown=(
            args.recovery_cooldown
            if args.recovery_cooldown is not None
            else profile.recovery_cooldown
        ),
    )


def _radio_from_profile(
    profile: ConnectionProfile,
    *,
    preference: TransportPreference | None,
    trace_path: Path | None,
    max_xml_retries: int,
    reconnect_policy: ReconnectPolicy,
    health_history_limit: int,
    capture_path: Path | None,
    capture_redactions: tuple[str, ...],
) -> SDSScanner:
    return SDSScanner.from_profile(
        profile,
        preference=preference,
        trace_path=trace_path,
        max_xml_retries=max_xml_retries,
        reconnect_policy=reconnect_policy,
        health_history_limit=health_history_limit,
        capture_path=capture_path,
        capture_redactions=capture_redactions,
    )


def selected_radio(
    args: argparse.Namespace,
    *,
    profile_store: ProfileStore | None = None,
) -> SDSScanner:
    reconnect_policy = _reconnect_policy_from_args(args)
    capture_redactions: tuple[str, ...] = tuple(args.redact)
    if capture_redactions and args.capture is None:
        raise ValueError("--redact requires --capture")
    if args.replay is not None:
        if args.connection_preference is not None:
            raise ValueError("--prefer cannot be used with --replay")
        recovery_options = (
            args.recover_preferred,
            args.recovery_probe_interval,
            args.recovery_probe_timeout,
            args.recovery_stability_window,
            args.recovery_cooldown,
        )
        if any(value is not None for value in recovery_options):
            raise ValueError("Preferred recovery options cannot be used with --replay")
        if args.udp_port is not None or args.bind_address or args.bind_port:
            raise ValueError("Network socket options cannot be used with --replay")
        if args.capture is not None:
            raise ValueError("--capture cannot be combined with --replay")
        return SDSScanner.replay(
            args.replay,
            speed=args.replay_speed,
            expected_model=args.model,
            trace_path=args.trace,
            health_history_limit=args.health_history_limit,
        )
    if args.replay_speed != 0:
        raise ValueError("--replay-speed requires --replay")
    if args.profile is not None:
        if args.model is not None:
            raise ValueError("--model cannot override a saved profile")
        if args.udp_port is not None or args.bind_address or args.bind_port:
            raise ValueError(
                "--udp-port, --bind-address, and --bind-port cannot override a profile"
            )
        store = profile_store or ProfileStore(args.config)
        profile = _profile_with_recovery_overrides(store.get(args.profile), args)
        return _radio_from_profile(
            profile,
            preference=args.connection_preference,
            trace_path=args.trace,
            max_xml_retries=args.max_xml_retries,
            reconnect_policy=reconnect_policy,
            health_history_limit=args.health_history_limit,
            capture_path=args.capture,
            capture_redactions=capture_redactions,
        )
    recovery_options = (
        args.recover_preferred,
        args.recovery_probe_interval,
        args.recovery_probe_timeout,
        args.recovery_stability_window,
        args.recovery_cooldown,
    )
    if any(value is not None for value in recovery_options):
        raise ValueError("Preferred recovery options require a fallback --profile")
    if args.connection_preference is not None:
        raise ValueError("--prefer requires a fallback --profile")
    if args.host is not None:
        if args.model not in {None, "SDS200"}:
            raise ValueError("Native network control is only available on the SDS200")
        return SDSScanner.network(
            args.host,
            remote_port=args.udp_port or DEFAULT_UDP_PORT,
            local_host=args.bind_address,
            local_port=args.bind_port,
            max_xml_retries=args.max_xml_retries,
            reconnect_policy=reconnect_policy,
            trace_path=args.trace,
            health_history_limit=args.health_history_limit,
            capture_path=args.capture,
            capture_redactions=capture_redactions,
        )
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError("--udp-port, --bind-address, and --bind-port require --host")
    return SDSScanner(
        selected_port(args.port, args.model),
        reconnect_policy=reconnect_policy,
        trace_path=args.trace,
        health_history_limit=args.health_history_limit,
        expected_model=args.model,
        capture_path=args.capture,
        capture_redactions=capture_redactions,
    )


def _print_health(health: RadioHealth, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(health.as_dict(), indent=2, sort_keys=True))
        return
    print(f"Checked:   {health.checked_at.isoformat()}")
    print(f"Status:    {health.status}")
    print(f"Endpoint:  {health.endpoint}")
    print(f"Connected: {'yes' if health.connected else 'no'}")
    print(f"Model:     {health.model or '-'}")
    print(f"Firmware:  {health.firmware or '-'}")
    print(
        "Latency:   "
        + (f"{health.latency_ms:.1f} ms" if health.latency_ms is not None else "-")
    )
    print(f"Connection events: {health.connection_events}")
    print(f"Last connected:    {health.last_connected_at or '-'}")
    print(f"Last disconnected: {health.last_disconnected_at or '-'}")
    print(f"Last response:     {health.last_response_at or '-'}")
    print(f"Last state:        {health.last_state_at or '-'}")
    print(f"PSI active:        {'yes' if health.psi_active else 'no'}")
    if health.error is not None:
        print(f"Error:      {health.error}")
    if health.statistics:
        print("Transport statistics:")
        for name, value in health.statistics.items():
            print(f"  {name.replace('_', ' ').title():28s} {value}")


def _print_health_summary(
    summary: HealthSummary,
    *,
    as_json: bool = False,
) -> None:
    if as_json:
        print(json.dumps({"history": summary.as_dict()}, sort_keys=True))
        return
    print("Health history:")
    print(f"  Samples:             {summary.samples}")
    print(f"  Healthy:             {summary.healthy_samples}")
    print(f"  Degraded:            {summary.degraded_samples}")
    print(f"  Unhealthy:           {summary.unhealthy_samples}")
    print(f"  Disconnected:        {summary.disconnected_samples}")
    print(f"  Error rate:          {summary.error_rate:.1%}")
    average = summary.average_latency_ms
    maximum = summary.maximum_latency_ms
    print(
        f"  Average latency:     {average:.1f} ms"
        if average is not None
        else "  Average latency:     -"
    )
    print(
        f"  Maximum latency:     {maximum:.1f} ms"
        if maximum is not None
        else "  Maximum latency:     -"
    )
    print(f"  Connection changes:  {summary.connection_events_delta}")
    print(f"  Reconnects:          {summary.reconnects}")
    print(f"  Failovers:           {summary.failovers}")
    print(f"  Preferred recoveries: {summary.preferred_recoveries}")
    if summary.recent_errors:
        print("  Recent errors:")
        for error in summary.recent_errors:
            print(f"    - {error}")


def _print_event(event: RadioEvent, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(event.as_dict(), sort_keys=True), flush=True)
        return
    endpoint = f" [{event.endpoint}]" if event.endpoint else ""
    print(
        f"{event.observed_at.isoformat()} {event.kind}{endpoint}: {event.message}",
        flush=True,
    )


def _run_events(radio: SDSScanner, args: argparse.Namespace) -> int:
    radio.on_event(lambda event: _print_event(event, as_json=args.json))
    _print_event(
        RadioEvent.create(
            "session.started",
            "Structured event stream started",
            endpoint=radio.endpoint,
            data={"connected": radio.connected},
        ),
        as_json=args.json,
    )
    try:
        with radio.scanner_info_push(args.interval):
            radio.wait()
    except KeyboardInterrupt:
        return 0
    return 0


def _print_health_observation(
    radio: SDSScanner,
    health: RadioHealth,
    args: argparse.Namespace,
) -> None:
    if args.json:
        payload = health.as_dict()
        if args.history:
            payload["history"] = radio.health_summary().as_dict()
        print(json.dumps(payload, sort_keys=True))
        return
    _print_health(health)
    if args.history:
        _print_health_summary(radio.health_summary())


def _run_health(radio: SDSScanner, args: argparse.Namespace) -> int:
    if args.watch is None:
        _print_health_observation(radio, radio.health_check(), args)
        return 0
    try:
        while True:
            try:
                health = radio.health_check()
            except SDS200Error as exc:
                health = radio.health_snapshot(error=str(exc))
            _print_health_observation(radio, health, args)
            if not args.json:
                print()
            sleep(args.watch)
    except KeyboardInterrupt:
        return 0


def _manage_profile(args: argparse.Namespace, store: ProfileStore) -> int:
    if args.profile_action == "list":
        profiles = store.list()
        if not profiles:
            print(f"No profiles in {store.path}")
            return 0
        for profile in profiles:
            if profile.kind == "serial":
                endpoint = profile.port
            elif profile.kind == "network":
                endpoint = profile.host
            else:
                endpoint = (
                    f"{profile.preference}: {profile.host} | {profile.port}"
                )
            model = profile.model or "unknown"
            print(f"{profile.name:20s} {model:7s} {profile.kind:8s} {endpoint}")
        return 0

    if args.profile_action == "show":
        profile = store.get(args.name)
        print(f"Name:         {profile.name}")
        print(f"Kind:         {profile.kind}")
        print(f"Model:        {profile.model or 'unknown'}")
        if profile.kind in {"serial", "fallback"}:
            print(f"Port:         {profile.port}")
        if profile.kind in {"network", "fallback"}:
            print(f"Host:         {profile.host}")
            print(f"UDP port:     {profile.udp_port}")
            print(f"Bind address: {profile.bind_address or '*'}")
            print(f"Bind port:    {profile.bind_port}")
        if profile.kind == "fallback":
            print(f"Preference:   {profile.preference}")
            print(
                "Recovery:     "
                + ("enabled" if profile.recover_preferred else "disabled")
            )
            if profile.recover_preferred:
                print(f"Probe interval: {profile.recovery_probe_interval:g} s")
                print(f"Probe timeout:  {profile.recovery_probe_timeout:g} s")
                print(
                    f"Stability:      {profile.recovery_stability_window:g} s"
                )
                print(f"Cooldown:       {profile.recovery_cooldown:g} s")
        return 0

    if args.profile_action == "remove":
        store.remove(args.name)
        print(f"Removed profile {args.name!r}")
        return 0

    if args.profile_action == "add":
        if args.profile_port is None and args.profile_host is None:
            raise ValueError("profile add requires --port, --host, or both")
        if args.profile_port is not None and args.profile_host is not None:
            if args.profile_model not in {None, "SDS200"}:
                raise ValueError("Fallback profiles are only supported for the SDS200")
            profile = ConnectionProfile.fallback(
                args.name,
                port=args.profile_port,
                host=args.profile_host,
                udp_port=args.profile_udp_port,
                bind_address=args.profile_bind_address,
                bind_port=args.profile_bind_port,
                preference=args.profile_preference,
                recover_preferred=args.profile_recover_preferred,
                recovery_probe_interval=args.profile_recovery_probe_interval,
                recovery_probe_timeout=args.profile_recovery_probe_timeout,
                recovery_stability_window=args.profile_recovery_stability_window,
                recovery_cooldown=args.profile_recovery_cooldown,
            )
        elif args.profile_port is not None:
            if args.profile_recover_preferred:
                raise ValueError("Preferred recovery requires a fallback profile")
            profile = ConnectionProfile.serial(
                args.name,
                args.profile_port,
                model=args.profile_model,
            )
        else:
            if args.profile_model not in {None, "SDS200"}:
                raise ValueError("Network profiles are only supported for the SDS200")
            if args.profile_recover_preferred:
                raise ValueError("Preferred recovery requires a fallback profile")
            assert args.profile_host is not None
            profile = ConnectionProfile.network(
                args.name,
                args.profile_host,
                udp_port=args.profile_udp_port,
                bind_address=args.profile_bind_address,
                bind_port=args.profile_bind_port,
            )
        store.put(profile)
        print(f"Saved profile {profile.name!r} in {store.path}")
        return 0
    if args.profile_action == "discover":
        serial_devices = (
            ()
            if args.network_only
            else tuple(discover_scanners(model=args.profile_model))
        )
        if args.network_only and args.profile_model not in {None, "SDS200"}:
            raise ValueError("Network discovery is only supported for the SDS200")
        network_scanners = (
            ()
            if args.usb_only or args.profile_model not in {None, "SDS200"}
            else tuple(
                discover_network_scanners(
                    args.profile_networks,
                    timeout=args.profile_timeout,
                    workers=args.profile_workers,
                    max_hosts=args.profile_max_hosts,
                )
            )
        )
        profile = profile_from_discovery(
            args.name,
            serial_devices,
            network_scanners,
            preference=args.profile_preference,
            recover_preferred=args.profile_recover_preferred,
            recovery_probe_interval=args.profile_recovery_probe_interval,
            recovery_probe_timeout=args.profile_recovery_probe_timeout,
            recovery_stability_window=args.profile_recovery_stability_window,
            recovery_cooldown=args.profile_recovery_cooldown,
        )
        store.put(profile)
        print(
            f"Saved discovered {profile.kind} profile {profile.name!r} "
            f"for {profile.model or 'unknown model'} in {store.path}"
        )
        if profile.kind == "fallback":
            print(f"Preferred: {profile.preference}")
            print(f"USB:       {profile.port}")
            print(f"Network:   udp://{profile.host}:{profile.udp_port}")
        return 0
    if args.profile_action == "repair":
        current = store.get(args.name)
        serial_devices = (
            tuple(discover_scanners(model=current.model))
            if current.kind in {"serial", "fallback"}
            else ()
        )
        network_scanners = (
            tuple(
                discover_network_scanners(
                    args.profile_networks,
                    timeout=args.profile_timeout,
                    workers=args.profile_workers,
                    max_hosts=args.profile_max_hosts,
                )
            )
            if current.kind in {"network", "fallback"}
            else ()
        )
        result: ProfileRepairResult = repair_profile(
            current,
            serial_devices,
            network_scanners,
        )
        if not result.changed:
            print(f"Profile {current.name!r} is already current.")
            return 0
        print(f"Repairs for profile {current.name!r}:")
        for field, change in result.changes.items():
            print(f"  {field}: {change}")
        if args.dry_run:
            print("Dry run; profile file was not changed.")
            return 0
        store.put(result.repaired)
        print(f"Updated profile in {store.path}")
        return 0
    raise ValueError(f"Unsupported profile action: {args.profile_action}")



def _daemon_host(
    args: argparse.Namespace,
    *,
    profile_store: ProfileStore | None = None,
) -> str:
    if args.replay is not None:
        raise ValueError("daemon does not support replay captures")

    if args.profile is not None:
        if args.model is not None:
            raise ValueError("--model cannot override a saved profile")
        store = profile_store or ProfileStore(args.config)
        profile = store.get(args.profile)
        if profile.kind not in {"network", "fallback"} or profile.host is None:
            raise ValueError(
                "daemon requires a network-capable SDS200 connection profile"
            )
        return profile.host

    if args.host is None:
        raise ValueError(
            "daemon requires --host or a network-capable SDS200 --profile"
        )
    if args.model not in {None, "SDS200"}:
        raise ValueError("Daemon network audio is only available on the SDS200")
    return cast(str, args.host)


def _run_daemon(
    args: argparse.Namespace,
    *,
    configuration_paths: ConfigurationPaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    profile_store = ProfileStore(args.config) if args.profile is not None else None
    host = _daemon_host(args, profile_store=profile_store)
    scanner = selected_radio(args, profile_store=profile_store)

    socket_location = resolve_daemon_socket_location(
        args.socket_path,
        environ=environ,
        configuration_paths=configuration_paths,
    )
    event_socket_location = resolve_daemon_event_socket_location(
        args.event_socket_path,
        environ=environ,
        configuration_paths=configuration_paths,
    )
    pcmu_socket_location = resolve_daemon_pcmu_socket_location(
        args.pcmu_socket_path,
        environ=environ,
        configuration_paths=configuration_paths,
    )

    router = PcmSinkRouter(name="daemon-pcm")
    transport = NetworkAudioTransport(
        host,
        rtsp_port=args.rtsp_port,
        local_host=args.rtp_bind_address,
        local_port=args.rtp_bind_port,
        rtsp_timeout=args.rtsp_timeout,
        keepalive_interval=args.keepalive_interval,
    )
    audio = AudioFanoutSession(AudioStream(transport), (router,))
    runtime = DaemonRuntime(
        scanner,
        audio,
        router,
        psi_interval_ms=args.interval,
        psi_timeout=args.psi_timeout,
    )

    listener = DaemonSocketListener(socket_location)
    api_server = DaemonApiServer(
        listener,
        DaemonReadOnlyApi(runtime),
        max_clients=args.api_max_clients,
        max_request_bytes=args.api_max_request_bytes,
        max_response_bytes=args.api_max_response_bytes,
        client_timeout=args.api_client_timeout,
        shutdown_timeout=args.api_shutdown_timeout,
    )

    event_stream = DaemonEventStream(
        runtime,
        queue_capacity=args.event_queue_capacity,
        max_subscribers=args.event_max_clients,
        max_event_bytes=args.event_max_bytes,
    )
    event_server = DaemonEventServer(
        DaemonSocketListener(event_socket_location),
        event_stream,
        max_clients=args.event_max_clients,
        max_event_bytes=args.event_max_bytes,
        send_timeout=args.event_send_timeout,
        shutdown_timeout=args.event_shutdown_timeout,
    )

    pcmu_stream: PcmuStream | None = None
    try:
        pcmu_stream = PcmuStream(
            transport,
            queue_capacity=args.pcmu_queue_capacity,
            max_subscribers=args.pcmu_max_clients,
            max_payload_bytes=args.pcmu_max_payload_bytes,
        )
        pcmu_server = DaemonPcmuServer(
            DaemonSocketListener(pcmu_socket_location),
            pcmu_stream,
            max_clients=args.pcmu_max_clients,
            max_endpoint_bytes=args.pcmu_max_endpoint_bytes,
            max_frame_bytes=args.pcmu_max_frame_bytes,
            send_timeout=args.pcmu_send_timeout,
            shutdown_timeout=args.pcmu_shutdown_timeout,
        )
    except BaseException as construction_error:
        cleanup_errors: list[BaseException] = []

        if pcmu_stream is not None:
            try:
                pcmu_stream.close()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)

        try:
            event_stream.close()
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)

        if cleanup_errors:
            logger.error(
                "daemon PCMU construction cleanup failed "
                "construction_error=%s cleanup_error=%s",
                construction_error.__class__.__name__,
                cleanup_errors[0].__class__.__name__,
            )
        raise

    result = DaemonProcess(
        runtime,
        api_server=api_server,
        event_server=event_server,
        pcmu_server=pcmu_server,
    ).run()
    logger.info(
        "foreground daemon stopped host=%s socket=%s event_socket=%s "
        "pcmu_socket=%s signal=%s",
        host,
        socket_location.path,
        event_socket_location.path,
        pcmu_socket_location.path,
        result.last_signal,
    )
    return 0


def _reject_daemon_client_scanner_options(args: argparse.Namespace) -> None:
    if any(
        value is not None
        for value in (
            args.config,
            args.model,
            args.port,
            args.host,
            args.replay,
            args.profile,
            args.connection_preference,
        )
    ):
        raise ValueError(
            "Scanner connection selectors are not used with daemon-client."
        )
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError(
            "Scanner network socket options are not used with daemon-client."
        )
    recovery_options = (
        args.recover_preferred,
        args.recovery_probe_interval,
        args.recovery_probe_timeout,
        args.recovery_stability_window,
        args.recovery_cooldown,
    )
    if any(value is not None for value in recovery_options):
        raise ValueError(
            "Scanner recovery options are not used with daemon-client."
        )
    if (
        args.trace is not None
        or args.capture is not None
        or args.redact
        or args.replay_speed != 0
    ):
        raise ValueError(
            "Scanner trace, capture, and replay options are not used with "
            "daemon-client."
        )


def _daemon_client_mapping(
    payload: Mapping[str, object],
    name: str,
) -> Mapping[str, object]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        return {}
    if any(not isinstance(key, str) for key in value):
        return {}
    return cast(Mapping[str, object], value)


def _daemon_client_flag(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "-"


def _print_daemon_client_status(
    socket_path: Path,
    socket_source: str,
    hello: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> None:
    audio = _daemon_client_mapping(snapshot, "audio")
    router = _daemon_client_mapping(snapshot, "router")

    print(f"Daemon socket:      {socket_path}")
    print(f"Socket source:      {socket_source}")
    print(
        "Protocol:           "
        f"{hello.get('protocol', '-')} v{hello.get('selected_version', '-')}"
    )
    print(f"Runtime:            {snapshot.get('state', '-')}")
    print(
        "Scanner connected:  "
        f"{_daemon_client_flag(snapshot.get('scanner_connected'))}"
    )
    print(f"Scanner endpoint:   {snapshot.get('scanner_endpoint', '-')}")
    print(f"PSI active:         {_daemon_client_flag(snapshot.get('psi_active'))}")
    print(f"PSI interval:       {snapshot.get('psi_interval_ms', '-')} ms")
    print(f"Audio running:      {_daemon_client_flag(audio.get('running'))}")
    print(f"Router running:     {_daemon_client_flag(router.get('running'))}")
    print(f"Last error:         {snapshot.get('last_error') or '-'}")


def _run_daemon_client(
    args: argparse.Namespace,
    *,
    configuration_paths: ConfigurationPaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    _reject_daemon_client_scanner_options(args)
    location = resolve_daemon_socket_location(
        args.socket_path,
        environ=environ,
        configuration_paths=configuration_paths,
    )

    with DaemonApiClient(
        location,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    ) as client:
        hello = client.hello()
        operations = hello["operations"]
        assert isinstance(operations, list)
        if DaemonApiOperation.RUNTIME_SNAPSHOT.value not in operations:
            raise DaemonProtocolError(
                "The daemon does not advertise runtime.snapshot support."
            )
        snapshot = client.runtime_snapshot()

    if args.daemon_client_action == "snapshot":
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return 0

    if args.daemon_client_action != "status":
        raise ValueError(
            f"Unsupported daemon-client action: {args.daemon_client_action}"
        )

    if args.json:
        print(
            json.dumps(
                {
                    "socket": {
                        "path": str(location.path),
                        "source": location.source.value,
                    },
                    "hello": hello,
                    "runtime": snapshot,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    _print_daemon_client_status(
        location.path,
        location.source.value,
        hello,
        snapshot,
    )
    return 0


def _asterisk_moh_host(args: argparse.Namespace) -> str:
    if args.port is not None:
        raise ValueError("asterisk-moh does not use USB serial control")
    if args.replay is not None:
        raise ValueError("asterisk-moh does not support replay captures")
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError(
            "--udp-port, --bind-address, and --bind-port are control-only; "
            "use the Asterisk MOH RTP options instead"
        )
    if args.connection_preference is not None:
        raise ValueError("--prefer is not used with asterisk-moh")
    recovery_options = (
        args.recover_preferred,
        args.recovery_probe_interval,
        args.recovery_probe_timeout,
        args.recovery_stability_window,
        args.recovery_cooldown,
    )
    if any(value is not None for value in recovery_options):
        raise ValueError("Preferred recovery options are not used with asterisk-moh")
    if args.capture is not None:
        raise ValueError("--capture is not supported for asterisk-moh")
    if args.redact:
        raise ValueError("--redact requires --capture")
    if args.trace is not None:
        raise ValueError("--trace currently records control traffic, not audio")
    if args.replay_speed != 0:
        raise ValueError("--replay-speed requires --replay")

    if args.profile is not None:
        if args.model is not None:
            raise ValueError("--model cannot override a saved profile")
        profile = ProfileStore(args.config).get(args.profile)
        if profile.kind not in {"network", "fallback"} or profile.host is None:
            raise ValueError(
                "asterisk-moh requires a network-capable SDS200 connection profile"
            )
        return profile.host

    if args.host is None:
        raise ValueError(
            "asterisk-moh requires --host or a network-capable SDS200 --profile"
        )
    if args.model not in {None, "SDS200"}:
        raise ValueError("Asterisk MOH network audio is only available on the SDS200")
    return cast(str, args.host)


def _stdout_binary_stream() -> BinaryIO:
    output = getattr(sys.stdout, "buffer", None)
    if output is None:
        raise ValueError("asterisk-moh requires a binary standard-output stream")
    return cast(BinaryIO, output)


def _run_asterisk_moh(args: argparse.Namespace) -> int:
    host = _asterisk_moh_host(args)
    transport = NetworkAudioTransport(
        host,
        rtsp_port=args.rtsp_port,
        local_host=args.rtp_bind_address,
        local_port=args.rtp_bind_port,
        rtsp_timeout=args.rtsp_timeout,
        keepalive_interval=args.keepalive_interval,
    )
    sink = PcmStreamSink(
        _stdout_binary_stream(),
        name="asterisk-moh",
        buffer_seconds=args.buffer_seconds,
        stop_timeout=args.stop_timeout,
    )
    session = AudioFanoutSession(AudioStream(transport), (sink,))

    with AsteriskMohSignalController() as stop:
        try:
            session.start()
            while not stop.wait(0.1):
                if sink.wait(0):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            session.stop()

    snapshot = sink.snapshot()
    logger.info(
        "Asterisk MOH bridge stopped host=%s submitted=%d written=%d dropped=%d "
        "reader_closed=%s",
        host,
        snapshot.statistics.bytes_submitted,
        snapshot.statistics.bytes_written,
        snapshot.statistics.bytes_dropped,
        snapshot.reader_closed,
    )
    return 0


def _run_audio_devices() -> int:
    backend = inspect_audio_backend()
    print(f"Backend: {backend.backend}")
    print(f"Version: {backend.version}")

    default = next(
        (device for device in backend.output_devices if device.default),
        None,
    )
    if default is None:
        print("Default output: unavailable")
    else:
        print(
            f"Default output: {default.index}: {default.name} "
            f"[{default.host_api_name}]"
        )

    print("Host APIs:")
    if backend.host_apis:
        for host_api in backend.host_apis:
            default_index = (
                str(host_api.default_output_device)
                if host_api.default_output_device is not None
                else "none"
            )
            print(
                f"  {host_api.index}: {host_api.name} "
                f"(default output: {default_index})"
            )
    else:
        print("  none")

    print("Output devices:")
    if backend.output_devices:
        for device in backend.output_devices:
            marker = " (default)" if device.default else ""
            print(
                f"  {device.index}: {device.name} [{device.host_api_name}] "
                f"channels={device.max_output_channels} "
                f"default-rate={device.default_samplerate:g} Hz{marker}"
            )
    else:
        print("  none")
    return 0


def _run_audio(args: argparse.Namespace) -> int:
    if args.host is None:
        raise ValueError("audio requires an explicit SDS200 --host")
    if args.model not in {None, "SDS200"}:
        raise ValueError("Network audio is only available on the SDS200")
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError(
            "--udp-port, --bind-address, and --bind-port are control-only; "
            "use the audio RTP options instead"
        )
    if args.connection_preference is not None:
        raise ValueError("--prefer is not used with audio")
    recovery_options = (
        args.recover_preferred,
        args.recovery_probe_interval,
        args.recovery_probe_timeout,
        args.recovery_stability_window,
        args.recovery_cooldown,
    )
    if any(value is not None for value in recovery_options):
        raise ValueError("Preferred recovery options are not used with audio")
    if args.capture is not None:
        raise ValueError("--capture is not supported for audio recordings")
    if args.redact:
        raise ValueError("--redact requires --capture")
    if args.trace is not None:
        raise ValueError("--trace currently records control traffic, not audio")
    if args.replay_speed != 0:
        raise ValueError("--replay-speed requires --replay")

    if args.force and args.output is None:
        raise ValueError("--force requires --output")
    if args.device is not None and not args.play:
        raise ValueError("--device requires --play")
    if args.output is None and not args.play:
        raise ValueError("audio requires --play, --output, or both")

    output = args.output.expanduser() if args.output is not None else None
    transport = NetworkAudioTransport(
        args.host,
        rtsp_port=args.rtsp_port,
        local_host=args.rtp_bind_address,
        local_port=args.rtp_bind_port,
        keepalive_interval=args.keepalive_interval,
    )
    stream = AudioStream(transport)
    sinks: list[PcmSink] = []
    playback: SoundDevicePlaybackSink | None = None
    if args.play:
        playback = SoundDevicePlaybackSink(
            device=args.device,
            buffer_ms=args.buffer_ms,
        )
        sinks.append(playback)
    if output is not None:
        sinks.append(PcmWavSink(PcmuWavRecorder(output, overwrite=args.force)))
    session = AudioFanoutSession(stream, sinks)

    try:
        session.start()
        if args.duration is None:
            while True:
                sleep(3600)
        else:
            sleep(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()

    snapshot = session.snapshot()
    print(f"Streamed {snapshot.audio_duration_seconds:.1f} seconds")
    print(f"Packets: {snapshot.packets}")
    print(f"Audio samples: {snapshot.samples}")
    statistics = transport.statistics
    print(f"RTP lost: {statistics.packets_lost}")
    print(f"RTP duplicates: {statistics.duplicate_packets}")
    print(f"RTP late: {statistics.late_packets}")
    print(f"RTP malformed: {statistics.malformed_packets}")
    print(f"RTP unexpected source: {statistics.unexpected_source_packets}")
    print(f"RTP SSRC mismatches: {statistics.ssrc_mismatch_packets}")
    print(f"Timestamp discontinuities: {statistics.timestamp_discontinuities}")
    if playback is not None:
        playback_statistics = playback.statistics
        print(f"Playback device: {args.device or 'default'}")
        print(f"Playback written bytes: {playback_statistics.bytes_written}")
        print(f"Playback dropped bytes: {playback_statistics.bytes_dropped}")
        print(f"Playback underflows: {playback_statistics.underflows}")
        print(f"Playback overflows: {playback_statistics.overflows}")
        print(
            "Playback callback statuses: "
            f"{playback_statistics.callback_statuses}"
        )
    if output is not None:
        print(f"Output: {output}")
    return 0


def _run_tui(
    args: argparse.Namespace,
    *,
    log_buffer: TuiLogBuffer | None = None,
) -> int:
    try:
        from .tui import run_tui
    except ModuleNotFoundError as exc:
        missing = exc.name.split(".", 1)[0] if exc.name is not None else ""
        if missing == "textual":
            raise ValueError(
                "Textual TUI support is not installed; install it with: "
                'python -m pip install "sds200[tui]"'
            ) from exc
        raise

    if args.audio_force and args.audio_output is None:
        raise ValueError("--audio-force requires --audio-output")
    if args.audio_template is not None and args.audio_directory is None:
        raise ValueError("--audio-template requires --audio-directory")
    if args.audio_organize_by is not None and args.audio_directory is None:
        raise ValueError("--audio-organize-by requires --audio-directory")
    if (
        args.audio_metadata
        and args.audio_output is None
        and args.audio_directory is None
    ):
        raise ValueError(
            "--audio-metadata requires --audio-output or --audio-directory"
        )
    audio_requested = args.host is not None or any(
        (
            args.audio_output is not None,
            args.audio_directory is not None,
            args.audio_organize_by is not None,
            args.audio_metadata,
            args.audio_playback,
            args.audio_device is not None,
        )
    )
    audio_session: TuiAudioSession | None = None
    if audio_requested:
        if args.host is None:
            raise ValueError(
                "TUI audio requires an explicit SDS200 --host connection"
            )
        if args.model not in {None, "SDS200"}:
            raise ValueError("TUI network audio is only available on the SDS200")
        audio_transport = NetworkAudioTransport(
            args.host,
            rtsp_port=args.audio_rtsp_port,
            local_host=args.audio_rtp_bind_address,
            local_port=args.audio_rtp_bind_port,
            keepalive_interval=args.audio_keepalive_interval,
        )
        audio_session = TuiAudioSession(
            AudioStream(audio_transport),
            RecordingPathPolicy(
                output=(
                    args.audio_output.expanduser()
                    if args.audio_output is not None
                    else None
                ),
                directory=(
                    args.audio_directory.expanduser()
                    if args.audio_directory is not None
                    else None
                ),
                template=args.audio_template or DEFAULT_RECORDING_TEMPLATE,
                overwrite=args.audio_force,
                organization=(
                    args.audio_organize_by or RecordingOrganizationPolicy()
                ),
            ),
            live_playback=args.audio_playback,
            device=args.audio_device,
            buffer_ms=args.audio_buffer_ms,
            history_limit=args.audio_history_limit,
            metadata=args.audio_metadata,
            scanner="SDS200",
        )

    with selected_radio(args) as radio:
        run_tui(
            endpoint=radio.endpoint,
            model=str(radio.get_model()),
            firmware=str(radio.get_firmware()),
            info=radio.get_scanner_info(),
            radio=radio,
            audio_session=audio_session,
            interval_ms=args.interval,
            stale_after=args.stale_after,
            psi_auto_recover=args.psi_auto_recover,
            psi_recover_after=args.psi_recover_after,
            psi_recovery_cooldown=args.psi_recovery_cooldown,
            connected=radio.connected,
            palette=palette_for_name(args.theme),
            log_buffer=log_buffer,
        )
    return 0


def _run_tui_with_logging(args: argparse.Namespace) -> int:
    log_buffer = TuiLogBuffer()
    with capture_package_logs(log_buffer):
        logger.info("sdsctl starting version=%s action=%s", __version__, args.action)
        try:
            return _run_tui(args, log_buffer=log_buffer)
        finally:
            logger.info("sdsctl stopped action=%s", args.action)


def _recording_retention_policy(
    args: argparse.Namespace,
) -> RecordingRetentionPolicy:
    maximum_age = (
        timedelta(days=args.maximum_age_days)
        if args.maximum_age_days is not None
        else None
    )
    return RecordingRetentionPolicy(
        maximum_age=maximum_age,
        maximum_units=args.maximum_units,
        maximum_total_bytes=args.maximum_total_bytes,
    )


def _recording_retention_plan_payload(
    plan: RecordingRetentionPlan,
    confirmation_token: str,
) -> dict[str, object]:
    return {
        "mode": "preview",
        "confirmation_token": confirmation_token,
        "plan": plan.as_dict(),
    }


def _print_recording_retention_plan(
    plan: RecordingRetentionPlan,
    confirmation_token: str,
) -> None:
    summary = plan.summary
    policy = plan.policy
    print("Recording retention preview")
    print(f"Root:                 {plan.inventory.root}")
    print(f"Planned at:           {plan.now.isoformat() if plan.now else '-'}")
    print(
        "Maximum age:          "
        + (
            f"{policy.maximum_age.total_seconds() / 86400:g} days"
            if policy.maximum_age is not None
            else "-"
        )
    )
    print(
        "Maximum units:        "
        + (
            str(policy.maximum_units)
            if policy.maximum_units is not None
            else "-"
        )
    )
    print(
        "Maximum total bytes:  "
        + (
            str(policy.maximum_total_bytes)
            if policy.maximum_total_bytes is not None
            else "-"
        )
    )
    print(f"Managed units:        {summary.managed_units}")
    print(f"Managed bytes:        {summary.managed_bytes}")
    print(f"Selected units:       {summary.selected_units}")
    print(f"Selected bytes:       {summary.selected_bytes}")
    print(f"Retained units:       {summary.retained_units}")
    print(f"Protected units:      {summary.protected_units}")
    print(f"Projected units:      {summary.projected_units}")
    print(f"Projected bytes:      {summary.projected_bytes}")
    print(
        "All limits satisfied: "
        + ("yes" if summary.all_limits_satisfied else "no")
    )
    print(f"Confirmation token:   {confirmation_token}")
    print("Decisions:")
    for decision in plan.decisions:
        reasons = ",".join(reason.value for reason in decision.reasons)
        print(
            f"  {decision.disposition.value:7s} "
            f"{decision.total_size_bytes:12d} "
            f"{decision.entry.relative_audio_path} "
            f"[{reasons}]"
        )


def _recording_retention_execution_payload(
    plan: RecordingRetentionPlan,
    result: RecordingRetentionExecutionResult,
) -> dict[str, object]:
    return {
        "mode": "execution",
        "plan": plan.as_dict(),
        "execution": result.as_dict(),
    }


def _print_recording_retention_execution(
    result: RecordingRetentionExecutionResult,
) -> None:
    summary = result.summary
    print()
    print("Recording retention execution")
    print(f"Attempted units:      {summary.attempted_units}")
    print(f"Completed units:      {summary.completed_units}")
    print(f"Skipped units:        {summary.skipped_units}")
    print(f"Failed units:         {summary.failed_units}")
    print(f"Audio files deleted:  {summary.audio_files_deleted}")
    print(f"Sidecars deleted:     {summary.metadata_files_deleted}")
    print(f"Deleted bytes:        {summary.deleted_bytes}")
    print(f"All completed:        {'yes' if summary.all_completed else 'no'}")
    print("Results:")
    for entry in result.entries:
        message = f" — {entry.message}" if entry.message else ""
        print(
            f"  {entry.status.value:9s} "
            f"{entry.deleted_bytes:12d} "
            f"{entry.entry.relative_audio_path} "
            f"[{entry.reason.value}]{message}"
        )


def _reject_recording_connection_options(args: argparse.Namespace) -> None:
    if any(
        value is not None
        for value in (
            args.model,
            args.port,
            args.host,
            args.replay,
            args.profile,
            args.connection_preference,
        )
    ):
        raise ValueError("Connection selectors are not used with recordings.")
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError("Network socket options are not used with recordings.")


def _run_recordings(args: argparse.Namespace) -> int:
    _reject_recording_connection_options(args)
    if args.recordings_action != "retention":
        raise ValueError(f"Unsupported recordings action: {args.recordings_action}")

    policy = _recording_retention_policy(args)
    if args.planned_at is not None and policy.maximum_age is None:
        raise ValueError("--planned-at requires --maximum-age-days.")
    if (
        args.execute is not None
        and policy.maximum_age is not None
        and args.planned_at is None
    ):
        raise ValueError(
            "--planned-at is required with --execute when "
            "--maximum-age-days is used."
        )

    planned_at = args.planned_at
    if policy.maximum_age is not None and planned_at is None:
        planned_at = datetime.now(UTC)

    inventory = scan_recording_inventory(args.root)
    plan = plan_recording_retention(
        inventory,
        policy,
        now=planned_at,
    )
    confirmation_token = recording_retention_confirmation_token(plan)

    if args.execute is None:
        if args.json:
            print(
                json.dumps(
                    _recording_retention_plan_payload(
                        plan,
                        confirmation_token,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            _print_recording_retention_plan(plan, confirmation_token)
        return 0 if plan.summary.all_limits_satisfied else 1

    result = execute_recording_retention(
        plan,
        confirmation=args.execute,
    )
    if args.json:
        print(
            json.dumps(
                _recording_retention_execution_payload(plan, result),
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_recording_retention_plan(plan, confirmation_token)
        _print_recording_retention_execution(result)

    return (
        0
        if plan.summary.all_limits_satisfied and result.summary.all_completed
        else 1
    )


def _run_discovery(args: argparse.Namespace) -> int:
    if (
        args.port is not None
        or args.host is not None
        or args.profile is not None
        or args.connection_preference is not None
    ):
        raise ValueError("Connection selectors are not used with discover.")
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError("Use discover --network CIDR instead of connection options.")

    found = False
    if not args.network_only:
        for device in discover_scanners(model=args.model):
            found = True
            model = device.model or "unknown"
            print(f"USB      {device.path} -> {device.resolved_path}  {model}")

    if args.network_only and args.model not in {None, "SDS200"}:
        raise ValueError("Network discovery is only supported for the SDS200")

    if not args.usb_only and args.model in {None, "SDS200"}:
        scanners = discover_network_scanners(
            args.network,
            timeout=args.timeout,
            workers=args.workers,
            max_hosts=args.max_hosts,
        )
        for scanner in scanners:
            found = True
            print(
                f"NETWORK  {scanner.endpoint}  {scanner.model}  "
                f"{scanner.latency_ms:.1f} ms"
            )

    if not found:
        print("No matching supported SDS-series scanner found.")
        return 1
    return 0


def main(
    argv: list[str] | None = None,
    *,
    configuration_paths: ConfigurationPaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(suppress_configuration_defaults=True)
    enable_tab_completion(parser)
    args = parser.parse_args(arguments)

    try:
        _apply_cli_configuration(
            args,
            paths=configuration_paths,
            environ=environ,
        )
    except (SDS200Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        configure_logging(
            args.verbose,
            level_name=args.log_level,
            log_file=args.log_file,
        )
    except OSError as exc:
        print(f"error: could not configure logging: {exc}", file=sys.stderr)
        return 2

    if args.action == "tui":
        try:
            return _run_tui_with_logging(args)
        except (SDS200Error, OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    logger.info("sdsctl starting version=%s action=%s", __version__, args.action)

    try:
        if args.action == "completion":
            print(completion_script(args.shell))
            return 0

        if args.action == "profile":
            return _manage_profile(args, ProfileStore(args.config))

        if args.action == "discover":
            return _run_discovery(args)

        if args.action == "audio-devices":
            return _run_audio_devices()

        if args.action == "daemon":
            return _run_daemon(
                args,
                configuration_paths=configuration_paths,
                environ=environ,
            )

        if args.action == "daemon-client":
            return _run_daemon_client(
                args,
                configuration_paths=configuration_paths,
                environ=environ,
            )

        if args.action == "asterisk-moh":
            return _run_asterisk_moh(args)

        if args.action == "audio":
            return _run_audio(args)

        if args.action == "recordings":
            return _run_recordings(args)

        with selected_radio(args) as radio:
            if args.action == "info":
                print(f"Endpoint: {radio.endpoint}")
                print(f"Model:    {radio.get_model()}")
                print(f"Firmware: {radio.get_firmware()}")
                print(f"Volume:   {radio.get_volume()}")
                print(f"Squelch:  {radio.get_squelch()}")
                return 0

            if args.action == "battery":
                model = radio.get_model()
                capabilities = radio.capabilities
                assert capabilities is not None
                if capabilities.battery_level:
                    level = radio.get_battery_level()
                    value = f"{level:g}" if level is not None else "unavailable"
                    print(f"Model:   {model}")
                    print(f"Battery: {value}")
                    print("Source:  GSI Property")
                    return 0

                status = radio.get_charge_status()
                print(f"Model:       {model}")
                print(f"Status:      {status.status}")
                print(f"Capacity:    {status.capacity_percent}%")
                print(f"Voltage:     {status.voltage_mv} mV")
                print(f"Current:     {status.current_ma} mA")
                print(f"Temperature: {status.temperature_c:.2f} C")
                return 0

            if args.action == "capabilities":
                model = radio.get_model()
                capabilities = radio.capabilities
                assert capabilities is not None
                print(f"Model:              {model}")
                print(f"Validation:         {capabilities.validation_status}")
                print(f"Endpoint:           {radio.endpoint}")
                print(f"Serial control:     {'yes' if capabilities.serial_control else 'no'}")
                print(f"Network control:    {'yes' if capabilities.network_control else 'no'}")
                print(f"Scanner info:       {'yes' if capabilities.scanner_info else 'no'}")
                print(f"PSI updates:        {'yes' if capabilities.scanner_info_push else 'no'}")
                print(f"Navigation control: {'yes' if capabilities.navigation_control else 'no'}")
                print(f"Battery level:      {'optional' if capabilities.battery_level else 'no'}")
                print(f"Charge status:      {'yes' if capabilities.charge_status else 'no'}")
                print(f"Maximum volume:     {capabilities.maximum_volume}")
                print(f"Maximum squelch:    {capabilities.maximum_squelch}")
                return 0

            if args.action == "hold":
                radio.hold(args.target, args.first, args.second, timeout=args.timeout)
                print("OK")
                return 0

            if args.action == "next":
                radio.next(
                    args.target,
                    args.first,
                    args.second,
                    count=args.count,
                    timeout=args.timeout,
                )
                print("OK")
                return 0

            if args.action == "previous":
                radio.previous(
                    args.target,
                    args.first,
                    args.second,
                    count=args.count,
                    timeout=args.timeout,
                )
                print("OK")
                return 0

            if args.action == "health":
                return _run_health(radio, args)

            if args.action == "events":
                return _run_events(radio, args)

            if args.action == "scanner-info":
                info = radio.get_scanner_info()
                RichCliRenderer(
                    palette=palette_for_name(args.theme),
                    color=args.color,
                ).print_scanner_info(info, connected=radio.connected)
                return 0

            if args.action == "monitor":
                terminal = TerminalMonitor(clear=not args.no_clear)
                radio.on_state(lambda state: terminal.render(state, radio.endpoint))
                with radio.scanner_info_push(args.interval):
                    radio.wait()
                return 0

            if args.action == "raw":
                radio.on_packet(lambda packet: print(packet.raw, flush=True))
                radio.wait()
                return 0

            if args.action == "command":
                response = radio.command(args.value, timeout=args.timeout)
                if isinstance(response, StatusResponse):
                    print(f"display_form={response.display_form}")
                    for number, line in enumerate(response.lines, start=1):
                        print(f"{number:02d}: {line.text!r} mode={line.mode!r}")
                elif hasattr(response, "packet"):
                    print(response)
                else:
                    print(getattr(response, "raw", response))
                return 0

    except (SDS200Error, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        logger.info("sdsctl stopped action=%s", args.action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
