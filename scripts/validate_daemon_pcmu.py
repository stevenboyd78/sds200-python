#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import cast

from sds200 import (
    PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
    PCMU_STREAM_HEADER_BYTES,
    PcmuPacketDelivery,
    decode_pcmu_delivery,
)

_EVENT_PROTOCOL = "sdsctl.daemon.events"
_EVENT_VERSION = 1
_API_PROTOCOL = "sdsctl.daemon"
_API_VERSION = 1
_PCMU_PREFIX = struct.Struct("!4sBBHI")


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError(
            "value must be a finite number greater than zero"
        )
    return parsed


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "value must be an integer greater than zero"
        )
    return parsed


def _require_mapping(
    value: object,
    *,
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object.")
    if any(not isinstance(key, str) for key in value):
        raise RuntimeError(f"{label} field names must be strings.")
    return cast(Mapping[str, object], value)


def _require_integer(
    value: object,
    *,
    label: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{label} must be an integer.")
    return value


def _require_boolean(
    value: object,
    *,
    label: str,
) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"{label} must be a boolean.")
    return value


def _require_string(
    value: object,
    *,
    label: str,
) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} must be a non-empty string.")
    return value


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    description: str,
    check: Callable[[], None] | None = None,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if check is not None:
            check()
        if predicate():
            return
        sleep(0.05)
    if check is not None:
        check()
    raise TimeoutError(f"Timed out waiting for {description}.")


def _connect_socket(
    path: Path,
    process: subprocess.Popen[str],
    *,
    timeout: float,
) -> socket.socket:
    deadline = monotonic() + timeout
    last_error: OSError | None = None

    while monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                "Daemon exited before accepting local clients "
                f"with status {return_code}."
            )

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.5)
        try:
            client.connect(str(path))
        except OSError as error:
            last_error = error
            client.close()
            sleep(0.05)
            continue

        return client

    detail = (
        f": {last_error.__class__.__name__}"
        if last_error is not None
        else ""
    )
    raise TimeoutError(
        f"Timed out connecting to daemon socket {path}{detail}."
    )


def _socket_mode(path: Path) -> int:
    status = path.stat()
    if not stat.S_ISSOCK(status.st_mode):
        raise RuntimeError(f"Expected a Unix socket at {path}.")
    return stat.S_IMODE(status.st_mode)


def _directory_mode(path: Path) -> int:
    status = path.stat()
    if not stat.S_ISDIR(status.st_mode):
        raise RuntimeError(f"Expected a directory at {path}.")
    return stat.S_IMODE(status.st_mode)


def _read_api_line(client: socket.socket) -> Mapping[str, object]:
    line = bytearray()
    while not line.endswith(b"\n"):
        chunk = client.recv(1)
        if not chunk:
            raise RuntimeError(
                "Daemon closed the API connection before responding."
            )
        line.extend(chunk)
        if len(line) > 1024 * 1024:
            raise RuntimeError("Daemon API response exceeded 1 MiB.")

    try:
        decoded = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Daemon API returned malformed JSON.") from error
    return _require_mapping(decoded, label="Daemon API response")


