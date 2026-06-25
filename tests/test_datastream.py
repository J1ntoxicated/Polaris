"""Tests for ``polaris.datastream`` — structured per-tick/per-update sink.

Covers the operational↔data split contract:
- ``emit`` writes one JSON object per line (JSONL) to the dedicated sink;
- the data records do NOT leak to the runtime root logger (``propagate=False``);
- ``emit`` is a no-op when the sink was never configured (tests / replay);
- the rotating handler caps on-disk size (unbounded-growth backstop).
"""

from __future__ import annotations

import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from polaris.datastream import (
    DATASTREAM_LOGGER_NAME,
    emit,
    setup_datastream,
)


@pytest.fixture(autouse=True)
def _reset_datastream_logger() -> None:
    """Drop datastream handlers + restore root after every test."""
    yield
    lg = logging.getLogger(DATASTREAM_LOGGER_NAME)
    for h in list(lg.handlers):
        lg.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()
    lg.propagate = True
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()
    root.setLevel(logging.WARNING)


def test_emit_writes_jsonl_record(tmp_path: Path) -> None:
    sink = tmp_path / "data_stream.jsonl"
    setup_datastream(str(sink))

    emit("cell/update", ticker="BTC-USDT", pnl_r=0.5, won=True)
    for h in logging.getLogger(DATASTREAM_LOGGER_NAME).handlers:
        h.flush()

    line = sink.read_text(encoding="utf-8").strip()
    rec = json.loads(line)  # each line must be a standalone JSON object
    assert rec["event"] == "cell/update"
    assert rec["ticker"] == "BTC-USDT"
    assert rec["pnl_r"] == 0.5
    assert rec["won"] is True
    assert "ts" in rec  # auto-stamped


def test_emit_noop_when_unconfigured(tmp_path: Path) -> None:
    # setup_datastream NOT called → no handler → emit writes nothing + never raises.
    sink = tmp_path / "data_stream.jsonl"
    emit("cell/update", ticker="ETH-USDT")
    assert not sink.exists()


def test_setup_datastream_none_disables(tmp_path: Path) -> None:
    setup_datastream(None)
    sink = tmp_path / "data_stream.jsonl"
    emit("learner/update", key="x")
    # No handler installed → no-op, nothing written anywhere.
    assert logging.getLogger(DATASTREAM_LOGGER_NAME).handlers == []
    assert not sink.exists()


def test_data_records_do_not_leak_to_runtime_root(tmp_path: Path) -> None:
    """The operator surface (root logger) must NOT receive data records."""
    runtime = tmp_path / "runtime.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root_handler = logging.FileHandler(str(runtime), encoding="utf-8")
    root.addHandler(root_handler)

    setup_datastream(str(tmp_path / "data_stream.jsonl"))
    emit("tick-gate/regime-active", venue="okx", symbol="BTC-USDT")
    for h in logging.getLogger(DATASTREAM_LOGGER_NAME).handlers:
        h.flush()
    root_handler.flush()

    # propagate=False → the data record never reaches the runtime root sink.
    assert runtime.read_text(encoding="utf-8") == ""


def test_setup_datastream_installs_rotating_handler(tmp_path: Path) -> None:
    setup_datastream(str(tmp_path / "data_stream.jsonl"))
    handlers = logging.getLogger(DATASTREAM_LOGGER_NAME).handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0], RotatingFileHandler)


def test_rotation_caps_disk_size(tmp_path: Path) -> None:
    """A tiny maxBytes forces rotation → backups exist, growth is bounded."""
    sink = tmp_path / "data_stream.jsonl"
    setup_datastream(str(sink), max_bytes=512, backup_count=2)

    for i in range(200):
        emit("learner/update", key=f"k{i}", pnl_r=i * 0.001)
    for h in logging.getLogger(DATASTREAM_LOGGER_NAME).handlers:
        h.flush()

    # Rotation produced at least one backup, and no single file exceeds the cap
    # by more than one record (the active file is rotated when it would exceed).
    backups = list(tmp_path.glob("data_stream.jsonl.*"))
    assert backups, "rotation must produce backup files under the cap"
    assert sink.stat().st_size <= 512 + 4096  # active file bounded (1-record slack)


def test_setup_datastream_idempotent(tmp_path: Path) -> None:
    setup_datastream(str(tmp_path / "a.jsonl"))
    setup_datastream(str(tmp_path / "b.jsonl"))
    # Re-invoke drops the prior handler — exactly one remains (no double-write).
    assert len(logging.getLogger(DATASTREAM_LOGGER_NAME).handlers) == 1


def test_emit_non_serialisable_value_falls_back_to_str(tmp_path: Path) -> None:
    sink = tmp_path / "data_stream.jsonl"
    setup_datastream(str(sink))

    class _Weird:
        def __str__(self) -> str:
            return "weird-obj"

    emit("x", obj=_Weird())  # must not raise into the hot path
    for h in logging.getLogger(DATASTREAM_LOGGER_NAME).handlers:
        h.flush()
    rec = json.loads(sink.read_text(encoding="utf-8").strip())
    assert rec["obj"] == "weird-obj"


def test_emit_non_scalar_key_never_raises(tmp_path: Path) -> None:
    """A pathological non-scalar dict KEY must fall back, never raise."""
    sink = tmp_path / "data_stream.jsonl"
    setup_datastream(str(sink))

    # A dict value with a tuple key is not JSON-serialisable even with
    # default=str (json rejects non-str keys before default fires).
    emit("x", weird={(1, 2): "v"})  # must not raise into the hot path
    for h in logging.getLogger(DATASTREAM_LOGGER_NAME).handlers:
        h.flush()
    line = sink.read_text(encoding="utf-8").strip()
    rec = json.loads(line)  # still a valid JSONL record (fallback form)
    assert rec["event"] == "x"
    assert "raw" in rec  # fallback stringified the fields
