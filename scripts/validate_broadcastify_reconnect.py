#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hmac
import json
import logging
import math
import os
import socket
import struct
import subprocess
import sys
import threading
from array import array
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from time import monotonic, sleep

from sds200 import (
    AudioFanoutSession,
    AudioStream,
    BroadcastifyConfig,
    EnvironmentSecret,
    NetworkAudioTransport,
    ReconnectPolicy,
    create_broadcastify_sink,
)

_SECRET_VARIABLE = "SDS200_BROADCASTIFY_PASSWORD"
_HEADER_LIMIT = 16 * 1024


class ReconnectReceiver:
    """Minimal Icecast receiver that drops session one and captures session two."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        mount: str,
        secret: str,
        output_directory: Path,
        drop_after_bytes: int,
    ) -> None:
        if drop_after_bytes <= 0:
            raise ValueError("drop_after_bytes must be greater than zero")
        self.host = host
        self.port = port
        self.mount = mount
        self.secret = secret
        self.output_directory = output_directory
        self.drop_after_bytes = drop_after_bytes
        self.mp3_paths = (
            output_directory / "broadcastify-reconnect-session-1.mp3",
            output_directory / "broadcastify-reconnect-session-2.mp3",
        )
        self.request_paths = (
            output_directory / "broadcastify-reconnect-request-1-sanitized.txt",
            output_directory / "broadcastify-reconnect-request-2-sanitized.txt",
        )
        self.bytes_received = [0, 0]
        self._connected = (threading.Event(), threading.Event())
        self._first_dropped = threading.Event()
        self._done = threading.Event()
        self._stopping = threading.Event()
        self._error: BaseException | None = None
        self._connection_lock = threading.Lock()
        self._connection: socket.socket | None = None
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen(2)
        self._listener.settimeout(0.25)
        self._thread = threading.Thread(
            target=self._run,
            name="broadcastify-reconnect-receiver",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def wait_connected(self, session: int, timeout: float) -> None:
        if session not in (1, 2):
            raise ValueError("session must be 1 or 2")
        self._wait_event(
            self._connected[session - 1],
            timeout,
            f"Loopback receiver did not accept source session {session}.",
        )

    def wait_first_dropped(self, timeout: float) -> None:
        self._wait_event(
            self._first_dropped,
            timeout,
            "Loopback receiver did not drop the first source session.",
        )

    def stop(self) -> None:
        self._stopping.set()
        with self._connection_lock:
            connection = self._connection
        if connection is not None:
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                connection.close()
        with suppress(OSError):
            self._listener.close()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise TimeoutError("Loopback reconnect receiver did not stop.")
        self._raise_if_failed()

    def _wait_event(self, event: threading.Event, timeout: float, message: str) -> None:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            if event.wait(timeout=0.05):
                return
            self._raise_if_failed()
            if self._done.is_set():
                break
        self._raise_if_failed()
        raise TimeoutError(message)

    def _raise_if_failed(self) -> None:
        error = self._error
        if error is not None:
            raise RuntimeError(f"Loopback reconnect receiver failed: {error}") from error

    def _run(self) -> None:
        try:
            for index in range(2):
                connection = self._accept()
                if connection is None:
                    return
                with self._connection_lock:
                    self._connection = connection
                try:
                    self._capture_session(connection, index)
                finally:
                    with self._connection_lock:
                        if self._connection is connection:
                            self._connection = None
                    with suppress(OSError):
                        connection.close()
        except BaseException as error:
            if not self._stopping.is_set():
                self._error = error
        finally:
            self._done.set()
            with suppress(OSError):
                self._listener.close()

    def _capture_session(self, connection: socket.socket, index: int) -> None:
        connection.settimeout(1.0)
        request = self._read_headers(connection)
        sanitized = self._validate_and_sanitize_request(request)
        self.request_paths[index].write_text(sanitized, encoding="utf-8")
        connection.sendall(b"HTTP/1.0 200 OK\r\n\r\n")
        self._connected[index].set()

        with self.mp3_paths[index].open("wb") as output:
            while not self._stopping.is_set():
                try:
                    chunk = connection.recv(64 * 1024)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                output.write(chunk)
                self.bytes_received[index] += len(chunk)
                if index == 0 and self.bytes_received[index] >= self.drop_after_bytes:
                    output.flush()
                    self._abort_connection(connection)
                    self._first_dropped.set()
                    return

        if index == 0 and not self._stopping.is_set():
            raise ConnectionError(
                "First source session ended before the deliberate disconnect threshold."
            )

    def _accept(self) -> socket.socket | None:
        while not self._stopping.is_set():
            try:
                connection, _address = self._listener.accept()
            except TimeoutError:
                continue
            return connection
        return None

    @staticmethod
    def _abort_connection(connection: socket.socket) -> None:
        # An abortive close makes the sender observe the failure promptly.
        with suppress(OSError):
            connection.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("ii", 1, 0),
            )
        with suppress(OSError):
            connection.close()

    @staticmethod
    def _read_headers(connection: socket.socket) -> bytes:
        data = bytearray()
        while b"\r\n\r\n" not in data and b"\n\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("Source closed before completing request headers.")
            data.extend(chunk)
            if len(data) > _HEADER_LIMIT:
                raise ValueError("Source request headers exceeded the validation limit.")
        return bytes(data)

    def _validate_and_sanitize_request(self, request: bytes) -> str:
        text = request.decode("iso-8859-1")
        header_text = text.split("\r\n\r\n", maxsplit=1)[0]
        lines = header_text.splitlines()
        if not lines or lines[0] != f"SOURCE {self.mount} ICE/1.0":
            raise ValueError(f"Unexpected source request line: {lines[:1]!r}")

        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if not separator:
                raise ValueError(f"Malformed source header: {line!r}")
            headers[name.strip().lower()] = value.strip()

        expected_authorization = "Basic " + base64.b64encode(
            f"source:{self.secret}".encode()
        ).decode("ascii")
        actual_authorization = headers.get("authorization", "")
        if not hmac.compare_digest(actual_authorization, expected_authorization):
            raise PermissionError("Source authorization did not match the loopback secret.")

        expected_headers = {
            "host": f"{self.host}:{self.port}",
            "content-type": "audio/mpeg",
            "ice-name": "SDS200 reconnect validation",
            "ice-genre": "Scanner",
            "ice-public": "0",
            "ice-bitrate": "16",
            "ice-audio-info": "bitrate=16;samplerate=22050;channels=1",
            "user-agent": "sds200-python",
        }
        for name, expected in expected_headers.items():
            actual = headers.get(name)
            if actual != expected:
                raise ValueError(
                    f"Unexpected {name!r} header: expected {expected!r}, got {actual!r}"
                )

        sanitized_lines = [
            (
                "Authorization: Basic <redacted>"
                if line.lower().startswith("authorization:")
                else line
            )
            for line in lines
        ]
        sanitized = "\n".join(sanitized_lines) + "\n"
        if self.secret in sanitized:
            raise AssertionError("Secret appeared in sanitized request evidence.")
        return sanitized


def _probe_mp3(path: Path) -> dict[str, object]:
    result = subprocess.run(
        (
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_name,sample_rate,channels,bit_rate",
            "-of",
            "json",
            str(path),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    probe = json.loads(result.stdout)
    streams = probe.get("streams")
    if not isinstance(streams, list) or not streams:
        raise RuntimeError("ffprobe did not find an audio stream in session two.")
    stream = streams[0]
    if not isinstance(stream, dict):
        raise RuntimeError("ffprobe returned an invalid stream description.")
    if stream.get("codec_name") != "mp3":
        raise RuntimeError(f"Expected MP3, got {stream.get('codec_name')!r}.")
    if stream.get("sample_rate") != "22050":
        raise RuntimeError(f"Expected 22050 Hz, got {stream.get('sample_rate')!r}.")
    if stream.get("channels") != 1:
        raise RuntimeError(f"Expected mono audio, got {stream.get('channels')!r}.")
    return probe


def _measure_decoded_audio(path: Path) -> dict[str, int | float]:
    result = subprocess.run(
        (
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "22050",
            "-ac",
            "1",
            "pipe:1",
        ),
        check=True,
        capture_output=True,
    )
    data = result.stdout
    if not data or len(data) % 2:
        raise RuntimeError("Decoded reconnect MP3 did not contain complete 16-bit samples.")
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    count = len(samples)
    nonzero = sum(sample != 0 for sample in samples)
    peak = max((abs(sample) for sample in samples), default=0)
    rms = math.sqrt(sum(sample * sample for sample in samples) / count)
    if nonzero == 0:
        raise RuntimeError("Decoded post-reconnect audio contains only silence.")
    return {
        "samples": count,
        "duration_seconds": count / 22050,
        "nonzero_samples": nonzero,
        "peak_amplitude": peak,
        "rms_amplitude": rms,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Broadcastify reconnect against a local Icecast loopback."
    )
    parser.add_argument("--host", required=True, help="SDS200 IPv4 address or hostname")
    parser.add_argument(
        "--drop-after-bytes",
        type=int,
        default=4096,
        help="Abort the first MP3 session after this many bytes (default: 4096)",
    )
    parser.add_argument(
        "--post-reconnect-duration",
        type=float,
        default=10.0,
        help="Seconds to stream after session two connects (default: 10)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for sanitized requests, MP3 captures, and JSON evidence",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.drop_after_bytes <= 0:
        raise ValueError("--drop-after-bytes must be greater than zero.")
    if args.post_reconnect_duration <= 0:
        raise ValueError("--post-reconnect-duration must be greater than zero.")

    secret = os.environ.get(_SECRET_VARIABLE)
    if not secret:
        raise RuntimeError(f"{_SECRET_VARIABLE} is not set.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    receiver = ReconnectReceiver(
        host="127.0.0.1",
        port=8500,
        mount="/sds200-reconnect",
        secret=secret,
        output_directory=args.output_dir,
        drop_after_bytes=args.drop_after_bytes,
    )
    config = BroadcastifyConfig(
        name="reconnect-loopback-feed",
        server="127.0.0.1",
        port=8500,
        mount="/sds200-reconnect",
        password=EnvironmentSecret(_SECRET_VARIABLE),
        stream_name="SDS200 reconnect validation",
        public=False,
        connect_timeout=2.0,
        socket_timeout=2.0,
        encoder_stop_timeout=2.0,
        buffer_seconds=5.0,
        stop_timeout=5.0,
        reconnect_policy=ReconnectPolicy(
            initial_delay=0.25,
            multiplier=1.0,
            max_delay=0.25,
            max_attempts=5,
        ),
    )
    sink = create_broadcastify_sink(config)
    transport = NetworkAudioTransport(args.host, rtsp_timeout=2.0)
    session = AudioFanoutSession(AudioStream(transport), (sink,))

    receiver.start()
    run_error: BaseException | None = None
    try:
        session.start()
        receiver.wait_connected(1, timeout=10.0)
        receiver.wait_first_dropped(timeout=10.0)
        receiver.wait_connected(2, timeout=10.0)

        deadline = monotonic() + args.post_reconnect_duration
        while monotonic() < deadline:
            snapshot = sink.snapshot()
            if snapshot.state == "failed":
                raise RuntimeError(
                    f"Broadcastify sink failed: {snapshot.last_error or 'unknown error'}"
                )
            sleep(0.1)
    except BaseException as error:
        run_error = error
    finally:
        try:
            session.stop()
        except BaseException as error:
            if run_error is None:
                run_error = error
        receiver.stop()
        try:
            receiver.join(timeout=5.0)
        except BaseException as error:
            if run_error is None:
                run_error = error

    if run_error is not None:
        raise run_error

    fanout_snapshot = session.snapshot()
    sink_snapshot = sink.snapshot()
    transport_snapshot = transport.statistics

    if receiver.bytes_received[0] < args.drop_after_bytes:
        raise RuntimeError("First MP3 session did not reach the disconnect threshold.")
    if receiver.bytes_received[1] <= 0:
        raise RuntimeError("Second MP3 session captured no bytes.")
    if sink_snapshot.connection_attempts < 2:
        raise RuntimeError("Broadcastify sink did not attempt a second connection.")
    if sink_snapshot.successful_connections < 2:
        raise RuntimeError("Broadcastify sink did not establish two connections.")
    if sink_snapshot.reconnects < 1:
        raise RuntimeError("Broadcastify sink did not count a reconnect.")
    if sink_snapshot.failures < 1:
        raise RuntimeError("Broadcastify sink did not record the deliberate disconnect.")
    if sink_snapshot.statistics.overflows:
        raise RuntimeError("Broadcastify reconnect queue overflowed.")
    if fanout_snapshot.packets <= 0:
        raise RuntimeError("No SDS200 packets reached the fanout.")
    if transport_snapshot.callback_errors:
        raise RuntimeError("SDS200 audio transport reported callback errors.")

    probe = _probe_mp3(receiver.mp3_paths[1])
    decoded_audio = _measure_decoded_audio(receiver.mp3_paths[1])
    summary = {
        "drop_after_bytes": args.drop_after_bytes,
        "post_reconnect_duration_seconds": args.post_reconnect_duration,
        "mp3_bytes_received": {
            "session_1": receiver.bytes_received[0],
            "session_2": receiver.bytes_received[1],
        },
        "fanout": asdict(fanout_snapshot),
        "remote_sink": asdict(sink_snapshot),
        "network_audio": asdict(transport_snapshot),
        "session_2_ffprobe": probe,
        "session_2_decoded_audio": decoded_audio,
    }
    summary_path = args.output_dir / "broadcastify-reconnect-summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    evidence_paths = (*receiver.request_paths, summary_path)
    for path in evidence_paths:
        if secret.encode() in path.read_bytes():
            raise AssertionError(f"Secret appeared in evidence file {path}.")
    if secret in repr(sink_snapshot):
        raise AssertionError("Secret appeared in the remote sink snapshot.")

    print(f"Session 1 MP3: {receiver.mp3_paths[0]}")
    print(f"Session 1 MP3 bytes: {receiver.bytes_received[0]}")
    print(f"Session 2 MP3: {receiver.mp3_paths[1]}")
    print(f"Session 2 MP3 bytes: {receiver.bytes_received[1]}")
    print(f"Connection attempts: {sink_snapshot.connection_attempts}")
    print(f"Successful connections: {sink_snapshot.successful_connections}")
    print(f"Reconnects: {sink_snapshot.reconnects}")
    print(f"Failures: {sink_snapshot.failures}")
    print(f"PCM bytes written: {sink_snapshot.statistics.bytes_written}")
    print(f"PCM bytes dropped: {sink_snapshot.statistics.bytes_dropped}")
    print(f"Queue overflows: {sink_snapshot.statistics.overflows}")
    print(f"RTP packets: {fanout_snapshot.packets}")
    print(f"Post-reconnect decoded RMS: {decoded_audio['rms_amplitude']:.2f}")
    print(f"Summary: {summary_path}")
    print("PASS: Broadcastify reconnect validation completed.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
