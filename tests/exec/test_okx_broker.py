"""Tests for src/exec/okx_broker.py — Phase 14.1 dry-run skeleton."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.exec.broker import OrderRequest, OrderSide, OrderStatus, OrderType
from src.exec.kill_switch import reset_kill_switch, set_kill_switch
from src.exec.okx_broker import OKXBroker, _live_armed


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip POLARIS_LIVE_MODE + OKX keys to default dry-run state."""
    for key in (
        "POLARIS_LIVE_MODE", "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE",
        "POLARIS_KILL_SWITCH",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.fixture
def broker():
    return OKXBroker(max_size_usd=500.0)


# ─── Live-armed gating ───────────────────────────────────────────────────────


class TestLiveArmed:
    def test_default_disarmed(self):
        assert _live_armed() is False

    def test_armed_requires_all_three_secrets(self, monkeypatch):
        monkeypatch.setenv("POLARIS_LIVE_MODE", "1")
        monkeypatch.setenv("OKX_API_KEY", "x")
        monkeypatch.setenv("OKX_API_SECRET", "x")
        # Missing passphrase
        assert _live_armed() is False
        monkeypatch.setenv("OKX_API_PASSPHRASE", "x")
        assert _live_armed() is True

    def test_live_mode_off_disarms(self, monkeypatch):
        monkeypatch.setenv("OKX_API_KEY", "x")
        monkeypatch.setenv("OKX_API_SECRET", "x")
        monkeypatch.setenv("OKX_API_PASSPHRASE", "x")
        # POLARIS_LIVE_MODE not set
        assert _live_armed() is False


# ─── Place order safety ──────────────────────────────────────────────────────


class TestPlaceOrderSafety:
    def test_dry_run_rejects(self, broker):
        req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0)
        res = broker.place_order(req)
        assert res.status == OrderStatus.REJECTED
        assert "dry_run" in res.error_msg

    def test_size_cap_blocks_large_order(self, broker):
        req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=1000.0)
        res = broker.place_order(req)
        assert res.status == OrderStatus.REJECTED
        assert "size_usd" in res.error_msg

    def test_kill_switch_blocks_even_when_armed(self, broker, monkeypatch):
        monkeypatch.setenv("POLARIS_LIVE_MODE", "1")
        monkeypatch.setenv("OKX_API_KEY", "x")
        monkeypatch.setenv("OKX_API_SECRET", "x")
        monkeypatch.setenv("OKX_API_PASSPHRASE", "x")
        set_kill_switch(True)
        req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0)
        res = broker.place_order(req)
        assert res.status == OrderStatus.REJECTED
        assert "kill_switch" in res.error_msg

    def test_armed_demo_market_order_filled(self, broker, monkeypatch):
        # All secrets set + live mode on + demo header on (default)
        monkeypatch.setenv("POLARIS_LIVE_MODE", "1")
        monkeypatch.setenv("OKX_API_KEY", "x")
        monkeypatch.setenv("OKX_API_SECRET", "x")
        monkeypatch.setenv("OKX_API_PASSPHRASE", "x")
        broker_armed = OKXBroker(max_size_usd=500.0)

        # Mock 1: place order success
        place_resp = {
            "code": "0", "msg": "",
            "data": [{"clOrdId": "X", "ordId": "12345", "sCode": "0", "sMsg": ""}],
        }
        # Mock 2: query fill — filled
        fill_resp = {
            "code": "0", "msg": "",
            "data": [{
                "ordId": "12345", "avgPx": "80000.0", "fillSz": "0.001",
                "fee": "-0.08", "state": "filled",
            }],
        }

        from unittest.mock import patch, MagicMock
        post_mock = MagicMock()
        post_mock.json.return_value = place_resp
        post_mock.raise_for_status = MagicMock()
        get_mock = MagicMock()
        get_mock.json.return_value = fill_resp
        get_mock.raise_for_status = MagicMock()

        with patch("src.exec.okx_broker.requests.post", return_value=post_mock) as p, \
             patch("src.exec.okx_broker.requests.get", return_value=get_mock):
            req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0)
            res = broker_armed.place_order(req)

        # Verify demo header in request
        call = p.call_args
        headers = call.kwargs["headers"]
        assert headers["x-simulated-trading"] == "1"
        assert "OK-ACCESS-SIGN" in headers
        assert headers["OK-ACCESS-KEY"] == "x"

        assert res.status == OrderStatus.FILLED
        assert res.order_id == "12345"
        assert res.avg_fill_price == 80000.0
        assert abs(res.filled_size_usd - 80.0) < 0.01  # 0.001 × 80000
        assert abs(res.fee_usd - 0.08) < 0.01

    def test_armed_okx_error_rejected(self, broker, monkeypatch):
        monkeypatch.setenv("POLARIS_LIVE_MODE", "1")
        monkeypatch.setenv("OKX_API_KEY", "x")
        monkeypatch.setenv("OKX_API_SECRET", "x")
        monkeypatch.setenv("OKX_API_PASSPHRASE", "x")
        broker_armed = OKXBroker(max_size_usd=500.0)

        err_resp = {
            "code": "1", "msg": "param error", "data": [],
        }
        from unittest.mock import patch, MagicMock
        post_mock = MagicMock()
        post_mock.json.return_value = err_resp
        post_mock.raise_for_status = MagicMock()

        with patch("src.exec.okx_broker.requests.post", return_value=post_mock):
            req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0)
            res = broker_armed.place_order(req)
        assert res.status == OrderStatus.REJECTED
        assert res.order_id == "OKX-ERROR"
        assert "param error" in res.error_msg

    def test_armed_demo_disabled_real_money_warning(self, broker, monkeypatch):
        # Explicit demo=0 → real money mode (no x-simulated-trading header)
        monkeypatch.setenv("POLARIS_LIVE_MODE", "1")
        monkeypatch.setenv("OKX_API_KEY", "x")
        monkeypatch.setenv("OKX_API_SECRET", "x")
        monkeypatch.setenv("OKX_API_PASSPHRASE", "x")
        monkeypatch.setenv("POLARIS_OKX_DEMO", "0")
        broker_armed = OKXBroker(max_size_usd=500.0)

        place_resp = {"code": "0", "data": [{"ordId": "X", "sCode": "0"}]}
        fill_resp = {"code": "0", "data": [{"ordId": "X"}]}
        from unittest.mock import patch, MagicMock
        post_mock = MagicMock()
        post_mock.json.return_value = place_resp
        post_mock.raise_for_status = MagicMock()
        get_mock = MagicMock()
        get_mock.json.return_value = fill_resp
        get_mock.raise_for_status = MagicMock()

        with patch("src.exec.okx_broker.requests.post", return_value=post_mock) as p, \
             patch("src.exec.okx_broker.requests.get", return_value=get_mock):
            req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0)
            broker_armed.place_order(req)

        call = p.call_args
        headers = call.kwargs["headers"]
        # Real money: no x-simulated-trading header
        assert "x-simulated-trading" not in headers


