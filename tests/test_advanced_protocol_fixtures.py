from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from sds200 import load_capture

FIXTURES = Path(__file__).parent / "fixtures" / "advanced_protocol"


def test_synthetic_glt_fl_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-glt-fl.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/glt"
    assert [event.direction for event in capture.events] == [
        "tx",
        "rx",
        "rx",
        "rx",
        "rx",
        "rx",
    ]
    assert capture.events[0].data == "GLT,FL"
    assert capture.events[1].data == "GLT,<XML>,"

    # Assembly is intentionally fixture-test-local; no production GLT API or
    # XmlResponseAssembler support is claimed or required by this evidence.
    xml_text = "\n".join(event.data or "" for event in capture.events[2:])
    root = ElementTree.fromstring(xml_text)
    records = list(root)

    assert root.tag == "GLT"
    assert [record.tag for record in records] == ["FL", "FL"]
    assert records[0].attrib == {
        "Index": "0",
        "Name": "Example Favorites A",
        "Monitor": "On",
        "Q_Key": "1",
        "N_Tag": "None",
        "FutureAttr": "preserve-me",
    }
    assert records[1].attrib == {
        "Index": "1",
        "Name": "Example Favorites B",
        "Monitor": "On",
        "Q_Key": "1",
        "N_Tag": "None",
    }
    assert records[0].attrib["FutureAttr"] == "preserve-me"


def test_synthetic_fqk_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-fqk.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/fqk"
    assert [event.direction for event in capture.events] == ["tx", "rx", "tx", "rx"]
    assert capture.events[0].data == "FQK"

    read_command, *read_statuses = (capture.events[1].data or "").split(",")
    assert read_command == "FQK"
    assert len(read_statuses) == 100
    assert set(read_statuses) == {"0", "1", "2"}

    write_command, *write_statuses = (capture.events[2].data or "").split(",")
    assert write_command == "FQK"
    assert len(write_statuses) == 100
    assert set(write_statuses) <= {"0", "1", "2"}
    assert capture.events[3].data == "FQK,OK"


def test_synthetic_urc_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-urc.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/urc"
    transactions = [
        (capture.events[index].data, capture.events[index + 1].data)
        for index in range(0, len(capture.events), 2)
    ]
    assert transactions[:4] == [
        ("URC", "URC,0"),
        ("URC,1", "URC,OK"),
        ("URC", "URC,1"),
        ("URC,0", "URC,OK"),
    ]
    assert transactions[4:] == [
        ("URC,1", "URC,ERR,0001"),
        ("URC,1", "URC,ERR,0002"),
        ("URC,1", "URC,ERR,0003"),
        ("URC,1", "URC,ERR,0004"),
        ("URC,1", "URC,ERR,9999"),
    ]
    assert {response.rsplit(",", 1)[-1] for _, response in transactions[4:]} == {
        "0001",
        "0002",
        "0003",
        "0004",
        "9999",
    }


def test_synthetic_ast_apr_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-ast-apr.jsonl")
    assert capture.endpoint == "fixture://advanced-protocol/ast-apr"
    assert all(event.delay_ms == 0.0 for event in capture.events)
    assert [event.data for event in capture.events if event.direction == "tx"] == [
        "AST,CURRENT_ACTIVITY,7",
        "APR,CURRENT_ACTIVITY",
        "AST,LCN_MONITOR,9",
        "APR,LCN_MONITOR",
    ]
    assert [event.data for event in capture.events if event.data == "APR,OK"] == [
        "APR,OK",
        "APR,OK",
    ]

    headers = [
        index
        for index, event in enumerate(capture.events)
        if event.data == "AST,<XML>,"
    ]
    roots = []
    for index in headers:
        lines = []
        for event in capture.events[index + 1 :]:
            lines.append(event.data or "")
            if event.data == "</AST>":
                break
        roots.append(ElementTree.fromstring("\n".join(lines)))
    assert [child.tag for child in roots[0]] == [
        "CurrentActivity",
        "CurrentActivity",
    ]
    assert roots[0].attrib["FutureRoot"] == "preserve-root"
    assert [child.tag for child in roots[1]] == [
        "LcnMonitor",
        "FutureRecord",
        "LcnMonitor",
    ]
    assert [child.attrib["ReceiveStaus"] for child in roots[1] if child.tag == "LcnMonitor"] == [
        "Active",
        "Idle",
    ]


def test_advanced_protocol_fixture_provenance_is_explicit() -> None:
    provenance = (FIXTURES / "README.md").read_text(encoding="utf-8")
    normalized_provenance = " ".join(provenance.split())

    assert "explicitly synthetic" in normalized_provenance
    assert "not derived from scanner hardware" in normalized_provenance
    assert "Uniden SDS Series Remote Command Specification V2.00" in normalized_provenance
    assert "2025-07-07" in normalized_provenance
    assert "no event was captured from scanner hardware" in normalized_provenance
    assert (
        "not physical timing, model, firmware, transport, or termination validation"
        in normalized_provenance
    )
