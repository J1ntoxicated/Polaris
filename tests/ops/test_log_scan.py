"""Incremental log scan: offsets, rotation reset, real live-log markers."""

from __future__ import annotations

import os

from tools.ops import log_scan
from tools.ops.ops_config import OpsConfig, load_state

STALL = "[tick-engine] STALL detected — iteration gap=12.00s (cadence=5.00s) at loop=42"
DB_LOCK = "sqlite3.OperationalError: database is locked"
WS_ERR = "[okx ws] connection error (attempt 3/8): ConnectionClosedError()"
WS_GAVE_UP = "[capital ws] 8 consecutive failures — giving up, REST-only mode"
# pts-classes group G — new structured telemetry markers (see watchdog.py /
# engine.py / probe_reranker.py emission points).
EXEC_STARVED = "[pts-classes] okx/gold_breakout_1h transition unchanged (EXEC_STARVED)"
PROBE_FEE_EXHAUSTED = "[probe-reranker] okx/gold_breakout_1h rank=2 FEE_CAP_EXHAUSTED"

_ZERO_COUNTS = {
    "stall": 0, "db_lock": 0, "ws_error": 0, "ws_gave_up": 0,
    "exec_starved": 0, "probe_fee_exhausted": 0,
}


def test_counts_only_new_lines_after_offset(cfg: OpsConfig) -> None:
    cfg.bot_log.write_text(STALL + "\n" + DB_LOCK + "\n", encoding="utf-8")
    first = log_scan.scan(cfg)
    assert first == {**_ZERO_COUNTS, "stall": 1, "db_lock": 1}

    second = log_scan.scan(cfg)  # no new bytes
    assert second == _ZERO_COUNTS

    with open(cfg.bot_log, "a", encoding="utf-8") as fh:
        fh.write(WS_ERR + "\n" + STALL + "\n")
    third = log_scan.scan(cfg)
    assert third == {**_ZERO_COUNTS, "stall": 1, "ws_error": 1}


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
        "\n".join([
            STALL, DB_LOCK, WS_ERR, WS_GAVE_UP, EXEC_STARVED, PROBE_FEE_EXHAUSTED,
            "plain INFO line",
        ]) + "\n",
        encoding="utf-8",
    )
    counts = log_scan.scan(cfg)
    assert counts == {
        "stall": 1, "db_lock": 1, "ws_error": 1, "ws_gave_up": 1,
        "exec_starved": 1, "probe_fee_exhausted": 1,
    }


def test_missing_log_returns_zero_counts(cfg: OpsConfig) -> None:
    assert log_scan.scan(cfg) == _ZERO_COUNTS


def test_exec_starved_marker_counts_independently(cfg: OpsConfig) -> None:
    cfg.bot_log.write_text(EXEC_STARVED + "\n" + EXEC_STARVED + "\n", encoding="utf-8")
    counts = log_scan.scan(cfg)
    assert counts == {**_ZERO_COUNTS, "exec_starved": 2}


def test_probe_fee_exhausted_marker_excludes_routine_concurrency_cap(
    cfg: OpsConfig,
) -> None:
    """CONCURRENCY_CAP_EXHAUSTED (only top-N ranks get a slot) is a routine,
    expected capacity outcome every rerank cycle — it must NOT alert. Only
    the shared 24h fee-BUDGET exhaustion (a real capital-constraint signal)
    matches this marker."""
    concurrency = "[probe-reranker] okx/other_strat rank=4 CONCURRENCY_CAP_EXHAUSTED"
    cfg.bot_log.write_text(PROBE_FEE_EXHAUSTED + "\n" + concurrency + "\n", encoding="utf-8")
    counts = log_scan.scan(cfg)
    assert counts == {**_ZERO_COUNTS, "probe_fee_exhausted": 1}
