"""Tests for the pts-classes boot sequence (group WIRE — hydrate + bootstrap).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved — bootstrap seeds a capital-ROUTING record only;
BENCH-seeded strategies still fully signal/learn (no_block_filter_architecture).
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.scripts._production_boot_classes import (
    boot_hydrate_and_bootstrap_strategy_class,
)
from polaris.storage.schema import init_db
from polaris.strategies import STRATEGY_REGISTRY


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def test_bootstrap_seeds_every_registered_candidate_on_empty_table(conn):
    n = boot_hydrate_and_bootstrap_strategy_class(conn, now_ts=1_700_000_000)
    assert n == len(STRATEGY_REGISTRY)
    rows = conn.execute("SELECT COUNT(*) FROM strategy_class").fetchone()[0]
    assert rows == len(STRATEGY_REGISTRY)


def test_bootstrap_defaults_to_prove_with_no_history(conn):
    boot_hydrate_and_bootstrap_strategy_class(conn, now_ts=1_700_000_000)
    row = conn.execute(
        "SELECT strategy_class FROM strategy_class WHERE strategy_id = ?",
        ("gold_riskoff_trend_amplify",),
    ).fetchone()
    assert row[0] == "PROVE"


def test_bootstrap_seeds_earn_for_a_proven_winner(conn):
    """A candidate with a clearly-positive replayed score_F is seeded straight
    into EARN (never held in probation) per the three-way outcome (SSOT S8)."""
    venue, strategy_id = "capital", "gold_riskoff_trend_amplify"
    for i in range(3):
        ts = 1_700_000_000 - (i + 1) * 3600
        position_id = f"p{i}"
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            "entry_strategy_id, active_strategy_id, side, qty, status, "
            "opened_ts, closed_ts) VALUES (?, ?, 'XAUUSD', ?, ?, ?, 'long', "
            "1.0, 'closed', ?, ?)",
            (position_id, venue, strategy_id, strategy_id, strategy_id,
             ts - 3600, ts),
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, ts_ms, order_id, "
            "contribution_id, pnl_usd, is_close) VALUES (?, ?, ?, ?, 'sell', "
            "100.0, 100.0, 1.0, ?, ?, ?, 500.0, 1)",
            (uuid.uuid4().hex, venue, f"{venue}:XAUUSD", strategy_id,
             ts * 1000, uuid.uuid4().hex, position_id),
        )
    conn.commit()
    boot_hydrate_and_bootstrap_strategy_class(conn, now_ts=1_700_000_000)
    row = conn.execute(
        "SELECT strategy_class FROM strategy_class WHERE venue = ? AND strategy_id = ?",
        (venue, strategy_id),
    ).fetchone()
    assert row[0] == "EARN"


def test_bootstrap_is_idempotent_never_resets_existing_row(conn):
    """A restart must NOT reset a live-tracked class/dwell/epoch."""
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, "
        "dwell, epoch_id) VALUES (?, ?, 'BENCH', 7, 3)",
        ("okx", "rsi_bb_pullback"),
    )
    conn.commit()
    n = boot_hydrate_and_bootstrap_strategy_class(conn, now_ts=1_700_000_000)
    assert n == 0  # hydrate-only path, no bootstrap replay
    row = conn.execute(
        "SELECT strategy_class, dwell, epoch_id FROM strategy_class "
        "WHERE venue = 'okx' AND strategy_id = 'rsi_bb_pullback'"
    ).fetchone()
    assert row == ("BENCH", 7, 3)
    # No candidate other than the pre-existing row got seeded either — the
    # empty-table check is table-wide, not per-candidate, on this path.
    total = conn.execute("SELECT COUNT(*) FROM strategy_class").fetchone()[0]
    assert total == 1


def test_boot_never_raises_on_internal_failure(conn, monkeypatch):
    def _boom(*a, **kw):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(
        "polaris.scripts._production_boot_classes.hydrate_strategy_class", _boom
    )
    n = boot_hydrate_and_bootstrap_strategy_class(conn, now_ts=1_700_000_000)  # no raise
    assert n == 0


def test_lookback_score_f_uses_legacy_axis_not_gross_bps(conn):
    """R2 rework fix: recover_classes.py's bootstrap thresholds
    (_BOOTSTRAP_EARN_THRESHOLD/_BOOTSTRAP_BENCH_THRESHOLD/
    _BOOTSTRAP_BENCH_BAND_A_THRESHOLD) are calibrated on the LEGACY
    net/fee-ratio scale and are out of the flip's remap scope —
    _lookback_score_f must sum legacy_score_contrib, never the (now
    gross_bps by default) judged score_contrib."""
    from polaris.scripts._production_boot_classes import _lookback_score_f

    venue, strategy_id = "okx", "rsi_bb_pullback"
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, closed_ts) VALUES ('p1', ?, 'BTC-USDT', ?, ?, ?, 'long', "
        "1.0, 'closed', 1699996400, 1700000000)",
        (venue, strategy_id, strategy_id, strategy_id),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'sell', 1000.0, 100.0, 1.0, "
        "1700000000000, ?, 'p1', 100.0, 1)",
        (uuid.uuid4().hex, venue, f"{venue}:BTC-USDT", strategy_id, uuid.uuid4().hex),
    )
    conn.commit()
    # legacy: net=100 / max(fee=1.0, 0.0001*notional=1000) = 100.0
    # gross_bps (would be wrong here): net=100 / notional=1000 * 10000 = 1000.0
    result = _lookback_score_f(conn, venue, strategy_id, lookback_days=35)
    assert result == pytest.approx(100.0)
