"""Tests for BTCDominanceLag — BTC Dominance Lead-Lag alpha.

TDD: RED → GREEN → REFACTOR.
Academic citation:
  - Stalder & Cosenza (2025) "Cross-cryptocurrency return predictability"
  - Liu et al. (2022) "Common risk factors in cryptocurrency"

BTC 5min momentum leads alt follow (30s-3min lag).
Pure P6 tests — no I/O.
"""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction

# Will fail (RED) until src/strategies/btc_dominance_lag.py exists
from src.strategies.btc_dominance_lag import BTCDominanceLag


def _make_tick(price: float = 1.0, ts_ms: int = 1_000_000) -> dict:
    """OKX-style alt ticker tick."""
    return {"last": str(price), "ts": str(ts_ms)}


def _make_btc_state(
    price_now: float = 100_000.0,
    price_5min_ago: float = 99_500.0,  # +0.5% — strong positive momentum
    ts_ms: int = 1_000_000,
) -> dict:
    """BTC state dict for lead-lag calculation."""
    return {
        "price_now": price_now,
        "price_5min_ago": price_5min_ago,
        "ts_ms": ts_ms,
    }


class TestBTCDominanceLagBasic:
    """Core lead-lag signal logic."""

    def test_enter_long_when_btc_strong_and_alt_lags(self):
        """BTC +0.5% in 5min + alt in same direction + alt lags BTC → ENTER_LONG."""
        strategy = BTCDominanceLag()
        # BTC: 99_500 → 100_000 = +0.503% (above btc_momentum_threshold=0.5%)
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_500.0)
        # Alt: 5min delta 0.2% (lagging BTC 0.503%) AND 24h positive
        alt_tick = _make_tick(price=1.002)
        alt_context = {
            "alt_5min_delta_pct": 0.002,   # 0.2% < BTC 0.503% (lag confirmed)
            "alt_24h_delta_pct": 0.005,    # positive 24h (same direction as BTC)
        }
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.action == SignalAction.ENTER_LONG

    def test_hold_when_btc_momentum_weak(self):
        """BTC momentum < threshold → HOLD regardless of alt."""
        strategy = BTCDominanceLag(btc_momentum_threshold_pct=0.5)
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_800.0)  # +0.2% only
        alt_tick = _make_tick(price=1.002)
        alt_context = {"alt_5min_delta_pct": 0.001, "alt_24h_delta_pct": 0.003}
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.action == SignalAction.HOLD

    def test_hold_when_alt_already_caught_up(self):
        """Alt 5min delta >= BTC 5min delta → already followed, no lag → HOLD."""
        strategy = BTCDominanceLag()
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_500.0)  # +0.503%
        alt_tick = _make_tick(price=1.006)
        alt_context = {
            "alt_5min_delta_pct": 0.006,   # +0.6% > BTC 0.503% — alt already outpaced BTC
            "alt_24h_delta_pct": 0.01,
        }
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.action == SignalAction.HOLD

    def test_hold_when_alt_24h_negative(self):
        """Alt 24h trend negative → not in BTC's direction → HOLD (filter weak alts)."""
        strategy = BTCDominanceLag()
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_500.0)  # +0.503%
        alt_tick = _make_tick(price=0.998)
        alt_context = {
            "alt_5min_delta_pct": 0.001,   # lagging
            "alt_24h_delta_pct": -0.02,    # 24h negative — alt in downtrend
        }
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.action == SignalAction.HOLD

    def test_hold_when_btc_state_none(self):
        """No BTC state (warmup) → HOLD."""
        strategy = BTCDominanceLag()
        alt_tick = _make_tick()
        signal = strategy.evaluate_lag(alt_tick, None, {"alt_5min_delta_pct": 0.001, "alt_24h_delta_pct": 0.005})
        assert signal.action == SignalAction.HOLD

    def test_hold_when_btc_state_stale(self):
        """BTC state older than max_btc_age_ms → HOLD (stale data guard)."""
        strategy = BTCDominanceLag(max_btc_age_ms=300_000)  # 5min
        now_ms = 10_000_000
        btc_state = _make_btc_state(
            price_now=100_000.0,
            price_5min_ago=99_500.0,
            ts_ms=now_ms - 360_000,  # 6min old — stale
        )
        alt_tick = {"last": "1.002", "ts": str(now_ms)}
        alt_context = {"alt_5min_delta_pct": 0.001, "alt_24h_delta_pct": 0.003}
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context, now_ms=now_ms)
        assert signal.action == SignalAction.HOLD


class TestBTCDominanceLagSignalProperties:
    """Signal metadata."""

    def test_enter_long_confidence_positive(self):
        """ENTER_LONG confidence > 0."""
        strategy = BTCDominanceLag()
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_500.0)
        alt_tick = _make_tick(price=1.002)
        alt_context = {"alt_5min_delta_pct": 0.002, "alt_24h_delta_pct": 0.005}
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.confidence > 0.0

    def test_enter_long_target_size_positive(self):
        """ENTER_LONG target_size_usd > 0."""
        strategy = BTCDominanceLag()
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_500.0)
        alt_tick = _make_tick(price=1.002)
        alt_context = {"alt_5min_delta_pct": 0.002, "alt_24h_delta_pct": 0.005}
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.target_size_usd > 0.0

    def test_signal_timestamp_from_alt_tick(self):
        """Signal timestamp_ms from alt tick ts."""
        strategy = BTCDominanceLag()
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_500.0)
        alt_tick = {"last": "1.002", "ts": "7_777_000"}
        alt_context = {"alt_5min_delta_pct": 0.002, "alt_24h_delta_pct": 0.005}
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.timestamp_ms == 7_777_000

    def test_strategy_name(self):
        """strategy.name == 'btc_dominance_lag'."""
        assert BTCDominanceLag().name == "btc_dominance_lag"

    def test_spot_only_no_enter_short(self):
        """Never returns ENTER_SHORT."""
        strategy = BTCDominanceLag()
        # Negative BTC momentum (falling) → at most EXIT, never ENTER_SHORT
        btc_state = _make_btc_state(price_now=99_000.0, price_5min_ago=100_000.0)  # -1% drop
        alt_tick = _make_tick(price=0.99)
        alt_context = {"alt_5min_delta_pct": -0.005, "alt_24h_delta_pct": -0.01}
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert signal.action != SignalAction.ENTER_SHORT

    def test_reason_mentions_btc_momentum(self):
        """Signal reason should mention BTC or momentum."""
        strategy = BTCDominanceLag()
        btc_state = _make_btc_state(price_now=100_000.0, price_5min_ago=99_500.0)
        alt_tick = _make_tick(price=1.002)
        alt_context = {"alt_5min_delta_pct": 0.002, "alt_24h_delta_pct": 0.005}
        signal = strategy.evaluate_lag(alt_tick, btc_state, alt_context)
        assert "btc" in signal.reason.lower() or "lag" in signal.reason.lower()
