"""Meta-labeling (#10) — triple-barrier label collection tests (TDD).

Spec / scope (Jin mandate 2026-05-29):
- DEMO/PAPER virtual funds. AGGRESSIVE bias preserved — this is **label
  collection only**. No act/skip secondary model, no sizing cut, no defensive
  throttle. Cold-start sample-count is insufficient, so labels are stored for a
  future 2nd-stage model and never gate anything now.
- Triple-barrier label = which barrier the realized outcome touched: TP (upper
  profit barrier), SL (lower loss barrier), or TIMEOUT (vertical barrier — the
  trade was closed by a gate/horizon before hitting either horizontal barrier).
- Each label also carries the realized profit sign and R multiple.

Label generation must be deterministic + pure (no I/O) so the property is
unit-testable; persistence is a thin separate helper.
"""

from __future__ import annotations

import sqlite3

from polaris.core.learners.meta_label import (
    TripleBarrierLabel,
    compute_triple_barrier_label,
    persist_meta_label,
)
from polaris.storage.schema import init_db

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# compute_triple_barrier_label — barrier classification
# ---------------------------------------------------------------------------


def test_tp_barrier_when_profit_reaches_target() -> None:
    label = compute_triple_barrier_label(
        pnl_r=2.4, won=True, holding_bars=8, expected_holding_bars=20
    )
    assert label.barrier == "tp"
    assert label.pnl_sign == 1
    assert label.r == 2.4
    assert label.hit_horizontal is True


def test_sl_barrier_when_loss_reaches_target() -> None:
    label = compute_triple_barrier_label(
        pnl_r=-1.5, won=False, holding_bars=5, expected_holding_bars=20
    )
    assert label.barrier == "sl"
    assert label.pnl_sign == -1
    assert label.r == -1.5
    assert label.hit_horizontal is True


def test_timeout_barrier_when_neither_target_reached() -> None:
    # Small positive R below the TP target and above the SL target -> the
    # trade was closed by a gate/horizon, not by a horizontal barrier.
    label = compute_triple_barrier_label(
        pnl_r=0.3, won=True, holding_bars=20, expected_holding_bars=20
    )
    assert label.barrier == "timeout"
    assert label.pnl_sign == 1
    assert label.r == 0.3
    assert label.hit_horizontal is False


def test_timeout_with_flat_pnl_has_zero_sign() -> None:
    label = compute_triple_barrier_label(
        pnl_r=0.0, won=False, holding_bars=20, expected_holding_bars=20
    )
    assert label.barrier == "timeout"
    assert label.pnl_sign == 0
    assert label.r == 0.0


def test_custom_targets_change_classification() -> None:
    # With a tighter TP target the same +0.7R now counts as a TP touch.
    label = compute_triple_barrier_label(
        pnl_r=0.7, won=True, holding_bars=4, expected_holding_bars=20,
        tp_r=0.5, sl_r=1.0,
    )
    assert label.barrier == "tp"


def test_sl_target_uses_magnitude_not_sign() -> None:
    # sl_r is supplied as a positive magnitude; a -1.0R close at the default
    # target must classify as SL.
    label = compute_triple_barrier_label(
        pnl_r=-1.0, won=False, holding_bars=3, expected_holding_bars=20,
        tp_r=1.0, sl_r=1.0,
    )
    assert label.barrier == "sl"


def test_horizon_fraction_recorded() -> None:
    label = compute_triple_barrier_label(
        pnl_r=0.2, won=True, holding_bars=10, expected_holding_bars=20
    )
    assert abs(label.horizon_fraction - 0.5) < 1e-9


def test_horizon_fraction_safe_when_expected_zero() -> None:
    label = compute_triple_barrier_label(
        pnl_r=0.2, won=True, holding_bars=10, expected_holding_bars=0
    )
    # No division-by-zero; fraction falls back to 0.0.
    assert label.horizon_fraction == 0.0


# ---------------------------------------------------------------------------
# persist_meta_label — storage round-trip
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    return init_db(":memory:")


