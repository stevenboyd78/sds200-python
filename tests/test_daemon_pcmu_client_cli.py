from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import (
    DaemonPcmuClientSnapshot,
    DaemonSocketLocation,
    PcmSinkStatistics,
    PcmuPacket,
    PcmuPacketDelivery,
    PcmuPublication,
    cli,
    decode_mulaw,
)

DELIVERY = PcmuPacketDelivery(
    publication=PcmuPublication(
        stream_sequence=12,
        packet=PcmuPacket(
            endpoint="rtsp://192.0.2.25/au:scanner.au",
            sequence=741,
            timestamp=1_407_173_956,
            ssrc=0x56650DAA,
            payload=bytes((0xFF, 0x80)),
            observed_at=datetime(2026, 8, 5, 13, tzinfo=UTC),
        ),
    ),
    packets_dropped=2,
    payload_bytes_dropped=640,
    overflows=2,
)


class FakeDaemonPcmuClient:
    instances: list[FakeDaemonPcmuClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_endpoint_bytes: int,
        max_frame_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_endpoint_bytes = max_endpoint_bytes
        self.max_frame_bytes = max_frame_bytes
        self.connected = False
        self.closed = False
        self.receive_calls = 0
        self.instances.append(self)

    def connect(self) -> object:
        self.connected = True
        return object()

    def receive(self) -> PcmuPacketDelivery:
        self.receive_calls += 1
        if self.receive_calls == 1:
            return DELIVERY
        raise KeyboardInterrupt

    def close(self) -> None:
        self.closed = True
        self.connected = False

    def snapshot(self) -> DaemonPcmuClientSnapshot:
        return DaemonPcmuClientSnapshot(
            connected=self.connected,
            packets_received=1,
            payload_bytes_received=2,
            samples_received=2,
            first_stream_sequence=12,
            last_stream_sequence=12,
            stream_packets_skipped=0,
            packets_dropped=2,
            payload_bytes_dropped=640,
            overflows=2,
            rtp_missing_packets=3,
            rtp_missing_samples=4,
            rtp_timestamp_backwards=1,
            endpoint="rtsp://192.0.2.25/au:scanner.au",
        )


class FakeSink:
    instances: list[FakeSink] = []

    def __init__(self, name: str) -> None:
        self._name = name
        self.started = False
        self.stopped = False
        self.received: list[bytes] = []
        self.instances.append(self)

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    @property
    def statistics(self) -> PcmSinkStatistics:
        total = sum(len(item) for item in self.received)
        return PcmSinkStatistics(
            bytes_submitted=total,
            bytes_written=total,
        )

    def start(self) -> None:
        self.started = True

    def submit_pcm(self, data: bytes) -> None:
        self.received.append(data)

    def stop(self) -> None:
        self.stopped = True


class FakePlaybackSink(FakeSink):
    def __init__(
        self,
        *,
        device: str | int | None,
        buffer_ms: int,
    ) -> None:
        self.device = device
        self.buffer_ms = buffer_ms
        super().__init__(
            "playback:default"
            if device is None
            else f"playback:{device}"
        )


class FakeRecorder:
    def __init__(
        self,
        path: Path,
        *,
        overwrite: bool,
    ) -> None:
        self.path = path
        self.overwrite = overwrite


class FakeWavSink(FakeSink):
    def __init__(self, recorder: FakeRecorder) -> None:
        self.recorder = recorder
        super().__init__(f"wav:{recorder.path}")


def install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDaemonPcmuClient.instances.clear()
    FakeSink.instances.clear()
    monkeypatch.setattr(
        cli,
        "DaemonPcmuClient",
        FakeDaemonPcmuClient,
    )
    monkeypatch.setattr(
        cli,
        "SoundDevicePlaybackSink",
        FakePlaybackSink,
    )
    monkeypatch.setattr(cli, "PcmuWavRecorder", FakeRecorder)
    monkeypatch.setattr(cli, "PcmWavSink", FakeWavSink)


def test_daemon_client_parser_accepts_audio_options(
    tmp_path: Path,
) -> None:
    output = tmp_path / "scanner.wav"
    args = cli.build_parser().parse_args(
        [
            "daemon-client",
            "--timeout",
            "1.5",
            "audio",
            "--pcmu-socket-path",
            "/tmp/sdsctl-pcmu.sock",
            "--max-endpoint-bytes",
            "2048",
            "--max-frame-bytes",
            "8192",
            "--play",
            "--device",
            "2",
            "--buffer-ms",
            "400",
            "--output",
            str(output),
            "--duration",
            "5",
            "--force",
        ]
    )

    assert args.action == "daemon-client"
    assert args.daemon_client_action == "audio"
    assert args.timeout == 1.5
    assert args.pcmu_socket_path == Path("/tmp/sdsctl-pcmu.sock")
    assert args.max_endpoint_bytes == 2048
    assert args.max_frame_bytes == 8192
    assert args.play
    assert args.device == 2
    assert args.buffer_ms == 400
    assert args.output == output
    assert args.duration == 5.0
    assert args.force


def test_daemon_client_audio_uses_pcmu_without_api_or_scanner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_fakes(monkeypatch)
    output = tmp_path / "scanner.wav"

    class UnexpectedApiClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            pytest.fail("daemon-client audio must not open the API socket")

    monkeypatch.setattr(cli, "DaemonApiClient", UnexpectedApiClient)

    assert (
        cli.main(
            [
                "daemon-client",
                "--timeout",
                "1.5",
                "audio",
                "--pcmu-socket-path",
                "/tmp/sdsctl-pcmu.sock",
                "--play",
                "--device",
                "USB Audio",
                "--buffer-ms",
                "400",
                "--output",
                str(output),
                "--force",
            ],
            environ={},
        )
        == 0
    )

    client = FakeDaemonPcmuClient.instances[0]
    assert client.location.path == Path("/tmp/sdsctl-pcmu.sock")
    assert client.timeout == 1.5
    assert client.closed
    assert client.receive_calls == 2

    playback = next(
        sink
        for sink in FakeSink.instances
        if isinstance(sink, FakePlaybackSink)
    )
    wav = next(
        sink
        for sink in FakeSink.instances
        if isinstance(sink, FakeWavSink)
    )
    expected_pcm = decode_mulaw(DELIVERY.packet.payload)
    assert playback.received == [expected_pcm]
    assert wav.received == [expected_pcm]
    assert playback.started and playback.stopped
    assert wav.started and wav.stopped
    assert playback.device == "USB Audio"
    assert playback.buffer_ms == 400
    assert wav.recorder.path == output
    assert wav.recorder.overwrite

    assert capsys.readouterr().out.splitlines() == [
        "Streamed 0.0 seconds",
        "Packets: 1",
        "Audio samples: 2",
        "PCMU first stream sequence: 12",
        "PCMU last stream sequence: 12",
        "PCMU stream packets skipped: 0",
        "PCMU queue packets dropped: 2",
        "PCMU queue payload bytes dropped: 640",
        "PCMU queue overflows: 2",
        "RTP missing packets: 3",
        "RTP missing samples: 4",
        "RTP timestamp backwards: 1",
        "Playback device: USB Audio",
        "Playback written bytes: 4",
        "Playback dropped bytes: 0",
        "Playback underflows: 0",
        "Playback overflows: 0",
        "Playback callback statuses: 0",
        f"Output: {output}",
    ]


def test_daemon_client_audio_reports_missing_pcmu_socket(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "missing-pcmu.sock"

    assert (
        cli.main(
            [
                "daemon-client",
                "audio",
                "--pcmu-socket-path",
                str(path),
                "--output",
                str(tmp_path / "scanner.wav"),
            ],
            environ={},
        )
        == 2
    )

    assert "Daemon PCMU socket was not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "value", "expected"),
    [
        (
            "--socket-path",
            "/tmp/sdsctl-api.sock",
            "--socket-path is not used with daemon-client audio",
        ),
        (
            "--max-response-bytes",
            "4096",
            "--max-response-bytes is not used with daemon-client audio",
        ),
    ],
)
def test_daemon_client_audio_rejects_api_only_options(
    option: str,
    value: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_fakes(monkeypatch)

    assert (
        cli.main(
            [
                "daemon-client",
                option,
                value,
                "audio",
                "--pcmu-socket-path",
                "/tmp/sdsctl-pcmu.sock",
                "--output",
                str(tmp_path / "scanner.wav"),
            ],
            environ={},
        )
        == 2
    )

    assert expected in capsys.readouterr().err
    assert FakeDaemonPcmuClient.instances == []


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ([], "requires --play, --output, or both"),
        (["--force"], "--force requires --output"),
        (["--device", "USB Audio"], "--device requires --play"),
    ],
)
def test_daemon_client_audio_rejects_invalid_destinations(
    options: list[str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_fakes(monkeypatch)

    assert (
        cli.main(
            [
                "daemon-client",
                "audio",
                "--pcmu-socket-path",
                "/tmp/sdsctl-pcmu.sock",
                *options,
            ],
            environ={},
        )
        == 2
    )

    assert message in capsys.readouterr().err
    assert FakeDaemonPcmuClient.instances == []
