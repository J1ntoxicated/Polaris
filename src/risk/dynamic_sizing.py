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
MIN_SIZE_USD = 50.0     # below this → skip signal (fee drag too high)
# NOTE (Codex Round 17): PerformanceTracker cold_start defaults
# (win_rate=0.5, avg_win=0.6%, avg_loss=0.5%) → Kelly = (1.2×0.5-0.5)/1.2 = 0.083 > _EPS,
# so tracker output never triggers the _EPS guard below under normal operation.
# _KELLY_COLD_START is only reached when caller explicitly passes win_rate=0 / avg_loss=0
# (e.g. unit tests or zero-trade bootstrap). Tracker rewrite should add explicit cold_start
# path rather than relying on _EPS guard.
_KELLY_COLD_START = 0.05  # baseline for subnormal-float / zero-history guard
_KELLY_HALF_CAP = 0.5     # half-Kelly cap (full Kelly too volatile)


def compute_size(inputs: SizingInputs) -> SizingOutput:
    """Compute dynamic position size.

    Pipeline:
        1. Kelly fraction (recent performance)
        2. Confidence multiplier (signal.confidence²)
        3. Regime multiplier (market context)
        4. Drawdown circuit breaker
        5. Hard cap at MAX_FRACTION
        6. Min size check — return (0, 0) if below MIN_SIZE_USD

    Args:
        inputs: SizingInputs with all required parameters.

    Returns:
        SizingOutput with size_usd=0 if signal should be skipped.
    """
    # 1. Kelly fraction (recent performance basis)
    # Minimum meaningful threshold — guards against subnormal floats (e.g. 5e-324)
    # that pass > 0 but produce inf/nan in Kelly division.
    _EPS = 1e-9
    if (inputs.recent_avg_loss_pct < _EPS
            or inputs.recent_win_rate < _EPS
            or inputs.recent_avg_win_pct < _EPS):
        # Cold start: insufficient data — use conservative baseline
        kelly = _KELLY_COLD_START
    else:
        # Kelly formula: f* = (b*p - q) / b  where b = win/loss ratio, p = win prob
        b = inputs.recent_avg_win_pct / inputs.recent_avg_loss_pct
        p = inputs.recent_win_rate
        q = 1.0 - p
        # b > 0 guaranteed (both >= _EPS checked above)
        kelly_raw = (b * p - q) / b
        # Clamp to [0, half-Kelly] — full Kelly is too aggressive in practice
        if not (kelly_raw == kelly_raw):  # NaN guard
            kelly_raw = 0.0
        kelly = max(0.0, min(kelly_raw, _KELLY_HALF_CAP))

    # 2. Confidence multiplier — squares penalise low confidence hard
    conf_mult = inputs.signal_confidence ** 2

    # 3. Regime multiplier — default to flat if unknown
    regime_mult = REGIME_MULT.get(inputs.regime, REGIME_MULT["flat"])

    # 4. Drawdown circuit breaker
    # -10% dd → 0.8x, -25% dd → 0.5x, -40%+ → floor 0.2x
    dd_mult = max(0.2, 1.0 - inputs.drawdown_pct * 2)

    # 5. Combine and cap
    fraction = kelly * conf_mult * regime_mult * dd_mult
    fraction = min(MAX_FRACTION, fraction)

    # 6. Compute USD size and enforce minimum
    size_usd = inputs.cash_usd * fraction
    if size_usd < MIN_SIZE_USD:
        size_usd = 0.0
        fraction = 0.0

    reason = (
        f"kelly={kelly:.2f} × conf²={conf_mult:.2f} × regime[{inputs.regime}]={regime_mult:.1f} "
        f"× dd[{inputs.drawdown_pct:.1%}]={dd_mult:.2f} = {fraction:.3f}"
    )
    return SizingOutput(size_usd=size_usd, fraction=fraction, reason=reason)
