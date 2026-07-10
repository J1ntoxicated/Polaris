"""fee-split v0 (group GROSS) — best_case_friction structural floor.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Pure
math — no DB, no I/O, never touches sizing/9-stack/-1.0R rail. Spec source:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item 1:

    "best_case_friction = 구조적 실행가능 플로어 (maker-capable -> maker floor,
    taker-only -> taker floor + spread p05); 실측 q20은 낮추기만 가능
    (치킨-에그 해소)."

``best_case_friction`` is the structurally-achievable round-trip cost floor
for a (venue, strategy) track — two legs (entry+exit) at the venue's own
best-tier fee, plus a slippage/spread floor. It is NEVER derived from a
strategy's own thin live-fill history (chicken-and-egg: a brand-new PROVE
candidate has no fills yet to derive a floor from), so it is always
computable from static venue/market parameters. The REALIZED q20 (a
decay-weighted 20th percentile of the track's actual last-200-fills
friction cost) is then composed via ``min()`` — the SAME min()-only
composition style used everywhere else in this project's sizing/cap chain
(9-stack ban precedent) — so realized evidence can only LOWER the floor,
never raise it above the structural best case.
"""

from __future__ import annotations

from dataclasses import dataclass

from polaris.core.classes.gross_scorer import time_decay_weight, weighted_percentile

__all__ = [
    "REALIZED_Q20_PERCENTILE",
    "FrictionSample",
    "best_case_friction",
    "effective_friction_floor",
    "realized_friction_q20",
]

REALIZED_Q20_PERCENTILE: float = 0.20


def best_case_friction(
    *,
    maker_capable: bool,
    maker_floor_bps: float,
    taker_floor_bps: float,
    slip_floor_bps: float,
    spread_p05_bps: float,
) -> float:
    """Structural round-trip friction floor, in bps of notional.

    - maker-capable: ``2 * maker_floor_bps + slip_floor_bps`` (two maker legs
      + a resting-order slippage floor).
    - taker-only: ``2 * taker_floor_bps + spread_p05_bps`` (two taker legs,
      each crossing the spread, plus the tightest-observed (p05) spread as
      the best-case crossing cost).

    All inputs are bps (non-negative expected; a negative floor input is
    passed through unclamped — the caller owns validating its own fee
    schedule, this function is a pure composition of whatever it is given).
    """
    if maker_capable:
        return 2.0 * maker_floor_bps + slip_floor_bps
    return 2.0 * taker_floor_bps + spread_p05_bps


@dataclass(slots=True, frozen=True)
class FrictionSample:
    """One realized fill's friction cost (bps of notional) + its timestamp,
    for the decay-weighted q20 realized-floor estimate."""

    cost_bps: float
    ts: int


def realized_friction_q20(
    samples: list[FrictionSample],
    *,
    now_ts: int,
    characteristic_time_seconds: float,
) -> float | None:
    """Decay-weighted 20th percentile of realized per-fill friction cost
    over the caller-supplied fill window (spec: "last 200 fills" — the
    caller is responsible for slicing to that window before calling this).

    ``None`` when ``samples`` is empty (no realized evidence yet — the
    chicken-and-egg case the structural floor alone must cover).
    """
    if not samples:
        return None
    weights = [
        time_decay_weight(now_ts - s.ts, characteristic_time_seconds) for s in samples
    ]
    values = [s.cost_bps for s in samples]
    return weighted_percentile(values, weights, REALIZED_Q20_PERCENTILE)


def effective_friction_floor(
    *,
    structural_floor_bps: float,
    realized_q20_bps: float | None,
) -> float:
    """``min(structural_floor_bps, realized_q20_bps)`` — realized evidence
    can only LOWER the floor, never raise it above the structural best case
    (min()-only composition, no new multiplier — 9-stack ban precedent).
    ``realized_q20_bps=None`` (no realized evidence yet) -> the structural
    floor stands alone.
    """
    if realized_q20_bps is None:
        return structural_floor_bps
    return min(structural_floor_bps, realized_q20_bps)
