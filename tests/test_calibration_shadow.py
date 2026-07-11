"""Probability calibration shadow (frontgate-scan item #4, G5) — TDD.

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched.

``record_entry_snapshot`` (sizing-time seam, self-contained fail-open) and
``record_close_outcome`` (close-time seam, raises — caller's ``_safe_*``
wrapper fails open) round-trip a (predicted_p_pos, realized_won) pair keyed
by ``signal_id``, and the entry snapshot is NEVER re-derived after close
(look-ahead guard).
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from polaris.core.learners.calibration_shadow import (
    record_close_outcome,
    record_entry_snapshot,
)
from polaris.storage.schema import init_db

NOW = 1_780_000_000


def _conn() -> sqlite3.Connection:
    return init_db(":memory:")


def _row(conn: sqlite3.Connection, signal_id: str) -> Any:
    return conn.execute(
        "SELECT venue, strategy, ticker, regime, predicted_p_pos, "
        "n_samples_at_entry, realized_won, realized_pnl_r, closed_ts, "
        "created_ts FROM calibration_pairs WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()


def test_entry_snapshot_cold_cell_defaults_to_uninformed_prior() -> None:
    """No learner_posterior row yet -> snapshot records the table's own
    uninformed default (p_pos=0.5, n_samples=0) rather than skipping."""
    conn = _conn()
    record_entry_snapshot(
        conn, signal_id="sig-1", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW,
    )
    row = _row(conn, "sig-1")
    assert row is not None
    venue, strategy, ticker, regime, p_pos, n_samples, won, pnl_r, closed_ts, created_ts = row
    assert (venue, strategy, ticker, regime) == ("okx", "tsmom", "BTC-USDT", "trend_up")
    assert p_pos == pytest.approx(0.5)
    assert n_samples == 0
    assert won is None
    assert pnl_r is None
    assert closed_ts is None
    assert created_ts == NOW


def test_entry_snapshot_reads_live_learner_posterior_p_pos() -> None:
    """A warm cell's p_pos/n_samples are captured verbatim at sizing time."""
    conn = _conn()
    conn.execute(
        "INSERT INTO learner_posterior "
        "(exchange, strategy, ticker, regime, p_pos, n_samples, updated_ts) "
        "VALUES ('okx', 'tsmom', 'BTC-USDT', 'trend_up', 0.72, 34, ?)",
        (NOW - 100,),
    )
    record_entry_snapshot(
        conn, signal_id="sig-2", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW,
    )
    row = _row(conn, "sig-2")
    assert row is not None
    assert row[4] == pytest.approx(0.72)
    assert row[5] == 34


def test_entry_snapshot_first_wins_on_resize() -> None:
    """A signal re-sized (e.g. G3 MODIFY re-price) keeps its FIRST snapshot —
    a later resize with a DIFFERENT live p_pos must not overwrite it."""
    conn = _conn()
    conn.execute(
        "INSERT INTO learner_posterior "
        "(exchange, strategy, ticker, regime, p_pos, n_samples, updated_ts) "
        "VALUES ('okx', 'tsmom', 'BTC-USDT', 'trend_up', 0.60, 10, ?)",
        (NOW - 100,),
    )
    record_entry_snapshot(
        conn, signal_id="sig-3", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW,
    )
    # Cell mutates (other trades closed) before the resize.
    conn.execute(
        "UPDATE learner_posterior SET p_pos = 0.91, n_samples = 25 "
        "WHERE exchange='okx' AND strategy='tsmom' AND ticker='BTC-USDT' "
        "AND regime='trend_up'"
    )
    record_entry_snapshot(
        conn, signal_id="sig-3", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW + 5,
    )
    row = _row(conn, "sig-3")
    assert row is not None
    assert row[4] == pytest.approx(0.60)  # first snapshot preserved
    assert row[5] == 10


def test_entry_snapshot_fails_open_never_raises() -> None:
    """A broken connection must not propagate — the sizing chain has no
    fault-isolation wrapper around this call (self-contained fail-open)."""
    conn = _conn()
    conn.execute("DROP TABLE calibration_pairs")
    # Must not raise despite the table being gone.
    record_entry_snapshot(
        conn, signal_id="sig-4", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW,
    )


