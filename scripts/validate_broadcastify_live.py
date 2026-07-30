#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from sds200 import (
    AudioFanoutSession,
    AudioStream,
    BroadcastifyConfig,
    EnvironmentSecret,
    NetworkAudioTransport,
    ReconnectPolicy,
    create_broadcastify_sink,
)

PASSWORD_VARIABLE = "SDS200_BROADCASTIFY_PASSWORD"
SERVER_VARIABLE = "SDS200_BROADCASTIFY_SERVER"
PORT_VARIABLE = "SDS200_BROADCASTIFY_PORT"
MOUNT_VARIABLE = "SDS200_BROADCASTIFY_MOUNT"
STREAM_NAME_VARIABLE = "SDS200_BROADCASTIFY_STREAM_NAME"
ALLOWED_PORTS = {80, 8000, 8080, 8500}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Broadcastify adapter against an assigned live feed."
    )
    parser.add_argument("--host", required=True, help="SDS200 IPv4 address or hostname")
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Seconds to stream after Broadcastify accepts the source (default: 60)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/sds200-broadcastify-live-summary.json"),
        help="Counters-only sanitized JSON evidence path",
    )
    return parser.parse_args()


def required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set.")
    return value


def counters_only(value: Any, *, key: str | None = None) -> Any:
    """Retain numeric/boolean counters and state labels, excluding endpoint text."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            filtered = counters_only(child_value, key=child_key)
            if filtered is not None:
                result[child_key] = filtered
        return result
    if isinstance(value, (list, tuple)):
        filtered_items = [counters_only(item, key=key) for item in value]
        filtered_items = [item for item in filtered_items if item is not None]
        return filtered_items or None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and key == "state":
        return value
    return None


def main() -> int:
    args = parse_args()
    if args.duration <= 0:
        raise ValueError("--duration must be greater than zero.")

    server = required_environment(SERVER_VARIABLE)
    port_text = required_environment(PORT_VARIABLE)
    mount = required_environment(MOUNT_VARIABLE)
    stream_name = required_environment(STREAM_NAME_VARIABLE)
    password = required_environment(PASSWORD_VARIABLE)

    if "://" in server:
        raise ValueError("Broadcastify server must not include a URL scheme.")
    try:
        port = int(port_text)
    except ValueError as error:
        raise ValueError("Broadcastify port must be an integer.") from error
    if port not in ALLOWED_PORTS:
        raise ValueError(
            "Broadcastify port must be one of: "
            + ", ".join(str(item) for item in sorted(ALLOWED_PORTS))
        )
    if not mount.startswith("/") or len(mount) <= 1:
        raise ValueError("Broadcastify mount must begin with '/'.")

    config = BroadcastifyConfig(
        name="live-validation",
        server=server,
        port=port,
        mount=mount,
        password=EnvironmentSecret(PASSWORD_VARIABLE),
        stream_name=stream_name,
        public=True,
        connect_timeout=10.0,
        socket_timeout=10.0,
        encoder_stop_timeout=2.0,
        buffer_seconds=5.0,
        stop_timeout=5.0,
        reconnect_policy=ReconnectPolicy(
            initial_delay=1.0,
            multiplier=2.0,
            max_delay=5.0,
            max_attempts=3,
        ),
    )
    sink = create_broadcastify_sink(config)
    transport = NetworkAudioTransport(args.host, rtsp_timeout=5.0)
    session = AudioFanoutSession(AudioStream(transport), (sink,))

    run_error: BaseException | None = None
    accepted = False
    started_at = monotonic()

    try:
        session.start()

        acceptance_deadline = monotonic() + 20.0
        while monotonic() < acceptance_deadline:
            snapshot = sink.snapshot()
            if snapshot.state == "failed":
                raise RuntimeError(
                    "Broadcastify sink failed before source acceptance: "
                    + (snapshot.last_error or "unknown error")
                )
            if (
                snapshot.successful_connections >= 1
                and snapshot.statistics.bytes_written > 0
            ):
                accepted = True
                break
            sleep(0.1)

        if not accepted:
            raise TimeoutError(
                "Broadcastify did not accept and receive the source within 20 seconds."
            )

        stream_deadline = monotonic() + args.duration
        while monotonic() < stream_deadline:
            snapshot = sink.snapshot()
            if snapshot.state == "failed":
                raise RuntimeError(
                    "Broadcastify sink failed during live validation: "
                    + (snapshot.last_error or "unknown error")
                )
            sleep(0.25)
    except BaseException as error:
        run_error = error
    finally:
        try:
            session.stop()
        except BaseException as error:
            if run_error is None:
                run_error = error

    elapsed = monotonic() - started_at
    fanout_snapshot = session.snapshot()
    sink_snapshot = sink.snapshot()
    transport_snapshot = transport.statistics

    if run_error is not None:
        raise run_error
    if fanout_snapshot.packets <= 0:
        raise RuntimeError("No SDS200 audio packets reached the fanout.")
    if sink_snapshot.successful_connections < 1:
        raise RuntimeError("Broadcastify source authorization was not accepted.")
    if sink_snapshot.statistics.bytes_written <= 0:
        raise RuntimeError("No PCM bytes reached the Broadcastify encoder.")
    if sink_snapshot.statistics.bytes_dropped:
        raise RuntimeError(
            "Live Broadcastify validation dropped "
            f"{sink_snapshot.statistics.bytes_dropped} PCM bytes."
        )

    summary = {
        "requested_duration_seconds": args.duration,
        "elapsed_seconds": elapsed,
        "source_accepted": accepted,
        "broadcastify_port": port,
        "fanout": counters_only(asdict(fanout_snapshot)),
        "remote_sink": counters_only(asdict(sink_snapshot)),
        "network_audio": counters_only(asdict(transport_snapshot)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"

    for sensitive in (password, server, mount, stream_name):
        if sensitive and sensitive in payload:
            raise AssertionError("Sensitive feed text appeared in sanitized evidence.")

    args.output.write_text(payload, encoding="utf-8")

    print("Broadcastify source accepted: yes")
    print(f"Requested live duration: {args.duration:.3f} seconds")
    print(f"Fanout packets: {fanout_snapshot.packets}")
    print(f"Audio seconds: {fanout_snapshot.audio_duration_seconds:.3f}")
    print(f"PCM bytes written: {sink_snapshot.statistics.bytes_written}")
    print(f"PCM bytes dropped: {sink_snapshot.statistics.bytes_dropped}")
    print(f"Connection attempts: {sink_snapshot.connection_attempts}")
    print(f"Successful connections: {sink_snapshot.successful_connections}")
    print(f"Reconnects: {sink_snapshot.reconnects}")
    print(f"Failures: {sink_snapshot.failures}")
    print(f"Summary: {args.output}")
    print("PASS: Live Broadcastify validation completed.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
