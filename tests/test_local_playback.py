from __future__ import annotations

import io
import subprocess
import threading
import time
from typing import BinaryIO, cast

import pytest

from sds200.exceptions import AudioOutputError
from sds200.local_playback import (
    CommandPlaybackAdapter,
    CommandPlaybackConfig,
    CommandPlaybackProcess,
)


class BlockingInput:
    def __init__(self, *, fail_write: bool = False) -> None:
        self.fail_write = fail_write
        self.first_write = threading.Event()
        self.release = threading.Event()
        self.closed = False
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.first_write.set()
        if self.fail_write:
            raise BrokenPipeError("playback pipe failed")
        self.release.wait(timeout=1.0)
        if self.closed:
            raise ValueError("stream is closed")
        self.writes.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("stream is closed")

    def close(self) -> None:
        self.closed = True
        self.release.set()


class FakePlaybackProcess:
    def __init__(
        self,
        *,
        returncode: int | None = None,
        diagnostic: bytes = b"",
        fail_write: bool = False,
    ) -> None:
        self.input = BlockingInput(fail_write=fail_write)
        self.error = io.BytesIO(diagnostic)
        self.returncode = returncode
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def stdin(self) -> BinaryIO:
        return cast(BinaryIO, self.input)

    @property
    def stderr(self) -> BinaryIO:
        return self.error

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(("fake-playback",), timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15
        self.input.release.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.input.release.set()


def wait_for_failure(adapter: CommandPlaybackAdapter) -> None:
    for _ in range(200):
        if adapter.last_error is not None:
            return
        time.sleep(0.005)
    raise AssertionError("Playback adapter did not report its worker failure")


def test_command_playback_config_is_immutable_and_validated() -> None:
    config = CommandPlaybackConfig(
        name="pipe:test",
        command=("fake-playback", "--raw"),
    )

    assert config.command == ("fake-playback", "--raw")
    assert config.executable == "fake-playback"
    assert config.chunk_bytes == 320

    with pytest.raises(ValueError, match="must not be empty"):
        CommandPlaybackConfig(name="", command=("fake-playback",))
    with pytest.raises(ValueError, match="must not be empty"):
        CommandPlaybackConfig(name="pipe:test", command=())
    with pytest.raises(ValueError, match="greater than zero milliseconds"):
        CommandPlaybackConfig(
            name="pipe:test",
            command=("fake-playback",),
            chunk_ms=0,
        )


def test_command_playback_adapter_uses_injected_process_lifecycle() -> None:
    process = FakePlaybackProcess()
    commands: list[tuple[str, ...]] = []
    requested: list[int] = []
    statuses: list[bool] = []

    def factory(command: tuple[str, ...]) -> CommandPlaybackProcess:
        commands.append(command)
        return process

    adapter = CommandPlaybackAdapter(
        CommandPlaybackConfig(
            name="pipe:test",
            command=("fake-playback", "--raw"),
        ),
        process_factory=factory,
    )

    assert isinstance(process, CommandPlaybackProcess)
    adapter.start(
        lambda size: requested.append(size) or bytes(size),
        statuses.append,
    )
    assert process.input.first_write.wait(timeout=1.0)

    snapshot = adapter.snapshot()
    assert snapshot.state == "running"
    assert snapshot.running
    assert commands == [("fake-playback", "--raw")]
    assert requested == [320]

    adapter.interrupt()
    assert not adapter.running
    adapter.close()

    snapshot = adapter.snapshot()
    assert snapshot.state == "stopped"
    assert snapshot.interrupted
    assert snapshot.returncode == -15
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert process.input.closed
    assert statuses == []


def test_command_playback_adapter_reports_startup_diagnostic() -> None:
    process = FakePlaybackProcess(
        returncode=2,
        diagnostic=b"audio server unavailable",
    )
    adapter = CommandPlaybackAdapter(
        CommandPlaybackConfig(
            name="pipe:test",
            command=("fake-playback",),
        ),
        process_factory=lambda command: process,
    )

    with pytest.raises(
        AudioOutputError,
        match="status 2: audio server unavailable",
    ):
        adapter.start(lambda size: bytes(size), lambda active: None)

    assert process.input.closed
    assert process.error.closed


def test_command_playback_adapter_defers_worker_failure_to_close() -> None:
    process = FakePlaybackProcess(
        diagnostic=b"device disconnected",
        fail_write=True,
    )
    statuses: list[bool] = []
    adapter = CommandPlaybackAdapter(
        CommandPlaybackConfig(
            name="pipe:test",
            command=("fake-playback",),
        ),
        process_factory=lambda command: process,
    )

    adapter.start(lambda size: bytes(size), statuses.append)
    wait_for_failure(adapter)

    snapshot = adapter.snapshot()
    assert snapshot.state == "failed"
    assert not snapshot.running
    assert snapshot.last_error == "playback pipe failed"
    assert snapshot.diagnostic == "device disconnected"
    assert statuses == [True]

    with pytest.raises(
        AudioOutputError,
        match="playback pipe failed.*device disconnected",
    ):
        adapter.close()

    assert process.terminate_calls == 1


def test_command_playback_adapter_rejects_invalid_factory_result() -> None:
    adapter = CommandPlaybackAdapter(
        CommandPlaybackConfig(
            name="pipe:test",
            command=("fake-playback",),
        ),
        process_factory=lambda command: object(),  # type: ignore[arg-type]
    )

    with pytest.raises(
        TypeError,
        match="CommandPlaybackProcess-compatible",
    ):
        adapter.start(lambda size: bytes(size), lambda active: None)
