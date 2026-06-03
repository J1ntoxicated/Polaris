"""Pure regime → active-signal gate (P5).

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md`` §"신규 모듈".

The regime decides WHICH tick signals are allowed to fire (the AI conductor
directs; technical decides) — it never throttles sizing. A momentum signal in a
chop regime is simply not in the active set (mis-aligned, not "too risky"); a
reversion signal in a strong trend is likewise gated out. This keeps each
regime trading the edge that actually exists there — degrade-never-halt: an
unknown regime still has an active signal (``flow_pressure``), so the engine is
never silenced.

Regime vocabulary: the codebase SSOT (``regime_flip.REGIME_VALUES``) is
``bull_trend`` / ``bear_trend`` / ``chop`` / ``crisis``. The spec phrases the
gate in generic buckets (trend / range-chop / crisis / unknown); this module
maps the canonical labels (and the generic aliases) onto those buckets so it
accepts whatever ``fetch_regime`` returns. Any unrecognized / None label →
the ``unknown`` bucket (never an error — an unclassified regime still trades
flow pressure).
"""

from __future__ import annotations

__all__ = ["active_signals", "direction_bias", "normalize_regime"]

# Active-signal sets per regime bucket (spec §regime_gate):
#   trend       → burst_rider + flow_pressure   (momentum edge)
#   range/chop  → micro_reversion + flow_pressure (mean-reversion edge)
#   crisis      → micro_reversion only           (fade the spike; no chasing)
#   unknown     → flow_pressure                  (always-on imbalance edge)
_TREND: frozenset[str] = frozenset({"burst_rider", "flow_pressure"})
_RANGE: frozenset[str] = frozenset({"micro_reversion", "flow_pressure"})
_CRISIS: frozenset[str] = frozenset({"micro_reversion"})
_UNKNOWN: frozenset[str] = frozenset({"flow_pressure"})

_ACTIVE_BY_BUCKET: dict[str, frozenset[str]] = {
    "trend": _TREND,
    "range": _RANGE,
    "crisis": _CRISIS,
    "unknown": _UNKNOWN,
}

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
    """Return the signal ids allowed to fire in ``regime``.

    Pure lookup via :func:`normalize_regime`:
      - trend       → ``{burst_rider, flow_pressure}``
      - range/chop  → ``{micro_reversion, flow_pressure}``
      - crisis      → ``{micro_reversion}``
      - unknown     → ``{flow_pressure}``

    An unknown regime is never empty — the engine always has at least
    ``flow_pressure`` (degrade-never-halt).
    """
    return _ACTIVE_BY_BUCKET[normalize_regime(regime)]


def direction_bias(regime: str | None) -> int:
    """Return the directional bias for ``regime``: +1 long-tilt / -1 short-tilt / 0.

    Only the canonical trend labels carry a bias (``bull_trend`` → +1,
    ``bear_trend`` → -1). Chop / crisis / unknown / generic ``trend`` (no
    direction) → 0. A bias, NOT a block: both sides stay tradable; the signal
    layer may use it only to tilt conviction / break ties.
    """
    key = (regime or "").strip().lower()
    return _DIRECTION_BIAS.get(key, 0)
