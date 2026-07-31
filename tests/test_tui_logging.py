from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sds200.logging_config import PackageStderrHandler, configure_logging
from sds200.tui_logging import TuiLogBuffer, TuiLogHandler, capture_package_logs


def _flush_package_handlers() -> None:
    for handler in logging.getLogger("sds200").handlers:
        handler.flush()


def test_tui_log_buffer_is_bounded_by_lines() -> None:
    buffer = TuiLogBuffer(limit=2)

    buffer.append("first\nsecond")
    buffer.append("third")

    snapshot = buffer.snapshot()
    assert snapshot.version == 2
    assert snapshot.lines == ("second", "third")
    assert buffer.limit == 2


def test_tui_log_buffer_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        TuiLogBuffer(limit=0)


def test_tui_capture_replaces_stderr_and_preserves_file_logging(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "sdsctl.log"
    try:
        configure_logging(level_name="INFO", log_file=path)
        package_logger = logging.getLogger("sds200")
        original_handlers = tuple(package_logger.handlers)
        buffer = TuiLogBuffer()

        with capture_package_logs(buffer):
            assert any(
                isinstance(handler, TuiLogHandler)
                for handler in package_logger.handlers
            )
            assert not any(
                isinstance(handler, PackageStderrHandler)
                for handler in package_logger.handlers
            )
            logging.getLogger("sds200.test").warning("captured TUI warning")
            _flush_package_handlers()

        assert tuple(package_logger.handlers) == original_handlers
        assert "captured TUI warning" in "\n".join(buffer.snapshot().lines)
        assert "captured TUI warning" in path.read_text(encoding="utf-8")
        assert capsys.readouterr().err == ""
    finally:
        configure_logging()
