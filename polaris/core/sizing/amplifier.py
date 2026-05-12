"""Layer 3 — Tier amplifier (3-win 1.5×, 5-win 2.0×, 8+win 3.0×, 1-loss reset).

Spec source:
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (Tier Amplifier Trigger Gate)

Trigger rules (P0):
- ``win_streak >= 8`` AND n>=10 AND hit≥0.70 → 3.0×
- ``win_streak >= 5`` AND n>=10 AND hit≥0.70 → 2.0×
- ``win_streak >= 3`` AND ((n in 8-9 AND hit≥0.75) OR (n>=10 AND hit≥0.70)) → 1.5×
- otherwise → 1.0× (no amplification)
- Any 1 loss → reset (caller updates state).

Pure function — caller carries `strategy_risk_state` (n, win_streak, hit_rate_10).
"""

from __future__ import annotations

from polaris.core.sizing.schema import (
    TIER_3WIN_AMP,
    TIER_3WIN_HIT_HIGH,
    TIER_3WIN_HIT_LOW,
    TIER_3WIN_MIN_N_HIGH,
    TIER_3WIN_MIN_N_LOW,
    TIER_5WIN_AMP,
    TIER_5WIN_HIT,
    TIER_5WIN_MIN_N,
    TIER_8WIN_AMP,
    TIER_8WIN_HIT,
    TIER_8WIN_MIN_N,
    TIER_RESET_AMP,
)

__all__ = ["resolve_tier_amplifier"]


def resolve_tier_amplifier(
    *,
    win_streak: int,
    n_closed: int,
    hit_rate_10: float,
) -> float:
    """Resolve current tier amplifier per ADR-005 Trigger Gate table.

    Returns one of: 1.0 (no amp), 1.5, 2.0, 3.0.
    """
    if win_streak >= 8 and n_closed >= TIER_8WIN_MIN_N and hit_rate_10 >= TIER_8WIN_HIT:
        return TIER_8WIN_AMP
    if win_streak >= 5 and n_closed >= TIER_5WIN_MIN_N and hit_rate_10 >= TIER_5WIN_HIT:
        return TIER_5WIN_AMP
    if win_streak >= 3:
        # 3-win gate: n=8-9 needs ≥75%, n>=10 needs ≥70%.
        if n_closed >= TIER_3WIN_MIN_N_HIGH and hit_rate_10 >= TIER_3WIN_HIT_HIGH:
            return TIER_3WIN_AMP
        if (
            TIER_3WIN_MIN_N_LOW <= n_closed < TIER_3WIN_MIN_N_HIGH
            and hit_rate_10 >= TIER_3WIN_HIT_LOW
        ):
            return TIER_3WIN_AMP
    return TIER_RESET_AMP
