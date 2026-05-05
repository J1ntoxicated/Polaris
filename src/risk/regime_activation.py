"""Regime × Strategy activation matrix (Phase 11 — P6 pure).

Block strategies in mismatched regimes to prevent fee bleed:
    - TSMOM in flat market = consecutive small losses (no trend)
    - GridBot in trend = breakout buffer hits constantly (no range)
    - NFI dipbuy in uptrend = false dips (true dips need bear context)

Matrix design:
    REGIME_ACTIVATION[strategy_name] = set of allowed regimes
    Empty / missing → ALL regimes allowed (backward-compat opt-in)

Regime values (from src/risk/regime_detector.py):
    "uptrend"   — BTC SMA20 > SMA50 × 1.02 + last > SMA20
    "downtrend" — opposite
    "flat"      — default sideways
    "crisis"    — 24h drop >= 8%

Reference:
    INSIGHT-035 — TSMOM 6 positions opened simultaneously in SMA50<SMA200
    sideways condition (regime-blind entry → 4-week hold tying capital).
"""
from __future__ import annotations

ALL_REGIMES: frozenset[str] = frozenset({"uptrend", "downtrend", "flat", "crisis"})


# Per-strategy allowed regime sets. Empty = all allowed.
# Conservative defaults: only block strategies with strong regime mismatch evidence.
REGIME_ACTIVATION: dict[str, frozenset[str]] = {
    # Trend continuation — only fire in confirmed uptrend (long-only)
    "tsmom":               frozenset({"uptrend", "crisis"}),  # crisis = mean-revert spike OK

    # Range-bound — block in strong trend (breakout hits constantly)
    "grid_bot":            frozenset({"flat", "downtrend"}),  # downtrend = grid still profitable on bounces

    # Mean-reverting dip-buy — block in strong uptrend (no real dips)
    "nfi_dipbuy":          frozenset({"flat", "downtrend", "crisis"}),

    # Crisis-driven — only fire in cascade regime
    "liquidation_cascade": frozenset({"crisis", "downtrend"}),

    # Funding-driven (price regime independent) — keep all
    "funding_carry":       ALL_REGIMES,
    "funding_rate_filter": ALL_REGIMES,

    # Momentum spike — keep all (volume bursts can fire anywhere)
    "volume_burst":        ALL_REGIMES,
    "tick_burst":          ALL_REGIMES,

    # Intraday mean-revert — block in crisis (volatility unhealthy)
    "rsi_15m_intraday":    frozenset({"uptrend", "downtrend", "flat"}),
}


def is_strategy_active(strategy_name: str, regime: str) -> bool:
    """Return True if strategy is allowed to fire in current regime.

    Default permissive — strategy not in matrix is allowed everywhere.
    Pure function (P6).
    """
    allowed = REGIME_ACTIVATION.get(strategy_name)
    if allowed is None or not allowed:
        return True
    return regime in allowed


def block_reason(strategy_name: str, regime: str) -> str:
    """Human-readable reason when blocked. Returns empty string if active."""
    if is_strategy_active(strategy_name, regime):
        return ""
    allowed = REGIME_ACTIVATION.get(strategy_name, ALL_REGIMES)
    return (
        f"regime_block strategy={strategy_name} regime={regime} "
        f"allowed={sorted(allowed)}"
    )
