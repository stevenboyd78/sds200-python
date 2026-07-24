# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from time import sleep
from typing import Protocol, cast

from .completion import (
    SUPPORTED_SHELLS,
    command_completer,
    completion_script,
    enable_tab_completion,
    port_completer,
    profile_completer,
)
from .device import choose_scanner, discover_scanners
from .discovery import (
    DEFAULT_DISCOVERY_TIMEOUT,
    DEFAULT_DISCOVERY_WORKERS,
    DEFAULT_MAX_DISCOVERY_HOSTS,
    discover_network_scanners,
)
from .exceptions import SDS200Error
from .models import HealthSummary, RadioEvent, RadioHealth, StatusResponse
from .monitor import TerminalMonitor
from .network import DEFAULT_UDP_PORT
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
from .reliability import ReconnectPolicy
from .scanner import SUPPORTED_SCANNER_MODELS, ScannerModel, normalize_model_name


class _CompletableAction(Protocol):
    completer: Callable[..., object]


def _set_completer(
    action: argparse.Action,
    completer: Callable[..., object],
) -> None:
    cast(_CompletableAction, action).completer = completer


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sds200")
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
    _add_network_options(parser)
    parser.add_argument(
        "--max-xml-retries",
        type=_non_negative_integer,
        default=2,
        metavar="COUNT",
        help="Automatic retries after a lost UDP XML fragment (default: 2)",
    )
    parser.add_argument(
        "--reconnect-attempts",
        type=_non_negative_integer,
        default=0,
        metavar="COUNT",
        help="Reconnect attempts after a disconnect; 0 retries forever (default: 0)",
    )
    parser.add_argument(
        "--reconnect-initial-delay",
        type=_positive_float,
        default=1.0,
        metavar="SECONDS",
        help="Initial reconnect delay (default: 1.0)",
    )
    parser.add_argument(
        "--reconnect-multiplier",
        type=_positive_float,
        default=2.0,
        metavar="FACTOR",
        help="Reconnect backoff multiplier (default: 2.0)",
    )
    parser.add_argument(
        "--reconnect-max-delay",
        type=_positive_float,
        default=30.0,
        metavar="SECONDS",
        help="Maximum reconnect delay (default: 30.0)",
    )
    parser.add_argument(
        "--health-history-limit",
        type=_positive_integer,
        default=100,
        metavar="COUNT",
        help="Maximum in-memory health observations (default: 100)",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--trace", type=Path, help="Append raw traffic to a trace file")

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
        help="Show SDS100 or SDS150 battery and charge status",
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
    profile_connection = profile_add.add_mutually_exclusive_group(required=True)
    profile_connection.add_argument("--port", dest="profile_port", type=Path)
    profile_connection.add_argument("--host", dest="profile_host")
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


def configure_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def selected_port(
    explicit: Path | None,
    model: ScannerModel | None = None,
) -> Path:
    return choose_scanner(explicit, model=model)


def _reconnect_policy_from_args(args: argparse.Namespace) -> ReconnectPolicy:
    max_attempts = args.reconnect_attempts or None
    return ReconnectPolicy(
        initial_delay=args.reconnect_initial_delay,
        multiplier=args.reconnect_multiplier,
        max_delay=args.reconnect_max_delay,
        max_attempts=max_attempts,
    )


def _radio_from_profile(
    profile: ConnectionProfile,
    *,
    preference: TransportPreference | None,
    trace_path: Path | None,
    max_xml_retries: int,
    reconnect_policy: ReconnectPolicy,
    health_history_limit: int,
) -> SDSScanner:
    return SDSScanner.from_profile(
        profile,
        preference=preference,
        trace_path=trace_path,
        max_xml_retries=max_xml_retries,
        reconnect_policy=reconnect_policy,
        health_history_limit=health_history_limit,
    )


def selected_radio(
    args: argparse.Namespace,
    *,
    profile_store: ProfileStore | None = None,
) -> SDSScanner:
    reconnect_policy = _reconnect_policy_from_args(args)
    if args.profile is not None:
        if args.model is not None:
            raise ValueError("--model cannot override a saved profile")
        if args.udp_port is not None or args.bind_address or args.bind_port:
            raise ValueError(
                "--udp-port, --bind-address, and --bind-port cannot override a profile"
            )
        store = profile_store or ProfileStore(args.config)
        return _radio_from_profile(
            store.get(args.profile),
            preference=args.connection_preference,
            trace_path=args.trace,
            max_xml_retries=args.max_xml_retries,
            reconnect_policy=reconnect_policy,
            health_history_limit=args.health_history_limit,
        )
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
        )
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError("--udp-port, --bind-address, and --bind-port require --host")
    return SDSScanner(
        selected_port(args.port, args.model),
        reconnect_policy=reconnect_policy,
        trace_path=args.trace,
        health_history_limit=args.health_history_limit,
        expected_model=args.model,
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
        return 0

    if args.profile_action == "remove":
        store.remove(args.name)
        print(f"Removed profile {args.name!r}")
        return 0

    if args.profile_action == "add":
        if args.profile_port is not None:
            profile = ConnectionProfile.serial(
                args.name,
                args.profile_port,
                model=args.profile_model,
            )
        else:
            if args.profile_model not in {None, "SDS200"}:
                raise ValueError("Network profiles are only supported for the SDS200")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    enable_tab_completion(parser)
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        if args.action == "completion":
            print(completion_script(args.shell))
            return 0

        if args.action == "profile":
            return _manage_profile(args, ProfileStore(args.config))

        if args.action == "discover":
            return _run_discovery(args)

        with selected_radio(args) as radio:
            if args.action == "info":
                print(f"Endpoint: {radio.endpoint}")
                print(f"Model:    {radio.get_model()}")
                print(f"Firmware: {radio.get_firmware()}")
                print(f"Volume:   {radio.get_volume()}")
                print(f"Squelch:  {radio.get_squelch()}")
                return 0

            if args.action == "battery":
                status = radio.get_charge_status()
                print(f"Model:       {radio.model}")
                print(f"Status:      {status.status}")
                print(f"Capacity:    {status.capacity_percent}%")
                print(f"Voltage:     {status.voltage_mv} mV")
                print(f"Current:     {status.current_ma} mA")
                print(f"Temperature: {status.temperature_c:.2f} C")
                return 0

            if args.action == "health":
                return _run_health(radio, args)

            if args.action == "events":
                return _run_events(radio, args)

            if args.action == "scanner-info":
                info = radio.get_scanner_info()
                print(f"Mode:       {info.mode}")
                print(f"Screen:     {info.screen}")
                print(f"System:     {info.system}")
                print(f"Department: {info.department}")
                print(f"Site:       {info.site}")
                print(f"Channel:    {info.channel}")
                print(f"Frequency:  {info.frequency}")
                print(f"Modulation: {info.modulation}")
                print(f"Service:    {info.service_type}")
                print(f"Signal:     {info.signal}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
