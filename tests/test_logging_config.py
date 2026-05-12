"""Tests for ``polaris.logging_config`` (Phase P1 verbose-logging patch)."""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path

import pytest

from polaris.logging_config import (
    DEFAULT_DATEFMT,
    DEFAULT_FORMAT,
    setup_polaris_logging,
)


@pytest.fixture(autouse=True)
def _reset_root_logger() -> None:
    """Drop all root handlers + restore default level after every test."""
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()
    root.setLevel(logging.WARNING)


def test_setup_polaris_logging_writes_file(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "polaris.log"
    setup_polaris_logging(level="INFO", log_file=str(log_file))

    logger = logging.getLogger("polaris.test.write")
    logger.info("hello-from-test")
    for h in logging.getLogger().handlers:
        h.flush()

    assert log_file.exists(), "log file must be created"
    content = log_file.read_text(encoding="utf-8")
    assert "hello-from-test" in content
    assert "INFO" in content
    assert "polaris.test.write" in content


def test_setup_polaris_logging_format_iso_utc(tmp_path: Path) -> None:
    log_file = tmp_path / "polaris.log"
    setup_polaris_logging(level="INFO", log_file=str(log_file))

    logging.getLogger("polaris.test.fmt").info("ts-check")
    for h in logging.getLogger().handlers:
        h.flush()

    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    # ISO ts: YYYY-MM-DDTHH:MM:SS.mmmZ
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z \[INFO\] polaris\.test\.fmt:\d+ ts-check",
        line,
    ), f"unexpected log line format: {line!r}"


def test_logger_propagation_levels(tmp_path: Path) -> None:
    log_file = tmp_path / "polaris.log"
    setup_polaris_logging(level="DEBUG", log_file=str(log_file))

    lg = logging.getLogger("polaris.test.levels")
    lg.debug("dbg-line")
    lg.info("info-line")
    lg.warning("warn-line")
    lg.error("err-line")
    for h in logging.getLogger().handlers:
        h.flush()

    text = log_file.read_text(encoding="utf-8")
    assert "dbg-line" in text
    assert "info-line" in text
    assert "warn-line" in text
    assert "err-line" in text


def test_setup_polaris_logging_info_filters_debug(tmp_path: Path) -> None:
    log_file = tmp_path / "polaris.log"
    setup_polaris_logging(level="INFO", log_file=str(log_file))

    lg = logging.getLogger("polaris.test.filter")
    lg.debug("should-not-appear")
    lg.info("should-appear")
    for h in logging.getLogger().handlers:
        h.flush()

    text = log_file.read_text(encoding="utf-8")
    assert "should-appear" in text
    assert "should-not-appear" not in text


def test_setup_polaris_logging_force_replaces_handlers(tmp_path: Path) -> None:
    f1 = tmp_path / "first.log"
    f2 = tmp_path / "second.log"
    setup_polaris_logging(level="INFO", log_file=str(f1))
    logging.getLogger("polaris.test.first").info("first-line")
    setup_polaris_logging(level="INFO", log_file=str(f2))
    logging.getLogger("polaris.test.second").info("second-line")
    for h in logging.getLogger().handlers:
        h.flush()

    # After the second setup, only the new file should receive new lines.
    assert "second-line" in f2.read_text(encoding="utf-8")
    assert "second-line" not in f1.read_text(encoding="utf-8")


def test_setup_polaris_logging_no_file_handler_when_none() -> None:
    setup_polaris_logging(level="INFO", log_file=None)
    handlers = logging.getLogger().handlers
    file_handlers = [h for h in handlers if isinstance(h, logging.FileHandler)]
    assert file_handlers == [], "no FileHandler should be created when log_file is None"


def test_default_format_constants_exposed() -> None:
    # Sanity — keep the public format string available for downstream consumers
    # that want to mirror Polaris' canonical layout.
    assert "%(asctime)s" in DEFAULT_FORMAT
    assert "%(name)s" in DEFAULT_FORMAT
    assert "%(lineno)d" in DEFAULT_FORMAT
    assert DEFAULT_DATEFMT == "%Y-%m-%dT%H:%M:%S"
