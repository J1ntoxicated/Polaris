"""Layer 3 — Risk-budget fill-rate cut + 60% hysteresis.

Spec source:
- vault/30_components/layer-3-sizing-risk.md (Q4 fill-rate cut)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (Risk-Budget Fill-Rate Cut)

Behavior:
- ``fill_rate >= 0.70`` → ``cut_active = True``: caller cuts the weakest signal.
- ``fill_rate <= 0.60`` → ``cut_active = False`` (hysteresis recovery).
- Between 0.60 and 0.70 → state holds (no toggle).

Weak-signal ranking (Q4 priority):
``(signal_strength asc, cell_quartile bottom first, listing_watch first, staleness desc)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from polaris.core.sizing.schema import (
    FILL_RATE_CUT_THRESHOLD,
    FILL_RATE_RESUME_THRESHOLD,
)

__all__ = [
    "CutCandidate",
    "compute_fill_rate",
    "rank_cut_candidates",
    "resolve_cut_state",
]


@dataclass(frozen=True, slots=True)
class CutCandidate:
    """One open signal/position considered for cut prioritization."""

    signal_id: str
    signal_strength: float
    cell_quartile: str  # "top" | "mid" | "bottom" | "cold"
    is_listing_watch: bool
    staleness_seconds: int


def compute_fill_rate(
    *,
    used_risk_pct: float,
    venue_daily_ceiling_pct: float,
) -> float:
    """``used / ceiling`` clamped to ``[0, +inf)``.

    Non-positive ceiling returns 0 (caller must skip cut logic).
    """
    if venue_daily_ceiling_pct <= 0.0:
        return 0.0
    return max(0.0, float(used_risk_pct) / float(venue_daily_ceiling_pct))


def resolve_cut_state(
    *,
    prev_active: bool,
    fill_rate: float,
    cut_threshold: float = FILL_RATE_CUT_THRESHOLD,
    resume_threshold: float = FILL_RATE_RESUME_THRESHOLD,
) -> bool:
    """State machine with hysteresis.

    - inactive → active when ``fill_rate >= cut_threshold``.
    - active → inactive when ``fill_rate <= resume_threshold``.
    - between → no change.
    """
    if prev_active:
        return fill_rate > resume_threshold
    return fill_rate >= cut_threshold


def rank_cut_candidates(candidates: list[CutCandidate]) -> list[CutCandidate]:
    """Sort candidates so the weakest cut target is first.

    Priority (Q4 spec):
        1. signal_strength ascending (weakest first)
        2. cell_quartile == 'bottom' first
        3. is_listing_watch first
        4. older first (staleness_seconds desc)
    """

    def _key(c: CutCandidate) -> tuple[float, int, int, int]:
        return (
            float(c.signal_strength),
            0 if c.cell_quartile == "bottom" else 1,
            0 if c.is_listing_watch else 1,
            -int(c.staleness_seconds),
        )

    return sorted(candidates, key=_key)
