"""Alpaca venue close — real_alpaca_close_fill + _real_close_fill routing.

ROOT CAUSE this pins: Alpaca had NO venue close path. An Alpaca position fell
through to the Capital branch of ``_real_close_fill`` (no deal_id → CloseOrphan)
→ reconciled in our DB but the shares were NEVER sold. This wires a real Alpaca
SELL.

* ``real_alpaca_close_fill`` SELLS the position's shares via
  ``adapter.place_market_order(side='sell', ...)`` → poll ``fetch_order`` →
  normalize to a ``Fill``.
* MARKET-CLOSED → return ``None`` (TRANSIENT — retry at open; do NOT fabricate a
  fill / falsely reconcile).
* OVER-COUNT (``fetch_positions`` shows fewer shares than ``base_qty``) →
  ``CloseOrphan(available=<actual>)`` so the caller reconciles (real state-drift).
* ``_real_close_fill`` routes ``venue='alpaca'`` to it BEFORE the Capital
  fallthrough; OKX + Capital branches stay byte-identical (golden).

DEMO/PAPER only; virtual funds. AGGRESSIVE bias intact — the close is the
position's own exit leg, never a defensive throttle / size dampen / P&L halt.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.scripts._production_close import _real_close_fill
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._smoke_real_roundtrip import (
    CloseOrphan,
    real_alpaca_close_fill,
)

NOW_MS = 1_780_000_000_000


# ---------------------------------------------------------------------------
# Alpaca adapter mock
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, is_open: bool) -> None:
        self.is_open = is_open


class _AlpacaMock:
    """Alpaca adapter mock for the close leg.

    ``is_open`` drives the market-closed branch; ``positions_qty`` (when set)
    drives the over-count branch; ``fill_qty`` is the share count the SELL
    order reports filled.
    """

    def __init__(
        self,
        *,
        is_open: bool = True,
        positions_qty: float | None = None,
        fill_qty: float = 2.0,
        fill_price: float = 101.0,
    ) -> None:
        self._is_open = is_open
        self._positions_qty = positions_qty
        self._fill_qty = fill_qty
        self._fill_price = fill_price
        self.sell_orders: list[dict[str, Any]] = []

    async def fetch_clock(self) -> _Clock:
        return _Clock(self._is_open)

    async def fetch_positions(self) -> list[dict[str, Any]]:
        # Default (None) → wallet qty UNKNOWN (best-effort read) → both clamps
        # skip → the close sells the tracked base_qty (prior behaviour).
        if self._positions_qty is None:
            return [{"symbol": "AAPL", "qty": ""}]
        return [{"symbol": "AAPL", "qty": str(self._positions_qty)}]

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        notional_usd: float = 0.0,
        qty: float | None = None,
        client_order_id: str,
        **_: Any,
    ) -> Any:
        self.sell_orders.append(
            {"symbol": symbol, "side": side, "qty": qty, "cl": client_order_id}
        )

        class _Resp:
            ok = True
            venue_order_id = f"alp_{uuid.uuid4().hex[:8]}"
            client_order_id = "x"
            code = "accepted"
            msg = ""
            raw: dict[str, Any] = {}

        return _Resp()

    async def fetch_order(
        self, *, order_id: str | None = None, client_order_id: str | None = None
    ) -> dict[str, Any]:
        return {
            "id": order_id or "alp_x",
            "symbol": "AAPL",
            "side": "sell",
            "status": "filled",
            "filled_qty": str(self._fill_qty),
            "filled_avg_price": str(self._fill_price),
            "filled_at": "2026-06-02T19:00:00Z",
            "client_order_id": client_order_id or "x",
        }


def _alpaca_trade(base_qty: float = 2.0) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="alpaca", symbol="AAPL",
        strategy_id="s", side="long", entry_price=100.0, notional_usd=200.0,
        open_ts=0, position_id="p_alp", base_qty=base_qty,
    )


# ---------------------------------------------------------------------------
# real_alpaca_close_fill — sell / market-closed / over-count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alpaca_close_sells_shares_returns_fill() -> None:
    """Market open + wallet holds exactly the qty → SELL → normalized close Fill."""
    adapter = _AlpacaMock(
        is_open=True, positions_qty=2.0, fill_qty=2.0, fill_price=101.0,
    )
    fill = await real_alpaca_close_fill(
        adapter, symbol="AAPL", base_qty=2.0, strategy_id="s",
    )
    assert not isinstance(fill, CloseOrphan)
    assert fill is not None
    assert fill.venue == "alpaca"
    assert fill.side == "sell"
    assert fill.base_qty == pytest.approx(2.0)
    assert fill.fill_price == pytest.approx(101.0)
    # A real SELL order was placed for the tracked share qty.
    assert len(adapter.sell_orders) == 1
    assert adapter.sell_orders[0]["side"] == "sell"
    assert adapter.sell_orders[0]["qty"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_alpaca_close_market_closed_returns_none_transient() -> None:
    """US market closed → None (TRANSIENT). No order placed, no fabricated fill,
    no false reconcile — the flatten retries at the next open."""
    adapter = _AlpacaMock(is_open=False)
    out = await real_alpaca_close_fill(
        adapter, symbol="AAPL", base_qty=2.0, strategy_id="s",
    )
    assert out is None
    assert adapter.sell_orders == []  # nothing submitted while closed


@pytest.mark.asyncio
async def test_alpaca_close_over_count_returns_orphan() -> None:
    """Wallet holds FEWER shares than tracked base_qty (state drift) →
    CloseOrphan(available=<actual>); the caller reconciles. No oversell."""
    adapter = _AlpacaMock(is_open=True, positions_qty=0.0)
    out = await real_alpaca_close_fill(
        adapter, symbol="AAPL", base_qty=2.0, strategy_id="s",
    )
    assert isinstance(out, CloseOrphan)
    assert out.available == pytest.approx(0.0)
    assert adapter.sell_orders == []  # never submit a sell we can't cover


@pytest.mark.asyncio
async def test_alpaca_close_partial_wallet_clamps_to_available() -> None:
    """Wallet holds SOME (less than base_qty but > 0) — still drift → orphan
    (mirrors the OKX over-count path: caller reconciles rather than oversell)."""
    adapter = _AlpacaMock(is_open=True, positions_qty=1.0)
    out = await real_alpaca_close_fill(
        adapter, symbol="AAPL", base_qty=2.0, strategy_id="s",
    )
    assert isinstance(out, CloseOrphan)
    assert out.available == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_alpaca_close_over_held_sells_actual_venue_qty() -> None:
    """SYMMETRIC over-count (latent blind spot): the wallet holds MORE than the
    tracked base_qty (open-side orphans landed surplus shares on this symbol).
    The close must SELL the ACTUAL venue-held qty so the surplus is not left
    orphaned (flow_not_block — flatten what is really there), NOT just base_qty."""
    adapter = _AlpacaMock(
        is_open=True, positions_qty=5.0, fill_qty=5.0, fill_price=101.0,
    )
    fill = await real_alpaca_close_fill(
        adapter, symbol="AAPL", base_qty=2.0, strategy_id="s",
    )
    assert not isinstance(fill, CloseOrphan)
    assert fill is not None
    # Sold the actual 5 shares the venue holds, not the tracked 2.
    assert len(adapter.sell_orders) == 1
    assert adapter.sell_orders[0]["qty"] == pytest.approx(5.0)
    assert fill.base_qty == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_alpaca_close_exact_match_byte_identical() -> None:
    """available == base_qty → behaviour byte-identical to today: sell base_qty."""
    adapter = _AlpacaMock(
        is_open=True, positions_qty=2.0, fill_qty=2.0, fill_price=101.0,
    )
    fill = await real_alpaca_close_fill(
        adapter, symbol="AAPL", base_qty=2.0, strategy_id="s",
    )
    assert not isinstance(fill, CloseOrphan)
    assert fill is not None
    assert len(adapter.sell_orders) == 1
    assert adapter.sell_orders[0]["qty"] == pytest.approx(2.0)
    assert fill.base_qty == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _real_close_fill routing — venue='alpaca' → real_alpaca_close_fill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_close_fill_routes_alpaca_to_sell() -> None:
    """venue='alpaca' routes to real_alpaca_close_fill (a real SELL), NOT the
    Capital deal_id fallthrough (which would orphan + never sell)."""
    adapter = _AlpacaMock(is_open=True, fill_qty=2.0, fill_price=101.0)
    trade = _alpaca_trade(base_qty=2.0)
    out = await _real_close_fill(trade=trade, alpaca_adapter=adapter)
    assert not isinstance(out, CloseOrphan)
    assert out is not None
    assert out.venue == "alpaca"
    assert out.side == "sell"
    assert len(adapter.sell_orders) == 1


@pytest.mark.asyncio
async def test_real_close_fill_alpaca_market_closed_none() -> None:
    """venue='alpaca' + market closed → None (transient) through _real_close_fill."""
    adapter = _AlpacaMock(is_open=False)
    trade = _alpaca_trade(base_qty=2.0)
    out = await _real_close_fill(trade=trade, alpaca_adapter=adapter)
    assert out is None


@pytest.mark.asyncio
async def test_real_close_fill_alpaca_no_adapter_skips_gracefully() -> None:
    """venue='alpaca' with no adapter + no creds → graceful None (like the other
    venues' None-adapter path), never an exception."""
    trade = _alpaca_trade(base_qty=2.0)
    out = await _real_close_fill(trade=trade, alpaca_adapter=None)
    assert out is None


# ---------------------------------------------------------------------------
# GOLDEN — OKX + Capital branches stay byte-identical (routing unchanged)
# ---------------------------------------------------------------------------


class _GoldenOKX:
    def __init__(self) -> None:
        self.sell_calls = 0

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        return {"data": []}  # unknown balance → skip clamp, sell full qty

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.sell_calls += 1

        class _R:
            ok = True
            venue_order_id = "okx_1"
            code = "0"
            msg = ""

        return _R()

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        return {
            "data": [{
                "instId": inst_id, "ordId": ord_id, "side": "sell",
                "state": "filled", "fillSz": "0.001", "avgPx": "80000",
                "sz": "0.001", "accFillSz": "0.001", "fillPx": "80000",
                "fee": "-0.08", "feeCcy": "USDT", "uTime": str(NOW_MS),
                "clOrdId": "c1",
            }]
        }


@pytest.mark.asyncio
async def test_real_close_fill_okx_branch_unchanged(memdb: sqlite3.Connection) -> None:
    """venue='okx' still routes to the OKX sell path (golden — not the alpaca
    branch). An okx_adapter present + alpaca_adapter present must NOT cross over."""
    okx = _GoldenOKX()
    alp = _AlpacaMock(is_open=True)
    trade = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id="s", side="long", entry_price=80000.0, notional_usd=80.0,
        open_ts=0, position_id="p_okx", base_qty=0.001,
    )
    out = await _real_close_fill(
        trade=trade, okx_adapter=okx, alpaca_adapter=alp,
    )
    assert out is not None
    assert not isinstance(out, CloseOrphan)
    assert out.venue == "okx"
    assert okx.sell_calls == 1  # OKX path drove the close
    assert alp.sell_orders == []  # alpaca path untouched


