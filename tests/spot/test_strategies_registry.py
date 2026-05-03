"""Strategy registry + ABC contract — ADR-007 Phase α."""
import time

import numpy as np
import pytest

from invasion.spot.strategies import _registry as reg
from invasion.spot.strategies.base import (
    SignalResult,
    Strategy,
    StrategyContext,
)


def _candles(n: int, slope: float = 0.0, base: float = 100.0,
              noise: float = 0.2, seed: int = 1) -> list[dict]:
    rng = np.random.default_rng(seed)
    closes = base + np.cumsum(rng.normal(slope, noise, n))
    out = []
    for i, p in enumerate(closes):
        p = float(p)
        out.append({"ts": i * 300, "open": p - 0.05, "high": p + 0.15,
                    "low": p - 0.15, "close": p,
                    "volume": float(1500 + rng.normal(0, 100))})
    return out


def test_registry_autoloads_three_crypto_strategies():
    ids = {s.id for s in reg.all_strategies()}
    assert {"bb_break_momentum", "vol_compression", "macd_cross"} <= ids


def test_evaluate_all_returns_score_sorted_capped(monkeypatch):
    reg.reset_cooldowns()
    candles = _candles(80, slope=0.05, seed=42)
    ctx = StrategyContext(
        ticker="BTC", inst_id="BTC-USDT", tier="major",
        candles_5m=candles, last_px=candles[-1]["close"],
        regime="neutral", session="asia",
    )
    fired = reg.evaluate_all(ctx)
    # All scores in [0,1]
    for _, r in fired:
        assert 0.0 <= r.score <= 1.0
    # Sorted descending
    scores = [r.score for _, r in fired]
    assert scores == sorted(scores, reverse=True)


def test_cooldown_blocks_repeat_fires():
    reg.reset_cooldowns()
    candles = _candles(80, slope=0.05, seed=99)
    ctx = StrategyContext(
        ticker="ETH", inst_id="ETH-USDT", tier="major",
        candles_5m=candles, last_px=candles[-1]["close"],
        regime="neutral", session="asia",
    )
    first = reg.evaluate_all(ctx)
    if not first:
        pytest.skip("synthetic candles failed to fire any strategy")
    # Mark them all fired
    for s, _ in first:
        reg.mark_fired("ETH", s.id)
    # Same context — all blocked by cooldown
    second = reg.evaluate_all(ctx)
    fired_ids_first = {s.id for s, _ in first}
    fired_ids_second = {s.id for s, _ in second}
    assert fired_ids_first.isdisjoint(fired_ids_second)


def test_crisis_regime_blocks_long(monkeypatch):
    reg.reset_cooldowns()
    candles = _candles(80, slope=0.05, seed=7)
    ctx = StrategyContext(
        ticker="BTC", inst_id="BTC-USDT", tier="major",
        candles_5m=candles, last_px=candles[-1]["close"],
        regime="crisis_high", session="asia",
    )
    fired = reg.evaluate_all(ctx)
    assert fired == []   # all long strategies blocked at .applicable()


def test_signal_result_reject_helper():
    r = SignalResult.reject("bad", x=1)
    assert r.enter is False
    assert r.reason == "bad"
    assert r.debug == {"x": 1}
