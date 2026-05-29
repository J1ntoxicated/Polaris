"""Layer 3 — vol-targeting for the single T4 continuous scalar (#8).

The T4 chain keeps exactly ONE continuous scalar (anti 9-stack). This module
makes that one scalar vol-aware: instead of the fixed signal-strength ramp, the
scalar can be derived from realized volatility via inverse weighting
``target_vol / realized_vol`` (ex-ante / past-only — no look-ahead), clipped to
the SAME ``[CONT_SCALAR_MIN, CONT_SCALAR_MAX]`` band. High realized vol →
smaller scalar; low → larger. No multiplier is added to the chain.

Spec source:
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4 single scalar)
"""

from __future__ import annotations

import math

from polaris.core.sizing.schema import (
    CONT_SCALAR_MAX,
    CONT_SCALAR_MIN,
    DEFAULT_TARGET_VOL,
    EWMA_VOL_LAMBDA,
)

__all__ = ["ewma_realized_vol", "vol_targeted_scalar"]


def ewma_realized_vol(
    returns: list[float], *, lam: float = EWMA_VOL_LAMBDA
) -> float | None:
    """Ex-ante realized vol = ``sqrt(EWMA of squared returns around zero)``.

    Bias-corrected EWMA of squared returns over the PAST series (oldest →
    newest): each observation gets weight ``lam^age`` (age 0 = newest) and the
    weighted mean is normalised by the weight sum, so the most recent return is
    weighted most. Caller passes only historical returns — this function never
    sees a future value, so there is no look-ahead.

    Non-finite returns are skipped. Empty (or all-non-finite) input → ``None``
    (caller falls back to the signal-strength ramp). A single finite return
    yields ``|r|``; constant-magnitude returns yield that magnitude.
    """
    num = 0.0
    den = 0.0
    for r in returns:
        if not math.isfinite(r):
            continue
        # Decay the running accumulators, then add the newest with weight 1.
        # After the loop the newest obs has the largest effective weight.
        num = lam * num + r * r
        den = lam * den + 1.0
    if den == 0.0:
        return None
    return math.sqrt(num / den)


def vol_targeted_scalar(
    *, realized_vol: float, target_vol: float = DEFAULT_TARGET_VOL
) -> float:
    """Vol-targeted continuous scalar = clip(``target_vol / realized_vol``).

    Inverse weighting: high realized vol → smaller scalar; low → larger. The
    result is clipped to the SAME ``[CONT_SCALAR_MIN, CONT_SCALAR_MAX]`` band as
    the legacy signal-strength ramp, so the T4 chain and the 9-stack ban are
    unchanged — this is a drop-in replacement for the one scalar, not a new
    multiplier.

    Non-finite / non-positive ``realized_vol`` or ``target_vol`` → the floor
    (anti-collapse: never 0, never raises).
    """
    if (
        not math.isfinite(realized_vol)
        or not math.isfinite(target_vol)
        or realized_vol <= 0.0
        or target_vol <= 0.0
    ):
        return CONT_SCALAR_MIN
    raw = target_vol / realized_vol
    return min(CONT_SCALAR_MAX, max(CONT_SCALAR_MIN, raw))