def test_close_outcome_fills_realized_fields_by_signal_id() -> None:
    conn = _conn()
    record_entry_snapshot(
        conn, signal_id="sig-5", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW,
    )
    record_close_outcome(
        conn, signal_id="sig-5", won=True, pnl_r=1.8, now_ts=NOW + 3600,
    )
    row = _row(conn, "sig-5")
    assert row is not None
    assert row[6] == 1  # realized_won
    assert row[7] == pytest.approx(1.8)  # realized_pnl_r
    assert row[8] == NOW + 3600  # closed_ts


def test_close_outcome_accumulates_across_partial_and_remainder_slices() -> None:
    """Multi-slice close (``fold_close_slice`` per partial + terminal
    remainder) calls ``record_close_outcome`` once PER SLICE — the persisted
    outcome must be the WHOLE-POSITION aggregate, never the last (remainder)
    slice alone (round-3 rework, verifier note)."""
    conn = _conn()
    record_entry_snapshot(
        conn, signal_id="sig-8", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW,
    )
    # Partial slice: +0.5R (a win on its own).
    record_close_outcome(conn, signal_id="sig-8", won=True, pnl_r=0.5, now_ts=NOW + 10)
    row = _row(conn, "sig-8")
    assert row is not None
    assert row[6] == 1  # intermediate: running total is a win so far
    assert row[7] == pytest.approx(0.5)
    # Remainder slice: -1.2R (a big loss) -> whole-position aggregate is
    # -0.7R, a LOSS, even though this slice alone is small vs the total swing.
    record_close_outcome(conn, signal_id="sig-8", won=False, pnl_r=-1.2, now_ts=NOW + 20)
    row = _row(conn, "sig-8")
    assert row is not None
    assert row[6] == 0  # whole-position aggregate flips to a loss
    assert row[7] == pytest.approx(-0.7)  # 0.5 + (-1.2)
    assert row[8] == NOW + 20  # closed_ts reflects the terminal write


def test_close_outcome_noop_when_no_entry_snapshot_exists() -> None:
    """A calibration miss at entry must never gate/abort the close — the
    close-side call is a silent 0-row UPDATE, not an error."""
    conn = _conn()
    record_close_outcome(
        conn, signal_id="never-snapshotted", won=False, pnl_r=-1.0, now_ts=NOW,
    )
    assert _row(conn, "never-snapshotted") is None


def test_close_outcome_raises_on_broken_connection() -> None:
    """Fail-open policy lives with the CALLER (see module docstring) — this
    function itself raises, mirroring maybe_update_posterior's contract."""
    conn = _conn()
    conn.execute("DROP TABLE calibration_pairs")
    with pytest.raises(sqlite3.OperationalError):
        record_close_outcome(
            conn, signal_id="sig-6", won=True, pnl_r=1.0, now_ts=NOW,
        )


def test_close_outcome_never_rereads_learner_posterior() -> None:
    """Look-ahead guard: record_close_outcome must not touch learner_posterior
    at all — the predicted value was already snapshotted at entry, and a
    post-close re-query would leak subsequent closes on the same cell into
    what must be a known-at-entry-time prediction."""
    conn = _conn()
    conn.execute(
        "INSERT INTO learner_posterior "
        "(exchange, strategy, ticker, regime, p_pos, n_samples, updated_ts) "
        "VALUES ('okx', 'tsmom', 'BTC-USDT', 'trend_up', 0.55, 5, ?)",
        (NOW - 100,),
    )
    record_entry_snapshot(
        conn, signal_id="sig-7", exchange="okx", strategy="tsmom",
        ticker="BTC-USDT", regime="trend_up", now_ts=NOW,
    )
    # The cell moves a LOT between entry and close (many other trades close).
    conn.execute(
        "UPDATE learner_posterior SET p_pos = 0.05, n_samples = 200 "
        "WHERE exchange='okx' AND strategy='tsmom' AND ticker='BTC-USDT' "
        "AND regime='trend_up'"
    )
    record_close_outcome(
        conn, signal_id="sig-7", won=True, pnl_r=1.2, now_ts=NOW + 60,
    )
    row = _row(conn, "sig-7")
    assert row is not None
    # predicted_p_pos is UNCHANGED by the close call (still the entry-time 0.55,
    # never the post-close-drift 0.05).
    assert row[4] == pytest.approx(0.55)
