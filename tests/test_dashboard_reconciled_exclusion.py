"""Hardening #1 (2026-06-23) — reconciled close-fill pollution exclusion.

A position reconciled away (``status='reconciled'``) is a TRACKING FAILURE, not a
trade. Its close-leg ``fills.pnl_usd`` must never pollute the headline realised-$
surfaces (daily count, WR, PF, equity, per-stream rollup) — exactly the exclusion
the R column (``_closed_position_r``) already applies via ``status='closed'``.
Its drift instead surfaces on the SEPARATE reconciled tile as the ACTUAL realized
``Σ fills.pnl_usd`` (primary) with the mae_r×risk est kept as a labeled secondary.

Display/telemetry only — ``pnl_usd`` dollar truth is never altered.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.scripts.dashboard.snapshot_queries import (
    _build_dual_equity_curve,
    _build_equity_curve,
    _daily_realised_pnl,
    _per_stream_summary,
    _strategy_stats,
)
from polaris.scripts.dashboard.snapshot_sections import _rotation_telemetry


def _seed(
    conn: sqlite3.Connection,
    *,
    pid: str,
    pnl_usd: float,
    venue: str = "okx",
    strategy: str = "s1",
    status: str = "closed",
    ts_ms: int = 1500,
) -> None:
    """Seed a position + its close fill (open leg too, for fee realism)."""
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, closed_ts, swap_count) "
        "VALUES (?, ?, 'BTC', 'crypto:BTC', ?, ?, ?, 'long', 1.0, ?, 1000, 2000, 0)",
        (pid, venue, strategy, strategy, strategy, status),
    )
    # open leg (is_close=0): pnl 0, carries a fee.
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, base_qty, pnl_usd, is_close) "
        "VALUES (?, ?, ?, ?, 'buy', 1000.0, 100.0, 1.0, 0.0, ?, ?, ?, 1.0, 0.0, 0)",
        (f"{pid}_o", venue, f"{venue}:BTC", strategy, ts_ms - 100, f"{pid}_oo", pid),
    )
    # close leg (is_close=1): carries the realized pnl_usd.
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, base_qty, pnl_usd, is_close) "
        "VALUES (?, ?, ?, ?, 'sell', 1000.0, 100.0, 1.0, 0.0, ?, ?, ?, 1.0, ?, 1)",
        (f"{pid}_c", venue, f"{venue}:BTC", strategy, ts_ms, f"{pid}_cc", pid, pnl_usd),
    )


def test_daily_realised_excludes_reconciled(memdb: sqlite3.Connection) -> None:
    _seed(memdb, pid="p1", pnl_usd=300.0)                       # real closed win
    _seed(memdb, pid="p2", pnl_usd=-100.0)                      # real closed loss
    _seed(memdb, pid="p3", pnl_usd=-500.0, status="reconciled")  # tracking failure

    net, count = _daily_realised_pnl(memdb, now_s=3000)

    # count = 2 real closed trades (reconciled close-fill excluded).
    assert count == 2
    # net pnl = (+300 -100) gross − fees on the 4 NON-reconciled legs (4×$1).
    # The reconciled −500 close pnl AND its 2 legs' fees are fully excluded.
    assert net == pytest.approx(300.0 - 100.0 - 4.0)


def test_strategy_stats_excludes_reconciled(memdb: sqlite3.Connection) -> None:
    _seed(memdb, pid="p1", pnl_usd=300.0, strategy="s1")
    _seed(memdb, pid="p2", pnl_usd=-100.0, strategy="s1")
    _seed(memdb, pid="p3", pnl_usd=-9000.0, strategy="s1", status="reconciled")

    stats = {s.strategy_id: s for s in _strategy_stats(memdb, now_s=3000, positions=[])}
    s1 = stats["s1"]

    assert s1.closed_n == 2  # reconciled close-fill not counted
    assert s1.wr_pct == pytest.approx(50.0)  # 1 win / 2 — not 1/3
    # PF = gross_win / gross_loss = 300 / 100 = 3.0 (reconciled −9000 excluded).
    assert s1.pf == pytest.approx(3.0)
    assert s1.pnl_usd == pytest.approx(200.0)  # 300 − 100, no −9000


def test_equity_curve_excludes_reconciled(memdb: sqlite3.Connection) -> None:
    _seed(memdb, pid="p1", pnl_usd=300.0)
    _seed(memdb, pid="p3", pnl_usd=-500.0, status="reconciled")

    _ts, equity, total = _build_equity_curve(
        memdb, now_s=3000, starting_capital=10_000.0,
    )
    # total realised = +300 gross − fees on the 2 non-reconciled legs (2×$1).
    assert total == pytest.approx(300.0 - 2.0)
    assert equity[-1] == pytest.approx(10_000.0 + 300.0 - 2.0)


def test_dual_equity_curve_excludes_reconciled(memdb: sqlite3.Connection) -> None:
    _seed(memdb, pid="p1", pnl_usd=300.0)
    _seed(memdb, pid="p3", pnl_usd=-500.0, status="reconciled")

    curve = _build_dual_equity_curve(
        memdb, now_s=3000, starting_capital=10_000.0,
    )
    # gross close pnl in both demo/real curves excludes the reconciled −500.
    # The curves recompute fees from notional, so assert the gross delta only:
    # demo equity ≈ start + 300 − demo_fees(>0); the reconciled −500 is gone.
    assert curve.equity_demo[-1] > 10_000.0  # +300 gross dominates the fee drain
    assert curve.equity_demo[-1] < 10_000.0 + 300.0  # fees deducted
    # Without exclusion this would be ~9_800 (300 − 500); proving exclusion:
    assert curve.equity_demo[-1] > 10_000.0 + 100.0


def test_per_stream_excludes_reconciled_and_reconciles_to_daily(
    memdb: sqlite3.Connection,
) -> None:
    _seed(memdb, pid="p1", pnl_usd=300.0, venue="okx")
    _seed(memdb, pid="p2", pnl_usd=-100.0, venue="okx")
    _seed(memdb, pid="p3", pnl_usd=-500.0, venue="okx", status="reconciled")

    streams = _per_stream_summary(memdb, now_s=3000, positions=[])
    okx = next(s for s in streams if s.venue == "okx")

    assert okx.daily_trades == 2  # reconciled excluded from count
    # Reconciliation invariant: Σ per-stream net == global daily net.
    daily_net, _ = _daily_realised_pnl(memdb, now_s=3000)
    total_stream_net = sum(s.net_pnl_usd for s in streams)
    assert total_stream_net == pytest.approx(daily_net)
    # And the okx net excludes the reconciled −500 close pnl.
    assert okx.net_pnl_usd == pytest.approx(300.0 - 100.0 - 4.0)


def test_drift_tile_primary_is_actual_realized(memdb: sqlite3.Connection) -> None:
    """The reconciled drift tile reports ACTUAL realized Σ pnl_usd as primary."""
    import json

    _seed(memdb, pid="p3", pnl_usd=-500.0, status="reconciled")
    _seed(memdb, pid="p4", pnl_usd=-13.0, status="reconciled")
    # the mae_r×risk EST recorded at reconcile time (secondary, kept labeled).
    for pid, est in (("p3", -480.0), ("p4", -20.0)):
        memdb.execute(
            "INSERT INTO risk_events (risk_event_id, strategy_id, event_type, "
            " created_ts, payload_json) VALUES (?, 's1', 'orphan_reconciled', 1500, ?)",
            (f"re_{pid}", json.dumps({"est_drift_usd": est})),
        )

    tel = _rotation_telemetry(memdb, now_s=3000)

    assert tel.reconciled_loss_n == 2
    # PRIMARY = actual realized Σ pnl_usd over reconciled close fills.
    assert tel.reconciled_realized_usd == pytest.approx(-513.0)
    # SECONDARY (est, kept labeled) = Σ est_drift_usd.
    assert tel.reconciled_loss_usd == pytest.approx(-500.0)