def _api_request(
    client: socket.socket,
    *,
    request_id: str,
    operation: str,
    params: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    request = {
        "protocol": _API_PROTOCOL,
        "version": _API_VERSION,
        "request_id": request_id,
        "operation": operation,
        "params": dict(params or {}),
    }
    encoded = (
        json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    client.sendall(encoded)

    response = _read_api_line(client)
    if response.get("protocol") != _API_PROTOCOL:
        raise RuntimeError("Daemon API response protocol is invalid.")
    if response.get("version") != _API_VERSION:
        raise RuntimeError("Daemon API response version is invalid.")
    if response.get("request_id") != request_id:
        raise RuntimeError("Daemon API response correlation is invalid.")
    if response.get("ok") is not True:
        raise RuntimeError(
            f"Daemon API request failed: {response.get('error')!r}"
        )

    return _require_mapping(
        response.get("result"),
        label="Daemon API result",
    )


def _runtime_metrics(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    audio = _require_mapping(
        snapshot.get("audio"),
        label="Runtime audio snapshot",
    )
    router = _require_mapping(
        snapshot.get("router"),
        label="Runtime router snapshot",
    )

    return {
        "state": _require_string(
            snapshot.get("state"),
            label="Runtime state",
        ),
        "scanner_connected": _require_boolean(
            snapshot.get("scanner_connected"),
            label="Runtime scanner connection",
        ),
        "psi_active": _require_boolean(
            snapshot.get("psi_active"),
            label="Runtime PSI state",
        ),
        "audio_running": _require_boolean(
            audio.get("running"),
            label="Runtime audio state",
        ),
        "audio_packets": _require_integer(
            audio.get("packets"),
            label="Runtime audio packet count",
        ),
        "audio_samples": _require_integer(
            audio.get("samples"),
            label="Runtime audio sample count",
        ),
        "router_running": _require_boolean(
            router.get("running"),
            label="Runtime router state",
        ),
    }


def _validate_running_runtime(
    metrics: Mapping[str, object],
    *,
    label: str,
) -> None:
    if metrics.get("state") != "running":
        raise RuntimeError(
            f"{label} runtime state was not running: "
            f"{metrics.get('state')!r}."
        )
    for field in (
        "scanner_connected",
        "psi_active",
        "audio_running",
        "router_running",
    ):
        if metrics.get(field) is not True:
            raise RuntimeError(f"{label} runtime field {field} was false.")


def _scanner_state(
    snapshot: Mapping[str, object],
) -> Mapping[str, object]:
    return _require_mapping(
        snapshot.get("radio_state"),
        label="Runtime radio state",
    )


def _navigation_selection(
    snapshot: Mapping[str, object],
) -> tuple[str, int] | None:
    state = _scanner_state(snapshot)
    kind = state.get("channel_kind")
    index = state.get("channel_index")
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    if kind == "TGID":
        return "TGID", index
    if kind == "ConvFrequency":
        return "CFREQ", index
    return None


def _channel_hold(
    snapshot: Mapping[str, object],
) -> str | None:
    value = _scanner_state(snapshot).get("channel_hold")
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("Runtime channel hold state must be a string.")
    normalized = value.strip().casefold()
    if normalized == "on":
        return "On"
    if normalized == "off":
        return "Off"
    raise RuntimeError(
        f"Runtime channel hold state is unsupported: {value!r}."
    )


def _event_kind_count(
    summary: Mapping[str, object],
    kind: str,
) -> int:
    kinds = _require_mapping(
        summary.get("kinds"),
        label="Daemon event-kind summary",
    )
    value = kinds.get(kind, 0)
    return _require_integer(
        value,
        label=f"Daemon event count for {kind}",
    )


def _check_live_clients(
    process: subprocess.Popen[str],
    event_collector: EventCollector,
    pcmu_collectors: list[PcmuCollector],
) -> None:
    return_code = process.poll()
    if return_code is not None:
        raise RuntimeError(
            "Daemon exited during control validation "
            f"with status {return_code}."
        )
    event_collector.raise_if_failed()
    for collector in pcmu_collectors:
        collector.raise_if_failed()


def _wait_for_snapshot(
    client: socket.socket,
    process: subprocess.Popen[str],
    event_collector: EventCollector,
    pcmu_collectors: list[PcmuCollector],
    *,
    request_prefix: str,
    timeout: float,
    description: str,
    predicate: Callable[[Mapping[str, object]], bool],
) -> Mapping[str, object]:
    deadline = monotonic() + timeout
    attempt = 0
    last_snapshot: Mapping[str, object] | None = None

    while monotonic() < deadline:
        _check_live_clients(
            process,
            event_collector,
            pcmu_collectors,
        )
        attempt += 1
        last_snapshot = _api_request(
            client,
            request_id=f"{request_prefix}-{attempt}",
            operation="runtime.snapshot",
        )
        if predicate(last_snapshot):
            return last_snapshot
        sleep(0.1)

    detail = ""
    if last_snapshot is not None:
        detail = (
            f"; selection={_navigation_selection(last_snapshot)!r}, "
            f"channel_hold={_channel_hold(last_snapshot)!r}"
        )
    raise TimeoutError(
        f"Timed out waiting for {description}{detail}."
    )


def _control_result(
    client: socket.socket,
    *,
    request_id: str,
    operation: str,
    params: Mapping[str, object],
    previous_sequence: int,
) -> tuple[dict[str, object], int]:
    result = _api_request(
        client,
        request_id=request_id,
        operation=operation,
        params=params,
    )

    sequence = _require_integer(
        result.get("sequence"),
        label=f"{operation} control sequence",
    )
    if sequence <= previous_sequence:
        raise RuntimeError(
            f"{operation} control sequence did not increase."
        )
    if result.get("operation") != operation:
        raise RuntimeError(
            f"{operation} result reported an unexpected operation."
        )
    _require_string(
        result.get("started_at"),
        label=f"{operation} start timestamp",
    )
    _require_string(
        result.get("completed_at"),
        label=f"{operation} completion timestamp",
    )

    snapshot = _require_mapping(
        result.get("snapshot"),
        label=f"{operation} completion snapshot",
    )
    metrics = _runtime_metrics(snapshot)
    _validate_running_runtime(
        metrics,
        label=operation,
    )

    return (
        {
            "operation": operation,
            "sequence": sequence,
            "runtime": metrics,
        },
        sequence,
    )


class EventCollector:
    """Read and validate one ordered local daemon event subscription."""

    def __init__(self, client: socket.socket) -> None:
        self._client = client
        self._stopping = threading.Event()
        self._lock = threading.RLock()
        self._error: BaseException | None = None
        self._events = 0
        self._first_sequence: int | None = None
        self._last_sequence: int | None = None
        self._sequence_gaps = 0
        self._kinds: Counter[str] = Counter()
        self._daemon_states: set[str] = set()
        self._audio_states: set[bool] = set()
        self._connection_states: set[bool] = set()
        self._first_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="daemon-validation-events",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def wait_first_event(self, timeout: float) -> None:
        _wait_until(
            self._first_event.is_set,
            timeout=timeout,
            description="the initial daemon event snapshot",
            check=self.raise_if_failed,
        )

    def stop(self) -> None:
        self._stopping.set()
        with suppress(OSError):
            self._client.shutdown(socket.SHUT_RDWR)
        with suppress(OSError):
            self._client.close()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("Daemon event reader did not stop.")
        self.raise_if_failed()

    def raise_if_failed(self) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise RuntimeError(
                f"Daemon event reader failed: {error}"
            ) from error

    def summary(self) -> dict[str, object]:
        with self._lock:
            return {
                "events": self._events,
                "first_sequence": self._first_sequence,
                "last_sequence": self._last_sequence,
                "sequence_gaps": self._sequence_gaps,
                "kinds": dict(sorted(self._kinds.items())),
                "daemon_states": sorted(self._daemon_states),
                "audio_running_states": sorted(self._audio_states),
                "scanner_connection_states": sorted(
                    self._connection_states
                ),
            }

    def _run(self) -> None:
        buffer = bytearray()

        try:
            while not self._stopping.is_set():
                try:
                    chunk = self._client.recv(64 * 1024)
                except TimeoutError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        return
                    raise

                if not chunk:
                    if buffer:
                        raise RuntimeError(
                            "Daemon event stream ended with a partial line."
                        )
                    return

                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw_line, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    if not raw_line:
                        raise RuntimeError(
                            "Daemon event stream emitted an empty line."
                        )
                    self._record(bytes(raw_line))
        except BaseException as error:
            if not self._stopping.is_set():
                with self._lock:
                    self._error = error

    def _record(self, raw_line: bytes) -> None:
        try:
            decoded = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Daemon event stream emitted malformed JSON."
            ) from error

        event = _require_mapping(decoded, label="Daemon event")
        if event.get("protocol") != _EVENT_PROTOCOL:
            raise RuntimeError("Daemon event protocol is invalid.")
        if event.get("version") != _EVENT_VERSION:
            raise RuntimeError("Daemon event version is invalid.")

        sequence = _require_integer(
            event.get("sequence"),
            label="Daemon event sequence",
        )
        kind = _require_string(
            event.get("kind"),
            label="Daemon event kind",
        )
        _require_string(
            event.get("observed_at"),
            label="Daemon event timestamp",
        )
        payload = _require_mapping(
            event.get("payload"),
            label="Daemon event payload",
        )

        with self._lock:
            if self._events == 0:
                if kind != "stream.snapshot":
                    raise RuntimeError(
                        "First daemon event was not stream.snapshot."
                    )
                self._first_sequence = sequence
            elif self._last_sequence is not None:
                if sequence <= self._last_sequence:
                    raise RuntimeError(
                        "Daemon event sequence did not increase."
                    )
                if sequence > self._last_sequence + 1:
                    self._sequence_gaps += (
                        sequence - self._last_sequence - 1
                    )

            self._events += 1
            self._last_sequence = sequence
            self._kinds[kind] += 1

            if kind == "stream.snapshot":
                state = payload.get("state")
                if isinstance(state, str):
                    self._daemon_states.add(state)

                audio = payload.get("audio")
                if isinstance(audio, Mapping):
                    running = audio.get("running")
                    if isinstance(running, bool):
                        self._audio_states.add(running)

                connected = payload.get("scanner_connected")
                if isinstance(connected, bool):
                    self._connection_states.add(connected)
            elif kind == "daemon.transition":
                state = payload.get("state")
                if isinstance(state, str):
                    self._daemon_states.add(state)
            elif kind == "audio.state":
                running = payload.get("running")
                if isinstance(running, bool):
                    self._audio_states.add(running)
            elif kind == "scanner.connection":
                connected = payload.get("connected")
                if isinstance(connected, bool):
                    self._connection_states.add(connected)

            self._first_event.set()


@dataclass(frozen=True, slots=True)
class FrameFingerprint:
    endpoint: str
    rtp_sequence: int
    rtp_timestamp: int
    ssrc: int
    marker: bool
    expected_sequence: int | None
    missing_packets: int
    expected_timestamp: int | None
    missing_samples: int
    timestamp_backwards: bool
    payload_size: int
    payload_digest: str


class PcmuCollector:
    """Read and validate one bounded local daemon PCMU subscription."""

    def __init__(self, client: socket.socket, *, name: str) -> None:
        self.name = name
        self._client = client
        self._stopping = threading.Event()
        self._lock = threading.RLock()
        self._error: BaseException | None = None
        self._frames: dict[int, FrameFingerprint] = {}
        self._payload_bytes = 0
        self._first_stream_sequence: int | None = None
        self._last_stream_sequence: int | None = None
        self._stream_sequence_gaps = 0
        self._packets_dropped = 0
        self._payload_bytes_dropped = 0
        self._overflows = 0
        self._network_missing_packets = 0
        self._network_missing_samples = 0
        self._timestamp_backwards = 0
        self._markers = 0
        self._first_frame = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"daemon-validation-{name}",
            daemon=True,
        )

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    def start(self) -> None:
        self._thread.start()

    def wait_for_frames(self, count: int, timeout: float) -> None:
        _wait_until(
            lambda: self.frame_count >= count,
            timeout=timeout,
            description=f"{count} frames for {self.name}",
            check=self.raise_if_failed,
        )

    def stop(self) -> None:
        self._stopping.set()
        with suppress(OSError):
            self._client.shutdown(socket.SHUT_RDWR)
        with suppress(OSError):
            self._client.close()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError(f"{self.name} reader did not stop.")
        self.raise_if_failed()

    def raise_if_failed(self) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise RuntimeError(
                f"{self.name} reader failed: {error}"
            ) from error

    def fingerprints(self) -> dict[int, FrameFingerprint]:
        with self._lock:
            return dict(self._frames)

    def summary(self) -> dict[str, object]:
        with self._lock:
            return {
                "frames": len(self._frames),
                "payload_bytes": self._payload_bytes,
                "first_stream_sequence": self._first_stream_sequence,
                "last_stream_sequence": self._last_stream_sequence,
                "stream_sequence_gaps": self._stream_sequence_gaps,
                "packets_dropped": self._packets_dropped,
                "payload_bytes_dropped": self._payload_bytes_dropped,
                "overflows": self._overflows,
                "network_missing_packets": (
                    self._network_missing_packets
                ),
                "network_missing_samples": (
                    self._network_missing_samples
                ),
                "timestamp_backwards": self._timestamp_backwards,
                "marker_packets": self._markers,
            }

    def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                prefix = self._receive_exact(_PCMU_PREFIX.size)
                if prefix is None:
                    return

                frame_size = _require_integer(
                    _PCMU_PREFIX.unpack(prefix)[4],
                    label="PCMU frame size",
                )
                if frame_size < PCMU_STREAM_HEADER_BYTES:
                    raise RuntimeError(
                        "PCMU frame size is smaller than its header."
                    )
                if frame_size > PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES:
                    raise RuntimeError(
                        "PCMU frame exceeds the configured validation limit."
                    )

                remainder = self._receive_exact(
                    frame_size - len(prefix)
                )
                if remainder is None:
                    if self._stopping.is_set():
                        return
                    raise RuntimeError(
                        "Daemon closed a partial PCMU frame."
                    )

                delivery = decode_pcmu_delivery(prefix + remainder)
                self._record(delivery)
        except BaseException as error:
            if not self._stopping.is_set():
                with self._lock:
                    self._error = error

    def _receive_exact(self, size: int) -> bytes | None:
        data = bytearray()

        while len(data) < size:
            try:
                chunk = self._client.recv(size - len(data))
            except TimeoutError:
                if self._stopping.is_set():
                    return None
                continue
            except OSError:
                if self._stopping.is_set():
                    return None
                raise

            if not chunk:
                if data and not self._stopping.is_set():
                    raise RuntimeError(
                        "Daemon closed a partial PCMU frame."
                    )
                return None
            data.extend(chunk)

        return bytes(data)

    def _record(self, delivery: PcmuPacketDelivery) -> None:
        publication = delivery.publication
        packet = publication.packet
        stream_sequence = _require_integer(
            publication.stream_sequence,
            label="PCMU stream sequence",
        )
        packets_dropped = _require_integer(
            delivery.packets_dropped,
            label="PCMU queue packet loss",
        )
        payload_bytes_dropped = _require_integer(
            delivery.payload_bytes_dropped,
            label="PCMU queue byte loss",
        )
        overflows = _require_integer(
            delivery.overflows,
            label="PCMU queue overflow count",
        )

        fingerprint = FrameFingerprint(
            endpoint=packet.endpoint,
            rtp_sequence=packet.sequence,
            rtp_timestamp=packet.timestamp,
            ssrc=packet.ssrc,
            marker=packet.marker,
            expected_sequence=packet.expected_sequence,
            missing_packets=packet.missing_packets,
            expected_timestamp=packet.expected_timestamp,
            missing_samples=packet.missing_samples,
            timestamp_backwards=packet.timestamp_backwards,
            payload_size=len(packet.payload),
            payload_digest=hashlib.sha256(packet.payload).hexdigest(),
        )

        with self._lock:
            if stream_sequence in self._frames:
                raise RuntimeError(
                    f"{self.name} received a duplicate stream sequence."
                )
            if self._last_stream_sequence is not None:
                if stream_sequence <= self._last_stream_sequence:
                    raise RuntimeError(
                        f"{self.name} stream sequence regressed."
                    )
                if stream_sequence > self._last_stream_sequence + 1:
                    self._stream_sequence_gaps += (
                        stream_sequence
                        - self._last_stream_sequence
                        - 1
                    )

            if packets_dropped < self._packets_dropped:
                raise RuntimeError(
                    f"{self.name} packet-loss counter regressed."
                )
            if payload_bytes_dropped < self._payload_bytes_dropped:
                raise RuntimeError(
                    f"{self.name} byte-loss counter regressed."
                )
            if overflows < self._overflows:
                raise RuntimeError(
                    f"{self.name} overflow counter regressed."
                )

            if self._first_stream_sequence is None:
                self._first_stream_sequence = stream_sequence
            self._last_stream_sequence = stream_sequence
            self._frames[stream_sequence] = fingerprint
            self._payload_bytes += len(packet.payload)
            self._packets_dropped = packets_dropped
            self._payload_bytes_dropped = payload_bytes_dropped
            self._overflows = overflows
            self._network_missing_packets += packet.missing_packets
            self._network_missing_samples += packet.missing_samples
            if packet.timestamp_backwards:
                self._timestamp_backwards += 1
            if packet.marker:
                self._markers += 1
            self._first_frame.set()


def _exercise_safe_controls(
    client: socket.socket,
    process: subprocess.Popen[str],
    event_collector: EventCollector,
    pcmu_collectors: list[PcmuCollector],
    *,
    timeout: float,
) -> dict[str, object]:
    capabilities = _api_request(
        client,
        request_id="controls-capabilities",
        operation="daemon.capabilities",
    )
    advertised = capabilities.get("control_operations")
    if not isinstance(advertised, list) or any(
        not isinstance(value, str) for value in advertised
    ):
        raise RuntimeError(
            "Daemon control operations were not advertised as strings."
        )

    required_operations = {
        "scanner.hold",
        "scanner.next",
        "scanner.previous",
        "scanner.reconnect",
    }
    missing_operations = sorted(
        required_operations - set(advertised)
    )
    if missing_operations:
        raise RuntimeError(
            "Daemon omitted required control operations: "
            f"{missing_operations!r}."
        )
    if capabilities.get("read_only") is not False:
        raise RuntimeError(
            "Daemon capabilities still reported a read-only API."
        )

    maximum_timeout = capabilities.get("max_control_timeout")
    if (
        isinstance(maximum_timeout, bool)
        or not isinstance(maximum_timeout, (int, float))
        or float(maximum_timeout) != 2.0
    ):
        raise RuntimeError(
            "Daemon maximum control timeout was not 2 seconds."
        )

    initial_snapshot = _wait_for_snapshot(
        client,
        process,
        event_collector,
        pcmu_collectors,
        request_prefix="controls-initial-state",
        timeout=timeout,
        description="a controllable unheld channel",
        predicate=lambda snapshot: (
            _navigation_selection(snapshot) is not None
            and _channel_hold(snapshot) == "Off"
        ),
    )
    requested_selection = _navigation_selection(initial_snapshot)
    assert requested_selection is not None
    requested_target, requested_index = requested_selection

    operations: list[dict[str, object]] = []
    previous_sequence = 0
    hold_enabled = False
    hold_restored = False
    next_changed_selection = False

    try:
        result, previous_sequence = _control_result(
            client,
            request_id="control-hold-on",
            operation="scanner.hold",
            params={
                "target": requested_target,
                "first": requested_index,
                "timeout": 2.0,
            },
            previous_sequence=previous_sequence,
        )
        operations.append(result)
        hold_enabled = True

        # The scanner may advance between the precondition snapshot and
        # HLD acknowledgement, so bind reversible navigation to the actual
        # PSI-reported held selection.
        held_snapshot = _wait_for_snapshot(
            client,
            process,
            event_collector,
            pcmu_collectors,
            request_prefix="control-hold-on-state",
            timeout=timeout,
            description="channel hold activation",
            predicate=lambda snapshot: (
                _navigation_selection(snapshot) is not None
                and _channel_hold(snapshot) == "On"
            ),
        )
        held_selection = _navigation_selection(held_snapshot)
        assert held_selection is not None
        held_target, held_index = held_selection

        result, previous_sequence = _control_result(
            client,
            request_id="control-next",
            operation="scanner.next",
            params={
                "target": held_target,
                "first": held_index,
                "count": 1,
                "timeout": 2.0,
            },
            previous_sequence=previous_sequence,
        )
        operations.append(result)

        next_snapshot = _wait_for_snapshot(
            client,
            process,
            event_collector,
            pcmu_collectors,
            request_prefix="control-next-state",
            timeout=timeout,
            description="a different held channel after next",
            predicate=lambda snapshot: (
                _navigation_selection(snapshot) is not None
                and _navigation_selection(snapshot) != held_selection
                and _channel_hold(snapshot) == "On"
            ),
        )
        next_selection = _navigation_selection(next_snapshot)
        assert next_selection is not None
        next_changed_selection = True
        next_target, next_index = next_selection

        result, previous_sequence = _control_result(
            client,
            request_id="control-previous",
            operation="scanner.previous",
            params={
                "target": next_target,
                "first": next_index,
                "count": 1,
                "timeout": 2.0,
            },
            previous_sequence=previous_sequence,
        )
        operations.append(result)

        _wait_for_snapshot(
            client,
            process,
            event_collector,
            pcmu_collectors,
            request_prefix="control-previous-state",
            timeout=timeout,
            description="the original held selection after previous",
            predicate=lambda snapshot: (
                _navigation_selection(snapshot) == held_selection
                and _channel_hold(snapshot) == "On"
            ),
        )

        result, previous_sequence = _control_result(
            client,
            request_id="control-hold-off",
            operation="scanner.hold",
            params={
                "target": held_target,
                "first": held_index,
                "timeout": 2.0,
            },
            previous_sequence=previous_sequence,
        )
        operations.append(result)

        _wait_for_snapshot(
            client,
            process,
            event_collector,
            pcmu_collectors,
            request_prefix="control-hold-off-state",
            timeout=timeout,
            description="restoration of an unheld controllable channel",
            predicate=lambda snapshot: (
                _navigation_selection(snapshot) is not None
                and _channel_hold(snapshot) == "Off"
            ),
        )
        hold_restored = True

        connection_events_before = _event_kind_count(
            event_collector.summary(),
            "scanner.connection",
        )
        pcmu_frames_before = [
            collector.frame_count for collector in pcmu_collectors
        ]

        result, previous_sequence = _control_result(
            client,
            request_id="control-reconnect",
            operation="scanner.reconnect",
            params={"timeout": 2.0},
            previous_sequence=previous_sequence,
        )
        operations.append(result)

        _wait_until(
            lambda: _event_kind_count(
                event_collector.summary(),
                "scanner.connection",
            )
            >= connection_events_before + 2,
            timeout=timeout,
            description="scanner disconnect and reconnect events",
            check=lambda: _check_live_clients(
                process,
                event_collector,
                pcmu_collectors,
            ),
        )

        for collector, prior_frames in zip(
            pcmu_collectors,
            pcmu_frames_before,
            strict=True,
        ):
            collector.wait_for_frames(
                prior_frames + 1,
                timeout,
            )

        final_snapshot = _wait_for_snapshot(
            client,
            process,
            event_collector,
            pcmu_collectors,
            request_prefix="control-reconnect-state",
            timeout=timeout,
            description="a healthy unheld channel after reconnect",
            predicate=lambda snapshot: (
                _navigation_selection(snapshot) is not None
                and _channel_hold(snapshot) == "Off"
            ),
        )
        final_metrics = _runtime_metrics(final_snapshot)
        _validate_running_runtime(
            final_metrics,
            label="Post-control",
        )

        return {
            "capabilities": {
                "read_only": False,
                "required_operations_advertised": True,
                "max_control_timeout_seconds": 2.0,
            },
            "requested_channel": {
                "target": requested_target,
                "hold": "Off",
            },
            "held_channel": {
                "target": held_target,
                "hold": "On",
            },
            "operations": operations,
            "next_changed_selection": next_changed_selection,
            "previous_returned_to_held_selection": True,
            "hold_restored": True,
            "reconnect_connection_events": 2,
            "pcmu_advanced_after_reconnect": True,
            "final_runtime": final_metrics,
        }
    finally:
        if hold_enabled and not hold_restored and process.poll() is None:
            with suppress(BaseException):
                current_snapshot = _api_request(
                    client,
                    request_id="control-emergency-state",
                    operation="runtime.snapshot",
                )
                current_selection = _navigation_selection(
                    current_snapshot
                )
                if (
                    current_selection is not None
                    and _channel_hold(current_snapshot) == "On"
                ):
                    current_target, current_index = current_selection
                    _api_request(
                        client,
                        request_id="control-emergency-hold-off",
                        operation="scanner.hold",
                        params={
                            "target": current_target,
                            "first": current_index,
                            "timeout": 2.0,
                        },
                    )

def _verify_excess_pcmu_client_rejected(
    path: Path,
    process: subprocess.Popen[str],
    *,
    timeout: float,
) -> None:
    client = _connect_socket(path, process, timeout=timeout)
    deadline = monotonic() + timeout

    try:
        while monotonic() < deadline:
            try:
                data = client.recv(1)
            except TimeoutError:
                continue
            if not data:
                return
            raise RuntimeError(
                "Excess PCMU client received stream data instead of rejection."
            )
    finally:
        with suppress(OSError):
            client.close()

    raise TimeoutError("Excess PCMU client was not closed.")


def _terminate_process(
    process: subprocess.Popen[str],
    *,
    timeout: float,
) -> int:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise TimeoutError(
            "Daemon did not stop before the shutdown deadline."
        ) from error


def _wait_for_socket_removal(
    paths: tuple[Path, ...],
    *,
    timeout: float,
) -> None:
    _wait_until(
        lambda: all(not path.exists() for path in paths),
        timeout=timeout,
        description="daemon socket removal",
    )


def _validate_event_summary(
    summary: Mapping[str, object],
) -> None:
    if summary.get("sequence_gaps") != 0:
        raise RuntimeError(
            "Daemon event subscription observed a sequence gap."
        )

    kinds = _require_mapping(
        summary.get("kinds"),
        label="Daemon event-kind summary",
    )
    required_kinds = {
        "stream.snapshot",
        "daemon.transition",
        "scanner.connection",
        "scanner.psi",
        "audio.state",
    }
    missing = sorted(required_kinds - set(kinds))
    if missing:
        raise RuntimeError(
            f"Daemon event subscription missed required kinds: {missing!r}."
        )

    daemon_states = set(
        cast(list[str], summary.get("daemon_states"))
    )
    required_states = {"running", "stopping", "stopped"}
    if not required_states.issubset(daemon_states):
        raise RuntimeError(
            "Daemon event subscription missed lifecycle states: "
            f"{sorted(required_states - daemon_states)!r}."
        )

    audio_states = set(
        cast(list[bool], summary.get("audio_running_states"))
    )
    if audio_states != {False, True}:
        raise RuntimeError(
            "Daemon event subscription did not observe audio start and stop."
        )

    connection_states = set(
        cast(
            list[bool],
            summary.get("scanner_connection_states"),
        )
    )
    if connection_states != {False, True}:
        raise RuntimeError(
            "Daemon event subscription did not observe scanner "
            "connect and disconnect."
        )


def _validate_pcmu_summary(
    summary: Mapping[str, object],
    *,
    name: str,
    minimum_frames: int,
) -> None:
    frames = _require_integer(
        summary.get("frames"),
        label=f"{name} frame count",
    )
    if frames < minimum_frames:
        raise RuntimeError(
            f"{name} received only {frames} PCMU frames; "
            f"expected at least {minimum_frames}."
        )

    for field in (
        "stream_sequence_gaps",
        "packets_dropped",
        "payload_bytes_dropped",
        "overflows",
    ):
        if summary.get(field) != 0:
            raise RuntimeError(
                f"{name} reported nonzero local-loss field {field}: "
                f"{summary.get(field)!r}."
            )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate simultaneous daemon API, event, and bounded PCMU "
            "clients against a physical SDS200, with optional safe "
            "daemon-control exercises."
        )
    )
    parser.add_argument(
        "--host",
        required=True,
        help="SDS200 IPv4 address or hostname",
    )
    parser.add_argument(
        "--duration",
        type=_positive_float,
        default=15.0,
        help="Concurrent-client observation duration in seconds (default: 15)",
    )
    parser.add_argument(
        "--minimum-pcmu-frames",
        type=_positive_integer,
        default=25,
        help="Minimum frames required from each PCMU client (default: 25)",
    )
    parser.add_argument(
        "--startup-timeout",
        type=_positive_float,
        default=20.0,
        help="Daemon and client startup deadline in seconds (default: 20)",
    )
    parser.add_argument(
        "--shutdown-timeout",
        type=_positive_float,
        default=10.0,
        help="Daemon shutdown deadline in seconds (default: 10)",
    )
    parser.add_argument(
        "--exercise-controls",
        action="store_true",
        help=(
            "Exercise hold, next, previous, and reconnect after requiring "
            "an initially unheld controllable channel"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for sanitized JSON evidence and daemon logs",
    )
    return parser.parse_args()


def _run_validation(args: argparse.Namespace) -> dict[str, object]:
    output_directory = Path(args.output_dir).expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    socket_directory = output_directory / "sockets"
    if socket_directory.exists():
        raise FileExistsError(
            f"Validation socket directory already exists: {socket_directory}"
        )
    socket_directory.mkdir(mode=0o700)
    socket_directory.chmod(0o700)

    summary_path = output_directory / "daemon-pcmu-validation-summary.json"
    stdout_path = output_directory / "daemon-stdout.log"
    stderr_path = output_directory / "daemon-stderr.log"
    for path in (summary_path, stdout_path, stderr_path):
        if path.exists():
            raise FileExistsError(f"Evidence file already exists: {path}")

    api_path = socket_directory / "daemon.sock"
    event_path = socket_directory / "events.sock"
    pcmu_path = socket_directory / "pcmu.sock"
    socket_paths = (api_path, event_path, pcmu_path)

    command = (
        sys.executable,
        "-m",
        "sds200.cli",
        "--log-level",
        "INFO",
        "--host",
        args.host,
        "daemon",
        "--socket-path",
        str(api_path),
        "--api-max-clients",
        "1",
        "--event-socket-path",
        str(event_path),
        "--event-max-clients",
        "1",
        "--pcmu-socket-path",
        str(pcmu_path),
        "--pcmu-max-clients",
        "2",
    )

    process: subprocess.Popen[str] | None = None
    api_client: socket.socket | None = None
    socket_modes: dict[str, int] | None = None
    event_collector: EventCollector | None = None
    pcmu_collectors: list[PcmuCollector] = []
    control_summary: dict[str, object] | None = None
    process_exited = False

    try:
        with (
            stdout_path.open("w", encoding="utf-8") as daemon_stdout,
            stderr_path.open("w", encoding="utf-8") as daemon_stderr,
        ):
            process = subprocess.Popen(
                command,
                stdout=daemon_stdout,
                stderr=daemon_stderr,
                text=True,
            )

            event_client = _connect_socket(
                event_path,
                process,
                timeout=args.startup_timeout,
            )
            event_collector = EventCollector(event_client)
            event_collector.start()
            event_collector.wait_first_event(args.startup_timeout)

            first_pcmu_client = _connect_socket(
                pcmu_path,
                process,
                timeout=args.startup_timeout,
            )
            second_pcmu_client = _connect_socket(
                pcmu_path,
                process,
                timeout=args.startup_timeout,
            )
            pcmu_collectors = [
                PcmuCollector(
                    first_pcmu_client,
                    name="pcmu-client-1",
                ),
                PcmuCollector(
                    second_pcmu_client,
                    name="pcmu-client-2",
                ),
            ]
            for collector in pcmu_collectors:
                collector.start()

            api_client = _connect_socket(
                api_path,
                process,
                timeout=args.startup_timeout,
            )
            api_client.settimeout(3.0)

            socket_modes = {
                "api": _socket_mode(api_path),
                "event": _socket_mode(event_path),
                "pcmu": _socket_mode(pcmu_path),
            }
            invalid_socket_modes = {
                name: mode
                for name, mode in socket_modes.items()
                if mode != 0o600
            }
            if invalid_socket_modes:
                formatted = {
                    name: f"{mode:#o}"
                    for name, mode in invalid_socket_modes.items()
                }
                raise RuntimeError(
                    "Daemon socket modes were not 0600: "
                    f"{formatted!r}."
                )

            ping_count = 0
            _api_request(
                api_client,
                request_id="ping-start",
                operation="ping",
            )
            ping_count += 1

            for collector in pcmu_collectors:
                collector.wait_for_frames(
                    1,
                    args.startup_timeout,
                )

            _verify_excess_pcmu_client_rejected(
                pcmu_path,
                process,
                timeout=3.0,
            )

            start_snapshot = _api_request(
                api_client,
                request_id="runtime-start",
                operation="runtime.snapshot",
            )
            start_metrics = _runtime_metrics(start_snapshot)
            _validate_running_runtime(
                start_metrics,
                label="Initial",
            )

            if args.exercise_controls:
                control_summary = _exercise_safe_controls(
                    api_client,
                    process,
                    event_collector,
                    pcmu_collectors,
                    timeout=args.startup_timeout,
                )

            deadline = monotonic() + args.duration
            next_ping = monotonic()
            while monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "Daemon exited during simultaneous-client validation."
                    )
                event_collector.raise_if_failed()
                for collector in pcmu_collectors:
                    collector.raise_if_failed()

                now = monotonic()
                if now >= next_ping:
                    _api_request(
                        api_client,
                        request_id=f"ping-{ping_count + 1}",
                        operation="ping",
                    )
                    ping_count += 1
                    next_ping = now + 1.0
                sleep(0.05)

            end_snapshot = _api_request(
                api_client,
                request_id="runtime-end",
                operation="runtime.snapshot",
            )
            end_metrics = _runtime_metrics(end_snapshot)
            _validate_running_runtime(
                end_metrics,
                label="Final",
            )

            start_packets = cast(int, start_metrics["audio_packets"])
            end_packets = cast(int, end_metrics["audio_packets"])
            start_samples = cast(int, start_metrics["audio_samples"])
            end_samples = cast(int, end_metrics["audio_samples"])
            packet_delta = end_packets - start_packets
            sample_delta = end_samples - start_samples
            if packet_delta <= 0 or sample_delta <= 0:
                raise RuntimeError(
                    "Decoded audio did not advance while all clients "
                    "were connected."
                )

            for collector in pcmu_collectors:
                collector.wait_for_frames(
                    args.minimum_pcmu_frames,
                    args.startup_timeout,
                )

            exit_status = _terminate_process(
                process,
                timeout=args.shutdown_timeout,
            )
            process_exited = True
            if exit_status != 0:
                raise RuntimeError(
                    f"Daemon exited with status {exit_status}."
                )

        if api_client is not None:
            with suppress(OSError):
                api_client.close()
            api_client = None

        assert event_collector is not None
        event_collector.join(args.shutdown_timeout)
        for collector in pcmu_collectors:
            collector.join(args.shutdown_timeout)

        _wait_for_socket_removal(
            socket_paths,
            timeout=args.shutdown_timeout,
        )

        event_summary = event_collector.summary()
        first_pcmu_summary = pcmu_collectors[0].summary()
        second_pcmu_summary = pcmu_collectors[1].summary()

        _validate_event_summary(event_summary)
        _validate_pcmu_summary(
            first_pcmu_summary,
            name="First PCMU client",
            minimum_frames=args.minimum_pcmu_frames,
        )
        _validate_pcmu_summary(
            second_pcmu_summary,
            name="Second PCMU client",
            minimum_frames=args.minimum_pcmu_frames,
        )

        first_frames = pcmu_collectors[0].fingerprints()
        second_frames = pcmu_collectors[1].fingerprints()
        overlap = sorted(set(first_frames) & set(second_frames))
        minimum_overlap = max(1, args.minimum_pcmu_frames // 2)
        if len(overlap) < minimum_overlap:
            raise RuntimeError(
                "PCMU clients did not retain enough overlapping frames: "
                f"{len(overlap)} < {minimum_overlap}."
            )

        mismatches = [
            sequence
            for sequence in overlap
            if first_frames[sequence] != second_frames[sequence]
        ]
        if mismatches:
            raise RuntimeError(
                "PCMU clients decoded different data for shared stream "
                f"sequences: {mismatches[:10]!r}."
            )

        directory_mode = _directory_mode(socket_directory)
        if directory_mode != 0o700:
            raise RuntimeError(
                "Validation socket directory mode was not 0700: "
                f"{directory_mode:#o}."
            )

        assert socket_modes is not None
        summary: dict[str, object] = {
            "schema": "sds200.daemon-pcmu-hardware-validation",
            "version": 2 if control_summary is not None else 1,
            "observed_at": datetime.now(UTC).isoformat(),
            "duration_seconds": args.duration,
            "permissions": {
                "socket_directory_mode": f"{directory_mode:04o}",
                "api_socket_mode": f"{socket_modes['api']:04o}",
                "event_socket_mode": f"{socket_modes['event']:04o}",
                "pcmu_socket_mode": f"{socket_modes['pcmu']:04o}",
            },
            "api": {
                "successful_pings": ping_count,
                "start": start_metrics,
                "end": end_metrics,
                "audio_packet_delta": packet_delta,
                "audio_sample_delta": sample_delta,
            },
            "events": event_summary,
            "pcmu": {
                "client_1": first_pcmu_summary,
                "client_2": second_pcmu_summary,
                "overlapping_frames": len(overlap),
                "matching_overlapping_frames": len(overlap),
                "excess_client_rejected": True,
            },
            "shutdown": {
                "signal": "SIGTERM",
                "exit_status": exit_status,
                "api_socket_removed": not api_path.exists(),
                "event_socket_removed": not event_path.exists(),
                "pcmu_socket_removed": not pcmu_path.exists(),
            },
            "evidence": {
                "daemon_stdout": stdout_path.name,
                "daemon_stderr": stderr_path.name,
            },
        }
        if control_summary is not None:
            summary["controls"] = control_summary

        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        if api_client is not None:
            with suppress(OSError):
                api_client.close()

        if process is not None and not process_exited:
            with suppress(BaseException):
                _terminate_process(
                    process,
                    timeout=args.shutdown_timeout,
                )

        if event_collector is not None:
            event_collector.stop()
            with suppress(BaseException):
                event_collector.join(2.0)

        for collector in pcmu_collectors:
            collector.stop()
            with suppress(BaseException):
                collector.join(2.0)


def main() -> int:
    args = _parse_args()

    try:
        summary = _run_validation(args)
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
