"""Probe slot reranker — pts-classes (Performance-Tiered Strategy classes),
group F.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
``rank_candidates``/``assign_slots`` are pure capital-ROUTING decision
functions, same shape as ``transition.evaluate_transition`` /
``r_pool.allocate_r_pool`` — no DB I/O, no sizing/9-stack/-1.0R rail touch.
Losing a probe slot is a capital-routing outcome (this track's concurrent-
probe/24h-fee budget is fully claimed by higher-ranked siblings this cycle),
never a block/reject — an unslotted PROVE candidate keeps signaling/learning/
shadow-pricing exactly as before (aggressive_always_profit /
no_block_filter_architecture). The DB-facing 07:30 sweeper
(``tools.ops.probe_reranker``) assembles ``ProbeCandidate`` rows from
``strategy_class`` + ``score_f_events`` and persists this module's output into
``probe_slot_assignment`` — this module performs no DB read/write itself.

Spec source: prove_then_scale_classes_2026-07-03.md 빌드 스펙 ⑥ + 유효반박 #2 —
"probe slot 랭커: 07:30 일일 rerank, pessimistic-shadow score_F desc(사다리
충족자만), tie-break fill_rate→expected fee→대기 age" + "동시 probe cap 3/2/1
per track + 24h probe fee cap 6/4/2×F_track_cap(07:30 동결 median), 초과
intent=shadow."

Interpretation notes (ambiguous-threshold precedent, same style as
``transition.py`` / ``r_pool.py``'s own docstrings — no separate SSOT doc
specifies the exact tie-break DIRECTION or wait-age semantics):

- "재진입 사다리 충족자만" (reentry-ladder qualifiers only): by the time a
  candidate reaches THIS module, that filter has already been applied by the
  ladder — ``transition.evaluate_transition``'s ``_check_reentry_ladder``
  already promotes a BENCH member straight to PROVE only once its step-2
  threshold clears (``LADDER_EXIT_TO_PROVE``). This module's input population
  is therefore simply "every current PROVE-class (venue, strategy_id) in the
  track" — group C's ladder gate already excludes anyone who has not
  qualified; this module does not re-derive ladder membership itself (zero
  import coupling to ``transition.py``, mirrors ``r_pool.py``'s injected-input
  shape).
- tie-break DIRECTION: ``fill_rate`` — higher wins (a candidate that actually
  gets filled is more valuable probe-budget spend than one that mostly
  reject/expires). ``expected_fee_usd`` — lower wins (a cheaper probe stretches
  the shared 24h fee budget further for the same rank). ``wait_age_sec`` —
  higher (longer-waiting) wins, a fairness tiebreak so a candidate that has sat
  PROVE-class without ever winning a slot is not perpetually starved by a
  same-scoring newcomer.
- 24h fee cap enforcement order: candidates are walked in RANK order (already
  score_F-desc + tie-break sorted) and a RUNNING cumulative-spend total is
  compared against ``PROBE_FEE_CAP_MULT[track] x f_track_cap_usd`` — a
  candidate's OWN ``probe_fee_24h_usd`` (already accrued this cycle,
  ``polaris.core.classes.probe_fee.accrue_probe_fee``) is folded into the
  running total whether or not it wins a slot (it already SPENT that fee
  regardless of today's rerank), so a lower-ranked sibling correctly sees a
  smaller remaining budget. This is a straight ``running_total <= budget``
  comparison, never a new T4 multiplier (9-stack ban intact — this module
  never touches ``compute_size``'s sizing chain at all, it only writes which
  slots are ``slot_active`` for the NEXT cycle's probes to read).
- ``F_track_cap`` basis for the BUDGET (as opposed to each candidate's own
  fee contribution): ``score_f.f_track_cap`` is defined per (venue,
  strategy_id), not per A/B/C track, so a track with multiple PROVE
  candidates has multiple own-``f_track_cap_usd`` readings. The track-wide
  24h budget uses the MEAN of every candidate's own value — deterministic and
  independent of rank order (unlike, e.g., picking the top-ranked candidate's
  own value, which would make the shared budget silently shift whenever the
  ranking reshuffles for reasons unrelated to fee history).
- concurrency cap wins over fee cap in ``reason`` labeling: a candidate beyond
  the top-``PROBE_CONCURRENCY_CAP[track]`` rank is reported
  ``CONCURRENCY_CAP_EXHAUSTED`` even when the 24h fee budget would also have
  blocked it — the caller only needs ONE reason string, and rank position is
  the more legible signal for a human reading the daily snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from polaris.core.classes.r_pool import PROBE_CONCURRENCY_CAP

__all__ = [
    "PROBE_FEE_CAP_MULT",
    "ProbeCandidate",
    "SlotAssignment",
    "SlotAssignmentResult",
    "assign_slots",
    "rank_candidates",
]

Track = Literal["A", "B", "C"]

# 24h probe-fee budget = this multiple x F_track_cap (07:30-frozen median
# expected fee, ``score_f.f_track_cap``). Same A/B/C list-order convention as
# ``r_pool.PROBE_CONCURRENCY_CAP`` (task spec: "24h probe fee cap 6/4/2 x
# F_track_cap").
PROBE_FEE_CAP_MULT: Final[dict[str, int]] = {"A": 6, "B": 4, "C": 2}


@dataclass(slots=True, frozen=True)
class ProbeCandidate:
    """One PROVE-class (venue, strategy_id) track-member's ranking inputs,
    assembled by the caller (DB-facing sweeper) from ``strategy_class`` +
    ``score_f_events`` — this module performs no DB read itself."""

    venue: str
    strategy_id: str
    track: Track
    shadow_score_mean: float
    fill_rate: float
    expected_fee_usd: float
    wait_age_sec: int
    probe_fee_24h_usd: float
    f_track_cap_usd: float


@dataclass(slots=True, frozen=True)
class SlotAssignment:
    """One candidate's ranked slot outcome for this rerank cycle."""

    venue: str
    strategy_id: str
    rank: int
    slot_active: bool
    reason: str
    shadow_score_mean: float
    fill_rate: float
    expected_fee_usd: float
    wait_age_sec: int


