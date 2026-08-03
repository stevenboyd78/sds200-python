from __future__ import annotations

import io
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from typing import cast

import pytest

from sds200 import (
    AudioEncoderConfig,
    AudioEncoderProcess,
    ManagedAudioEncoder,
    start_audio_encoder_process,
)
from sds200.exceptions import AudioOutputError


class RecordingWriter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.fail:
            raise BrokenPipeError("input pipe closed")
        if self.closed:
            raise BrokenPipeError("input stream closed")
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        if self.fail or self.closed:
            raise BrokenPipeError("input pipe closed")

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(
        self,
        *,
        stdin: RecordingWriter | None = None,
        output: bytes = b"",
        diagnostic: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self.stdin = RecordingWriter() if stdin is None else stdin
        self.stdout = io.BytesIO(output)
        self.stderr = io.BytesIO(diagnostic)
        self.final_returncode = returncode
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self.returncode is None:
            self.returncode = self.final_returncode
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class BlockingWaitProcess(FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.wait_started = threading.Event()
        self.release_wait = threading.Event()

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        self.wait_started.set()
        assert self.release_wait.wait(timeout=1.0)
        if self.returncode is None:
            self.returncode = self.final_returncode
        return self.returncode


class SequencedWaitProcess(FakeProcess):
    def __init__(
        self,
        waits: list[int | BaseException],
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.waits = deque(waits)

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        result = self.waits.popleft()
        if isinstance(result, BaseException):
            raise result
        self.returncode = result
        return result

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def encoder_config(**overrides: object) -> AudioEncoderConfig:
    values: dict[str, object] = {
        "name": "test encoder",
        "command": ("encoder", "--output", "pipe:1"),
        "stop_timeout": 0.1,
        "diagnostic_limit": 128,
    }
    values.update(overrides)
    return AudioEncoderConfig(**values)  # type: ignore[arg-type]


def process_factory(
    process: AudioEncoderProcess,
    commands: list[tuple[str, ...]] | None = None,
) -> Callable[[tuple[str, ...]], AudioEncoderProcess]:
    def create(command: tuple[str, ...]) -> AudioEncoderProcess:
        if commands is not None:
            commands.append(command)
        return process

    return create


def test_audio_encoder_config_is_immutable_and_validated() -> None:
    command = ["encoder", "--flag"]
    config = encoder_config(command=cast(tuple[str, ...], command))
    command.append("changed")

    assert config.command == ("encoder", "--flag")
    assert config.executable == "encoder"

    with pytest.raises(ValueError, match="must not be empty or padded"):
        encoder_config(name=" padded ")
    with pytest.raises(ValueError, match="command must not be empty"):
        encoder_config(command=())
    with pytest.raises(ValueError, match="executable must not be empty"):
        encoder_config(command=("",))
    with pytest.raises(ValueError, match="NUL"):
        encoder_config(command=("encoder", "bad\x00argument"))
    with pytest.raises(ValueError, match="stop timeout"):
        encoder_config(stop_timeout=0.0)
    with pytest.raises(ValueError, match="diagnostic limit"):
        encoder_config(diagnostic_limit=0)


def test_managed_encoder_writes_reads_and_finalizes_idempotently() -> None:
    process = FakeProcess(output=b"encoded-audio")
    commands: list[tuple[str, ...]] = []
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process, commands),
    )

    assert encoder.snapshot().state == "running"
    assert encoder.snapshot().running

    encoder.write_pcm(b"pcm")
    assert encoder.read_encoded(4096) == b"encoded-audio"

    result = encoder.finalize()

    assert commands == [encoder_config().command]
    assert process.stdin.writes == [b"pcm"]
    assert result.returncode == 0
    assert not result.interrupted
    assert result.diagnostic == ""
    assert not result.exit_reported
    assert encoder.snapshot().state == "stopped"
    assert not encoder.snapshot().running
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed
    assert encoder.finalize() is result
    assert encoder.close() is result


def test_managed_encoder_allows_output_drain_during_finalize() -> None:
    process = FakeProcess(output=b"final-encoded-audio")
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process),
    )
    drained: list[bytes] = []

    def wait_for_output(timeout: float) -> bool:
        assert timeout >= 0.0
        drained.append(encoder.read_encoded(4096))
        return True

    result = encoder.finalize(output_waiter=wait_for_output)

    assert result.returncode == 0
    assert drained == [b"final-encoded-audio"]
    assert process.stdout.closed


