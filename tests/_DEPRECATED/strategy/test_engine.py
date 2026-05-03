"""Unit tests — Strategy Engine (F-N7 PR4).

Covers:
  * RegimeDetector classification (crisis on wide HY spread,
    risk_on on high F&G + tight spread)
  * RegimeDetector hysteresis (single counter-sample does not flip)
  * WeightController confidence scaling (low conf = closer to base,
    high conf = full multiplier)
  * WeightController sum-normalisation (output sums to ~100)
  * StrategyStore JSON load (tmp_path)

Scope: unit — no DB, no providers, no network. Uses a real
RegimeDetector/WeightController/StrategyStore and drives them via their
public surface.

Run: `pytest tests/strategy/test_engine.py -v`
"""
from __future__ import annotations

import json

import pytest

from invasion.strategy.engine import (
    PROVIDER_NAMES,
    Regime,
    RegimeDetector,
    StrategyStore,
    WeightController,
)


# ── RegimeDetector ──────────────────────────────────────────────────


def test_regime_detector_crisis_on_wide_hy_spread() -> None:
    """Wide HY spread (>500) + low F&G → CRISIS.

    Scoring (from engine.py):
      F&G=15 → RISK_OFF +3 + CRISIS +1.5
      hy_spread=600 → CRISIS +3
      move_index=150 → CRISIS +2
      → CRISIS total 6.5 beats RISK_OFF 3 → winner = CRISIS.
    """
    det = RegimeDetector()
    state = det.update(
        fear_greed=15, fg_delta_7d=-5,
        hy_spread=600, move_index=150,
        avg_funding=-0.02, oi_change_24h=0.05, adx=10,
    )
    assert state.regime == Regime.CRISIS
    assert 0.0 <= state.confidence <= 1.0


def test_regime_detector_risk_on_high_fg_low_spread() -> None:
    """High F&G (>75) + tight HY spread (<300) → RISK_ON."""
    det = RegimeDetector()
    state = det.update(
        fear_greed=80, fg_delta_7d=5,
        hy_spread=250, move_index=70,
        avg_funding=0.02, oi_change_24h=0.05, adx=10,
    )
    assert state.regime == Regime.RISK_ON


def test_regime_hysteresis_blocks_single_flip() -> None:
    """Hysteresis: single counter-sample does not flip regime.

    Prime with CRISIS (3 samples), then feed 1 RISK_ON — hysteresis
    requires 3 consecutive same-candidate samples to flip, so the state
    must remain CRISIS after the single RISK_ON sample.
    """
    det = RegimeDetector()
    # Prime CRISIS: adx=0 (skip the ADX branch that also feeds RISK_OFF)
    # to avoid a score tie between CRISIS and RISK_OFF. With adx=0:
    #   F&G=10  → RISK_OFF +3 + CRISIS +1.5
    #   hy=650  → CRISIS +3
    #   move=150 → CRISIS +2
    #   funding=-0.03 → RISK_OFF +1.5
    # CRISIS=6.5, RISK_OFF=4.5 → clean CRISIS winner.
    crisis_kw = dict(fear_greed=10, fg_delta_7d=-10, hy_spread=650,
                     move_index=150, avg_funding=-0.03,
                     oi_change_24h=0.05, adx=0)
    for _ in range(3):
        det.update(**crisis_kw)
    assert det.current().regime == Regime.CRISIS

    # Now try a single RISK_ON sample
    state = det.update(
        fear_greed=85, fg_delta_7d=20, hy_spread=250, move_index=65,
        avg_funding=0.02, oi_change_24h=0.05, adx=0,
    )
    # Hysteresis: state should be held as CRISIS (not flipped to RISK_ON)
    assert state.regime == Regime.CRISIS, (
        f"hysteresis must block single flip; got {state.regime}"
    )


# ── WeightController ────────────────────────────────────────────────


