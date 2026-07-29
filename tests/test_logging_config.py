from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sds200.logging_config import configure_logging, resolve_log_level


def _flush_package_handlers() -> None:
    for handler in logging.getLogger("sds200").handlers:
        handler.flush()


def test_resolve_log_level_preserves_verbose_shortcuts() -> None:
    assert resolve_log_level(0) == logging.WARNING
    assert resolve_log_level(1) == logging.INFO
    assert resolve_log_level(2) == logging.DEBUG
    assert resolve_log_level(20) == logging.DEBUG


def test_explicit_log_level_overrides_verbose() -> None:
    assert resolve_log_level(2, "error") == logging.ERROR


def test_invalid_explicit_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="Log level must be one of"):
        resolve_log_level(0, "trace")


def test_configure_logging_appends_selected_messages_to_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sdsctl.log"
    try:
        assert configure_logging(level_name="INFO", log_file=path) == logging.INFO
        test_logger = logging.getLogger("sds200.test")
        test_logger.debug("hidden diagnostic")
        test_logger.info("persistent diagnostic")
        _flush_package_handlers()

        text = path.read_text(encoding="utf-8")
        assert "INFO sds200.test: persistent diagnostic" in text
        assert "hidden diagnostic" not in text
    finally:
        configure_logging()


def test_configure_logging_reports_unwritable_parent(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "sdsctl.log"
    with pytest.raises(OSError):
        configure_logging(log_file=missing)
    configure_logging()
