"""Dynamic position sizing — Kelly + confidence + regime + drawdown.

모태 (auto_invasion_mk1) AI sizing 패턴 부활 (Phase 2j):
- Kelly criterion (Thorp 1969, basic): f = (bp - q) / b
- Confidence multiplier (signal.confidence² — 0.7→0.49, 0.9→0.81)
- Regime mult (북극성 crisis escalation: fear → max bet)
- Drawdown circuit breaker (loss streak → size auto cut)

Pure function (P6). I/O는 caller (realtime_runner shell).

References:
- INSIGHT-032: Phase 2j AI sizing 부활
- ADR-007: paper sizing baseline
- ADR-010: risk management caps
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingInputs:
    """All inputs required for dynamic sizing computation."""

    cash_usd: float
    signal_confidence: float        # 0.0-1.0 (Signal.confidence)
    recent_win_rate: float          # last N trades win count / N
    recent_avg_win_pct: float       # avg gross % per win (e.g. 0.8 = 0.8%)
    recent_avg_loss_pct: float      # avg gross % per loss (positive, e.g. 0.5 = 0.5%)
    regime: str                     # "crisis" / "uptrend" / "flat" / "downtrend"
    drawdown_pct: float
    # Phase 27: optional calibrated_confidence from bayesian.calibrate_confidence().
    # When provided, replaces signal_confidence in conf² multiplier.
    # Caller sets this when CompositeScorer + bayesian calibration is available.
    calibrated_confidence: float | None = None
    """drawdown_pct: rolling peak-to-trough drawdown ratio (0.0-1.0).

    Rolling window: account 시작 또는 last 30d high 기준.
    NOT daily PnL (그건 ADR-010 daily 5% 한도 별도 layer).
    Caller (runner) 가 (peak_equity - current_equity) / peak_equity 로 계산해 전달.
    예: current_equity=$4,700, peak_equity=$5,000 → drawdown_pct=0.06 (6%).
    """


@dataclass(frozen=True)
class SizingOutput:
    """Result of dynamic sizing computation."""

    size_usd: float
    fraction: float                 # fraction of cash used (0.0-MAX_FRACTION)
    reason: str                     # decomposition: Kelly × conf² × regime × dd


# Regime multipliers — North Star: crisis = max bet (fear = opportunity)
REGIME_MULT: dict[str, float] = {
    "crisis": 1.5,
    "uptrend": 1.0,
    "flat": 0.7,
    "downtrend": 0.3,
}

MAX_FRACTION = 0.20     # hard cap — prevents over-betting even at full Kelly
# Phase 2N: MIN_SIZE_USD $50 → $100 (Jin mandate — small size skip ↑, fee efficiency ↑).
# Previously $50: ~0.28% fee on $50 = $0.14, needs 0.56% move to break even (high noise).
# At $100: same fee overhead but 2× trade value → better signal-to-noise per trade.
MIN_SIZE_USD = 100.0    # below this → skip signal (fee drag too high)
# NOTE (Phase 2N): PerformanceTracker cold_start defaults updated.
# Previous: win=0.5, avg_win=0.6%, avg_loss=0.5% → Kelly=0.083
# Updated:  win=0.55, avg_win=0.8%, avg_loss=0.5% → Kelly=0.225 (2.7x)
# _KELLY_COLD_START is only reached when caller explicitly passes win_rate=0/avg_loss=0
# (e.g. unit tests or zero-trade bootstrap). Under normal cold-start tracker returns 0.225.
_KELLY_COLD_START = 0.05  # baseline for subnormal-float / zero-history guard
_KELLY_HALF_CAP = 0.5     # half-Kelly cap (full Kelly too volatile)

# Phase 27: Auto-scaling cold start (Jin mandate "위험해도 먹고 나와").
# Default 300 (legacy safety) — production sets POLARIS_COLD_START_MAX_USD=0
# to disable cap and let Kelly+confidence² do dynamic sizing.
COLD_START_N = 20
import os as _os
COLD_START_MAX_USD = float(_os.environ.get("POLARIS_COLD_START_MAX_USD", "300"))


def compute_size(inputs: SizingInputs, n_trades: int = 0) -> SizingOutput:
    """Aggressive sizing — single confidence×regime scaler, no Kelly stack.

    Phase 27.5 simplification (Jin mandate "위험해도 먹고 나와"):
        size = cash × MAX_FRACTION × confidence × regime × dd_floor

    Removed (defensive over-engineering that compounded to near-zero):
        - Kelly criterion (risk-aversion math, ≤0.5 always)
        - conf³ cube (sub-1 values shrink on cube)
        - Cold-start cap (zeroed all entries first 20 trades)
        - Min-size skip (signal noise filter is upstream)

    confidence = calibrated if available else signal_confidence (linear, not squared/cubed).
    Hard ceiling at MAX_FRACTION (20% cash) + per-order live cap (caller's POLARIS_LIVE_MAX_USD).
    """
    conf = inputs.calibrated_confidence if inputs.calibrated_confidence is not None else inputs.signal_confidence
    conf = max(0.0, min(1.0, conf))

    regime_mult = REGIME_MULT.get(inputs.regime, REGIME_MULT["flat"])
    dd_mult = max(0.5, 1.0 - inputs.drawdown_pct)  # -50% dd → 0.5 floor

    fraction = MAX_FRACTION * conf * regime_mult * dd_mult
    fraction = min(MAX_FRACTION, fraction)

    size_usd = inputs.cash_usd * fraction
    _conf_tag = "cal_conf" if inputs.calibrated_confidence is not None else "conf"
    reason = (
        f"cash×MAX({MAX_FRACTION:.2f})×{_conf_tag}({conf:.2f})"
        f"×regime[{inputs.regime}]({regime_mult:.1f})×dd({dd_mult:.2f})={fraction:.3f}"
    )
    return SizingOutput(size_usd=size_usd, fraction=fraction, reason=reason)
