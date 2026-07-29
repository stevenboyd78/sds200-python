from __future__ import annotations

import logging
from logging.handlers import WatchedFileHandler
from pathlib import Path

LOGGER_NAME = "sds200"
LOG_LEVELS: dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}
LOG_LEVEL_NAMES: tuple[str, ...] = tuple(LOG_LEVELS)
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def resolve_log_level(verbose: int, level_name: str | None = None) -> int:
    """Resolve an explicit level or the backward-compatible verbosity mapping."""

    if level_name is not None:
        normalized = level_name.strip().upper()
        try:
            return LOG_LEVELS[normalized]
        except KeyError as exc:
            choices = ", ".join(LOG_LEVEL_NAMES)
            raise ValueError(f"Log level must be one of: {choices}") from exc
    if verbose == 1:
        return logging.INFO
    if verbose >= 2:
        return logging.DEBUG
    return logging.WARNING


def configure_logging(
    verbose: int = 0,
    *,
    level_name: str | None = None,
    log_file: Path | None = None,
) -> int:
    """Configure package stderr logging and an optional watched file."""

    level = resolve_log_level(verbose, level_name)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        handlers.append(
            WatchedFileHandler(
                log_file.expanduser(),
                encoding="utf-8",
            )
        )

    package_logger = logging.getLogger(LOGGER_NAME)
    package_logger.setLevel(level)
    package_logger.propagate = False
    for handler in tuple(package_logger.handlers):
        package_logger.removeHandler(handler)
        handler.close()
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        package_logger.addHandler(handler)
    return level
