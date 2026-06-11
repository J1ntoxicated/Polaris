"""Incremental log scan: offsets, rotation reset, real live-log markers."""

from __future__ import annotations

import os

from tools.ops import log_scan
from tools.ops.ops_config import OpsConfig, load_state

STALL = "[tick-engine] STALL detected — iteration gap=12.00s (cadence=5.00s) at loop=42"
DB_LOCK = "sqlite3.OperationalError: database is locked"
WS_ERR = "[okx ws] connection error (attempt 3/8): ConnectionClosedError()"
WS_GAVE_UP = "[capital ws] 8 consecutive failures — giving up, REST-only mode"


def test_counts_only_new_lines_after_offset(cfg: OpsConfig) -> None:
    cfg.bot_log.write_text(STALL + "\n" + DB_LOCK + "\n", encoding="utf-8")
    first = log_scan.scan(cfg)
    assert first == {"stall": 1, "db_lock": 1, "ws_error": 0, "ws_gave_up": 0}

    second = log_scan.scan(cfg)  # no new bytes
    assert second == {"stall": 0, "db_lock": 0, "ws_error": 0, "ws_gave_up": 0}

    with open(cfg.bot_log, "a", encoding="utf-8") as fh:
        fh.write(WS_ERR + "\n" + STALL + "\n")
    third = log_scan.scan(cfg)
    assert third == {"stall": 1, "db_lock": 0, "ws_error": 1, "ws_gave_up": 0}


def test_rotation_inode_change_resets_offset(cfg: OpsConfig) -> None:
    cfg.bot_log.write_text(STALL + "\n" * 3, encoding="utf-8")
    log_scan.scan(cfg)
    # rotate: old file moved away, brand-new file at the fixed path (new inode)
    os.replace(cfg.bot_log, cfg.bot_log.with_name(cfg.bot_log.name + ".20260610"))
    cfg.bot_log.write_text(WS_GAVE_UP + "\n", encoding="utf-8")
    counts = log_scan.scan(cfg)
    assert counts["ws_gave_up"] == 1  # full rescan from offset 0
    state = load_state(cfg.state_path)
    assert state["log_scan"]["offset"] == cfg.bot_log.stat().st_size


def test_real_marker_strings_match(cfg: OpsConfig) -> None:
    cfg.bot_log.write_text(
        "\n".join([STALL, DB_LOCK, WS_ERR, WS_GAVE_UP, "plain INFO line"]) + "\n",
        encoding="utf-8",
    )
    counts = log_scan.scan(cfg)
    assert counts == {"stall": 1, "db_lock": 1, "ws_error": 1, "ws_gave_up": 1}


def test_missing_log_returns_zero_counts(cfg: OpsConfig) -> None:
    assert log_scan.scan(cfg) == {
        "stall": 0, "db_lock": 0, "ws_error": 0, "ws_gave_up": 0,
    }
