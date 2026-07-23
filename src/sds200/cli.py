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
)
from .device import choose_scanner, discover_scanners
from .exceptions import SDS200Error
from .models import StatusResponse
from .monitor import TerminalMonitor
from .network import DEFAULT_UDP_PORT
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sds200")
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

    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--trace", type=Path, help="Append raw traffic to a trace file")

    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("discover", help="List matching scanner devices")
    subparsers.add_parser("info", help="Show model, firmware, volume, and squelch")
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
    command.add_argument("--timeout", type=float, default=2.0)

    completion = subparsers.add_parser(
        "completion",
        help="Print a shell tab-completion activation script",
    )
    completion.add_argument("shell", choices=SUPPORTED_SHELLS)
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


def selected_radio(args: argparse.Namespace) -> SDS200:
    if args.host is not None:
        return SDS200.network(
            args.host,
            remote_port=args.udp_port or DEFAULT_UDP_PORT,
            local_host=args.bind_address,
            local_port=args.bind_port,
            trace_path=args.trace,
        )
    if args.udp_port is not None or args.bind_address or args.bind_port:
        raise ValueError(
            "--udp-port, --bind-address, and --bind-port require --host"
        )
    return SDS200(selected_port(args.port), trace_path=args.trace)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    enable_tab_completion(parser)
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        if args.action == "completion":
            print(completion_script(args.shell))
            return 0

        if args.action == "discover":
            if args.host is not None:
                raise ValueError("LAN discovery is not supported; omit --host.")
            devices = discover_scanners()
            if not devices:
                print("No matching SDS200 scanner found.")
                return 1
            for device in devices:
                print(f"{device.path} -> {device.resolved_path}")
            return 0

        with selected_radio(args) as radio:
            if args.action == "info":
                print(f"Endpoint: {radio.endpoint}")
                print(f"Model:    {radio.get_model()}")
                print(f"Firmware: {radio.get_firmware()}")
                print(f"Volume:   {radio.get_volume()}")
                print(f"Squelch:  {radio.get_squelch()}")
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
