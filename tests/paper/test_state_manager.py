"""Tests for src/paper/state_manager.py — balance cache + persist."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.paper.state import PaperBalance, Position
from src.paper.state_manager import (
    StateManager,
    get_default_manager,
    reset_default_manager,
)


@pytest.fixture
def sm():
    """Fresh StateManager — no shared state between tests."""
    return StateManager()


def _bal(starting=5000.0, cash=4700.0):
    return PaperBalance(starting_usd=starting, cash_usd=cash)


class TestCache:
    def test_load_miss_calls_disk(self, sm, monkeypatch):
        with patch("src.paper.state_manager.load_state") as mock_load:
            mock_load.return_value = _bal()
            sm.load("BTC-USDT", "vol_burst", starting_usd=5000.0)
        mock_load.assert_called_once()

    def test_load_hit_skips_disk(self, sm):
        sm._cache[("BTC-USDT", "vol_burst")] = _bal()
        with patch("src.paper.state_manager.load_state") as mock_load:
            sm.load("BTC-USDT", "vol_burst", starting_usd=5000.0)
        mock_load.assert_not_called()

    def test_save_writes_disk_and_cache(self, sm):
        bal = _bal()
        with patch("src.paper.state_manager.save_state") as mock_save:
            sm.save("BTC-USDT", "vol_burst", bal)
        mock_save.assert_called_once_with("BTC-USDT", "vol_burst", bal)
        assert sm.get_cached("BTC-USDT", "vol_burst") is bal

    def test_invalidate(self, sm):
        sm._cache[("BTC-USDT", "x")] = _bal()
        sm.invalidate("BTC-USDT", "x")
        assert sm.get_cached("BTC-USDT", "x") is None

    def test_clear(self, sm):
        sm._cache[("BTC-USDT", "x")] = _bal()
        sm._cache[("ETH-USDT", "y")] = _bal()
        sm.clear()
        assert sm.cache_size() == 0


class TestSingleton:
    def test_default_manager_is_singleton(self):
        reset_default_manager()
        m1 = get_default_manager()
        m2 = get_default_manager()
        assert m1 is m2

    def test_reset_creates_new_instance(self):
        m1 = get_default_manager()
        reset_default_manager()
        m2 = get_default_manager()
        assert m1 is not m2

    def test_singleton_independent_caches_across_resets(self):
        reset_default_manager()
        m1 = get_default_manager()
        m1._cache[("X", "y")] = _bal()
        reset_default_manager()
        m2 = get_default_manager()
        assert m2.cache_size() == 0


class TestRoundTrip:
    def test_save_then_load_returns_cached(self, sm):
        bal = _bal()
        with patch("src.paper.state_manager.save_state"):
            sm.save("BTC-USDT", "vol_burst", bal)
        # Load should hit cache, not disk
        with patch("src.paper.state_manager.load_state") as mock_load:
            result = sm.load("BTC-USDT", "vol_burst", 5000.0)
        mock_load.assert_not_called()
        assert result is bal
