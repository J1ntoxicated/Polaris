"""Measurement-reset baseline (Jin 2026-06-23) — forward edge that restarts clean
at each main-logic change. DEMO/PAPER; fills.$ (fee-net) = truth.

Jin: '실측 위해서 메인로직 바뀌면 pnl 리셋해서 측정해줘.' = a FORWARD measurement
window that restarts at each main-logic change. Old trades are archived /
referenceable (NEVER deleted), not mixed into the new edge.

These tests pin the OBSERVABILITY-ONLY contract (no trading/sizing/gate change,
aggressive bias preserved, flow_not_block — nothing here blocks the pipeline):

  1. SCHEMA: ``measurement_resets`` table exists after DDL bootstrap, idempotent.
  2. STAMP: ``stamp_measurement_reset`` inserts ONE row at the current ts carrying
     the current real-fee-net equity baseline; old rows are untouched.
  3. SINCE-RESET: ``_since_reset_rollup`` counts ONLY trades OPENED under the new
     logic (``positions.opened_ts >= LATEST reset_ts``); PF / net$ / win% / avg_r
     / equity-change over that window match the fills.$ truth.
  4. FALLBACK: no reset row → since_reset is None (all-time stays available).
  5. PRE-RESET DATA PRESERVED: stamping never deletes a position / fill / reset.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.metrics.risk_unit import r_budget_for_venue
from polaris.scripts.dashboard.snapshot_queries import _since_reset_rollup
from polaris.storage.measurement_reset import (
    latest_reset,
    stamp_measurement_reset,
)

_OKX_BUDGET = r_budget_for_venue("okx")


def _seed_trade(
    conn: sqlite3.Connection,
    *,
    pid: str,
    pnl_usd: float,
    opened_ts: int,
    fee_usd: float = 0.0,
    symbol: str = "BTC",
    venue: str = "okx",
    strategy: str = "s",
    status: str = "closed",
    with_close_fill: bool = True,
) -> None:
    """One position OPENED at ``opened_ts`` + its close fill carrying realized $.

    The close fill's ``ts_ms`` is the dollar truth; the position's ``opened_ts``
    is the since-reset window key (a trade counts only if it was OPENED under the
    new logic).
    """
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, closed_ts, swap_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 1.0, ?, ?, ?, 0)",
        (pid, venue, symbol, f"crypto:{symbol}", strategy, strategy, strategy,
         status, opened_ts, opened_ts + 100),
    )
    if with_close_fill:
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
            " size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
            " base_qty, pnl_usd, is_close) "
            "VALUES (?, ?, ?, ?, 'sell', 1000.0, 100.0, ?, ?, ?, ?, 1.0, ?, 1)",
            (f"{pid}_c", venue, f"{venue}:{symbol}", strategy, fee_usd,
             (opened_ts + 100) * 1000, f"{pid}_ord", pid, pnl_usd),
        )


# ---------------------------------------------------------------------------
# 1. SCHEMA
# ---------------------------------------------------------------------------


def test_measurement_resets_table_exists(memdb: sqlite3.Connection) -> None:
    row = memdb.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='measurement_resets'"
    ).fetchone()
    assert row is not None
    cols = {r[1] for r in memdb.execute("PRAGMA table_info(measurement_resets)")}
    assert cols == {
        "reset_id", "reset_ts", "equity_baseline_usd", "label", "git_sha",
    }


# ---------------------------------------------------------------------------
# 2. STAMP
# ---------------------------------------------------------------------------


def test_stamp_inserts_row_with_baseline(memdb: sqlite3.Connection) -> None:
    rid = stamp_measurement_reset(
        memdb, label="W4 cutover", git_sha="abc1234",
        reset_ts=5000, equity_baseline_usd=160_000.0,
    )
    row = latest_reset(memdb)
    assert row is not None
    assert row.reset_id == rid
    assert row.reset_ts == 5000
    assert row.label == "W4 cutover"
    assert row.git_sha == "abc1234"
    assert row.equity_baseline_usd == pytest.approx(160_000.0)


def test_latest_reset_returns_newest(memdb: sqlite3.Connection) -> None:
    stamp_measurement_reset(memdb, label="first", git_sha="aaa",
                            reset_ts=1000, equity_baseline_usd=100.0)
    stamp_measurement_reset(memdb, label="second", git_sha="bbb",
                            reset_ts=9000, equity_baseline_usd=200.0)
    row = latest_reset(memdb)
    assert row is not None
    assert row.label == "second"
    assert row.reset_ts == 9000
    # OLD row untouched — both rows persist (archived, never deleted).
    n = memdb.execute("SELECT COUNT(*) FROM measurement_resets").fetchone()[0]
    assert n == 2


# ---------------------------------------------------------------------------
# 3. SINCE-RESET ROLLUP — opened_ts window, fills.$ truth
# ---------------------------------------------------------------------------


def test_since_reset_counts_only_post_reset_opened(memdb: sqlite3.Connection) -> None:
    # PRE-reset trades (opened before the reset_ts) — must be EXCLUDED even though
    # their close fills land AFTER the reset_ts. The window key is opened_ts.
    _seed_trade(memdb, pid="old1", pnl_usd=5000.0, opened_ts=1000)
    _seed_trade(memdb, pid="old2", pnl_usd=-9000.0, opened_ts=2000)
    # Stamp the reset at ts=5000 with a baseline equity.
    stamp_measurement_reset(memdb, label="W4", git_sha="sha",
                            reset_ts=5000, equity_baseline_usd=160_000.0)
    # POST-reset trades (opened_ts >= 5000) — the new-logic edge.
    _seed_trade(memdb, pid="new1", pnl_usd=3160.0, opened_ts=6000)   # +2R win
    _seed_trade(memdb, pid="new2", pnl_usd=1580.0, opened_ts=7000)   # +1R win
    _seed_trade(memdb, pid="new3", pnl_usd=-1580.0, opened_ts=8000)  # −1R loss

    roll = _since_reset_rollup(memdb)
    assert roll is not None
    # Only the 3 new-logic trades counted (old1/old2 excluded by opened_ts).
    assert roll.n == 3
    # net$ = Σ close pnl − Σ fee, over the post-reset window = 3160+1580−1580 = 3160
    assert roll.net_usd == pytest.approx(3160.0)
    # win% = 2/3.
    assert roll.win_pct == pytest.approx(2 / 3 * 100.0)
    # PF = gross_win / gross_loss = (3160+1580)/1580 = 3.0.
    assert roll.pf == pytest.approx(3.0)
    # avg_r = mean(stream-common R) = mean(+2, +1, −1) = +0.6667R.
    assert roll.avg_r == pytest.approx((2.0 + 1.0 - 1.0) / 3.0, abs=1e-3)
    # equity_change = net$ over the window (baseline + net$ = new equity).
    assert roll.equity_change_usd == pytest.approx(3160.0)
    assert roll.reset_ts == 5000
    assert roll.label == "W4"


def test_since_reset_net_is_fee_net(memdb: sqlite3.Connection) -> None:
    stamp_measurement_reset(memdb, label="W4", git_sha="sha",
                            reset_ts=5000, equity_baseline_usd=100_000.0)
    # gross +2000, fee 100 on the close leg → net$ = 1900.
    _seed_trade(memdb, pid="n1", pnl_usd=2000.0, fee_usd=100.0, opened_ts=6000)
    roll = _since_reset_rollup(memdb)
    assert roll is not None
    assert roll.net_usd == pytest.approx(1900.0)


def test_since_reset_excludes_reconciled(memdb: sqlite3.Connection) -> None:
    # RECONCILED (tracking-failure) positions are NOT trades — excluded from the
    # R/WR/PF ledger exactly like the all-time ticker/strategy rollups.
    stamp_measurement_reset(memdb, label="W4", git_sha="sha",
                            reset_ts=5000, equity_baseline_usd=100_000.0)
    _seed_trade(memdb, pid="n1", pnl_usd=1580.0, opened_ts=6000)
    _seed_trade(memdb, pid="rec", pnl_usd=-99000.0, opened_ts=6500,
                status="reconciled")
    roll = _since_reset_rollup(memdb)
    assert roll is not None
    assert roll.n == 1
    assert roll.net_usd == pytest.approx(1580.0)


# ---------------------------------------------------------------------------
# 4. FALLBACK — no reset row
# ---------------------------------------------------------------------------


def test_no_reset_returns_none(memdb: sqlite3.Connection) -> None:
    _seed_trade(memdb, pid="p1", pnl_usd=1000.0, opened_ts=1000)
    assert latest_reset(memdb) is None
    assert _since_reset_rollup(memdb) is None


# ---------------------------------------------------------------------------
# 5. PRE-RESET DATA PRESERVED
# ---------------------------------------------------------------------------


def test_stamp_never_deletes_pre_reset_data(memdb: sqlite3.Connection) -> None:
    _seed_trade(memdb, pid="old1", pnl_usd=5000.0, opened_ts=1000)
    _seed_trade(memdb, pid="old2", pnl_usd=-2000.0, opened_ts=2000)
    pos_before = memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    fills_before = memdb.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    stamp_measurement_reset(memdb, label="W4", git_sha="sha",
                            reset_ts=5000, equity_baseline_usd=160_000.0)
    pos_after = memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    fills_after = memdb.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert pos_after == pos_before == 2
    assert fills_after == fills_before == 2
