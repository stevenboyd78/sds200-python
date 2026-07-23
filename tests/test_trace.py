import re
from pathlib import Path

from sds200.trace import TrafficTrace


def test_trace_includes_utc_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "scanner.trace"
    trace = TrafficTrace(path)

    trace.tx("MDL")
    trace.rx("MDL,SDS200")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T.*Z  TX  MDL$", lines[0])
    assert lines[1].endswith("  RX  MDL,SDS200")
