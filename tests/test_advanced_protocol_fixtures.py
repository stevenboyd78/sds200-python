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


def test_advanced_protocol_fixture_provenance_is_explicit() -> None:
    provenance = (FIXTURES / "README.md").read_text(encoding="utf-8")
    normalized_provenance = " ".join(provenance.split())

    assert "explicitly synthetic" in normalized_provenance
    assert "not derived from scanner hardware" in normalized_provenance
    assert "Uniden SDS Series Remote Command Specification V2.00" in normalized_provenance
    assert "2025-07-07" in normalized_provenance
