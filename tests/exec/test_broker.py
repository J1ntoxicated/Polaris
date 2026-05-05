"""Tests for src/exec/broker.py — abstract Broker dataclasses."""
from __future__ import annotations

import pytest

from src.exec.broker import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)


class TestOrderRequest:
    def test_market_buy_minimal(self):
        req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=300.0)
        assert req.side == OrderSide.BUY
        assert req.order_type == OrderType.MARKET

    def test_zero_size_raises(self):
        with pytest.raises(ValueError, match="size_usd"):
            OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=0)

    def test_negative_size_raises(self):
        with pytest.raises(ValueError):
            OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=-100)

    def test_limit_post_only_requires_price(self):
        with pytest.raises(ValueError, match="limit_price required"):
            OrderRequest(
                side=OrderSide.BUY, ticker="BTC-USDT", size_usd=300.0,
                order_type=OrderType.LIMIT_POST_ONLY,
            )

    def test_limit_post_only_with_price(self):
        req = OrderRequest(
            side=OrderSide.BUY, ticker="BTC-USDT", size_usd=300.0,
            order_type=OrderType.LIMIT_POST_ONLY, limit_price=80000.0,
        )
        assert req.limit_price == 80000.0

    def test_immutable(self):
        req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=300.0)
        with pytest.raises(Exception):
            req.size_usd = 500  # type: ignore[misc]


class TestOrderResult:
    def test_filled(self):
        r = OrderResult(
            status=OrderStatus.FILLED, order_id="X",
            filled_size_usd=100, avg_fill_price=50, fee_usd=0.1,
            slippage_bps=2.5, ts_ms=1,
        )
        assert r.status == OrderStatus.FILLED

    def test_rejected_with_error(self):
        r = OrderResult(
            status=OrderStatus.REJECTED, order_id="X",
            filled_size_usd=0, avg_fill_price=0, fee_usd=0,
            slippage_bps=0, ts_ms=1, error_msg="boom",
        )
        assert r.error_msg == "boom"


class TestEnums:
    def test_order_side_values(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_order_type_values(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT_POST_ONLY.value == "limit_post_only"

    def test_order_status_values(self):
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.REJECTED.value == "rejected"
