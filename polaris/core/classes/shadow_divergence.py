"""fee-split v0 (group GROSS) — shadow dual-score divergence measurement.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Pure
computation — no DB, no I/O, never touches a live transition. Spec source:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item 5:

    "임계 이관 = per-transition ECDF percentile 보존 + isotonic 순서 강제 +
    섀도우 120 청산 병행(행동 발산 <=15%*tier <=10%*전이율 <=7.5pp) 후 flip."

v0/v1 boundary (explicit — read before touching this module)
--------------------------------------------------------------
The survival FSM (``polaris.core.classes.transition.evaluate_transition``)
keeps consuming the EXISTING fee-normalized ``score_f_events.score_contrib``
UNCHANGED in v0 — this module NEVER drives a live transition. It exists
solely to accumulate a dual-score comparison (OLD fee-normalized verdict vs
NEW gross-proxy verdict, both bucketed against the SAME 3-way UP/HOLD/DOWN
scale) across closes, so that once 120 have accumulated the divergence can
be checked against the flip-readiness bar. Only after ``ready_to_flip`` is
True (measured over real production history, a judgment call OUT OF this
module's scope — it only reports the number) may a future v1 change wire
the gross axis into ``transition.py`` for real.

Scoping simplification (v0, documented — NOT full-FSM replay): reproducing
``transition.py``'s entire 8-priority state machine (dwell/epoch/ladder/
tripwire/Schmitt) twice per close, once per scorer, is out of scope for a
measurement-only module. Instead each close is bucketed into one of three
verdict buckets (UP / HOLD / DOWN) per scorer via
:func:`classify_against_thresholds`, using ``transition.py``'s own Schmitt
thresholds (-4.0 BENCH-partial / -3.0 BENCH-full / -1.0 PROVE demotion) as
the OLD-axis reference and their ECDF-percentile-migrated counterparts
(``threshold_migration.migrate_thresholds``) as the NEW-axis reference:

- **behavior divergence** = fraction of closes where the two scorers'
  3-way bucket disagrees (fine-grained per-close agreement).
- **tier divergence** = fraction of closes where the COARSE 2-way bucket
  (any-transition ["UP" or "DOWN"] vs "HOLD") disagrees — a proxy for
  "would the resulting class differ" without full dwell/ladder replay.
- **transition rate diff** = |old_rate - new_rate| in percentage points,
  where each scorer's rate = fraction of closes bucketed away from "HOLD".

Cold-start non-starvation (R2 item 2, "12-19: 상향/HOLD만"): the NEW axis's
raw bucket is clamped to never emit "DOWN" while the underlying
``GrossLcbResult.verdict_band`` is ``COLD_START`` or ``INSUFFICIENT`` — a
thin new-scorer sample can promote/hold a bucket but never demote it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polaris.core.classes.gross_scorer import GrossLcbResult

__all__ = [
    "BEHAVIOR_DIVERGENCE_MAX_PCT",
    "MIN_SHADOW_CLOSES",
    "TIER_DIVERGENCE_MAX_PCT",
    "TRANSITION_RATE_DIFF_MAX_PP",
    "DualScoreSample",
    "ShadowDivergenceResult",
    "classify_against_thresholds",
    "classify_new_axis_with_cold_start_guard",
    "compute_shadow_divergence",
]

Verdict = Literal["UP", "HOLD", "DOWN"]

MIN_SHADOW_CLOSES: int = 120
BEHAVIOR_DIVERGENCE_MAX_PCT: float = 0.15
TIER_DIVERGENCE_MAX_PCT: float = 0.10
TRANSITION_RATE_DIFF_MAX_PP: float = 7.5


def classify_against_thresholds(
    score: float, *, down_threshold: float, up_threshold: float,
) -> Verdict:
    """3-way bucket: ``score < down_threshold`` -> DOWN,
    ``score > up_threshold`` -> UP, else HOLD. ``down_threshold`` and
    ``up_threshold`` are caller-ordered (down < up expected; an inverted
    pair degrades to always-HOLD between them, never raises)."""
    if score < down_threshold:
        return "DOWN"
    if score > up_threshold:
        return "UP"
    return "HOLD"


def classify_new_axis_with_cold_start_guard(
    lcb_result: GrossLcbResult, *, down_threshold: float, up_threshold: float,
) -> Verdict:
    """New-axis 3-way bucket with the R2 item 2 cold-start non-starvation
    guard: no DOWN verdict unless ``verdict_band == "FULL"``. A missing
    point-estimate (``mean is None`` — INSUFFICIENT band) is HOLD."""
    if lcb_result.mean is None:
        return "HOLD"
    raw = classify_against_thresholds(
        lcb_result.mean, down_threshold=down_threshold, up_threshold=up_threshold,
    )
    if raw == "DOWN" and lcb_result.verdict_band != "FULL":
        return "HOLD"
    return raw


@dataclass(slots=True, frozen=True)
class DualScoreSample:
    """One closed lifecycle's paired OLD-axis / NEW-axis verdict."""

    old_verdict: Verdict
    new_verdict: Verdict


@dataclass(slots=True, frozen=True)
class ShadowDivergenceResult:
    """Accumulated dual-score divergence over a shadow run."""

    n_samples: int
    behavior_divergence_pct: float
    tier_divergence_pct: float
    old_transition_rate: float
    new_transition_rate: float
    transition_rate_diff_pp: float
    ready_to_flip: bool


def _coarse(verdict: Verdict) -> Literal["ACTIVE", "HOLD"]:
    return "HOLD" if verdict == "HOLD" else "ACTIVE"


def compute_shadow_divergence(
    samples: list[DualScoreSample], *, min_n: int = MIN_SHADOW_CLOSES,
) -> ShadowDivergenceResult:
    """Aggregate dual-score divergence across ``samples`` and evaluate
    flip-readiness against the R2 item 5 bar (all three metrics within
    bound AND at least ``min_n`` closes accumulated).

    Empty ``samples`` -> zero divergence but ``ready_to_flip=False`` (never
    "ready" off zero evidence — the sample-size floor is itself part of the
    gate, not just the divergence metrics).
    """
    n = len(samples)
    if n == 0:
        return ShadowDivergenceResult(
            n_samples=0, behavior_divergence_pct=0.0, tier_divergence_pct=0.0,
            old_transition_rate=0.0, new_transition_rate=0.0,
            transition_rate_diff_pp=0.0, ready_to_flip=False,
        )

    behavior_mismatches = sum(1 for s in samples if s.old_verdict != s.new_verdict)
    tier_mismatches = sum(
        1 for s in samples if _coarse(s.old_verdict) != _coarse(s.new_verdict)
    )
    old_active = sum(1 for s in samples if s.old_verdict != "HOLD")
    new_active = sum(1 for s in samples if s.new_verdict != "HOLD")

    behavior_pct = behavior_mismatches / n
    tier_pct = tier_mismatches / n
    old_rate = old_active / n
    new_rate = new_active / n
    rate_diff_pp = abs(old_rate - new_rate) * 100.0

    ready = (
        n >= min_n
        and behavior_pct <= BEHAVIOR_DIVERGENCE_MAX_PCT
        and tier_pct <= TIER_DIVERGENCE_MAX_PCT
        and rate_diff_pp <= TRANSITION_RATE_DIFF_MAX_PP
    )

    return ShadowDivergenceResult(
        n_samples=n,
        behavior_divergence_pct=behavior_pct,
        tier_divergence_pct=tier_pct,
        old_transition_rate=old_rate,
        new_transition_rate=new_rate,
        transition_rate_diff_pp=rate_diff_pp,
        ready_to_flip=ready,
    )
