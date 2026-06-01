"""FIX B — a PARTIAL real close must NOT mark the position fully closed.

LIVE P0 round-2 defect: ``_close_trade_with_real_pnl`` treated ANY non-None
real close fill as a complete close (persist close fill, UPDATE status='closed',
pop from open_trades) WITHOUT comparing the filled base to the tracked base_qty.
A mid-sequence child reject (or a within-child partial) returns only the FILLED
portion → the unsold remainder becomes untracked LIVE venue exposure.

FIX: compare filled base vs trade.base_qty.
* filled >= base_qty × (1 - EPS) (near-full) → full close + pop (behavior-0).
* filled < that (genuine partial) → keep status='open', decrement
  positions.qty AND trade.base_qty by the filled amount, persist the partial
  close fill, do NOT pop — the exit engine closes the remainder next tick.

Also pins FIX A: the close sizes off the FRESH 1m bar mark (not entry).

DEMO/PAPER only; virtual funds. This is precise-exit loss-defense bookkeeping
(flow_not_block) — no defensive throttle, no size dampen, no chain multiplier.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


def _seed(
    conn: sqlite3.Connection, *, position_id: str, base_qty: float,
    entry_price: float = 100.0, bar_close: float = 130.0,
) -> None:
    """Seed an OPEN position + its entry fill + a fresh 1m bar (mark > entry)."""
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'okx', 'SOL-USDT', 'crypto:SOL', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', ?, 'open', ?, 0)",
        (position_id, base_qty, NOW),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'tsmom', 'okx:SOL-USDT', 'okx', 'long', ?, ?, ?, 0.05, "
        " 1.0, 0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, base_qty, entry_price,
            base_qty * entry_price, position_id, uuid.uuid4().hex,
        ),
    )
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES ('okx:SOL-USDT', 'crypto:SOL', 'okx', 'SOL-USDT', '1m', ?, "
            " ?, ?, ?, ?, 100.0, 13000.0, 1, ?, ?, ?, 1.0, 'rest')",
            (ts, bar_close, bar_close + 1.0, bar_close - 1.0, bar_close,
             bar_close, bar_close, bar_close),
        )


def _trade(position_id: str, base_qty: float) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=base_qty * 100.0, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


class _FixedFillOKX:
    """OKX adapter mock that closes EXACTLY ``filled_base`` regardless of the
    requested qty (simulates a within-child partial / mid-sequence reject). It
    also records the ``mark_price``-derived child requests so FIX A can be
    asserted. No real network call ever happens.
    """

    def __init__(self, *, filled_base: float, price: float = 130.0) -> None:
        self._filled = filled_base
        self._price = price
        self.requested: list[float] = []
        self._used = False

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        return {"data": []}  # unknown → clamp skipped

    async def place_market_order(self, **kwargs: Any) -> Any:
        from polaris.venues.okx.adapter import OKXOrderResponse

        self.requested.append(float(kwargs["notional_usd"]))
        ord_id = f"sell_{len(self.requested)}"
        return OKXOrderResponse(
            ok=True, venue_order_id=ord_id, client_order_id="cl",
            code="0", msg="", raw={},
        )

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        # Only the FIRST child reports a (reduced) fill; later children Non-fill
        # → the close aggregates a partial that is < the tracked base_qty.
        if self._used:
            return {"data": []}
        self._used = True
        return {
            "data": [{
                "ordId": ord_id, "clOrdId": "cl", "instId": inst_id,
                "side": "sell", "tgtCcy": "base_ccy",
                "accFillSz": f"{self._filled:.10f}", "avgPx": str(self._price),
                "fee": "-0.02", "feeCcy": "USDT", "state": "filled",
                "uTime": str(NOW * 1000),
            }]
        }


@pytest.mark.asyncio
async def test_partial_close_keeps_position_open_reduced_qty(
    memdb: sqlite3.Connection,
) -> None:
    """A close that fills ~60% leaves the position OPEN with base_qty reduced by
    the filled amount, persists a partial close fill, status stays 'open', NOT
    popped — the remainder closes next tick (no untracked exposure)."""
    base_qty = 100.0
    _seed(memdb, position_id="pos-partial", base_qty=base_qty)
    state = ProdLoopState()
    trade = _trade("pos-partial", base_qty)
    state.open_trades = [trade]
    # Venue fills only 60 of 100 base.
    adapter = _FixedFillOKX(filled_base=60.0)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    assert ok is True
    # Position kept OPEN, NOT popped.
    assert len(state.open_trades) == 1
    assert state.open_trades[0] is trade
    assert len(state.closed_trades) == 0
    # In-memory + durable qty reduced by the filled amount (100 - 60 = 40).
    assert trade.base_qty == pytest.approx(40.0, rel=1e-9)
    assert trade.closed is False
    row = memdb.execute(
        "SELECT status, qty FROM positions WHERE position_id = 'pos-partial'"
    ).fetchone()
    assert row[0] == "open"
    assert row[1] == pytest.approx(40.0, rel=1e-9)
    # A partial close fill was persisted (is_close=1) for the SOLD 60 base.
    close_rows = memdb.execute(
        "SELECT base_qty FROM fills WHERE contribution_id = 'pos-partial' "
        "AND is_close = 1"
    ).fetchall()
    assert len(close_rows) == 1
    assert close_rows[0][0] == pytest.approx(60.0, rel=1e-9)


@pytest.mark.asyncio
async def test_near_full_close_closes_and_pops_behavior_zero(
    memdb: sqlite3.Connection,
) -> None:
    """A ~full fill (>= 99.5% of base_qty) closes + pops as before (behavior-0
    for the normal full-fill path): status='closed', trade popped + recorded."""
    base_qty = 100.0
    _seed(memdb, position_id="pos-full", base_qty=base_qty)
    state = ProdLoopState()
    trade = _trade("pos-full", base_qty)
    state.open_trades = [trade]
    # Venue fills 99.8 of 100 (>= 99.5% → near-full → full close).
    adapter = _FixedFillOKX(filled_base=99.8)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    assert ok is True
    assert len(state.open_trades) == 0  # popped
    assert len(state.closed_trades) == 1
    assert trade.closed is True
    status = memdb.execute(
        "SELECT status FROM positions WHERE position_id = 'pos-full'"
    ).fetchone()[0]
    assert status == "closed"


@pytest.mark.asyncio
async def test_close_sizes_off_fresh_bar_mark_not_entry(
    memdb: sqlite3.Connection,
) -> None:
    """FIX A: the close splits off the FRESH 1m bar mark (130), not the stale
    entry (100). A 60-base position (60×130 = 7800 USDT @ fresh mark) therefore
    splits into multiple children; sizing off entry (60×100 = 6000) would split
    differently. We assert the children were sized at the FRESH mark."""
    base_qty = 60.0
    # entry 100, fresh bar close 130 → mark used for the split must be 130.
    _seed(memdb, position_id="pos-mark", base_qty=base_qty,
          entry_price=100.0, bar_close=130.0)
    state = ProdLoopState()
    trade = _trade("pos-mark", base_qty)
    state.open_trades = [trade]
    # filled_base == base_qty → full close (we only care about the requests).
    adapter = _FixedFillOKX(filled_base=base_qty, price=130.0)

    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    # First child request (base qty) × fresh mark 130 must be <= buffered cap
    # 900 USDT → child base <= ~6.923. Sized off ENTRY 100 it would be <= 9.0.
    assert adapter.requested, "no child submitted"
    first_child_quote_at_mark = adapter.requested[0] * 130.0
    assert first_child_quote_at_mark <= 900.0 + 1e-6
    # Proof it used 130 not 100: the first child base is 900/130 ≈ 6.923, which
    # × 100 (entry) = 692 != 900 — i.e. the split denominator was the fresh mark.
    assert adapter.requested[0] == pytest.approx(900.0 / 130.0, rel=1e-6)
