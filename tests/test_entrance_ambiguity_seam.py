"""Increment 1 — entrance ambiguity seam (sidecar, observe-only, AI-free).

DEMO/PAPER · in-loop GPT = 0. The EntranceJudge writes one ``entrance_judgments``
row per judged candidate carrying the ambiguous flag + per-lens leans. PURE
TELEMETRY: NO AI call, NO runtime consumer — the explicit hand-off point for
Increment 2's deferred async-advisory. These tests prove the DDL bootstraps, the
writer persists rows + the ambiguous flag, and the writer is FAIL-OPEN.
"""

from __future__ import annotations

import sqlite3

from polaris.core.probes.entrance import JudgeReading
from polaris.core.probes.tuning_log import (
    ENTRANCE_JUDGMENT_NEAR_THRESHOLD_BAND,
    PROBE_DDL,
    log_entrance_judgments,
    open_probe_db,
)


def _reading(iid: str, *, score: float, eligible: bool, ambiguous: bool) -> JudgeReading:
    return JudgeReading(
        instrument_id=iid,
        opportunity_score=score,
        trade_eligible=eligible,
        ambiguous=ambiguous,
        judge_margin=score - 0.3,
        contributing=["liquidity", "atr"],
        evidence={"liquidity": 0.5, "technical": -0.9},
    )


def test_ddl_bootstraps_entrance_table_and_index(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )
    }
    assert "entrance_judgments" in names
    assert "idx_entrance_judgments_ambiguous" in names
    conn.close()


def test_ddl_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    for _ in range(2):
        for stmt in PROBE_DDL:
            conn.execute(stmt)
    conn.close()


def test_writer_persists_rows_and_ambiguous_flag(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    readings = {
        "okx:BTC-USDT": _reading("okx:BTC-USDT", score=0.8, eligible=True, ambiguous=False),
        "okx:ETH-USDT": _reading("okx:ETH-USDT", score=0.5, eligible=True, ambiguous=True),
    }
    log_entrance_judgments(
        conn, ts=1000, run_id="r1", cycle_ts=1000, readings=readings
    )
    rows = {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT instrument_id, opportunity_score, trade_eligible, ambiguous "
            "FROM entrance_judgments"
        )
    }
    assert rows["okx:BTC-USDT"] == (0.8, 1, 0)
    assert rows["okx:ETH-USDT"] == (0.5, 1, 1)
    # venue/symbol split from the instrument_id
    vs = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT instrument_id, venue, symbol FROM entrance_judgments")
    }
    assert vs["okx:BTC-USDT"] == ("okx", "BTC-USDT")
    # ambiguous partial index targets only the flagged row
    n_ambig = conn.execute(
        "SELECT COUNT(*) FROM entrance_judgments WHERE ambiguous = 1"
    ).fetchone()[0]
    assert n_ambig == 1
    conn.close()


def test_writer_is_fail_open_on_none_conn() -> None:
    # No raise, no-op.
    log_entrance_judgments(None, ts=1, run_id="r", cycle_ts=1, readings={
        "okx:X": _reading("okx:X", score=0.5, eligible=True, ambiguous=False)
    })


def test_writer_is_fail_open_on_missing_table() -> None:
    conn = sqlite3.connect(":memory:")  # no DDL applied → table absent
    log_entrance_judgments(conn, ts=1, run_id="r", cycle_ts=1, readings={
        "okx:X": _reading("okx:X", score=0.5, eligible=True, ambiguous=False)
    })
    conn.close()


def test_deep_never_actionable_row_is_not_written(tmp_path: object) -> None:
    """A3: trade_eligible=0 far below the floor is DROPPED (write-amp cut)."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    deep_margin = -(ENTRANCE_JUDGMENT_NEAR_THRESHOLD_BAND + 0.20)
    readings = {
        "okx:DEEP": _reading(
            "okx:DEEP", score=0.30 + deep_margin, eligible=False, ambiguous=False
        ),
    }
    log_entrance_judgments(
        conn, ts=1000, run_id="r1", cycle_ts=1000, readings=readings
    )
    n = conn.execute("SELECT COUNT(*) FROM entrance_judgments").fetchone()[0]
    assert n == 0
    conn.close()


def test_eligible_and_near_threshold_rows_are_written(tmp_path: object) -> None:
    """A3: trade_eligible=1 rows AND near-threshold trade_eligible=0 rows persist."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    near_margin = -(ENTRANCE_JUDGMENT_NEAR_THRESHOLD_BAND - 0.01)
    readings = {
        "okx:ELIGIBLE": _reading("okx:ELIGIBLE", score=0.9, eligible=True, ambiguous=False),
        "okx:NEAR": _reading(
            "okx:NEAR", score=0.30 + near_margin, eligible=False, ambiguous=False
        ),
    }
    log_entrance_judgments(
        conn, ts=1000, run_id="r1", cycle_ts=1000, readings=readings
    )
    ids = {
        r[0] for r in conn.execute("SELECT instrument_id FROM entrance_judgments")
    }
    assert ids == {"okx:ELIGIBLE", "okx:NEAR"}
    conn.close()
