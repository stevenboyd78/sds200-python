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



def test_synthetic_pwf_gwf_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-pwf-gwf.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/pwf-gwf"
    assert [event.direction for event in capture.events] == ["rx", "rx", "rx"]
    assert all(event.delay_ms == 0.0 for event in capture.events)

    first_pwf = (capture.events[0].data or "").split(",")
    second_pwf = (capture.events[1].data or "").split(",")
    gwf = (capture.events[2].data or "").split(",")

    assert first_pwf == ["PWF", "17", "", "23", "FUTURE"]
    assert second_pwf == ["PWF", "1", "2", "3"]
    assert gwf[0] == "GWF"
    assert len(gwf[1:]) == 240
    assert gwf[1:] == [str(index) for index in range(240)]


def test_pwf_gwf_fixture_documents_receive_only_scope() -> None:
    provenance = (FIXTURES / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(provenance.split())

    assert "synthetic-pwf-gwf.jsonl" in normalized
    assert "receive-only synthetic framing evidence" in normalized
    assert "contains no start/stop transmission" in normalized
    assert "does not establish ON/OFF token semantics" in normalized
    assert "GW2 binary framing" in normalized

def test_synthetic_msi_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-msi.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/msi"
    assert all(event.direction == "rx" for event in capture.events)
    assert all(event.delay_ms == 0.0 for event in capture.events)
    assert capture.events[0].data == "MSI,<XML>,"

    xml = "\n".join(event.data or "" for event in capture.events[1:])
    root = ElementTree.fromstring(xml)

    assert root.tag == "MSI"
    assert root.attrib == {"FutureRoot": "keep-root"}
    assert [child.tag for child in root] == [
        "SyntheticRecord",
        "Container",
        "SyntheticRecord",
    ]
    assert root[0].attrib == {
        "SyntheticId": "first",
        "FutureAttr": "keep-first",
    }
    assert root[1][0].tag == "FutureRecord"
    assert root[1][0].attrib == {
        "Value": "nested",
        "FutureNested": "keep-nested",
    }


def test_msi_fixture_documents_receive_only_model_parser_scope() -> None:
    provenance = (FIXTURES / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(provenance.split())

    assert "synthetic-msi.jsonl" in normalized
    assert "receive-only synthetic bounded-XML evidence" in normalized
    assert "contains no MSI command transmission" in normalized
    assert "does not register MSI in the default XML command map" in normalized
    assert "does not establish menu field semantics" in normalized
    assert "MNU, MSV, or MSB control behavior" in normalized


def test_synthetic_msi_retrieval_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-msi-retrieval.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/msi-retrieval"
    assert [event.direction for event in capture.events] == [
        "tx",
        "rx",
        "rx",
        "rx",
        "rx",
        "rx",
        "rx",
    ]
    assert all(event.delay_ms == 0.0 for event in capture.events)
    assert capture.events[0].data == "MSI"
    assert capture.events[1].data == "MSI,<XML>,"

    xml = "\n".join(event.data or "" for event in capture.events[2:])
    root = ElementTree.fromstring(xml)

    assert root.tag == "MSI"
    assert root.attrib == {"FutureRoot": "keep-root"}
    assert [child.tag for child in root] == [
        "SyntheticRecord",
        "Container",
        "SyntheticRecord",
    ]
    assert root[0].attrib["FutureAttr"] == "keep-first"
    assert root[1][0].attrib == {
        "Value": "nested",
        "FutureNested": "keep-nested",
    }


def test_synthetic_mnu_indexed_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-mnu-indexed.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/mnu-indexed"
    assert all(event.delay_ms == 0.0 for event in capture.events)
    assert [event.direction for event in capture.events] == ["tx", "rx"] * 6

    transactions = [
        (capture.events[index].data, capture.events[index + 1].data)
        for index in range(0, len(capture.events), 2)
    ]
    assert transactions == [
        ("MNU,SCAN_SYSTEM,000001", "MNU,OK"),
        ("MNU,SCAN_DEPARTMENT,000002", "MNU,OK"),
        ("MNU,SCAN_SITE,000003", "MNU,OK"),
        ("MNU,SCAN_CHANNEL,000004", "MNU,OK"),
        ("MNU,SRCH_RANGE,000005", "MNU,OK"),
        ("MNU,FTO_CHANNEL,000006", "MNU,OK"),
    ]


def test_mnu_indexed_fixture_documents_dual_spec_scope() -> None:
    provenance = (FIXTURES / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(provenance.split())

    assert "SDS100/SDS200 Remote Command Specification V1.02" in normalized
    assert "Uniden SDS Series Remote Command Specification V2.00" in normalized
    assert "synthetic-mnu-indexed.jsonl" in normalized
    assert "V1.02 and V2.00 official command tables agree" in normalized
    assert "deliberately fabricated opaque tokens" in normalized
    assert "neither specification establishes those literal values" in normalized
    assert "Unindexed MNU rows, negative/error responses" in normalized
    assert "MSV/MSB execution" in normalized


def test_msi_retrieval_fixture_documents_narrow_transport_scope() -> None:
    provenance = (FIXTURES / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(provenance.split())

    assert "synthetic-msi-retrieval.jsonl" in normalized
    assert "exact `MSI` request transmission" in normalized
    assert "CR-line/replay integration" in normalized
    assert "shared UDP XML command map remains unchanged" in normalized
    assert (
        "does not establish UDP expectation, retry, fragment, or bare-XML behavior"
        in normalized
    )
    assert "MNU, MSV, or MSB control behavior" in normalized


def test_synthetic_msi_menu_projection_fixture_contract() -> None:
    capture = load_capture(FIXTURES / "synthetic-msi-menu-projection.jsonl")

    assert capture.endpoint == "fixture://advanced-protocol/msi-menu-projection"
    assert all(event.delay_ms == 0.0 for event in capture.events)
    assert [event.data for event in capture.events if event.direction == "tx"] == [
        "MSI",
        "MSI",
        "MSI",
        "MSI",
    ]

    roots = []
    index = 0
    while index < len(capture.events):
        event = capture.events[index]
        if event.direction != "tx":
            index += 1
            continue
        assert event.data == "MSI"
        assert capture.events[index + 1].data == "MSI,<XML>,"
        lines = []
        index += 2
        while index < len(capture.events):
            data = capture.events[index].data or ""
            lines.append(data)
            index += 1
            if data == "</MSI>":
                break
        roots.append(ElementTree.fromstring("\n".join(lines)))

    assert [root.attrib["MenuType"] for root in roots] == [
        "TypeSelect",
        "TypeInput",
        "TypeLocation",
        "TypeError",
    ]
    assert [child.tag for child in roots[0]] == [
        "MenuItem",
        "MenuItem",
        "FutureMenuNode",
    ]
    assert roots[0].attrib["Selected"] == "selected-opaque"
    assert roots[0].attrib["FutureRoot"] == "keep-select-root"
    assert roots[0][0].attrib["FutureItem"] == "keep-item-a"
    assert roots[1][0].tag == "MenuInput"
    assert roots[1][0].attrib["MaxLength"] == "64"
    assert roots[1][0].attrib["FutureInput"] == "keep-input"
    assert roots[2][0].tag == "MenuLocation"
    assert roots[2][0].attrib["IsLatitude"] == "1"
    assert roots[2][0].attrib["FutureLocation"] == "keep-location"
    assert roots[3][0].tag == "MenuErrorMsg"
    assert roots[3][0].attrib["ScanButton"] == "0"
    assert roots[3][0].attrib["FutureError"] == "keep-error"


def test_msi_menu_projection_fixture_documents_dual_spec_read_only_scope() -> None:
    provenance = (FIXTURES / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(provenance.split())

    assert "synthetic-msi-menu-projection.jsonl" in normalized
    assert "V1.02 and V2.00 have the same MSI attribute table" in normalized
    assert "TypeSelect" in normalized
    assert "TypeInput" in normalized
    assert "TypeLocation" in normalized
    assert "TypeError" in normalized
    assert "deliberately synthetic exact strings" in normalized
    assert "does not establish numeric/boolean coercion" in normalized
    assert "does not establish MSV/MSB reserved field serialization" in normalized
