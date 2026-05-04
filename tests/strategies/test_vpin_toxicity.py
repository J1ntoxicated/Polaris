"""Tests for VPINToxicity — Volume-Synchronized Probability of Informed Trading.

TDD: RED → GREEN → REFACTOR.
Academic citation: Easley, Lopez de Prado, O'Hara (2012) RFS —
"Flow Toxicity and Liquidity in a High-frequency World".

Pure P6 tests — no I/O. evaluate_vpin(tick, buckets) pure function.
"""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction

# Will fail (RED) until src/strategies/vpin_toxicity.py exists
from src.strategies.vpin_toxicity import VPINToxicity


def _make_tick(price: float = 100.0, ts_ms: int = 1_000_000) -> dict:
    """OKX-style tick dict."""
    return {"last": str(price), "ts": str(ts_ms)}


def _make_bucket(buy_vol: float, sell_vol: float) -> dict:
    """Volume bucket with buy/sell split."""
    return {"buy_vol": buy_vol, "sell_vol": sell_vol}


def _high_toxicity_buckets(n: int = 50, bias: float = 0.8) -> list[dict]:
    """VPIN > 0.7: heavy buy imbalance buckets (buy_vol >> sell_vol)."""
    buckets = []
    for _ in range(n):
        buy = bias * 1000.0
        sell = (1.0 - bias) * 1000.0
        buckets.append(_make_bucket(buy, sell))
    return buckets


def _low_toxicity_buckets(n: int = 50) -> list[dict]:
    """VPIN < 0.3: nearly balanced buckets (low informed trading)."""
    buckets = []
    for _ in range(n):
        buckets.append(_make_bucket(500.0, 500.0))  # perfectly balanced → VPIN ~0
    return buckets


def _make_falling_tick(price: float = 97.0, ts_ms: int = 1_000_000) -> dict:
    """Tick after price drop (for mean-revert signal)."""
    return {"last": str(price), "ts": str(ts_ms)}


class TestVPINBasic:
    """Core VPIN signal logic."""

    def test_high_toxicity_enters_long(self):
        """VPIN > 0.7 (high informed buy flow) → ENTER_LONG (momentum signal)."""
        strategy = VPINToxicity(high_toxicity_threshold=0.7, low_toxicity_threshold=0.3)
        buckets = _high_toxicity_buckets(50, bias=0.85)
        tick = _make_tick(price=100.0)
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.ENTER_LONG

    def test_balanced_flow_returns_hold(self):
        """Balanced VPIN (≈ 0.0 — equal buy/sell) → HOLD."""
        strategy = VPINToxicity()
        buckets = _low_toxicity_buckets(50)
        tick = _make_tick(price=100.0)
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.HOLD

    def test_insufficient_buckets_returns_hold(self):
        """Less than min_buckets → HOLD (warmup)."""
        strategy = VPINToxicity(min_buckets=10)
        buckets = _high_toxicity_buckets(5)  # need 10
        tick = _make_tick()
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.HOLD

    def test_empty_buckets_returns_hold(self):
        """Empty bucket list → HOLD."""
        strategy = VPINToxicity()
        signal = strategy.evaluate_vpin(_make_tick(), [])
        assert signal.action == SignalAction.HOLD

    def test_vpin_formula_pure_calculation(self):
        """VPIN = sum(|buy-sell|) / sum(total) must be computed correctly."""
        strategy = VPINToxicity()
        # 5 buckets: each buy=800, sell=200 → |diff|=600, total=1000 → VPIN = 3000/5000 = 0.6
        buckets = [_make_bucket(800.0, 200.0) for _ in range(5)]
        vpin = strategy.compute_vpin(buckets)
        assert abs(vpin - 0.6) < 1e-9


class TestVPINSignalProperties:
    """Signal metadata."""

    def test_enter_long_has_positive_confidence(self):
        """ENTER_LONG confidence > 0."""
        strategy = VPINToxicity()
        buckets = _high_toxicity_buckets(50, bias=0.90)
        signal = strategy.evaluate_vpin(_make_tick(), buckets)
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.confidence > 0.0

    def test_enter_long_has_positive_target_size(self):
        """ENTER_LONG target_size_usd > 0."""
        strategy = VPINToxicity()
        buckets = _high_toxicity_buckets(50, bias=0.90)
        signal = strategy.evaluate_vpin(_make_tick(), buckets)
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.target_size_usd > 0.0

    def test_signal_timestamp_from_tick(self):
        """Signal timestamp_ms comes from tick ts."""
        strategy = VPINToxicity()
        buckets = _high_toxicity_buckets(50, bias=0.90)
        tick = _make_tick(ts_ms=9_999_000)
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.timestamp_ms == 9_999_000

    def test_strategy_name_is_vpin(self):
        """strategy.name == 'vpin_toxicity'."""
        strategy = VPINToxicity()
        assert strategy.name == "vpin_toxicity"

    def test_spot_only_no_short_signal(self):
        """Never returns ENTER_SHORT (SPOT-only)."""
        strategy = VPINToxicity()
        # Both high and low toxicity → no SHORT
        for buckets in [_high_toxicity_buckets(50), _low_toxicity_buckets(50)]:
            signal = strategy.evaluate_vpin(_make_tick(), buckets)
            assert signal.action != SignalAction.ENTER_SHORT


