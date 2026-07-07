"""FIX_EXIT — OKX pooled-wallet-drain WINNER must be mark-closed, not reconciled.

ROOT CAUSE (autopsy 2026-06-26, DB-proven): volume_burst's biggest winners
(SOL 9.75R, ETH 1.77R, HYPE 6.57R MFE) ended ``status='reconciled'`` with
``pnl_r=NULL`` — their edge silently discarded. The forensic audit
(``risk_events.orphan_reconciled``) shows the leaked positions share the OKX
SPOT demo's ONE fungible base-ccy wallet with the (now-KILLed) tick strategies
micro_reversion / flow_pressure / burst_rider. Those same-ccy siblings drained
the shared pool and CLOSED FIRST, so when volume_burst's SOL position finally
closed, ``availBal~0`` AND ``sibling_base==0`` (the draining siblings were
already gone) → the close fell through to ``CloseOrphan`` → reconcile-with-NULL.

The ``sibling_base > 0`` gate ([[okx_pooled_wallet_close]]) only catches a drain
while a sibling is STILL OPEN; it misses the dominant case where the draining
siblings already closed. A tracked OKX position with a REAL persisted entry fill
WAS real and its base WAS sold into the shared USDT pool (proceeds conserved) —
dropping it is the same survivorship bias the SiblingDrainClose fix removed, just
on the no-open-sibling timeline.

FIX (flow_not_block, exit-capture — not an entry block, not a throttle): when the
OKX close returns ``CloseOrphan`` but the position has a REAL entry fill and a
usable fresh mark, book a MARK-to-market close (real pnl_r, learner-fed) instead
of reconcile-with-NULL — the winner's edge is captured, not discarded. A genuine
over-count with NO real entry fill (or no priceable mark) still reconciles.

DEMO/PAPER only; virtual funds. All adapters are mocks; no network call.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.metrics.risk_unit import realised_r
from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


class _DrainedOKX:
    """OKX adapter mock whose wallet reads ~0 (pool already drained by siblings)."""

    def __init__(self, *, avail: float = 1.07e-04, price: float = 150.0) -> None:
        self._avail = avail
        self._price = price
        self.sell_base_qtys: list[float] = []

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        return {
            "data": [{"details": [{"ccy": ccy or "SOL", "availBal": str(self._avail)}]}]
        }

    async def place_market_order(self, **kwargs: Any) -> Any:  # pragma: no cover
        # Nothing should be submitted on a drained wallet — pool already sold.
        self.sell_base_qtys.append(float(kwargs["notional_usd"]))
        raise AssertionError("drained pool must not submit a sell order")


def _seed_open(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    base_qty: float,
    entry_price: float,
    symbol: str = "SOL-USDT",
    with_entry_fill: bool = True,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, mfe_r, mae_r, risk_usd) "
        "VALUES (?, 'okx', ?, ?, 'volume_burst', 'volume_burst', "
        " 'volume_burst', 'long', ?, 'open', ?, 0, 9.75, -0.64, 24.7)",
        (position_id, symbol, f"crypto:{symbol.split('-')[0]}", base_qty, NOW),
    )
    if with_entry_fill:
        conn.execute(
            "INSERT INTO fills "
            "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
            " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
            " contribution_id, order_id, state) "
            f"VALUES (?, ?, 'volume_burst', 'okx:{symbol}', 'okx', 'long', ?, ?, ?, "
            " 0.05, 1.0, 0.0, 0, ?, ?, 'filled')",
            (
                uuid.uuid4().hex, NOW * 1000, base_qty, entry_price,
                base_qty * entry_price, position_id, uuid.uuid4().hex,
            ),
        )


def _trade(
    position_id: str, base_qty: float, entry_price: float,
    symbol: str = "SOL-USDT",
) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol=symbol,
        strategy_id="volume_burst", side="long", entry_price=entry_price,
        notional_usd=base_qty * entry_price, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


def _seed_fresh_bar(conn: sqlite3.Connection, *, close: float,
                    symbol: str = "SOL-USDT") -> None:
    conn.execute(f"DELETE FROM bars WHERE instrument_id='okx:{symbol}'")
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) "
        f"VALUES ('okx:{symbol}', 'crypto:{symbol.split('-')[0]}', 'okx', ?, "
        " '1m', ?, ?, ?, ?, ?, 1000.0)",
        (symbol, NOW, close, close, close, close),
    )


@pytest.mark.asyncio
async def test_drained_winner_with_entry_fill_books_mark_close_not_reconcile(
    memdb: sqlite3.Connection,
) -> None:
    """The SOL 9.75R winner: drained wallet, NO open sibling, but a REAL entry
    fill + fresh mark → MARK close (status='closed', pnl_r STAMPED, close fill
    persisted, learner-fed) — NOT reconciled-with-NULL. The edge is captured."""
    entry, mark = 150.0, 165.0  # +10% favourable move, a real winner
    _seed_open(memdb, position_id="pos-win", base_qty=8.53, entry_price=entry)
    _seed_fresh_bar(memdb, close=mark)
    state = ProdLoopState()
    trade = _trade("pos-win", 8.53, entry)
    state.open_trades = [trade]  # NO sibling — the drainers already closed
    adapter = _DrainedOKX(price=mark)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    assert ok is True
    row = memdb.execute(
        "SELECT status, exit_state, pnl_r FROM positions WHERE position_id='pos-win'"
    ).fetchone()
    assert row[0] == "closed"  # NOT 'reconciled'
    assert row[1] != "reconciled"
    assert row[2] is not None  # pnl_r STAMPED — the winner stays in the R ledger
    assert row[2] > 0.0  # a real WIN is recorded, not silently zeroed
    # A real mark close fill IS persisted (the captured exit).
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills WHERE contribution_id='pos-win' AND is_close=1"
    ).fetchone()
    assert close_rows[0] == 1
    # Learner-fed (real outcome), popped from open_trades.
    assert trade in state.closed_trades
    assert trade not in state.open_trades
    # NO survivorship-bias orphan reconcile — this is a real close.
    audit = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='orphan_reconciled'"
    ).fetchone()
    assert audit[0] == 0


@pytest.mark.asyncio
async def test_drained_mark_close_books_true_winner_pnl(
    memdb: sqlite3.Connection,
) -> None:
    """The booked mark-close PnL reflects the REAL move (entry vs fresh mark), so
    the captured win is the actual edge — entry 150 → mark 165 on 8.53 base =
    +$127.95, the realized R the SOL winner should have kept (no longer NULL)."""
    entry, mark, qty = 150.0, 165.0, 8.53
    _seed_open(memdb, position_id="pos-win2", base_qty=qty, entry_price=entry)
    _seed_fresh_bar(memdb, close=mark)
    state = ProdLoopState()
    trade = _trade("pos-win2", qty, entry)
    state.open_trades = [trade]
    adapter = _DrainedOKX(price=mark)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert ok is True
    row = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id='pos-win2'"
    ).fetchone()
    assert row[0] is not None
    expected_pnl_usd = (mark - entry) * qty  # +127.95
    assert row[0] == pytest.approx(
        realised_r(pnl_usd=expected_pnl_usd, risk_usd=24.7), rel=1e-6,
    )
    assert row[0] > 0.0  # captured a real WIN, not discarded


@pytest.mark.asyncio
async def test_true_overcount_no_entry_fill_still_reconciles(
    memdb: sqlite3.Connection,
) -> None:
    """A GENUINE over-count (no real entry fill — the close-chunk artifact the
    CloseOrphan path was built for) still reconciles with NULL pnl_r: there is no
    real base to book. The mark-close path applies ONLY to real tracked positions,
    so the over-count recovery is preserved (no fabricated win)."""
    _seed_open(memdb, position_id="pos-ghost", base_qty=8.53, entry_price=150.0,
               with_entry_fill=False)
    _seed_fresh_bar(memdb, close=165.0)
    state = ProdLoopState()
    trade = _trade("pos-ghost", 8.53, 150.0)
    state.open_trades = [trade]
    adapter = _DrainedOKX(price=165.0)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert ok is True
    row = memdb.execute(
        "SELECT status, pnl_r FROM positions WHERE position_id='pos-ghost'"
    ).fetchone()
    assert row[0] == "reconciled"  # genuine over-count → reconcile preserved
    assert row[1] is None  # NO fabricated PnL
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills WHERE contribution_id='pos-ghost' AND is_close=1"
    ).fetchone()
    assert close_rows[0] == 0  # no fabricated close fill
    audit = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='orphan_reconciled'"
    ).fetchone()
    assert audit[0] == 1  # over-count audit still written
