"""Tests for src/exec/paper_broker.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.exec.broker import OrderRequest, OrderSide, OrderStatus, OrderType
from src.exec.paper_broker import PaperBroker


@pytest.fixture
def broker():
    return PaperBroker(fee_round_trip=0.002, cash_usd=5000.0)


@pytest.fixture
def fake_book():
    return {
        "bids": [(99.99, 100.0), (99.98, 100.0)],
        "asks": [(100.01, 100.0), (100.02, 100.0)],
    }


@pytest.fixture
def fake_tick():
    return {"last": 100.0, "bid": 99.99, "ask": 100.01}


class TestPaperBrokerProperties:
    def test_is_paper(self, broker):
        assert broker.is_live is False

    def test_balance(self, broker):
        assert broker.get_balance() == {"USDT": 5000.0}


class TestMarketOrder:
    def test_market_buy_fills(self, broker, fake_book, fake_tick):
        with patch("src.exec.paper_broker.get_book", return_value=fake_book), \
             patch("src.exec.paper_broker.get_tick", return_value=fake_tick):
            req = OrderRequest(
                side=OrderSide.BUY, ticker="BTC-USDT", size_usd=300.0,
            )
            res = broker.place_order(req)
        assert res.status == OrderStatus.FILLED
        assert res.filled_size_usd == 300.0
        # BUY fills near ask 100.01 (or walked-book avg)
        assert 100.0 <= res.avg_fill_price <= 100.05
        # fee = 0.002 / 2 = 0.001 per side × 300 = 0.3
        assert abs(res.fee_usd - 0.3) < 0.01

    def test_market_sell_fills(self, broker, fake_book, fake_tick):
        with patch("src.exec.paper_broker.get_book", return_value=fake_book), \
             patch("src.exec.paper_broker.get_tick", return_value=fake_tick):
            req = OrderRequest(
                side=OrderSide.SELL, ticker="BTC-USDT", size_usd=300.0,
            )
            res = broker.place_order(req)
        assert res.status == OrderStatus.FILLED
        # SELL fills near bid 99.99
        assert 99.95 <= res.avg_fill_price <= 100.0

    def test_no_price_data_rejects(self, broker):
        with patch("src.exec.paper_broker.get_book", return_value=None), \
             patch("src.exec.paper_broker.get_tick", return_value=None):
            req = OrderRequest(
                side=OrderSide.BUY, ticker="UNKNOWN", size_usd=100.0,
            )
            res = broker.place_order(req)
        assert res.status == OrderStatus.REJECTED
        assert "no price data" in res.error_msg


class TestLimitPostOnly:
    def test_paper_rejects_post_only(self, broker, fake_book, fake_tick):
        with patch("src.exec.paper_broker.get_book", return_value=fake_book), \
             patch("src.exec.paper_broker.get_tick", return_value=fake_tick):
            req = OrderRequest(
                side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0,
                order_type=OrderType.LIMIT_POST_ONLY, limit_price=99.50,
            )
            res = broker.place_order(req)
        assert res.status == OrderStatus.REJECTED
        assert "maker" in res.error_msg.lower()


class TestUniqueOrderIds:
    def test_ids_increment(self, broker, fake_book, fake_tick):
        with patch("src.exec.paper_broker.get_book", return_value=fake_book), \
             patch("src.exec.paper_broker.get_tick", return_value=fake_tick):
            ids = []
            for _ in range(3):
                res = broker.place_order(OrderRequest(
                    side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0,
                ))
                ids.append(res.order_id)
        assert len(set(ids)) == 3  # all unique
