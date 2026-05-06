"""Tests for src/signals/wire.py — signal_to_factor_inputs + calibrate_signal (F6).

TDD: tests cover:
- signal_to_factor_inputs field mapping + clamping
- calibrate_signal end-to-end pipeline
- edge cases: missing keys, boundary values
- property-based: output always in [0, 1]
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.signals.wire import calibrate_signal, signal_to_factor_inputs
from src.signals.composer import FactorInputs


# ── minimal signal stub ─────────────────────────────────────────────────────

class _Sig:
    """Minimal signal stub with .confidence attribute."""
    def __init__(self, confidence: float) -> None:
        self.confidence = confidence


# ── signal_to_factor_inputs ─────────────────────────────────────────────────

class TestSignalToFactorInputs:
    def test_confidence_maps_to_strength(self):
        fi = signal_to_factor_inputs(_Sig(0.75), {})
        assert fi.strength == pytest.approx(0.75)

    def test_missing_keys_use_neutral_defaults(self):
        fi = signal_to_factor_inputs(_Sig(0.5), {})
        assert fi.momentum == pytest.approx(0.5)
        assert fi.volume == pytest.approx(0.5)
        # regime=flat → regime_fit=0.5
        assert fi.regime_fit == pytest.approx(0.5)
        # spread_bps=5 → microstructure = 1 - 5/20 = 0.75
        assert fi.microstructure == pytest.approx(0.75)

    def test_uptrend_gives_full_regime_fit(self):
        fi = signal_to_factor_inputs(_Sig(0.5), {"regime": "uptrend"})
        assert fi.regime_fit == pytest.approx(1.0)

    def test_non_uptrend_gives_half_regime_fit(self):
        for r in ("downtrend", "flat", "crisis", "unknown"):
            fi = signal_to_factor_inputs(_Sig(0.5), {"regime": r})
            assert fi.regime_fit == pytest.approx(0.5), f"regime={r}"

    def test_zero_spread_gives_max_microstructure(self):
        fi = signal_to_factor_inputs(_Sig(0.5), {"spread_bps": 0.0})
        assert fi.microstructure == pytest.approx(1.0)

    def test_large_spread_clamps_microstructure_to_zero(self):
        # spread_bps=20 → 1 - 20/20 = 0; spread_bps>20 → clamped to 0
        fi = signal_to_factor_inputs(_Sig(0.5), {"spread_bps": 25.0})
        assert fi.microstructure == pytest.approx(0.0)

    def test_extreme_confidence_clamped(self):
        # confidence=1.5 → clamped to 1.0
        fi = signal_to_factor_inputs(_Sig(1.5), {})
        assert fi.strength == pytest.approx(1.0)

    def test_all_factors_in_unit_interval(self):
        fi = signal_to_factor_inputs(_Sig(0.6), {
            "regime": "uptrend",
            "momentum_1h": 0.8,
            "volume_ratio": 0.6,
            "spread_bps": 3.0,
        })
        for name in ("strength", "momentum", "volume", "regime_fit", "microstructure"):
            val = getattr(fi, name)
            assert 0.0 <= val <= 1.0, f"{name}={val} out of [0,1]"

    def test_returns_factor_inputs_dataclass(self):
        result = signal_to_factor_inputs(_Sig(0.5), {})
        assert isinstance(result, FactorInputs)


# ── calibrate_signal end-to-end ─────────────────────────────────────────────

class TestCalibrateSignal:
    def test_output_in_unit_interval(self):
        cal = calibrate_signal(_Sig(0.8), {}, win_rate=0.6, n_trades=50)
        assert 0.0 <= cal <= 1.0

    def test_cold_start_dominated_by_composite_score(self):
        # n_trades=0 → evidence_weight=0 → calibrated = composite_score only
        cal = calibrate_signal(_Sig(0.9), {"regime": "uptrend"}, win_rate=0.3, n_trades=0)
        # composite_score with strong signal will be high; result should be moderate-high
        assert cal > 0.4

    def test_high_confidence_high_win_rate_gives_high_cal(self):
        cal = calibrate_signal(_Sig(0.9), {"regime": "uptrend"}, win_rate=0.8, n_trades=100)
        assert cal >= 0.6

    def test_low_confidence_suppressed(self):
        cal_low = calibrate_signal(_Sig(0.3), {}, win_rate=0.5, n_trades=50)
        cal_high = calibrate_signal(_Sig(0.9), {}, win_rate=0.5, n_trades=50)
        assert cal_high > cal_low

    def test_pure_function_same_input_same_output(self):
        sig = _Sig(0.7)
        ctx = {"regime": "flat", "spread_bps": 4.0}
        r1 = calibrate_signal(sig, ctx, win_rate=0.55, n_trades=30)
        r2 = calibrate_signal(sig, ctx, win_rate=0.55, n_trades=30)
        assert r1 == r2

    def test_negative_n_trades_guarded(self):
        # negative n_trades must not raise — clamped to 0
        cal = calibrate_signal(_Sig(0.5), {}, win_rate=0.5, n_trades=-5)
        assert 0.0 <= cal <= 1.0

    def test_win_rate_out_of_range_clamped(self):
        # win_rate=1.5 should not raise (clamped to 1.0 before CalibrationInput)
        cal = calibrate_signal(_Sig(0.5), {}, win_rate=1.5, n_trades=10)
        assert 0.0 <= cal <= 1.0


# ── property-based (P7) ─────────────────────────────────────────────────────

@given(
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    momentum=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    volume=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    spread_bps=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    regime=st.sampled_from(["uptrend", "downtrend", "flat", "crisis"]),
    win_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    n_trades=st.integers(min_value=0, max_value=1000),
)
@settings(max_examples=300)
def test_property_calibrate_signal_always_in_unit_interval(
    confidence, momentum, volume, spread_bps, regime, win_rate, n_trades
):
    """Property: calibrate_signal always returns float in [0, 1]."""
    sig = _Sig(confidence)
    ctx = {
        "momentum_1h": momentum,
        "volume_ratio": volume,
        "spread_bps": spread_bps,
        "regime": regime,
    }
    cal = calibrate_signal(sig, ctx, win_rate=win_rate, n_trades=n_trades)
    assert 0.0 <= cal <= 1.0, f"cal={cal} out of [0,1] for conf={confidence}"


@given(
    strength=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    spread_bps=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    momentum=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    volume=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=200)
def test_property_factor_inputs_always_valid(strength, spread_bps, momentum, volume):
    """Property: signal_to_factor_inputs always produces valid FactorInputs."""
    fi = signal_to_factor_inputs(_Sig(strength), {
        "spread_bps": spread_bps,
        "momentum_1h": momentum,
        "volume_ratio": volume,
    })
    for name in ("strength", "momentum", "volume", "regime_fit", "microstructure"):
        val = getattr(fi, name)
        assert val is not None
        assert 0.0 <= val <= 1.0, f"{name}={val} out of [0,1]"