def test_weight_controller_confidence_scaling() -> None:
    """Confidence=0 → weights ~= base; confidence=1 → full multiplier.

    At confidence=0 the formula `1.0 + conf*(mult-1)` collapses to 1.0
    (no adjustment). At confidence=1 the full regime multiplier
    applies. We compare sentiment weight under RISK_OFF where its
    regime multiplier is 1.3.
    """
    det = RegimeDetector()
    wc = WeightController(det)
    base = {n: 10.0 for n in PROVIDER_NAMES}

    # Force state low-confidence
    from invasion.strategy.engine import RegimeState
    import time as _t

    det._state = RegimeState(Regime.RISK_OFF, confidence=0.0,
                             detected_at=_t.time())
    # Reset blended mults so blend_alpha doesn't leak from prior ticks.
    wc._active_mults = [1.0] * 10
    adj_low = wc.get_weights(base)

    det._state = RegimeState(Regime.RISK_OFF, confidence=1.0,
                             detected_at=_t.time())
    # After blend_alpha=0.3, one call moves `active_mults` only 30% to
    # target — reset so we measure the conf-1.0 branch against
    # a neutral starting point. Then iterate a few blends so the
    # multipliers approach RISK_OFF targets (sentiment→1.3).
    wc._active_mults = [1.0] * 10
    for _ in range(20):  # converge to target
        adj_high = wc.get_weights(base)

    # Both outputs are sum-normalised to 100; compare sentiment share.
    # With all 10 providers at base=10 and conf=0 the shares are equal
    # → sentiment share = 10.0.
    assert adj_low["sentiment"] == pytest.approx(10.0, abs=0.5)
    # With conf=1 and RISK_OFF (sentiment mult=1.3, taker=0.8, fg=1.5,
    # technical=0.7, etc.) sentiment gets amplified relative to taker.
    # We assert sentiment share > taker share (ordering holds).
    assert adj_high["sentiment"] > adj_high["taker"]


def test_weight_controller_sum_normalized_100() -> None:
    """Adjusted weights sum to 100 (±0.5 for rounding)."""
    det = RegimeDetector()
    wc = WeightController(det)

    from invasion.strategy.engine import RegimeState
    import time as _t

    det._state = RegimeState(Regime.CRISIS, confidence=0.8,
                             detected_at=_t.time())
    base = {
        "sentiment": 25, "funding": 25, "ls_ratio": 20, "taker": 15,
        "fear_greed": 30, "technical": 15, "liquidation": 15,
        "cross_exchange": 15, "macro_regime": 10, "institutional": 12,
    }
    adj = wc.get_weights(base)
    total = sum(adj.values())
    assert total == pytest.approx(100.0, abs=0.5), (
        f"adjusted weights must sum to 100, got {total}"
    )


# ── StrategyStore ───────────────────────────────────────────────────


def test_strategy_store_loads_json(tmp_path, monkeypatch) -> None:
    """StrategyStore loads strategy JSON files from STRATEGIES_DIR.

    We point STRATEGIES_DIR at tmp_path and drop 2 valid strategy JSONs
    plus 1 malformed file. Loader must pick up the 2 valid ones and
    swallow the bad one (logged via log_event — we don't assert logs).
    """
    # Seed 2 valid strategy JSONs
    strat_a = {
        "name": "crypto_test_contrarian",
        "id": 101,
        "status": "active",
        "thesis": "contrarian",
        "match": {"groups": ["crypto"], "direction": ["LONG", "SHORT"]},
    }
    strat_b = {
        "name": "forex_test_trend",
        "id": 202,
        "status": "active",
        "match": {"groups": ["forex"], "direction": ["LONG"]},
    }
    (tmp_path / "crypto_test_contrarian.json").write_text(json.dumps(strat_a))
    (tmp_path / "forex_test_trend.json").write_text(json.dumps(strat_b))
    # Drop a malformed file — must not crash the loader
    (tmp_path / "broken.json").write_text("{not valid json")

    # Patch STRATEGIES_DIR + stub DataStore().get_strategies() to
    # return [] so we test the filesystem branch in isolation.
    monkeypatch.setattr(
        "invasion.strategy.engine.STRATEGIES_DIR", tmp_path
    )

    class _StubStore:
        def get_strategies(self):
            return []

    monkeypatch.setattr(
        "invasion.data.store.DataStore", lambda: _StubStore()
    )

    store = StrategyStore()
    names = {s["name"] for s in store.list()}
    assert "crypto_test_contrarian" in names
    assert "forex_test_trend" in names
    # Malformed file must not produce a loaded strategy.
    assert len(names) == 2

    # match() filters by group + direction
    crypto_matches = store.match("crypto", direction="LONG")
    assert len(crypto_matches) == 1
    assert crypto_matches[0]["name"] == "crypto_test_contrarian"

    forex_matches = store.match("forex", direction="LONG")
    assert len(forex_matches) == 1
    assert forex_matches[0]["name"] == "forex_test_trend"
