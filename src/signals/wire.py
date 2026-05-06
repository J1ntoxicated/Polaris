"""Signal wire — strategy-agnostic adapter from Signal → FactorInputs → calibrated confidence (F6).

Pure function (P6). No I/O, deterministic.

Usage pattern (in realtime_runner shell):
    from src.signals.wire import calibrate_signal
    cal = calibrate_signal(signal, market_ctx, win_rate=wr, n_trades=n)
    sizing = compute_size(SizingInputs(..., calibrated_confidence=cal))

market_ctx keys (all optional — missing keys fall back to neutral 0.5):
    momentum_1h   float [0, 1]  — normalized recent price momentum
    volume_ratio  float [0, 1]  — current volume vs rolling avg (capped at 1.0)
    regime        str           — "uptrend" / "downtrend" / "flat" / "crisis"
    spread_bps    float >= 0    — bid-ask spread in basis points

References:
- F6 Codex Phase 27.4 consensus (ADR pending)
- composer.py, bayesian.py for downstream calcs
"""
from __future__ import annotations

from src.signals.composer import FactorInputs, composite_score
from src.signals.bayesian import CalibrationInput, calibrate_confidence


def signal_to_factor_inputs(signal, market_ctx: dict) -> FactorInputs:
    """Strategy-agnostic adapter: Signal + market_ctx dict → FactorInputs.

    Args:
        signal: any object with `.confidence` float attribute [0, 1]
        market_ctx: optional dict with keys momentum_1h, volume_ratio, regime, spread_bps.
                    Missing keys use neutral defaults (0.5 / "flat" / 5 bps).

    Returns:
        FactorInputs with all 5 factors populated.
    """
    strength = float(signal.confidence)

    # momentum: short-term directional agreement [0, 1]; neutral = 0.5
    momentum = float(market_ctx.get("momentum_1h", 0.5))

    # volume: confirmation of the move [0, 1]; neutral = 0.5
    volume = float(market_ctx.get("volume_ratio", 0.5))

    # regime_fit: uptrend = full credit; anything else = half credit
    regime = market_ctx.get("regime", "flat")
    regime_fit = 1.0 if regime == "uptrend" else 0.5

    # microstructure: tighter spread = better quality. 0 bps → 1.0, 20 bps → 0.0
    spread_bps = float(market_ctx.get("spread_bps", 5))
    microstructure = 1.0 - min(spread_bps / 20.0, 1.0)

    # Clamp all to [0, 1] defensively (callers may pass raw values slightly outside)
    def _clamp(x: float) -> float:
        return max(0.0, min(1.0, x))

    return FactorInputs(
        strength=_clamp(strength),
        momentum=_clamp(momentum),
        volume=_clamp(volume),
        regime_fit=_clamp(regime_fit),
        microstructure=_clamp(microstructure),
    )


def calibrate_signal(
    signal,
    market_ctx: dict,
    win_rate: float,
    n_trades: int,
) -> float:
    """Full calibration pipeline: Signal → composite_score → calibrated_confidence.

    Pipeline:
        1. signal_to_factor_inputs(signal, market_ctx) → FactorInputs
        2. composite_score(factor_inputs) → score in [0, 1]
        3. calibrate_confidence(CalibrationInput(score, win_rate, n_trades)) → [0, 1]

    Args:
        signal: object with `.confidence` float [0, 1]
        market_ctx: dict with optional keys (see signal_to_factor_inputs)
        win_rate: lifetime win fraction for this strategy/ticker [0, 1]
        n_trades: number of closed trades in history (>= 0)

    Returns:
        float in [0.0, 1.0] — calibrated confidence for use in SizingInputs
    """
    factor_inputs = signal_to_factor_inputs(signal, market_ctx)
    score = composite_score(factor_inputs)
    cal_input = CalibrationInput(
        composite_score=score,
        win_rate=max(0.0, min(1.0, win_rate)),
        n_trades=max(0, n_trades),
    )
    return calibrate_confidence(cal_input)