@dataclass(slots=True, frozen=True)
class SlotAssignmentResult:
    assignments: list[SlotAssignment]


def rank_candidates(candidates: list[ProbeCandidate]) -> list[ProbeCandidate]:
    """Sort ``candidates`` pessimistic-shadow score_F desc, tie-broken
    fill_rate desc -> expected_fee_usd asc -> wait_age_sec desc.

    Pure sort — no track scoping (mixed-track input sorts as one list; the
    caller/``assign_slots`` scopes per-track cap enforcement).
    """
    return sorted(
        candidates,
        key=lambda c: (
            -c.shadow_score_mean,
            -c.fill_rate,
            c.expected_fee_usd,
            -c.wait_age_sec,
        ),
    )


def assign_slots(ranked: list[ProbeCandidate], *, track: Track) -> SlotAssignmentResult:
    """Assign concurrent probe slots for one track's already-ranked
    candidate list (see ``rank_candidates``).

    Two independent caps, both must clear for ``slot_active=True``:
      1. Concurrency cap — only the top ``PROBE_CONCURRENCY_CAP[track]``
         ranks are eligible at all.
      2. 24h fee cap — walking in rank order, a running cumulative spend
         (each candidate's OWN ``probe_fee_24h_usd``, already accrued this
         cycle regardless of slot outcome) must stay within
         ``PROBE_FEE_CAP_MULT[track] x mean(f_track_cap_usd)`` for a candidate
         to receive a slot (mean over every candidate in ``ranked`` — see
         module docstring's "F_track_cap basis" note on why a mean, not the
         top-ranked candidate's own value alone).
    """
    concurrency_cap = PROBE_CONCURRENCY_CAP[track]
    mean_f_track_cap = (
        sum(c.f_track_cap_usd for c in ranked) / len(ranked) if ranked else 0.0
    )
    fee_budget = PROBE_FEE_CAP_MULT[track] * mean_f_track_cap

    assignments: list[SlotAssignment] = []
    running_spend = 0.0
    for i, c in enumerate(ranked):
        rank = i + 1
        running_spend += c.probe_fee_24h_usd
        if rank > concurrency_cap:
            slot_active, reason = False, "CONCURRENCY_CAP_EXHAUSTED"
        elif running_spend > fee_budget:
            slot_active, reason = False, "FEE_CAP_EXHAUSTED"
        else:
            slot_active, reason = True, "SLOT_ACTIVE"
        assignments.append(
            SlotAssignment(
                venue=c.venue,
                strategy_id=c.strategy_id,
                rank=rank,
                slot_active=slot_active,
                reason=reason,
                shadow_score_mean=c.shadow_score_mean,
                fill_rate=c.fill_rate,
                expected_fee_usd=c.expected_fee_usd,
                wait_age_sec=c.wait_age_sec,
            )
        )
    return SlotAssignmentResult(assignments=assignments)
