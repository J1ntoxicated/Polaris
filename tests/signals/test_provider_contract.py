"""T2-2 PR2 — per-provider SignalContract edge_prob unit tests.

Covers the 7 core providers migrated in PR2:
  1. TechnicalSignal         (providers.py)
  2. SentimentSignal         (providers.py)
  3. FearGreedSignal         (providers.py)
  4. FundingSignal           (providers.py)
  5. LSRatioSignal           (providers.py)
  6. TakerSignal             (providers.py)
  7. OrderFlowImbalanceSignal (providers_microstructure.py)
  8. VolatilitySignal        (providers_technical.py)

Each test verifies:
  * result.contract is a SignalContract (not neutral() when signal fires)
  * edge_prob stays inside the documented band
  * reversal_horizon_sec matches provider-specific spec
  * execution_risk_bps is asset-group-aware (crypto baseline)
  * direction_certainty = |score|/100 within [0, 1]
  * confidence field is preserved (legacy; PR5 removes)
"""
from __future__ import annotations

import pytest

from invasion.signals.contract import SignalContract
from invasion.signals.providers import (
    FearGreedSignal,
    FundingSignal,
    LSRatioSignal,
    SentimentSignal,
    TakerSignal,
    TechnicalSignal,
)
from invasion.signals.providers_microstructure import OrderFlowImbalanceSignal
from invasion.signals.providers_technical import VolatilitySignal


# ── Sentiment: contrarian extremity ─────────────────────────────────


def test_sentiment_extreme_long_contract():
    s = SentimentSignal()
    # Crowd 90% long → we short. Extremity = 0.8 → edge ≈ 0.74
    r = s.compute("BTC", market_data={"sentiment_pct": 90})
    assert isinstance(r.contract, SignalContract)
    assert 0.2 <= r.contract.edge_prob <= 0.8
    assert r.contract.edge_prob > 0.6  # extreme crowd long
    assert r.contract.reversal_horizon_sec == 3600
    assert r.contract.execution_risk_bps == 5.0  # crypto baseline
    assert 0.0 <= r.contract.direction_certainty <= 1.0


def test_sentiment_neutral_50pct_low_edge():
    r = SentimentSignal().compute("BTC", market_data={"sentiment_pct": 50})
    # Extremity 0 → edge_prob = 0.5
    assert r.contract.edge_prob == pytest.approx(0.5, abs=0.05)


# ── Funding: z-score magnitude → tanh ───────────────────────────────


def test_funding_extreme_rate_high_edge():
    # rate 0.005 (0.5%) → z_proxy = 50, tanh(25) ≈ 1.0 → edge = 0.85
    r = FundingSignal().compute("BTC", market_data={"funding_rate": 0.005})
    assert 0.5 <= r.contract.edge_prob <= 0.85
    assert r.contract.edge_prob > 0.8
    assert r.contract.reversal_horizon_sec == 14400


def test_funding_zero_rate_neutral_contract():
    # rate=0 returns early → default neutral contract
    r = FundingSignal().compute("BTC", market_data={"funding_rate": 0})
    assert r.contract == SignalContract.neutral()


# ── LSRatio: extremity tanh ─────────────────────────────────────────


def test_lsratio_extreme_crowd_long():
    r = LSRatioSignal().compute("BTC", market_data={"ls_ratio": 2.5})
    assert 0.2 <= r.contract.edge_prob <= 0.8
    assert r.contract.edge_prob > 0.65
    assert r.contract.reversal_horizon_sec == 7200


def test_lsratio_balanced():
    r = LSRatioSignal().compute("BTC", market_data={"ls_ratio": 1.0})
    # ratio 1.0 → extremity 0 → edge = 0.5; but conf 0 → zero path.
    # Provider emits score=0 via the (1.0 - 1.0)*50 branch BUT returns
    # early only on `not ratio`. So check ratio=1.0 → score=0 → contract
    # built with edge_prob ≈ 0.5
    assert r.contract.edge_prob == pytest.approx(0.5, abs=0.05)


# ── Taker: skew tanh ────────────────────────────────────────────────


def test_taker_buy_heavy_skew():
    # ratio = 2.0 → skew 1.0 → tanh(1.0) ≈ 0.76 → edge ≈ 0.77
    r = TakerSignal().compute(
        "BTC", market_data={"taker_buy": 200, "taker_sell": 100}
    )
    assert 0.2 <= r.contract.edge_prob <= 0.8
    assert r.contract.edge_prob > 0.65
    assert r.contract.reversal_horizon_sec == 1800


def test_taker_missing_returns_neutral():
    r = TakerSignal().compute("BTC", market_data={})
    assert r.contract == SignalContract.neutral()


# ── FearGreed: contrarian extremity ─────────────────────────────────


def test_fearGreed_extreme_fear_high_edge():
    r = FearGreedSignal().compute("BTC", market_data={"fear_greed": 10})
    assert r.contract.edge_prob > 0.6  # extremity 0.8 → 0.74
    assert r.contract.reversal_horizon_sec == 3600


def test_fearGreed_neutral():
    r = FearGreedSignal().compute("BTC", market_data={"fear_greed": 50})
    assert r.contract.edge_prob == pytest.approx(0.5, abs=0.02)


def test_fearGreed_missing_returns_neutral_contract():
    # missing FG → early return with default (neutral) contract
    r = FearGreedSignal().compute("BTC", market_data={})
    assert r.contract == SignalContract.neutral()


