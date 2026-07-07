"""Pooled-wallet drain — OKX flow_pressure concurrent same-ccy close fix.

The OKX SPOT demo holds ONE fungible base-ccy balance shared by every concurrent
same-ccy position. The tick engine opens MANY flow_pressure positions on the SAME
base ccy (11 concurrent LTC in a 140s window; XRP 58 positions 100% orphaned).
Each tracks its OWN base_qty slice, but ``real_okx_close_fill`` clamps each close
to the LIVE wallet availBal. The FIRST sibling to close drains the shared pool, so
every later sibling sees availBal~0 < its tracked base_qty and was mis-classified a
CloseOrphan — status='reconciled', pnl_r=NULL, DROPPED from the R ledger
(survivorship bias: ~32% of flow_pressure positions, heavily the late closers).

The fix distinguishes:
* TRUE orphan (CloseOrphan): availBal~0 AND NO other same-ccy position open to
  explain the missing pool → genuine over-count → reconcile, pnl_r NULL.
* SIBLING-DRAIN (SiblingDrainClose): availBal~0 BUT a still-open same-ccy sibling
  holds/sold the pool → the base WAS real (proceeds conserved at the wallet) →
  book a MARK-to-market close (real pnl_r, learner feed) so the position stays in
  the R ledger. NOT a tracking failure, NOT dropped.

Plus an idempotency guard: a position that already has a persisted close fill is
NOT re-reconciled on a later tick (the 0.7% second-order dust duplicate-orphan).

DEMO/PAPER only; virtual funds. flow_not_block — no defensive throttle, no size
dampen, no chain multiplier, no P&L halt. All adapters are mocks; no network call.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.metrics.risk_unit import realised_r
from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._smoke_real_roundtrip import (
    CloseOrphan,
    real_okx_close_fill,
)
from polaris.scripts._smoke_roundtrip_shared import SiblingDrainClose
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Layer 1 — real_okx_close_fill: sibling-drain vs true orphan
# ---------------------------------------------------------------------------


class _PoolOKX:
    """OKX adapter mock; ``avail`` controls the (shared) wallet balance clamp."""

    def __init__(self, *, avail: float | None = None, price: float = 0.12) -> None:
        self._avail = avail
        self._price = price
        self.sell_base_qtys: list[float] = []

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        if self._avail is None:
            return {"data": []}
        return {
            "data": [{"details": [{"ccy": ccy or "LTC", "availBal": str(self._avail)}]}]
        }

    async def place_market_order(self, **kwargs: Any) -> Any:
        from polaris.venues.okx.adapter import OKXOrderResponse

        base_qty = float(kwargs["notional_usd"])
        self.sell_base_qtys.append(base_qty)
        self._last = {
            "ordId": f"sell_{len(self.sell_base_qtys)}", "clOrdId": "cl",
            "instId": kwargs["inst_id"], "side": "sell", "tgtCcy": "base_ccy",
            "accFillSz": f"{base_qty:.10f}", "avgPx": str(self._price),
            "fee": "-0.01", "feeCcy": "USDT", "state": "filled",
            "uTime": str(NOW * 1000),
        }
        return OKXOrderResponse(
            ok=True, venue_order_id=self._last["ordId"], client_order_id="cl",
            code="0", msg="", raw={},
        )

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        return {"data": [self._last]}


@pytest.mark.asyncio
async def test_drained_pool_with_open_sibling_returns_sibling_drain() -> None:
    """availBal~0 BUT a still-open same-ccy sibling holds the pool → the drain is
    EXPECTED (sibling sold the shared base) → SiblingDrainClose, NOT CloseOrphan,
    and nothing is submitted (there is nothing left to sell)."""
    adapter = _PoolOKX(avail=1.8e-05, price=0.12)  # measured drained LTC wallet
    out = await real_okx_close_fill(
        adapter, inst_id="LTC-USDT", base_qty=3.5, strategy_id="flow_pressure",
        mark_price=0.12, poll_delay_sec=0.0, sibling_base=8.0,
    )
    assert isinstance(out, SiblingDrainClose)
    assert not isinstance(out, CloseOrphan)
    assert adapter.sell_base_qtys == []  # nothing to sell — pool already drained


@pytest.mark.asyncio
async def test_drained_pool_no_sibling_is_true_orphan() -> None:
    """availBal~0 with NO open same-ccy sibling → a genuine over-count tracking
    failure → CloseOrphan (reconcile, pnl_r NULL), unchanged behaviour."""
    adapter = _PoolOKX(avail=1.8e-05, price=0.12)
    out = await real_okx_close_fill(
        adapter, inst_id="LTC-USDT", base_qty=3.5, strategy_id="flow_pressure",
        mark_price=0.12, poll_delay_sec=0.0, sibling_base=0.0,
    )
    assert isinstance(out, CloseOrphan)
    assert adapter.sell_base_qtys == []


@pytest.mark.asyncio
async def test_full_availbal_closes_normally_with_siblings() -> None:
    """When the wallet still holds this position's full base (e.g. THIS is the
    first sibling to close) the close sells normally — siblings present does not
    change a healthy close."""
    adapter = _PoolOKX(avail=100.0, price=0.12)  # pool intact
    out = await real_okx_close_fill(
        adapter, inst_id="LTC-USDT", base_qty=3.5, strategy_id="flow_pressure",
        mark_price=0.12, poll_delay_sec=0.0, sibling_base=8.0,
    )
    from polaris.core.data.fill_normalizer import Fill

    assert isinstance(out, Fill)
    assert sum(adapter.sell_base_qtys) == pytest.approx(3.5, rel=1e-9)


# ---------------------------------------------------------------------------
# Layer 2 — caller books a real MARK close on the sibling-drain signal
# ---------------------------------------------------------------------------


def _seed_open(
    conn: sqlite3.Connection, *, position_id: str, base_qty: float,
    symbol: str = "LTC-USDT", group: str = "crypto:LTC", entry_price: float = 0.12,
    risk_usd: float = 5.0,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES (?, 'okx', ?, ?, 'flow_pressure', 'flow_pressure', "
        " 'flow_pressure', 'long', ?, 'open', ?, 0, ?)",
        (position_id, symbol, group, base_qty, NOW, risk_usd),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        f"VALUES (?, ?, 'flow_pressure', 'okx:{symbol}', 'okx', 'long', ?, ?, ?, "
        " 0.05, 1.0, 0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, base_qty, entry_price,
            base_qty * entry_price, position_id, uuid.uuid4().hex,
        ),
    )


def _trade(
    position_id: str, base_qty: float, symbol: str = "LTC-USDT",
    entry_price: float = 0.12,
) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol=symbol,
        strategy_id="flow_pressure", side="long", entry_price=entry_price,
        notional_usd=base_qty * entry_price, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "chop"


@pytest.mark.asyncio
async def test_sibling_drain_books_real_close_not_reconcile(
    memdb: sqlite3.Connection,
) -> None:
    """Two concurrent LTC positions. The FIRST close drains the shared wallet.
    The SECOND (sibling still implies the pool is gone) must be booked as a REAL
    mark-to-market close — status='closed', pnl_r STAMPED (not NULL), close fill
    persisted, learner-fed — NOT reconciled out of the R ledger."""
    _seed_open(memdb, position_id="pos-A", base_qty=3.5)
    _seed_open(memdb, position_id="pos-B", base_qty=8.0)
    state = ProdLoopState()
    trade_a = _trade("pos-A", 3.5)
    trade_b = _trade("pos-B", 8.0)
    # Both still open: closing A while B is open → A's pool drain is explained.
    state.open_trades = [trade_a, trade_b]
    adapter = _PoolOKX(avail=1.8e-05, price=0.12)  # wallet already drained

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade_a, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    assert ok is True
    row = memdb.execute(
        "SELECT status, exit_state, pnl_r FROM positions WHERE position_id='pos-A'"
    ).fetchone()
    assert row[0] == "closed"  # NOT 'reconciled'
    assert row[1] == "closed"
    assert row[2] is not None  # pnl_r STAMPED — stays in the R ledger
    # A real close fill IS persisted (mark close, booked into the ledger).
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills WHERE contribution_id='pos-A' AND is_close=1"
    ).fetchone()
    assert close_rows[0] == 1
    # Learner-fed (a real trade outcome), popped from open_trades.
    assert trade_a in state.closed_trades
    assert trade_a not in state.open_trades
    # NO orphan reconcile audit — this is a real close, not a tracking failure.
    audit = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='orphan_reconciled'"
    ).fetchone()
    assert audit[0] == 0


@pytest.mark.asyncio
async def test_sibling_drain_mark_close_books_true_pnl(
    memdb: sqlite3.Connection,
) -> None:
    """The mark-close P&L reflects the REAL move (entry vs fresh mark), not a
    fabricated zero — a position that fell from 0.12 entry to a 0.10 mark books a
    real LOSS in the R ledger (no survivorship bias toward winners)."""
    _seed_open(memdb, position_id="pos-loss", base_qty=10.0, entry_price=0.12)
    _seed_open(memdb, position_id="pos-sib", base_qty=8.0)
    # Fresh 1m bar close 0.10 < entry 0.12 → loss of (0.10-0.12)*10 = -$0.20.
    # This is the fresh mark the sibling-drain close books against.
    memdb.execute("DELETE FROM bars WHERE instrument_id='okx:LTC-USDT'")
    memdb.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) "
        "VALUES ('okx:LTC-USDT', 'crypto:LTC', 'okx', 'LTC-USDT', '1m', ?, "
        " 0.11, 0.11, 0.10, 0.10, 1000.0)",
        (NOW,),
    )
    state = ProdLoopState()
    trade = _trade("pos-loss", 10.0)
    sib = _trade("pos-sib", 8.0)
    state.open_trades = [trade, sib]
    adapter = _PoolOKX(avail=1.8e-05, price=0.10)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert ok is True
    row = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id='pos-loss'"
    ).fetchone()
    assert row[0] is not None
    assert row[0] == pytest.approx(realised_r(pnl_usd=-0.20, risk_usd=5.0), rel=1e-6)
    assert row[0] < 0.0  # a real loss is recorded, NOT dropped/zeroed


# ---------------------------------------------------------------------------
# Layer 3 — idempotency: an already-closed position is not re-reconciled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_closed_position_not_rechurned(
    memdb: sqlite3.Connection,
) -> None:
    """The 0.7% second-order dust duplicate: a position that ALREADY has a
    persisted close fill must NOT be re-reconciled on a later tick (no churn, no
    second orphan row). With no open sibling, a drained wallet would normally
    orphan — but the prior full close makes it idempotently a no-op handled=True."""
    _seed_open(memdb, position_id="pos-done", base_qty=3.5)
    # Simulate the position already had a real close fill persisted earlier.
    memdb.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'flow_pressure', 'okx:LTC-USDT', 'okx', 'sell', 3.5, 0.12, "
        " 0.42, 0.01, 0.0, 0.0, 1, 'pos-done', ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, uuid.uuid4().hex),
    )
    state = ProdLoopState()
    trade = _trade("pos-done", 3.5)
    state.open_trades = [trade]
    adapter = _PoolOKX(avail=1.8e-05, price=0.12)  # drained, no sibling

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert ok is True  # handled (terminal), stops being retried
    # No SECOND close fill churned, no orphan_reconciled audit row.
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills WHERE contribution_id='pos-done' AND is_close=1"
    ).fetchone()
    assert close_rows[0] == 1  # still exactly the one prior close
    assert trade not in state.open_trades
    row = memdb.execute(
        "SELECT status FROM positions WHERE position_id='pos-done'"
    ).fetchone()
    assert row[0] == "closed"  # idempotently terminal as closed, not reconciled
