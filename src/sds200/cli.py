from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .device import choose_scanner, discover_scanners
from .exceptions import SDS200Error
from .models import StatusResponse
from .radio import SDS200


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sds200")
    parser.add_argument("--port", type=Path, help="Serial port or stable by-id path")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--trace", type=Path, help="Append raw traffic to a trace file")

    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("discover", help="List matching scanner devices")
    subparsers.add_parser("info", help="Show model, firmware, volume, and squelch")
    subparsers.add_parser("raw", help="Print packets until interrupted")
    subparsers.add_parser("scanner-info", help="Get structured GSI scanner information")

    command = subparsers.add_parser("command", help="Send one raw command")
    command.add_argument("value", help="Command without the terminating carriage return")
    command.add_argument("--timeout", type=float, default=2.0)
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    try:
        if args.action == "discover":
            devices = discover_scanners()
            if not devices:
                print("No matching SDS200 scanner found.")
                return 1
            for device in devices:
                print(f"{device.path} -> {device.resolved_path}")
            return 0

        with SDS200(selected_port(args.port), trace_path=args.trace) as radio:
            if args.action == "info":
                print(f"Port:     {radio.port}")
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
                print(f"Channel:    {info.channel}")
                print(f"Frequency:  {info.frequency}")
                print(f"Modulation: {info.modulation}")
                print(f"Signal:     {info.signal}")
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
