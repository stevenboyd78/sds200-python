# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path
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
from .models import RadioHealth, StatusResponse
from .monitor import TerminalMonitor
from .network import DEFAULT_UDP_PORT
from .profiles import ConnectionProfile, ProfileStore
from .radio import SDS200


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
    connection = parser.add_mutually_exclusive_group()
    port_action = connection.add_argument(
        "--port",
        type=Path,
        help="Serial port or stable by-id path",
    )
    _set_completer(port_action, port_completer)
    connection.add_argument(
        "--host",
        help="SDS200 LAN hostname or IP address",
    )
    profile_action = connection.add_argument(
        "--profile",
        help="Use a saved serial or network connection profile",
    )
    _set_completer(profile_action, profile_completer)
    _add_network_options(parser)
    parser.add_argument(
        "--max-xml-retries",
        type=_non_negative_integer,
        default=2,
        metavar="COUNT",
        help="Automatic retries after a lost UDP XML fragment (default: 2)",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--trace", type=Path, help="Append raw traffic to a trace file")

    subparsers = parser.add_subparsers(dest="action", required=True)

    discover = subparsers.add_parser(
        "discover",
        help="Find USB and LAN-connected SDS200 scanners",
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
    subparsers.add_parser("health", help="Run a command round-trip health check")
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
    return parser


def configure_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def selected_port(explicit: Path | None) -> Path:
    return choose_scanner(explicit)


def _radio_from_profile(
    profile: ConnectionProfile,
    *,
    trace_path: Path | None,
    max_xml_retries: int,
) -> SDS200:
    if profile.kind == "serial":
        assert profile.port is not None
        return SDS200(profile.port, trace_path=trace_path)
    assert profile.host is not None
    return SDS200.network(
        profile.host,
        remote_port=profile.udp_port,
        local_host=profile.bind_address,
        local_port=profile.bind_port,
        max_xml_retries=max_xml_retries,
        trace_path=trace_path,
    )


def selected_radio(
    args: argparse.Namespace,
    *,
    profile_store: ProfileStore | None = None,
) -> SDS200:
    if args.profile is not None:
        if args.udp_port is not None or args.bind_address or args.bind_port:
            raise ValueError(
                "--udp-port, --bind-address, and --bind-port cannot override a profile"
            )
        store = profile_store or ProfileStore(args.config)
        return _radio_from_profile(
            store.get(args.profile),
            trace_path=args.trace,
            max_xml_retries=args.max_xml_retries,
        )
    if args.host is not None:
        return SDS200.network(
            args.host,
            remote_port=args.udp_port or DEFAULT_UDP_PORT,
            local_host=args.bind_address,
            local_port=args.bind_port,
            max_xml_retries=args.max_xml_retries,
            trace_path=args.trace,
        )
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError("--udp-port, --bind-address, and --bind-port require --host")
    return SDS200(selected_port(args.port), trace_path=args.trace)


def _print_health(health: RadioHealth) -> None:
    print(f"Endpoint:  {health.endpoint}")
    print(f"Connected: {'yes' if health.connected else 'no'}")
    print(f"Model:     {health.model}")
    print(f"Firmware:  {health.firmware}")
    print(f"Latency:   {health.latency_ms:.1f} ms")
    if health.statistics:
        print("Network statistics:")
        for name, value in health.statistics.items():
            print(f"  {name.replace('_', ' ').title():24s} {value}")


def _manage_profile(args: argparse.Namespace, store: ProfileStore) -> int:
    if args.profile_action == "list":
        profiles = store.list()
        if not profiles:
            print(f"No profiles in {store.path}")
            return 0
        for profile in profiles:
            endpoint = profile.port if profile.kind == "serial" else profile.host
            print(f"{profile.name:20s} {profile.kind:7s} {endpoint}")
        return 0

    if args.profile_action == "show":
        profile = store.get(args.name)
        print(f"Name:         {profile.name}")
        print(f"Kind:         {profile.kind}")
        if profile.kind == "serial":
            print(f"Port:         {profile.port}")
        else:
            print(f"Host:         {profile.host}")
            print(f"UDP port:     {profile.udp_port}")
            print(f"Bind address: {profile.bind_address or '*'}")
            print(f"Bind port:    {profile.bind_port}")
        return 0

    if args.profile_action == "remove":
        store.remove(args.name)
        print(f"Removed profile {args.name!r}")
        return 0

    if args.profile_action == "add":
        if args.profile_port is not None:
            profile = ConnectionProfile.serial(args.name, args.profile_port)
        else:
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

    raise ValueError(f"Unsupported profile action: {args.profile_action}")


def _run_discovery(args: argparse.Namespace) -> int:
    if args.port is not None or args.host is not None or args.profile is not None:
        raise ValueError("Connection selectors are not used with discover.")
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError("Use discover --network CIDR instead of connection options.")

    found = False
    if not args.network_only:
        for device in discover_scanners():
            found = True
            print(f"USB      {device.path} -> {device.resolved_path}")

    if not args.usb_only:
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
        print("No matching SDS200 scanner found.")
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

            if args.action == "health":
                _print_health(radio.health_check())
                return 0

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