class TestVPINComputeFunction:
    """compute_vpin pure function isolation."""

    def test_compute_vpin_zero_for_balanced(self):
        """Perfectly balanced: VPIN = 0."""
        strategy = VPINToxicity()
        buckets = [_make_bucket(500.0, 500.0) for _ in range(5)]
        assert strategy.compute_vpin(buckets) == 0.0

    def test_compute_vpin_one_for_all_buy(self):
        """All buy volume: VPIN = 1.0."""
        strategy = VPINToxicity()
        buckets = [_make_bucket(1000.0, 0.0) for _ in range(5)]
        assert abs(strategy.compute_vpin(buckets) - 1.0) < 1e-9

    def test_compute_vpin_empty_returns_zero(self):
        """Empty buckets: VPIN = 0.0 (safe default)."""
        strategy = VPINToxicity()
        assert strategy.compute_vpin([]) == 0.0

    def test_compute_vpin_zero_volume_bucket_skipped(self):
        """Zero-volume bucket: should not cause division by zero."""
        strategy = VPINToxicity()
        buckets = [_make_bucket(0.0, 0.0), _make_bucket(800.0, 200.0)]
        # Only non-zero bucket contributes → 600/1000 = 0.6
        vpin = strategy.compute_vpin(buckets)
        assert 0.0 <= vpin <= 1.0  # no crash, valid range


def _build_buckets_from_tuple_trades(
    trades: list[tuple], bucket_size: int = 10
) -> list[dict]:
    """Shell logic: build VPIN buckets from OKX tuple trades.

    Replicates realtime_runner.py bucket construction for isolation testing.
    trades format: list of (ts_ms, side, size, price) tuples — as returned
    by okx_ws.get_recent_trades().
    """
    buckets: list[dict] = []
    for i in range(0, len(trades), bucket_size):
        chunk = trades[i : i + bucket_size]
        if not chunk:
            continue
        buy_vol = sum(
            size * price for ts_ms, side, size, price in chunk if side == "buy"
        )
        sell_vol = sum(
            size * price for ts_ms, side, size, price in chunk if side == "sell"
        )
        buckets.append({"buy_vol": buy_vol, "sell_vol": sell_vol})
    return buckets


class TestVPINWithTupleTrades:
    """HYPO-033 runtime fix: OKX get_recent_trades() returns list[tuple].

    realtime_runner shell must unpack tuples — not call .get() dict methods.
    Tests isolate bucket construction from tuple trades (shell logic).
    """

    def _make_tuple_trade(
        self, side: str, size: float = 1.0, price: float = 100.0, ts_ms: int = 1_000_000
    ) -> tuple:
        """OKX-style tuple: (ts_ms, side, size, price)."""
        return (ts_ms, side, size, price)

    def test_bucket_from_tuple_trades_no_attribute_error(self):
        """Bucket construction from tuple trades must not raise AttributeError.

        Regression: 'tuple' object has no attribute 'get' — HYPO-033 runtime error.
        """
        trades = [
            self._make_tuple_trade("buy", size=2.0, price=50000.0),
            self._make_tuple_trade("sell", size=1.0, price=50000.0),
            self._make_tuple_trade("buy", size=3.0, price=50000.0),
        ]
        # Must not raise
        buckets = _build_buckets_from_tuple_trades(trades, bucket_size=10)
        assert isinstance(buckets, list)

    def test_bucket_buy_vol_computed_correctly_from_tuples(self):
        """buy_vol = sum(size * price) for buy-side tuple trades."""
        trades = [
            self._make_tuple_trade("buy", size=2.0, price=100.0),   # notional 200
            self._make_tuple_trade("buy", size=3.0, price=100.0),   # notional 300
            self._make_tuple_trade("sell", size=1.0, price=100.0),  # notional 100
        ]
        buckets = _build_buckets_from_tuple_trades(trades, bucket_size=10)
        assert len(buckets) == 1
        assert abs(buckets[0]["buy_vol"] - 500.0) < 1e-9
        assert abs(buckets[0]["sell_vol"] - 100.0) < 1e-9

    def test_evaluate_vpin_with_tuple_derived_buckets_enters_long(self):
        """evaluate_vpin with buckets derived from tuple trades → ENTER_LONG when buy-dominant."""
        strategy = VPINToxicity(high_toxicity_threshold=0.7, min_buckets=5)
        # 60 trades: 80% buy-side → high VPIN
        trades = []
        for i in range(48):
            trades.append((1_000_000 + i, "buy", 10.0, 100.0))   # buy notional 1000
        for i in range(12):
            trades.append((1_001_000 + i, "sell", 10.0, 100.0))  # sell notional 1000
        buckets = _build_buckets_from_tuple_trades(trades, bucket_size=10)
        tick = {"last": "100.0", "ts": "1_002_000"}
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.ENTER_LONG

    def test_evaluate_vpin_with_tuple_derived_buckets_holds_balanced(self):
        """evaluate_vpin with balanced tuple-derived buckets → HOLD.

        Trades must be interleaved buy/sell so each bucket contains equal
        buy and sell volume (not batched — batching would make each bucket
        all-one-side giving VPIN=1.0 globally).
        """
        strategy = VPINToxicity(high_toxicity_threshold=0.7, min_buckets=5)
        # 60 interleaved trades: alternating buy/sell → each bucket ~50/50 → VPIN ~ 0
        trades = []
        for i in range(60):
            side = "buy" if i % 2 == 0 else "sell"
            trades.append((1_000_000 + i, side, 5.0, 100.0))
        buckets = _build_buckets_from_tuple_trades(trades, bucket_size=10)
        tick = {"last": "100.0", "ts": "1_002_000"}
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.HOLD

    def test_tuple_trade_format_matches_okx_ws_store(self):
        """Tuple format (ts_ms, side, size, price) matches _trade_store deque format."""
        # okx_ws._handle_trade appends: (ts, side, size, price)
        trade: tuple = (1_717_000_000_000, "buy", 0.5, 62345.0)
        ts_ms, side, size, price = trade  # must unpack cleanly
        assert isinstance(ts_ms, int)
        assert side in ("buy", "sell")
        assert isinstance(size, float)
        assert isinstance(price, float)
