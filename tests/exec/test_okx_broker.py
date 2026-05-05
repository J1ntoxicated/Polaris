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

    def test_armed_but_phase142_pending(self, broker, monkeypatch):
        # All secrets set + live mode on + kill switch off — but Phase 14.2 not built
        monkeypatch.setenv("POLARIS_LIVE_MODE", "1")
        monkeypatch.setenv("OKX_API_KEY", "x")
        monkeypatch.setenv("OKX_API_SECRET", "x")
        monkeypatch.setenv("OKX_API_PASSPHRASE", "x")
        broker_armed = OKXBroker(max_size_usd=500.0)
        req = OrderRequest(side=OrderSide.BUY, ticker="BTC-USDT", size_usd=100.0)
        res = broker_armed.place_order(req)
        # Phase 14.2 placeholder
        assert res.status == OrderStatus.REJECTED
        assert "not yet implemented" in res.error_msg


class TestProperties:
    def test_is_live(self, broker):
        assert broker.is_live is True

    def test_balance_empty_phase141(self, broker):
        assert broker.get_balance() == {}

    def test_cancel_returns_false_phase141(self, broker):
        assert broker.cancel_order("X") is False
