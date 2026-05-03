"""F-N7 PR5 — MockSignalAugmenter / MockExitAdviser unit tests (3).

Covers `invasion/ai/mocks.py`:
  5. test_mock_augmenter_passthrough
  6. test_mock_exit_adviser_kill_on_losing_old
  7. test_mock_exit_adviser_tighten_on_giveback
"""
from __future__ import annotations

from invasion.ai.base import AugmentedSignal, ExitDecision, SignalContext
from invasion.ai.mocks import MockExitAdviser, MockSignalAugmenter


def _ctx(score: float = 42.0) -> SignalContext:
    return SignalContext(
        ticker="BTC", group="crypto", tier="mid",
        direction="BUY", composite_score=score,
        agreement=0.8, factors=3,
        provider_scores={"tech": score},
        regime="neutral",
    )


# ── 5. Augmenter pass-through ────────────────────────────────────────


def test_mock_augmenter_passthrough():
    """MockSignalAugmenter returns original score unchanged + 0.5 confidence."""
    aug = MockSignalAugmenter()
    ctx = _ctx(score=42.0)
    out = aug.augment(ctx)
    assert isinstance(out, AugmentedSignal)
    assert out.original_score == 42.0
    assert out.adjusted_score == 42.0
    assert out.confidence == 0.5
    assert out.skip is False
    assert out.flags == []


# ── 6. Exit adviser KILL rule ────────────────────────────────────────


def test_mock_exit_adviser_kill_on_losing_old():
    """KILL when pnl < -1.5, age > 10min, max_pnl < 0.15."""
    adv = MockExitAdviser()
    positions = [{
        "ticker": "BTC",
        "direction": "long",
        "pnl_pct": -2.5,
        "age_seconds": 15 * 60,   # 15min
        "max_profit_pct": 0.1,     # edge gone
    }]
    decisions = adv.advise(positions, trigger="PERIODIC", market_context={})
    assert len(decisions) == 1
    d = decisions[0]
    assert isinstance(d, ExitDecision)
    assert d.action == "KILL"
    assert d.confidence == 7
    assert "losing" in d.reason.lower()


# ── 7. Exit adviser TIGHTEN rule ─────────────────────────────────────


def test_mock_exit_adviser_tighten_on_giveback():
    """TIGHTEN when max_pnl > 1% and now < 30% of peak (e.g., peak 3%, now 0.5%)."""
    adv = MockExitAdviser()
    positions = [{
        "ticker": "ETH",
        "direction": "long",
        "pnl_pct": 0.5,             # 0.5% < 30% of 3.0 (= 0.9)
        "age_seconds": 5 * 60,
        "max_profit_pct": 3.0,
    }]
    decisions = adv.advise(positions, trigger="PERIODIC", market_context={})
    assert len(decisions) == 1
    d = decisions[0]
    assert d.action == "TIGHTEN"
    assert d.confidence == 6
    # tighten_to should be pnl + 0.15 = 0.65
    assert d.tighten_to is not None
    assert abs(d.tighten_to - 0.65) < 1e-9
    assert "peak" in d.reason.lower()