# ── Technical: multi-TF consensus ───────────────────────────────────


def _tech_ticker_with(rsi_val=30, rsi_tf_vals=None, ema_tf=None, adx_val=10):
    """Build a TechnicalSignal with _tech_cache pre-populated."""
    prov = TechnicalSignal()
    cache = {
        "rsi": rsi_val,
        "bb_pos": 50,
        "macd": 0,
        "price": 50000,
        "ema9v21": "",
        "stoch_rsi": 50,
        "adx": adx_val,
    }
    if rsi_tf_vals:
        for tf, v in rsi_tf_vals.items():
            cache[f"rsi_{tf}"] = v
    if ema_tf:
        for tf, v in ema_tf.items():
            cache[f"ema_cross_{tf}"] = v
    prov._tech_cache = {"BTC": cache}
    return prov


def test_technical_all_tf_agree_high_edge():
    # All 3 TFs (1h/4h/1d) agree → consensus 1.0 → edge = 0.7 (clamp)
    prov = _tech_ticker_with(
        rsi_val=25,
        rsi_tf_vals={"1h": 25, "4h": 25, "1d": 25},
        ema_tf={"1h": "BEAR", "4h": "BEAR", "1d": "BEAR"},
    )
    r = prov.compute("BTC")
    assert 0.3 <= r.contract.edge_prob <= 0.7
    assert r.contract.edge_prob == pytest.approx(0.7, abs=0.02)
    assert r.contract.reversal_horizon_sec == 900


def test_technical_mixed_tf_skips_but_defaults_neutral():
    # Force "mixed" MTF branch (skip return) → default neutral contract
    prov = _tech_ticker_with(
        rsi_val=50,
        rsi_tf_vals={"1h": 60, "4h": 40, "1d": 50},
        ema_tf={"1h": "BULL", "4h": "BEAR", "1d": ""},
    )
    r = prov.compute("BTC")
    # early-return path keeps neutral contract
    assert r.contract == SignalContract.neutral()


# ── OrderFlowImbalance: skew tanh ───────────────────────────────────


def test_order_flow_heavy_buy_skew():
    r = OrderFlowImbalanceSignal().compute(
        "BTC", market_data={"taker_buy": 800, "taker_sell": 200}
    )
    # imbalance = 0.6 → tanh(0.6) ≈ 0.537 → edge ≈ 0.69
    assert 0.2 <= r.contract.edge_prob <= 0.8
    assert r.contract.edge_prob > 0.6
    assert r.contract.reversal_horizon_sec == 900
    # Microstructure bump: exec_risk ≥ 8 bps even on crypto baseline
    assert r.contract.execution_risk_bps >= 8.0


def test_order_flow_no_data_neutral():
    r = OrderFlowImbalanceSignal().compute("BTC", market_data={})
    assert r.contract == SignalContract.neutral()


# ── Volatility: inverse vol conservative edge ───────────────────────


def test_volatility_high_vol_conservative_edge():
    # atr_pct 2.0 → vol_z ≈ 4 → edge_prob ≈ 0.55
    r = VolatilitySignal().compute("BTC", market_data={"atr_pct": 2.0})
    assert 0.5 <= r.contract.edge_prob <= 0.75
    assert r.contract.reversal_horizon_sec == 1800
    # Exec risk scales with vol
    assert r.contract.execution_risk_bps >= 10.0


def test_volatility_low_vol_higher_edge():
    # atr_pct 0.05 → vol_z = 0.1 → edge ≈ 0.73
    r = VolatilitySignal().compute("BTC", market_data={"atr_pct": 0.05})
    assert r.contract.edge_prob > 0.65


def test_volatility_missing_data_neutral():
    r = VolatilitySignal().compute("BTC", market_data={})
    assert r.contract == SignalContract.neutral()


# ── Invariant: edge_prob ∈ [0, 1] across all providers ──────────────


def test_all_providers_edge_prob_bounded():
    """Smoke: no provider emits edge_prob outside [0, 1]."""
    cases = [
        (SentimentSignal(), {"sentiment_pct": 95}),
        (SentimentSignal(), {"sentiment_pct": 5}),
        (FundingSignal(), {"funding_rate": 0.01}),
        (FundingSignal(), {"funding_rate": -0.01}),
        (LSRatioSignal(), {"ls_ratio": 5.0}),
        (LSRatioSignal(), {"ls_ratio": 0.1}),
        (TakerSignal(), {"taker_buy": 900, "taker_sell": 100}),
        (FearGreedSignal(), {"fear_greed": 5}),
        (FearGreedSignal(), {"fear_greed": 95}),
        (OrderFlowImbalanceSignal(), {"taker_buy": 1, "taker_sell": 999}),
        (VolatilitySignal(), {"atr_pct": 5.0}),
    ]
    for prov, md in cases:
        r = prov.compute("BTC", market_data=md)
        assert 0.0 <= r.contract.edge_prob <= 1.0, (
            f"{prov.name}: edge_prob={r.contract.edge_prob} out of range"
        )
        assert 0.0 <= r.contract.direction_certainty <= 1.0
        assert r.contract.execution_risk_bps >= 0.0


def test_confidence_field_preserved_legacy():
    """PR2: legacy `confidence` field still populated (removed in PR5)."""
    r = SentimentSignal().compute("BTC", market_data={"sentiment_pct": 90})
    assert hasattr(r, "confidence")
    assert 0.0 <= r.confidence <= 1.0