@pytest.mark.asyncio
async def test_real_close_fill_capital_branch_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """venue='capital' with a deal_id still routes to the Capital
    close-by-deal_id path (golden — alpaca_adapter present must NOT divert it).

    We spy on ``real_capital_close_fill`` so the golden assertion is purely
    'the Capital branch was taken with the right deal_id, the alpaca branch was
    not' — without standing up a live Capital session.
    """
    import polaris.scripts._production_close as pc

    seen: dict[str, Any] = {}

    async def _spy_capital(adapter: Any, *, deal_id: str, strategy_id: str, **_: Any):
        seen["deal_id"] = deal_id
        return None  # routing is what we assert; None == transient (no fill)

    monkeypatch.setattr(pc, "real_capital_close_fill", _spy_capital)

    cap = object()  # a non-None session sentinel (CapitalAdapter just wraps it)
    alp = _AlpacaMock(is_open=True)
    trade = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="capital", symbol="EURUSD",
        strategy_id="s", side="long", entry_price=1.105, notional_usd=80.0,
        open_ts=0, position_id="p_cap", base_qty=1.0, deal_id="d1",
    )
    out = await _real_close_fill(
        trade=trade, capital_session=cap, alpaca_adapter=alp,
    )
    assert out is None  # the spy returned None (transient) — Capital branch ran
    assert seen.get("deal_id") == "d1"  # closed by the tracked deal_id
    assert alp.sell_orders == []  # alpaca path untouched (no cross-over)
