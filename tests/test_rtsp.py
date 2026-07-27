from __future__ import annotations

from collections.abc import Iterable

import pytest

from sds200.rtsp import RtspClient, RtspProtocolError, RtspStatusError, parse_sdp_audio


class FakeStreamSocket:
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        del size
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True


def response(
    cseq: int,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    status: str = "200 OK",
) -> bytes:
    values = {
        "CSeq": str(cseq),
        "Content-Length": str(len(body)),
    }
    if headers:
        values.update(headers)
    return (
        f"RTSP/1.0 {status}\r\n"
        + "".join(f"{name}: {value}\r\n" for name, value in values.items())
        + "\r\n"
    ).encode("ascii") + body


def make_client(stream: FakeStreamSocket) -> RtspClient:
    def factory(address: tuple[str, int], timeout: float) -> FakeStreamSocket:
        assert address == ("192.0.2.25", 554)
        assert timeout == 2.0
        return stream

    return RtspClient("192.0.2.25", timeout=2.0, connection_factory=factory)


def test_scanner_specific_session_sequence_with_fragmented_responses() -> None:
    sdp = (
        b"v=0\r\n"
        b"m=audio 0 RTP/AVP 0\r\n"
        b"a=control:trackID=1\r\n"
    )
    all_responses = b"".join(
        (
            response(1),
            response(
                2,
                headers={
                    "Content-Type": "application/sdp",
                    "Content-Base": "rtsp://192.0.2.25/au:scanner.au/",
                },
                body=sdp,
            ),
            response(3, headers={"Session": "30026000"}),
            response(4, headers={"Session": "30026000"}),
            response(5, headers={"Session": "30026000"}),
            response(6, headers={"Session": "30026000"}),
        )
    )
    stream = FakeStreamSocket(
        all_responses[index : index + 7] for index in range(0, len(all_responses), 7)
    )
    client = make_client(stream)

    client.start(48607)
    client.get_parameter()
    client.teardown()
    client.close()

    requests = [value.decode("ascii") for value in stream.sent]
    assert requests[0].startswith("OPTIONS ")
    assert requests[1].startswith("DESCRIBE ")
    assert requests[2].startswith(
        "SETUP rtsp://192.0.2.25/au:scanner.au/trackID=1 RTSP/1.0"
    )
    assert "Transport: RTP/AVP;unicast;client_port=48607\r\n" in requests[2]
    assert "RTP/AVP/UDP" not in requests[2]
    assert "client_port=48607-" not in requests[2]
    assert requests[3].startswith("PLAY ")
    assert "Session: 30026000\r\n" in requests[3]
    assert "Range: npt=0.000-\r\n" in requests[3]
    assert requests[4].startswith("GET_PARAMETER ")
    assert requests[5].startswith("TEARDOWN ")
    for cseq, request in enumerate(requests, start=1):
        assert f"CSeq: {cseq}\r\n" in request
    assert stream.closed


def test_multiple_responses_in_one_read_are_preserved() -> None:
    stream = FakeStreamSocket([response(1) + response(2)])
    client = make_client(stream)
    client.connect()

    client.options()
    client.options()

    assert len(stream.sent) == 2


def test_non_success_response_raises_status_error() -> None:
    stream = FakeStreamSocket([response(1, status="400 Bad Request")])
    client = make_client(stream)
    client.connect()

    with pytest.raises(RtspStatusError, match="400 Bad Request"):
        client.options()


def test_parse_sdp_requires_pcmu_audio_track() -> None:
    description = parse_sdp_audio(
        b"v=0\r\nm=audio 0 RTP/AVP 0\r\na=control:trackID=1\r\n"
    )
    assert description.control == "trackID=1"
    assert description.payload_types == (0,)

    with pytest.raises(RtspProtocolError, match="PCMU"):
        parse_sdp_audio(
            b"v=0\r\nm=audio 0 RTP/AVP 8\r\na=control:trackID=1\r\n"
        )