class TestSignature:
    """OKX HMAC-SHA256 signature determinism."""

    def test_sign_deterministic(self):
        from src.exec.okx_broker import _sign
        s1 = _sign("secret", "2024-01-01T00:00:00.000Z", "POST", "/api/v5/trade/order", '{"a":1}')
        s2 = _sign("secret", "2024-01-01T00:00:00.000Z", "POST", "/api/v5/trade/order", '{"a":1}')
        assert s1 == s2
        assert len(s1) > 0

    def test_sign_different_for_different_inputs(self):
        from src.exec.okx_broker import _sign
        s1 = _sign("secret", "2024-01-01T00:00:00.000Z", "POST", "/api/v5/trade/order", '{"a":1}')
        s2 = _sign("secret", "2024-01-01T00:00:00.000Z", "POST", "/api/v5/trade/order", '{"a":2}')
        assert s1 != s2

    def test_iso_timestamp_format(self):
        from src.exec.okx_broker import _iso_timestamp
        ts = _iso_timestamp()
        assert ts.endswith("Z")
        assert "T" in ts
        assert ts.count("-") == 2
        assert ts.count(":") == 2


class TestProperties:
    def test_is_live(self, broker):
        assert broker.is_live is True

    def test_balance_empty_phase141(self, broker):
        assert broker.get_balance() == {}

    def test_cancel_returns_false_phase141(self, broker):
        assert broker.cancel_order("X") is False
