"""FIX 1 — cell matrix + Layer-5 learners learn the REAL-fee NET R, not gross.

Before this fix only the NIG posterior (``_safe_update_posterior``) subtracted the
real round-trip fee; the Layer-4 cell matrix + Layer-5 learner network + the
``won`` verdict all folded the GROSS price-drift ``pnl_r`` (fee-free). That made a
fee≈gross scalp look like a winner to the cell/learner stats while the posterior
(correctly) saw it as a loss. ``compute_net_pnl_r`` lifts the SAME cost-adjusted
net the posterior already used into one place so the close path computes it ONCE
and feeds the identical net R to cell / learners / meta-label / posterior / won.

Measurement/learning only — fills.pnl_usd (GROSS) + fills.fee_usd (REAL) stay the
single truth (#46); net is an in-memory derivation, no new truth column. Leg-once
(entry is_close=0 + exit is_close=1) → no double-count. Never read by sizing.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.scripts._production_close_effects import compute_net_pnl_r
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.storage.schema import init_db


def _trade(*, venue: str = "okx", symbol: str = "BTC-USDT") -> SimulatedTrade:
    return SimulatedTrade(
        signal_id="sig1",
        venue=venue,
        symbol=symbol,
        strategy_id="tsmom",
        side="long",
        entry_price=60_000.0,
        notional_usd=600.0,
        open_ts=1_780_000_000,
        position_id="pos1",
        base_qty=0.01,
    )


def _persist_legs(
    conn: sqlite3.Connection, *, venue: str, symbol: str,
    entry_fee: float, exit_fee: float,
) -> None:
    inst = f"{venue}:{symbol}"
    entry = Fill(
        venue=venue, instrument_id=inst, strategy_id="tsmom", side="buy",
        size_usd=600.0, fill_price=60_000.0, fee_usd=entry_fee, slippage_bps=0.0,
        ts_ms=1_780_000_000_000, order_id="e1", base_qty=0.01, quote_qty=600.0,
    )
    exit_fill = Fill(
        venue=venue, instrument_id=inst, strategy_id="tsmom", side="sell",
        size_usd=600.0, fill_price=60_060.0, fee_usd=exit_fee, slippage_bps=0.0,
        ts_ms=1_780_000_060_000, order_id="x1", base_qty=0.01, quote_qty=600.6,
    )
    persist_fill(conn, entry, is_close=False, contribution_id="pos1")
    persist_fill(conn, exit_fill, is_close=True, pnl_usd=0.6, contribution_id="pos1")


def _seed_bars(conn: sqlite3.Connection, *, venue: str, symbol: str) -> None:
    # A non-degenerate ATR window so the cost-in-R denominator is finite (not the
    # None sentinel) — exercises the real net-R subtraction path.
    inst = f"{venue}:{symbol}"
    base_ts = 1_780_000_000
    for i in range(14):
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
            "bar_interval, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, ?)",
            (inst, symbol, venue, symbol, base_ts - i * 60,
             60_000.0, 60_300.0, 59_700.0, 60_000.0, 1.0),
        )


def test_compute_net_subtracts_real_fee() -> None:
    """A gross-positive OKX trade has its REAL round-trip fee subtracted: the net
    R/USD is strictly below gross (the posterior path already did this; now the
    SAME value is available to cell/learners)."""
    conn = init_db(":memory:")
    _persist_legs(conn, venue="okx", symbol="BTC-USDT", entry_fee=0.48, exit_fee=0.6)
    _seed_bars(conn, venue="okx", symbol="BTC-USDT")
    pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        conn, trade=_trade(), gross_pnl_r=0.10, gross_pnl_usd=0.6,
    )
    # entry maker fee 0.48 + exit taker fee 0.6 = 1.08 round-trip (+ tiny slip).
    assert pnl_usd_net < 0.6
    assert pnl_usd_net == pytest.approx(0.6 - 1.08, abs=0.2)
    assert pnl_r_net < 0.10


def test_compute_net_won_flips_when_fee_eats_gross() -> None:
    """A scalp whose gross PnL is a hair positive becomes a NET LOSS once the real
    round-trip fee is charged — the close path's ``won = pnl_r_net > 0`` must then
    be False (the cell/learner verdict matches the posterior, not the fee-free
    gross sign)."""
    conn = init_db(":memory:")
    _persist_legs(conn, venue="okx", symbol="BTC-USDT", entry_fee=0.48, exit_fee=0.6)
    _seed_bars(conn, venue="okx", symbol="BTC-USDT")
    _pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        conn, trade=_trade(), gross_pnl_r=0.001, gross_pnl_usd=0.6,
    )
    # gross_pnl_r was +0.001 (a win by the gross sign) but the fee in R pushes it
    # negative → the NET verdict is a loss.
    assert pnl_r_net < 0.0
    assert (pnl_r_net > 0.0) is False


def test_compute_net_degenerate_atr_falls_back_to_gross() -> None:
    """No bars + a degenerate entry price (<=0) → the cost-in-R denominator is the
    None sentinel, so pnl_r_net falls back to gross (never an exploded net-R). The
    dollar net still subtracts the fee."""
    conn = init_db(":memory:")
    inst = "okx:BTC-USDT"
    # Entry fill with fill_price 0 → _read_cost_inputs returns atr_usd=None.
    entry = Fill(
        venue="okx", instrument_id=inst, strategy_id="tsmom", side="buy",
        size_usd=600.0, fill_price=0.0, fee_usd=0.48, slippage_bps=0.0,
        ts_ms=1_780_000_000_000, order_id="e1", base_qty=0.01, quote_qty=600.0,
    )
    exit_fill = Fill(
        venue="okx", instrument_id=inst, strategy_id="tsmom", side="sell",
        size_usd=600.0, fill_price=60_060.0, fee_usd=0.6, slippage_bps=0.0,
        ts_ms=1_780_000_060_000, order_id="x1", base_qty=0.01, quote_qty=600.6,
    )
    persist_fill(conn, entry, is_close=False, contribution_id="pos1")
    persist_fill(conn, exit_fill, is_close=True, pnl_usd=0.6, contribution_id="pos1")
    _pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        conn, trade=_trade(), gross_pnl_r=-5.0, gross_pnl_usd=0.6,
    )
    assert pnl_r_net == pytest.approx(-5.0)  # net == gross (cost-in-R skipped)


def test_compute_net_capital_uses_proxy_bps() -> None:
    """Capital demo reports fee=0 in fills, but the net uses the 3 bps round-trip
    proxy (cost_adjusted_pnl_r const path) so a Capital close is fee-charged too."""
    conn = init_db(":memory:")
    _persist_legs(
        conn, venue="capital", symbol="GOLD", entry_fee=0.0, exit_fee=0.0,
    )
    _seed_bars(conn, venue="capital", symbol="GOLD")
    pnl_usd_net, _pnl_r_net = compute_net_pnl_r(
        conn, trade=_trade(venue="capital", symbol="GOLD"),
        gross_pnl_r=0.10, gross_pnl_usd=5.0,
    )
    # 2 × 3 bps × 600 size = 0.36 round-trip proxy.
    assert pnl_usd_net == pytest.approx(5.0 - 0.36, rel=1e-6)


def test_compute_net_fail_open_returns_gross() -> None:
    """A broken connection (no fills/bars tables) must NEVER abort the close —
    compute_net_pnl_r fails open to (gross, gross) so the fan-out still folds a
    value (the close already committed; learning is best-effort)."""
    conn = sqlite3.connect(":memory:")  # no schema → reads raise
    pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        conn, trade=_trade(), gross_pnl_r=0.25, gross_pnl_usd=3.0,
    )
    assert pnl_usd_net == pytest.approx(3.0)
    assert pnl_r_net == pytest.approx(0.25)
