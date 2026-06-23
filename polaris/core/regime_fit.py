"""Regime-fit — bidirectional conviction shaping (NEVER a tradeable yes/no gate).

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack collapse permanently blocked.

The "buying-something-to-immediately-sell" chop churn is fixed by making the
signal layer *regime-aware* — with PRECISION, not blocking:

  * :func:`regime_fit` maps ``(signal_family, regime)`` → ``[-1, +1]`` via a
    deterministic lookup. ``momentum × chop = -1`` (the churn case);
    ``reversion × chop = +1`` (the mean-reversion edge).
  * :func:`regime_scalar` folds the fit into the ONE T4 continuous scalar
    (``1.0 + fit·0.5`` clamped to ``[0.75, 1.5]``). Good fit BOOSTS (≤1.5×),
    bad fit shrinks to the 0.75 FLOOR — never 0 (anti-collapse), never a new
    multiplier (the 9-stack count is unchanged: this only sets the VALUE of the
    existing continuous scalar, exactly like ``signal_strength``).
  * :func:`confirm_tightness` modulates the EXISTING entry follow-through bar
    (bad fit → stricter post-spike confirmation = a DELAY; good fit → looser).
    A genuine strong imbalance still clears the raised bar and fires
    bidirectionally — TIMING precision, NOT a veto / skip / size-cut.
  * :func:`exit_tightness` tightens harvest/protect on a bad fit (bank a
    mis-regime trade sooner) — precise EXIT, the mandate-aligned loss defense.

PURE — no I/O, no GPT, no DB. All four are total functions: an unknown family
or regime degrades to the NEUTRAL value (0 fit / 1.0× scalar), never an error.
"""

from __future__ import annotations

from typing import Final

from polaris.core.ticks.regime_gate import normalize_regime

# The continuous-scalar band (T4 ADR-005). Mirrors
# ``polaris.core.sizing.schema.CONT_SCALAR_{MIN,MAX}`` — duplicated as module
# constants (not imported) so this pure leaf never pulls the ``sizing`` package,
# which imports back into ``regime_fit`` (engine seam1) and would cycle.
CONT_SCALAR_MIN: Final[float] = 0.75
CONT_SCALAR_MAX: Final[float] = 1.50

__all__ = [
    "confirm_tightness",
    "exit_tightness",
    "regime_fit",
    "regime_scalar",
]

# (signal_family, regime_bucket) → fit ∈ [-1, +1]. The regime key is the
# NORMALISED bucket (regime_gate SSOT): bull_trend / bear_trend / trend → "trend";
# chop / range → "range"; crisis → "crisis"; None / unknown → "unknown".
#
# momentum wants TREND (continuation); chop is the churn case it must lean OUT of.
# reversion wants RANGE/chop (mean-revert); a trend chews it up.
# crisis: momentum half-bad (whippy), reversion half-good (sharp snapbacks).
# unknown: neutral 0 (no shaping → full flow, never a block).
_FIT_TABLE: dict[tuple[str, str], float] = {
    ("momentum", "trend"): 1.0,
    ("momentum", "range"): -1.0,
    ("momentum", "crisis"): -0.5,
    ("reversion", "trend"): -1.0,
    ("reversion", "range"): 1.0,
    ("reversion", "crisis"): 0.5,
}


def regime_fit(signal_family: str, regime: str | None) -> float:
    """Deterministic ``(family × regime)`` fit ∈ ``[-1, +1]``.

    An unrecognised family or an unknown/None regime → ``0.0`` (neutral — no
    shaping, the signal flows at its natural conviction). This is a *lean*, never
    a tradeable yes/no gate: every signal can still flow if its conviction is
    enough; the fit only tilts the size / confirmation / exit precision.
    """
    bucket = normalize_regime(regime)
    return _FIT_TABLE.get((signal_family, bucket), 0.0)


def regime_scalar(fit: float) -> float:
    """Fold the fit into the ONE T4 continuous scalar: ``clamp(1 + fit·0.5)``.

    Symmetric about 1.0 — ``+1 → 1.5×`` (boost), ``-1 → 0.75×`` (shrink to the
    FLOOR, never 0), ``0 → 1.0×`` (neutral). Clamped to the SAME
    ``[CONT_SCALAR_MIN, CONT_SCALAR_MAX]`` band the scalar already lives in, so
    it is NOT a stacked ≤1 dampener and NOT a new multiplier — it sets the VALUE
    of the single existing continuous scalar (9-stack ban preserved).
    """
    raw = 1.0 + fit * 0.5
    return max(CONT_SCALAR_MIN, min(CONT_SCALAR_MAX, raw))


def confirm_tightness(fit: float) -> float:
    """Entry-confirmation tightness multiplier from the fit (bidirectional).

    ``> 1`` on a BAD fit (raise the post-spike follow-through bar → a DELAY to a
    confirmed tick, the chop-churn fix); ``< 1`` on a GOOD fit (relax — let a
    strong genuine imbalance fire sooner); ``1.0`` at neutral. Bounded to a
    moderate ``[0.5, 1.5]`` so the bar is shaped, never made impossible: a strong
    imbalance still clears it and fires bidirectionally — TIMING, not a veto.
    """
    return max(0.5, min(1.5, 1.0 - fit * 0.5))


def exit_tightness(fit: float) -> float:
    """Exit-tightness multiplier from the fit (bidirectional).

    ``> 1`` on a BAD fit (tighter bep/protect + smaller giveback → bank the
    mis-regime trade sooner = precise exit, mandate-aligned loss defense);
    ``< 1`` on a GOOD fit (loosen → LET_RUN); ``1.0`` at neutral. Bounded to
    ``[0.5, 1.5]``. Only ratchets the stop TOWARD profit downstream — it never
    reduces size, never blocks/vetoes entry, never loosens below the G6 -1.0R
    rail.
    """
    return max(0.5, min(1.5, 1.0 - fit * 0.5))
