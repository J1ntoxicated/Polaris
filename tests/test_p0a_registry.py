"""P0a STEP 3 — honest-N variant registry (TDD).

OFFLINE harness only. The registry persists one row per (variant × cell × split)
replay outcome to a SEPARATE ``data/p0a_registry.sqlite`` (NEW db). It touches NO
live trading / sizing / T4 chain / gate thresholds, and NEVER writes to
``data/polaris_live.sqlite``. behavior-0: a pure read-model, not consulted by the
live loop.

Test plan (from step brief / design.test_plan TDD #6):
  (a) round-trip + reconnect: append N rows, close, reopen, read them back.
  (b) PK (cell_key, variant_run_id) uniqueness: duplicate insert is an upsert,
      not a corruption / not a second row.
  (c) aggregate_pass_rate: correct per-cell / per-strategy / overall IS & OOS
      pass-rates + total trials_searched on a fixture.
  (d) cumulative trials per cell sums across two separate runs (persisted).
  (e) sequential single-writer append over many rows stays intact (no corruption).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from polaris.core.evolve.registry import (
    TrialRow,
    aggregate_pass_rate,
    cumulative_trials_per_cell,
    open_registry,
    record_trial,
)


def _row(
    *,
    cell_key: str,
    variant_run_id: str,
    strategy: str = "volume_burst",
    exchange: str = "okx",
    ticker: str = "BTC-USDT",
    regime: str = "chop",
    variant_id: str = "volume_burst|vol_z_threshold=2.0",
    param_json: str = '{"vol_z_threshold": 2.0}',
    is_pass: int = 0,
    oos_pass: int = 0,
    n_trades: int = 0,
    pnl_r_mean: float = 0.0,
    lcb: float = 0.0,
    ucb: float = 0.0,
    dsr: float = 0.0,
    trials_searched: int = 1,
    is_oos_spread: float = 0.0,
    created_ts: int | None = None,
) -> TrialRow:
    return TrialRow(
        cell_key=cell_key,
        variant_run_id=variant_run_id,
        exchange=exchange,
        strategy=strategy,
        ticker=ticker,
        regime=regime,
        variant_id=variant_id,
        param_json=param_json,
        is_pass=is_pass,
        oos_pass=oos_pass,
        n_trades=n_trades,
        pnl_r_mean=pnl_r_mean,
        lcb=lcb,
        ucb=ucb,
        dsr=dsr,
        trials_searched=trials_searched,
        is_oos_spread=is_oos_spread,
        created_ts=created_ts if created_ts is not None else int(time.time()),
    )


# ---------------------------------------------------------------------------
# (a) round-trip + reconnect — persistence survives close/reopen
# ---------------------------------------------------------------------------


def test_round_trip_survives_reconnect(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    rows = [
        _row(cell_key="okx|volume_burst|BTC-USDT|chop", variant_run_id=f"run{i}") for i in range(5)
    ]
    for r in rows:
        record_trial(conn, r)
    conn.close()

    # Reopen a fresh connection to the same file — data must be on disk.
    conn2 = open_registry(str(db))
    got = conn2.execute("SELECT COUNT(*) FROM variant_trials").fetchone()[0]
    assert got == 5
    ids = {r[0] for r in conn2.execute("SELECT variant_run_id FROM variant_trials").fetchall()}
    assert ids == {f"run{i}" for i in range(5)}
    conn2.close()


# ---------------------------------------------------------------------------
# (b) PK uniqueness — duplicate (cell_key, variant_run_id) upserts, no dup row
# ---------------------------------------------------------------------------


def test_pk_upsert_no_duplicate_row(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck = "okx|volume_burst|BTC-USDT|chop"
    record_trial(conn, _row(cell_key=ck, variant_run_id="r1", oos_pass=0, n_trades=3))
    # Same PK, updated outcome — must UPSERT (overwrite) not insert a 2nd row.
    record_trial(conn, _row(cell_key=ck, variant_run_id="r1", oos_pass=1, n_trades=9))

    n = conn.execute("SELECT COUNT(*) FROM variant_trials").fetchone()[0]
    assert n == 1
    oos, nt = conn.execute(
        "SELECT oos_pass, n_trades FROM variant_trials WHERE cell_key=? AND variant_run_id=?",
        (ck, "r1"),
    ).fetchone()
    assert oos == 1
    assert nt == 9
    conn.close()


def test_raw_pk_violation_still_raises(tmp_path: Path) -> None:
    """The underlying PK constraint is real (a raw INSERT dup raises)."""
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck = "okx|volume_burst|BTC-USDT|chop"
    record_trial(conn, _row(cell_key=ck, variant_run_id="r1"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO variant_trials "
            "(cell_key, variant_run_id, exchange, strategy, ticker, regime, "
            " variant_id, param_json, created_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ck, "r1", "okx", "volume_burst", "BTC-USDT", "chop", "v", "{}", 0),
        )
    conn.close()


# ---------------------------------------------------------------------------
# (c) aggregate_pass_rate — IS/OOS rates + trials_searched per cell/strategy/all
# ---------------------------------------------------------------------------


def test_aggregate_pass_rate_fixture(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck_a = "okx|volume_burst|BTC-USDT|chop"
    ck_b = "okx|tsmom|ETH-USDT|bull_trend"
    # cell A: 2 trials, 1 oos_pass, 2 is_pass; trials_searched 9 + 9
    record_trial(
        conn,
        _row(
            cell_key=ck_a,
            variant_run_id="a1",
            strategy="volume_burst",
            is_pass=1,
            oos_pass=1,
            trials_searched=9,
        ),
    )
    record_trial(
        conn,
        _row(
            cell_key=ck_a,
            variant_run_id="a2",
            strategy="volume_burst",
            is_pass=1,
            oos_pass=0,
            trials_searched=9,
        ),
    )
    # cell B: 2 trials, 0 oos_pass, 1 is_pass; trials_searched 4 + 4
    record_trial(
        conn,
        _row(
            cell_key=ck_b,
            variant_run_id="b1",
            strategy="tsmom",
            ticker="ETH-USDT",
            regime="bull_trend",
            is_pass=1,
            oos_pass=0,
            trials_searched=4,
        ),
    )
    record_trial(
        conn,
        _row(
            cell_key=ck_b,
            variant_run_id="b2",
            strategy="tsmom",
            ticker="ETH-USDT",
            regime="bull_trend",
            is_pass=0,
            oos_pass=0,
            trials_searched=4,
        ),
    )

    agg = aggregate_pass_rate(conn)

    # overall: 4 trials, is_pass 3/4, oos_pass 1/4, trials_searched 9+9+4+4=26
    assert agg.overall.n_trials == 4
    assert agg.overall.is_pass == 3
    assert agg.overall.oos_pass == 1
    assert agg.overall.is_pass_rate == pytest.approx(0.75)
    assert agg.overall.oos_pass_rate == pytest.approx(0.25)
    assert agg.overall.trials_searched == 26

    # per-cell
    by_cell = {c.key: c for c in agg.per_cell}
    assert by_cell[ck_a].n_trials == 2
    assert by_cell[ck_a].oos_pass == 1
    assert by_cell[ck_a].oos_pass_rate == pytest.approx(0.5)
    assert by_cell[ck_a].trials_searched == 18
    assert by_cell[ck_b].oos_pass == 0
    assert by_cell[ck_b].oos_pass_rate == pytest.approx(0.0)

    # per-strategy
    by_strat = {c.key: c for c in agg.per_strategy}
    assert by_strat["volume_burst"].n_trials == 2
    assert by_strat["volume_burst"].is_pass == 2
    assert by_strat["volume_burst"].oos_pass == 1
    assert by_strat["tsmom"].is_pass == 1
    assert by_strat["tsmom"].oos_pass == 0
    conn.close()


# ---------------------------------------------------------------------------
# (d) cumulative trials per cell sums across two separate runs (persisted)
# ---------------------------------------------------------------------------


def test_cumulative_trials_accumulate_across_runs(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    ck = "okx|volume_burst|BTC-USDT|chop"

    # run 1 — 3 distinct variant_run_ids in the cell, then close.
    conn = open_registry(str(db))
    for i in range(3):
        record_trial(conn, _row(cell_key=ck, variant_run_id=f"run1_{i}"))
    conn.close()

    # run 2 — reopen, add 2 more distinct variant_run_ids in the same cell.
    conn2 = open_registry(str(db))
    for i in range(2):
        record_trial(conn2, _row(cell_key=ck, variant_run_id=f"run2_{i}"))

    cum = cumulative_trials_per_cell(conn2)
    assert cum[ck] == 5  # 3 from run1 + 2 from run2, persisted across reconnect
    conn2.close()


# ---------------------------------------------------------------------------
# (e) sequential single-writer append over many rows stays intact
# ---------------------------------------------------------------------------


def test_sequential_bulk_append_intact(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    ck = "okx|volume_burst|BTC-USDT|chop"
    for i in range(250):
        record_trial(conn, _row(cell_key=ck, variant_run_id=f"r{i}", n_trades=i))
    conn.close()

    conn2 = open_registry(str(db))
    cnt, total = conn2.execute("SELECT COUNT(*), SUM(n_trades) FROM variant_trials").fetchone()
    assert cnt == 250
    assert total == sum(range(250))
    conn2.close()


# ---------------------------------------------------------------------------
# offline / no-live-DB guard — open_registry never targets the live DB path
# ---------------------------------------------------------------------------


def test_open_registry_is_isolated_file(tmp_path: Path) -> None:
    db = tmp_path / "p0a_registry.sqlite"
    conn = open_registry(str(db))
    record_trial(conn, _row(cell_key="okx|x|y|z", variant_run_id="r1"))
    conn.close()
    # The registry file exists; no other DB was created beside it.
    assert db.exists()
    siblings = {p.name for p in db.parent.iterdir()}
    assert "polaris_live.sqlite" not in siblings
