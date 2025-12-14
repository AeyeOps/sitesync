"""Tests for logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sitesync.logging import configure_logging


def _cleanup(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_configure_logging_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logger = configure_logging()

    try:
        handler = next(h for h in logger.handlers if hasattr(h, "baseFilename"))
        log_path = Path(handler.baseFilename)
        assert log_path == tmp_path / "sitesync.log"
        logger.info("test message")
        assert log_path.exists()
    finally:
        _cleanup(logger)


@pytest.mark.parametrize(
    "provided,expected",
    [
        (Path("custom.log"), "custom.log"),
        (Path("logs"), "logs/sitesync.log"),
    ],
)
def test_configure_logging_with_override(tmp_path, monkeypatch, provided, expected):
    monkeypatch.chdir(tmp_path)
    logger = configure_logging(log_path=provided, level="debug")

    try:
        handler = next(h for h in logger.handlers if hasattr(h, "baseFilename"))
        log_path = Path(handler.baseFilename)
        assert log_path == tmp_path / expected
        assert logger.level == logging.DEBUG
    finally:
        _cleanup(logger)