def test_persist_meta_label_round_trip() -> None:
    conn = _conn()
    label = compute_triple_barrier_label(
        pnl_r=2.0, won=True, holding_bars=6, expected_holding_bars=20
    )
    label_id = persist_meta_label(
        conn,
        trade_id="trd-1",
        strategy_id="tsmom",
        venue="okx",
        ticker="BTC-USDT",
        regime="trend_up",
        session="asia",
        label=label,
        now_ts=NOW,
    )
    row = conn.execute(
        "SELECT trade_id, strategy_id, venue, ticker, regime, session, "
        "barrier, pnl_sign, r, hit_horizontal, holding_bars, "
        "expected_holding_bars, horizon_fraction, created_ts "
        "FROM meta_labels WHERE label_id = ?",
        (label_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "trd-1"
    assert row[1] == "tsmom"
    assert row[2] == "okx"
    assert row[3] == "BTC-USDT"
    assert row[4] == "trend_up"
    assert row[5] == "asia"
    assert row[6] == "tp"
    assert row[7] == 1
    assert abs(row[8] - 2.0) < 1e-9
    assert row[9] == 1  # hit_horizontal stored as int
    assert row[10] == 6
    assert row[11] == 20
    assert abs(row[12] - 0.3) < 1e-9
    assert row[13] == NOW


def test_persist_is_idempotent_on_trade_id() -> None:
    conn = _conn()
    label = compute_triple_barrier_label(
        pnl_r=-1.2, won=False, holding_bars=4, expected_holding_bars=20
    )
    persist_meta_label(
        conn, trade_id="trd-2", strategy_id="rsi_bb", venue="okx",
        ticker="ETH-USDT", regime="chop", session="asia",
        label=label, now_ts=NOW,
    )
    # Re-persisting the same trade_id overwrites rather than duplicating.
    persist_meta_label(
        conn, trade_id="trd-2", strategy_id="rsi_bb", venue="okx",
        ticker="ETH-USDT", regime="chop", session="asia",
        label=label, now_ts=NOW + 100,
    )
    n = conn.execute(
        "SELECT COUNT(*) FROM meta_labels WHERE trade_id = ?", ("trd-2",)
    ).fetchone()[0]
    assert n == 1


def test_close_path_helper_records_label_and_counts() -> None:
    """``_safe_record_meta_label`` writes a row + bumps the loop counter."""
    from polaris.scripts._production_close_effects import _safe_record_meta_label
    from polaris.scripts._production_state import ProdLoopState
    from polaris.scripts._smoke_fills import SimulatedTrade

    conn = _conn()
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="trd-close-1", venue="okx", symbol="BTC-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=1000.0, open_ts=NOW - 600,  # 10 minutes -> 10 bars
        position_id="pos-1",
    )
    _safe_record_meta_label(
        conn, trade=trade, regime="trend_up", pnl_r=2.0, won=True,
        now_ts=NOW, state=state,
    )
    assert state.meta_labels == 1
    row = conn.execute(
        "SELECT barrier, r, holding_bars, expected_holding_bars "
        "FROM meta_labels WHERE trade_id = ?",
        ("trd-close-1",),
    ).fetchone()
    assert row is not None
    assert row[0] == "tp"
    assert abs(row[1] - 2.0) < 1e-9
    assert row[2] == 10
    assert row[3] == 20


def test_close_path_helper_fail_open_on_bad_conn() -> None:
    """A label failure must never raise out of the close fan-out."""
    from polaris.scripts._production_close_effects import _safe_record_meta_label
    from polaris.scripts._production_state import ProdLoopState
    from polaris.scripts._smoke_fills import SimulatedTrade

    conn = _conn()
    conn.close()  # force an OperationalError inside persist
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="trd-bad", venue="okx", symbol="ETH-USDT",
        strategy_id="rsi_bb", side="short", entry_price=10.0,
        notional_usd=500.0, open_ts=NOW - 120,
    )
    # Must not raise; counter stays 0.
    _safe_record_meta_label(
        conn, trade=trade, regime="chop", pnl_r=-1.0, won=False,
        now_ts=NOW, state=state,
    )
    assert state.meta_labels == 0


def test_label_is_frozen_dataclass() -> None:
    label = TripleBarrierLabel(
        barrier="tp", pnl_sign=1, r=1.5, hit_horizontal=True,
        holding_bars=5, expected_holding_bars=20, horizon_fraction=0.25,
    )
    try:
        label.r = 2.0  # type: ignore[misc]
    except AttributeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("TripleBarrierLabel must be frozen")
