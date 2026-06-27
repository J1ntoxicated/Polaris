"""won-log nit — the ``[close]`` INFO log reports the REAL-fee NET ``won`` verdict.

The close fan-out (cell matrix / Layer-5 learners / meta-label / posterior / G8)
folds the real-fee NET R + a NET ``won`` (FIX 1). The ``[close]`` INFO log,
however, reported ``won = pnl_r > 0.0`` — the fee-FREE GROSS sign — so a hair-
positive scalp the fee turns into a NET LOSS was logged ``won=True`` while every
learner recorded it as a loss. This drives the real close path with a tiny
favourable move whose REAL OKX round-trip fee flips it net-negative and asserts
the logged ``won`` is the net verdict (``False``), matching what the learners fold.

Measurement/display only — fills.pnl_usd (GROSS) + fills.fee_usd (REAL) stay the
single truth (#46); the log change reports the already-computed net sign and
alters no behaviour. DEMO/PAPER only; virtual funds; the adapter is a mock.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Any

import pytest

from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


class _DrainedOKX:
    """OKX adapter mock whose wallet reads ~0 → mark-close path (no live sell)."""

    def __init__(self, *, avail: float = 1.07e-04) -> None:
        self._avail = avail

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        return {
            "data": [{"details": [{"ccy": ccy or "SOL", "availBal": str(self._avail)}]}]
        }

    async def place_market_order(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("drained pool must not submit a sell order")


def _seed_open(conn: sqlite3.Connection, *, position_id: str, base_qty: float,
               entry_price: float, symbol: str = "SOL-USDT") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, mfe_r, mae_r, risk_usd) "
        "VALUES (?, 'okx', ?, ?, 'volume_burst', 'volume_burst', "
        " 'volume_burst', 'long', ?, 'open', ?, 0, 0.01, -0.01, 24.7)",
        (position_id, symbol, f"crypto:{symbol.split('-')[0]}", base_qty, NOW),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        f"VALUES (?, ?, 'volume_burst', 'okx:{symbol}', 'okx', 'long', ?, ?, ?, "
        " 0.05, 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, base_qty, entry_price,
         base_qty * entry_price, position_id, uuid.uuid4().hex),
    )


def _seed_atr_bars(conn: sqlite3.Connection, *, mark: float, half_range: float = 0.3,
                   symbol: str = "SOL-USDT") -> None:
    # A 14-bar window so compute_net_pnl_r's cost-in-R denominator is finite (the
    # real net-R subtraction path, not the degenerate net==gross fallback). The
    # newest bar (ts=NOW) is the fresh mark used for the close. ``half_range`` sets
    # the per-bar high-low spread → the ATR magnitude that the round-trip cost is
    # expressed in R against (a realistic spread keeps a genuine winner net-positive).
    conn.execute(f"DELETE FROM bars WHERE instrument_id='okx:{symbol}'")
    for i in range(14):
        close = mark if i == 0 else mark - 0.5
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
            " bar_interval, ts, open, high, low, close, volume) "
            f"VALUES ('okx:{symbol}', 'crypto:{symbol.split('-')[0]}', 'okx', ?, "
            " '1m', ?, ?, ?, ?, ?, 1000.0)",
            (symbol, NOW - i * 60, close, close + half_range, close - half_range, close),
        )


def _trade(position_id: str, base_qty: float, entry_price: float) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="volume_burst", side="long", entry_price=entry_price,
        notional_usd=base_qty * entry_price, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


def _close_won_field(caplog: pytest.LogCaptureFixture) -> str:
    """Pull the ``won=<x>`` token out of the single ``[close] closed`` INFO line."""
    lines = [r.getMessage() for r in caplog.records if "[close] closed" in r.getMessage()]
    assert len(lines) == 1, f"expected one [close] line, got {lines!r}"
    tokens = {t.split("=", 1)[0]: t.split("=", 1)[1] for t in lines[0].split() if "=" in t}
    return tokens["won"]


@pytest.mark.asyncio
async def test_close_log_won_is_net_loss_when_fee_eats_tiny_gross(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    """entry 150 → mark 150.05 on 8.53 base = +$0.43 GROSS (a win by the gross
    sign), but the REAL OKX round-trip fee (~$1.33) flips it to a ~$0.90 NET LOSS.
    The ``[close]`` log MUST report ``won=False`` — the net verdict the cell /
    learners / posterior fold — not the fee-free gross ``won=True``."""
    entry, mark, qty = 150.0, 150.05, 8.53
    _seed_open(memdb, position_id="pos-tiny", base_qty=qty, entry_price=entry)
    _seed_atr_bars(memdb, mark=mark)
    state = ProdLoopState()
    trade = _trade("pos-tiny", qty, entry)
    state.open_trades = [trade]

    with caplog.at_level(logging.INFO, logger="polaris.scripts._production_close"):
        ok = await _close_trade_with_real_pnl(
            memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
            lookup_regime=_regime_stub, gpt_client=None, phase="P0",
            real_roundtrip=True, okx_adapter=_DrainedOKX(),
        )

    assert ok is True
    # GROSS is positive (the realised price drift) — the log still prints it.
    assert trade.pnl_r > 0.0
    # But the logged ``won`` is the NET sign: the fee made this a loss.
    assert _close_won_field(caplog) == "False"


@pytest.mark.asyncio
async def test_close_log_won_true_for_real_winner(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    """A genuine winner (entry 150 → mark 165 on 8.53 base = +$127.95 gross) stays
    a NET win after the ~$1.40 fee → the ``[close]`` log reports ``won=True``. The
    net-won wiring never mislabels a real winner. A realistic ATR window (per-bar
    ±$5 range) keeps the round-trip cost a small fraction of R."""
    entry, mark, qty = 150.0, 165.0, 8.53
    _seed_open(memdb, position_id="pos-big", base_qty=qty, entry_price=entry)
    _seed_atr_bars(memdb, mark=mark, half_range=5.0)
    state = ProdLoopState()
    trade = _trade("pos-big", qty, entry)
    state.open_trades = [trade]

    with caplog.at_level(logging.INFO, logger="polaris.scripts._production_close"):
        ok = await _close_trade_with_real_pnl(
            memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
            lookup_regime=_regime_stub, gpt_client=None, phase="P0",
            real_roundtrip=True, okx_adapter=_DrainedOKX(),
        )

    assert ok is True
    assert _close_won_field(caplog) == "True"