def test_managed_encoder_reports_early_exit_before_input() -> None:
    process = FakeProcess(returncode=7)
    process.returncode = 7
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process),
    )

    with pytest.raises(AudioOutputError, match="exited unexpectedly.*status 7"):
        encoder.write_pcm(b"pcm")

    result = encoder.finalize()
    assert result.returncode == 7
    assert result.exit_reported


def test_managed_encoder_reports_input_failure() -> None:
    process = FakeProcess(stdin=RecordingWriter(fail=True))
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process),
    )

    with pytest.raises(AudioOutputError, match="input failed.*BrokenPipeError"):
        encoder.write_pcm(b"pcm")

    encoder.interrupt()
    encoder.finalize()


def test_managed_encoder_interrupt_is_idempotent() -> None:
    process = FakeProcess()
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process),
    )

    encoder.interrupt()
    encoder.interrupt()
    result = encoder.finalize()

    assert process.stdin.closed
    assert process.terminated
    assert result.interrupted
    assert result.returncode == -15
    assert result.diagnostic == ""
    assert encoder.snapshot().state == "stopped"


def test_managed_encoder_records_concurrent_interrupt_during_finalize() -> None:
    process = BlockingWaitProcess()
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process),
    )
    results: list[object] = []
    errors: list[BaseException] = []

    def finalize() -> None:
        try:
            results.append(encoder.finalize())
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=finalize)
    worker.start()
    assert process.wait_started.wait(timeout=1.0)

    encoder.interrupt()
    process.release_wait.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert errors == []
    assert len(results) == 1
    result = results[0]
    assert result.interrupted  # type: ignore[union-attr]
    assert result.returncode == -15  # type: ignore[union-attr]


def test_managed_encoder_returns_nonzero_exit_diagnostic() -> None:
    process = FakeProcess(
        diagnostic=b"encoder configuration failed\n",
        returncode=3,
    )
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process),
    )

    result = encoder.finalize()

    assert result.returncode == 3
    assert result.diagnostic == "encoder configuration failed"
    assert not result.interrupted


def test_managed_encoder_escalates_terminate_and_kill() -> None:
    timeout = subprocess.TimeoutExpired(cmd="encoder", timeout=0.01)
    process = SequencedWaitProcess(
        [timeout, timeout, timeout],
    )
    encoder = ManagedAudioEncoder(
        encoder_config(stop_timeout=0.03),
        process_factory=process_factory(process),
    )

    with pytest.raises(AudioOutputError, match="test encoder did not stop"):
        encoder.finalize()

    assert process.terminated
    assert process.killed
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed
    assert encoder.snapshot().state == "stopped"

    with pytest.raises(AudioOutputError, match="test encoder did not stop"):
        encoder.finalize()


def test_start_audio_encoder_process_reports_missing_executable() -> None:
    command = (
        "/definitely/not/a/real/sds200-encoder",
        "--version",
    )

    with pytest.raises(AudioOutputError, match="executable.*was not found"):
        start_audio_encoder_process(command)


def test_audio_encoder_protocol_is_runtime_checkable() -> None:
    process = FakeProcess()
    assert isinstance(process, AudioEncoderProcess)

    invalid = cast(AudioEncoderProcess, object())
    with pytest.raises(TypeError, match="AudioEncoderProcess-compatible"):
        ManagedAudioEncoder(
            encoder_config(),
            process_factory=lambda command: invalid,
        )


def test_managed_encoder_validates_input_and_read_size() -> None:
    process = FakeProcess()
    encoder = ManagedAudioEncoder(
        encoder_config(),
        process_factory=process_factory(process),
    )

    encoder.write_pcm(b"")

    with pytest.raises(TypeError, match="PCM input must be bytes"):
        encoder.write_pcm(cast(bytes, bytearray(b"pcm")))
    with pytest.raises(ValueError, match="read size"):
        encoder.read_encoded(0)

    encoder.interrupt()
    encoder.finalize()
