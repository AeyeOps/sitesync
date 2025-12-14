"""Logging setup helpers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "sitesync"
LOG_FILENAME = f"{LOGGER_NAME}.log"
LOG_FORMAT = "%(asctime)s %(process)08x %(thread)08x %(levelname).1s %(module)s %(message)s"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5


def configure_logging(
    log_path: Path | None = None,
    level: str = "INFO",
    mirror_to_console: bool = True,
) -> logging.Logger:
    """Configure the Sitesync logger."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.propagate = False

    numeric_level = _normalize_level(level)
    logger.setLevel(numeric_level)

    file_path = _resolve_log_path(log_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        file_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if mirror_to_console:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(numeric_level)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def _normalize_level(level: str) -> int:
    """Convert log level strings to logging constants."""

    candidate = level.strip().upper()
    if candidate == "WARN":
        candidate = "WARNING"
    numeric = getattr(logging, candidate, None)
    if not isinstance(numeric, int):
        raise ValueError(f"Unsupported log level: {level!r}")
    return numeric


def _resolve_log_path(log_path: Path | None) -> Path:
    """Resolve the effective log file path."""

    if log_path is None:
        return Path.cwd() / LOG_FILENAME

    candidate = log_path if log_path.is_absolute() else Path.cwd() / log_path
    if candidate.is_dir() or candidate.suffix == "":
        return candidate / LOG_FILENAME
    return candidate
