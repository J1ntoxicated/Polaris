"""Pure regime → active-signal gate (P5).

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md`` §"신규 모듈".

The three tick signals this gate selected (``burst_rider`` / ``flow_pressure`` /
``micro_reversion``) were KILLed 2026-06-26 — gross-negative entry expectancy
(negative BEFORE fees, cross-validated over two windows). ``active_signals``
now returns the empty set for every regime, so the engine dispatches no tick
signal. This is the removal of a no-edge generator, NOT a defensive throttle:
there is no edge left here to throttle.

``normalize_regime`` and ``direction_bias`` are retained — they are consumed by
``regime_fit`` and the entrance-leans probe (independent of tick dispatch).

Regime vocabulary: the codebase SSOT (``regime_flip.REGIME_VALUES``) is
``bull_trend`` / ``bear_trend`` / ``chop`` / ``crisis``. The spec phrases the
gate in generic buckets (trend / range-chop / crisis / unknown); this module
maps the canonical labels (and the generic aliases) onto those buckets so it
accepts whatever ``fetch_regime`` returns. Any unrecognized / None label →
the ``unknown`` bucket (never an error).
"""

from __future__ import annotations

__all__ = ["active_signals", "direction_bias", "normalize_regime"]

# Tick signals KILLed 2026-06-26 → no signal is active in any regime.
_EMPTY: frozenset[str] = frozenset()

# Canonical + generic label → bucket. ``bull_trend``/``bear_trend`` and the
# generic ``trend`` collapse to "trend"; ``chop``/``range`` → "range".
_LABEL_TO_BUCKET: dict[str, str] = {
    "bull_trend": "trend",
    "bear_trend": "trend",
    "trend": "trend",
    "chop": "range",
    "range": "range",
    "crisis": "crisis",
}

# Directional bias per bucket+label: a long-tilt regime (+1) / short-tilt (-1) /
# none (0). Only the canonical trend labels carry a directional bias; chop /
# crisis / unknown are direction-neutral (0). This is a *bias* the signal layer
# may use to break ties — it never forces or blocks a side (both directions stay
# tradable; "모든 상황 수익(양방향)").
_DIRECTION_BIAS: dict[str, int] = {
    "bull_trend": 1,
    "bear_trend": -1,
}


def normalize_regime(regime: str | None) -> str:
    """Map a raw regime label onto its bucket (``trend``/``range``/``crisis``/``unknown``).

    Accepts the canonical labels (``bull_trend``/``bear_trend``/``chop``/
    ``crisis``) and the generic aliases (``trend``/``range``). Case-insensitive.
    ``None`` / empty / unrecognized → ``"unknown"`` (never raises — an
    unclassified regime still trades flow pressure).
    """
    key = (regime or "").strip().lower()
    return _LABEL_TO_BUCKET.get(key, "unknown")


def active_signals(regime: str | None) -> frozenset[str]:
    """Return the signal ids allowed to fire in ``regime`` — now always empty.

    The three tick signals were KILLed (gross-negative entry expectancy), so no
    signal is active in any regime; the engine dispatch loop iterates an empty
    set and emits nothing. ``regime`` is accepted (and normalized for symmetry)
    but no longer selects a signal subset.
    """
    normalize_regime(regime)
    return _EMPTY


def direction_bias(regime: str | None) -> int:
    """Return the directional bias for ``regime``: +1 long-tilt / -1 short-tilt / 0.

    Only the canonical trend labels carry a bias (``bull_trend`` → +1,
    ``bear_trend`` → -1). Chop / crisis / unknown / generic ``trend`` (no
    direction) → 0. A bias, NOT a block: both sides stay tradable; the signal
    layer may use it only to tilt conviction / break ties.
    """
    key = (regime or "").strip().lower()
    return _DIRECTION_BIAS.get(key, 0)
