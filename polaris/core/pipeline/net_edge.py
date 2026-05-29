"""Net-edge cost INFRASTRUCTURE — DISPLAY/LOG-ONLY (T14).

This module is a **cost optimisation, not a defensive throttle** (mirrors the
``g6_call_gate`` philosophy). It computes three R-denominated measurements —
``roundtrip_cost_r``, ``gross_edge_r``, ``net_edge_r`` — purely so the pipeline
can *surface* them (payload + structured log + future dashboard). Nothing here
gates, skips, or reduces any entry: the kill-switch
``SKIP_ON_NEGATIVE_NET_EDGE`` is ``False`` and turning it on is a future
``/debate`` decision (an unvalidated skip would violate flow_not_block /
AGGRESSIVE bias). We ship measurement first; gating is deferred.

R units
-------
Costs/edges are expressed in **R** (multiples of the per-trade risk budget,
i.e. the 1R stop distance). 1 bps = 0.01% of price. A round-trip pays the
half-spread on entry and again on exit, so the spread cost in *price* terms is
``spread_bps * 2`` bps. To convert that price-fraction into R we divide by the
stop distance expressed as a price-fraction (``stop_distance_pct``, default
0.01 == a 1% / 1R stop). This conversion is explicit and approximate — it is a
transparent placeholder, not a calibrated execution model.

gross_edge_r honesty note
-------------------------
``gross_edge_r`` is a TRANSPARENT placeholder, NOT a tuned alpha. It scales the
strategy's signal_strength by the expected per-trade move (~ATR) and normalises
by the same stop distance. When inputs are weak (strength ~0, atr ~0) the
estimate is ~0 and should be read as "no measured edge", not "no edge exists".
Do not trade off this number; it exists to be compared against cost so the
upcoming AI-validity /debate has data.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "SKIP_ON_NEGATIVE_NET_EDGE",
    "gross_edge_r",
    "net_edge_r",
    "roundtrip_cost_r",
]

# Kill-switch is OFF. Enabling a skip on negative net edge is a /debate
# decision (it would gate entries — flow_not_block forbids shipping it
# unvalidated). This step is measurement-only.
SKIP_ON_NEGATIVE_NET_EDGE: Final[bool] = False

# Default 1R stop distance as a price-fraction (1% == 1R). Used only to convert
# bps-denominated spread / ATR-denominated move into R units for display. Not a
# sizing input; the T4 chain is untouched.
_DEFAULT_STOP_DISTANCE_PCT: Final[float] = 0.01

# CFD spreads are quoted/charged differently from spot taker fees, but at this
# measurement stage both cost models use the same spread*2 round-trip
# convention; the label is surfaced so the /debate can refine per-model later.
_BPS_TO_FRACTION: Final[float] = 1e-4  # 1 bps == 0.0001 of price


def roundtrip_cost_r(
    *,
    spread_bps: float,
    cost_model: str,
    stop_distance_pct: float = _DEFAULT_STOP_DISTANCE_PCT,
) -> float:
    """Round-trip execution cost in R units (entry + exit).

    The spread component is ``spread_bps * 2`` (half-spread paid on entry and
    again on exit), converted from bps to a price-fraction and normalised by
    ``stop_distance_pct`` (the 1R stop as a price-fraction) to land in R.

    Honest about its limits: this is the spread-only round-trip estimate. It
    does not yet model maker/taker tiers, financing, or slippage — ``cost_model``
    ("spot_taker"/"cfd_spread") is carried for a later /debate that may diverge
    the two. Monotonically non-decreasing in ``spread_bps`` (a wider spread is
    never cheaper). Pure function; no I/O. Cost-measurement, not a throttle.
    """
    del cost_model  # carried for display/future refinement; same math today.
    spread_fraction = max(0.0, float(spread_bps)) * 2.0 * _BPS_TO_FRACTION
    denom = max(float(stop_distance_pct), 1e-9)
    return spread_fraction / denom


def gross_edge_r(
    *,
    signal_strength: float,
    atr_pct: float,
    stop_distance_pct: float = _DEFAULT_STOP_DISTANCE_PCT,
) -> float:
    """TRANSPARENT gross-edge estimate in R units (placeholder, NOT alpha).

    Estimate = (signal_strength scaled) * (expected move ~ ATR%) / stop. The
    expected move proxy is ``atr_pct`` (a fraction, e.g. 0.012 == 1.2% ATR) and
    ``signal_strength`` (typically ~0.75-1.5 from the strategy) scales it. The
    result is normalised by the 1R stop distance so it is directly comparable to
    :func:`roundtrip_cost_r`.

    This is deliberately crude: when inputs are weak it returns ~0, which means
    "no *measured* edge", not "no edge". It is a measurement placeholder for the
    upcoming AI-validity /debate, not a tuned signal. Non-negative. Pure; no I/O.
    """
    strength = max(0.0, float(signal_strength))
    move_fraction = max(0.0, float(atr_pct))
    denom = max(float(stop_distance_pct), 1e-9)
    return (strength * move_fraction) / denom


def net_edge_r(*, gross_edge: float, roundtrip_cost: float) -> float:
    """Net edge in R units: ``gross_edge_r - roundtrip_cost_r``.

    May be negative — that is a *measurement*, surfaced for inspection, never
    acted on (``SKIP_ON_NEGATIVE_NET_EDGE`` is False). Pure function.
    """
    return float(gross_edge) - float(roundtrip_cost)
