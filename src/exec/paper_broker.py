"""PaperBroker — Broker interface backed by Phase 6+ slippage_model (shell).

Wraps the existing paper engine fill simulation behind the abstract Broker
API. Pure functional simulation of taker fills using current OKX WS book
data. Limit orders simulated as pending (always reject for now — paper
cannot truly simulate maker queue position).

This lets realtime_runner / cron / backtest swap a live broker in by
changing the constructor — no logic changes.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from src.data.okx_ws import get_book, get_tick
from src.exec.broker import (
    Broker,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.paper.slippage_model import compute_fill_price

logger = logging.getLogger(__name__)


class PaperBroker(Broker):
    """Paper broker — simulates fills via slippage_model + WS book.

    NOT thread-safe. Single-loop usage assumed (matches asyncio runner).
    """

    def __init__(
        self,
        fee_round_trip: float = 0.002,
        cash_usd: float = 5000.0,
    ) -> None:
        self._fee_round_trip = fee_round_trip
        self._cash_usd = cash_usd
        self._next_order_id = 1

    @property
    def is_live(self) -> bool:
        return False

    def place_order(self, request: OrderRequest) -> OrderResult:
        ts_ms = int(time.time() * 1000)

        # MARKET: simulate immediate fill via slippage_model
        if request.order_type == OrderType.MARKET:
            return self._fill_market(request, ts_ms)

        # LIMIT_POST_ONLY: paper engine cannot truly simulate maker queue
        # position. Reject for now — Phase 14.2 (live) handles this properly.
        return OrderResult(
            status=OrderStatus.REJECTED,
            order_id=self._gen_id(),
            filled_size_usd=0.0,
            avg_fill_price=0.0,
            fee_usd=0.0,
            slippage_bps=0.0,
            ts_ms=ts_ms,
            error_msg="paper broker does not simulate maker fills (LIMIT_POST_ONLY)",
        )

    def cancel_order(self, order_id: str) -> bool:
        # Paper has no pending orders (all market fills are immediate)
        return True

    def get_balance(self) -> dict[str, float]:
        return {"USDT": self._cash_usd}

    # ── internals ────────────────────────────────────────────────────────────

    def _fill_market(self, request: OrderRequest, ts_ms: int) -> OrderResult:
        """Use slippage_model walks the WS book to produce a fill price."""
        ticker = request.ticker
        book = get_book(ticker) or {}
        tick = get_tick(ticker) or {}
        last = float(tick.get("last", 0) or 0)
        bid = float(tick.get("bid", 0) or 0)
        ask = float(tick.get("ask", 0) or 0)

        if last <= 0:
            return OrderResult(
                status=OrderStatus.REJECTED,
                order_id=self._gen_id(),
                filled_size_usd=0.0, avg_fill_price=0.0,
                fee_usd=0.0, slippage_bps=0.0,
                ts_ms=ts_ms,
                error_msg=f"no price data for {ticker}",
            )

        side_str = "buy" if request.side == OrderSide.BUY else "sell"
        fill_price, slip_bps = compute_fill_price(
            side_str, size_usd=request.size_usd, book=book,
            last=last, bid=bid, ask=ask,
        )
        if fill_price <= 0:
            return OrderResult(
                status=OrderStatus.REJECTED,
                order_id=self._gen_id(),
                filled_size_usd=0.0, avg_fill_price=0.0,
                fee_usd=0.0, slippage_bps=0.0,
                ts_ms=ts_ms,
                error_msg=f"compute_fill_price returned 0 for {ticker}",
            )

        # Fee = half of round-trip per side (entry side only here)
        fee_per_side = self._fee_round_trip / 2.0
        fee_usd = request.size_usd * fee_per_side

        return OrderResult(
            status=OrderStatus.FILLED,
            order_id=self._gen_id(),
            filled_size_usd=request.size_usd,
            avg_fill_price=fill_price,
            fee_usd=fee_usd,
            slippage_bps=slip_bps,
            ts_ms=ts_ms,
            raw_response={
                "side": side_str, "ticker": ticker, "last": last,
                "bid": bid, "ask": ask, "fill": fill_price,
            },
        )

    def _gen_id(self) -> str:
        order_id = f"PAPER-{self._next_order_id}"
        self._next_order_id += 1
        return order_id
