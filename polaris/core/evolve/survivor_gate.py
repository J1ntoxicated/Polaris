"""P0a survivor -> virtual-account PROVE wire (offline admission guards).

OFFLINE ONLY. Reads/writes ONLY ``data/p0a_registry.sqlite`` (the evolve
registry — write-allowed, see ``registry.py``'s own module docstring); NEVER
touches ``data/polaris_live.sqlite`` or the live ``strategy_class`` table.
This is the "1 new wire" a KILL-spike SEARCH_SPACE_EXISTS verdict needs before
a grid-search winner can become a real admission decision for the virtual
PROVE track — WITHOUT this module ever deciding EARN itself. EARN promotion
stays the EXCLUSIVE job of ``polaris.core.classes.transition.
evaluate_transition`` (dwell=10 + fill gate + Schmitt hysteresis), fed by REAL
virtual-account fills once a survivor is live-forward in PROVE — this module
never imports or calls ``evaluate_transition``, and ``SurvivorAdmissionRow``
carries no "class" field at all, so there is no code path here capable of
writing anything other than a PROVE admission (uncounterfeitable by
construction, not by convention).

Three admission guards, evaluated in order (first REJECT wins — the same
priority-order shape as ``transition.evaluate_transition``; these are boolean
admission gates, never sizing multipliers, so no 9-stack stacking risk):

  1. ``grid_corner_guard`` — a winning variant sitting at every axis' grid
     EXTREME (a "corner" of its strategy's ``PARAM_BOUNDS`` grid) is REJECTED
     unless at least one grid-ADJACENT neighbor (one param stepped by exactly
     one grid index, same ``cell_key``) ALSO OOS-passed. Guards against a
     single boundary-point fluke reading as a real edge. Reuses
     ``PARAM_BOUNDS`` (evolve's own grid SSOT) — no new grid encoding. A
     non-corner winner, or a strategy with no grid at all, passes trivially
     (the guard only concerns isolated CORNER results).
  2. ``independent_cell_guard`` — the same ``variant_id`` must OOS-pass in
     >= ``K_MIN_INDEPENDENT_CELLS`` DISTINCT ``cell_key`` partitions, never
     winner-carried by one lucky cell. ``K_MIN_INDEPENDENT_CELLS`` mirrors
     ``polaris.scripts._p0a_verdict.K_MIN_EVALUABLE`` (=3, the project's own
     "not too thin a sample" evidence floor for this exact P0a offline
     context) — not re-imported (that module lives in ``polaris.scripts``,
     importing it here would invert the scripts-depend-on-core layering), but
     the SAME value for the same reason, documented at its definition below.
  3. ``cohort_cap_guard`` — throttles CONCURRENT admission per track to
     ``PROBE_CONCURRENCY_CAP[track]``, reused VERBATIM from
     ``polaris.core.classes.r_pool`` — the SAME concurrent-PROVE-probe budget
     every other live PROVE candidate already shares (a P0a survivor is one
     more claimant on that existing budget, not a second cap SSOT). A
     throttle, not a permanent block (``flow_not_block``):
     ``resolve_survivor_admission`` frees the slot once a survivor resolves
     out of PROVE, so the next evaluation cycle can admit again.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NamedTuple

from polaris.core.classes.r_pool import PROBE_CONCURRENCY_CAP
from polaris.core.evolve.param_bounds import PARAM_BOUNDS
from polaris.core.evolve.registry import (
    SurvivorAdmissionRow,
    count_active_admissions,
    record_survivor_admission,
)
from polaris.core.streams.config import resolve_stream

__all__ = [
    "K_MIN_INDEPENDENT_CELLS",
    "GuardVerdict",
    "SurvivorTrial",
    "SurvivorVerdict",
    "cohort_cap_guard",
    "evaluate_survivor",
    "grid_corner_guard",
    "independent_cell_guard",
    "load_variant_trials",
]

# See module docstring guard 2 — mirrors _p0a_verdict.K_MIN_EVALUABLE's same
# evidence-floor rationale (kept as a literal, not a cross-layer import).
K_MIN_INDEPENDENT_CELLS: Final[int] = 3


class SurvivorTrial(NamedTuple):
    """One ``variant_trials`` row's admission-relevant fields (read-only view)."""

    variant_id: str
    cell_key: str
    param_json: str
    oos_pass: bool


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """One guard's pass/fail + a human-readable reason."""

    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class SurvivorVerdict:
    """The full admission decision for one (strategy, venue, variant_id)."""

    admit: bool
    reason: str
    variant_id: str
    strategy: str
    venue: str
    track: str
    n_independent_cells: int


def load_variant_trials(conn: sqlite3.Connection, *, strategy: str) -> list[SurvivorTrial]:
    """Every persisted ``variant_trials`` row for ``strategy`` (all cells, all runs)."""
    rows = conn.execute(
        "SELECT variant_id, cell_key, param_json, oos_pass FROM variant_trials WHERE strategy = ?",
        (strategy,),
    ).fetchall()
    return [
        SurvivorTrial(
            variant_id=str(r[0]), cell_key=str(r[1]), param_json=str(r[2]), oos_pass=bool(r[3])
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Guard 1 — isolated grid-corner ban
# ---------------------------------------------------------------------------


def _parse_overrides(param_json: str) -> dict[str, Any]:
    try:
        decoded = json.loads(param_json)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _grid_indices(strategy: str, overrides: Mapping[str, Any]) -> dict[str, int] | None:
    """Per-param grid-point index for ``overrides``, or ``None`` if ``strategy``
    has no grid (nothing to corner-check) or a value can't be located on its
    axis (malformed/foreign data — permissive: never blocks on a bad read)."""
    axes = PARAM_BOUNDS.get(strategy)
    if not axes:
        return None
    indices: dict[str, int] = {}
    for name, axis in axes.items():
        if name not in overrides:
            return None
        try:
            indices[name] = axis.index(float(overrides[name]))
        except (ValueError, TypeError):
            return None
    return indices


def _is_grid_corner(strategy: str, indices: Mapping[str, int]) -> bool:
    axes = PARAM_BOUNDS[strategy]
    return all(idx in (0, len(axes[name]) - 1) for name, idx in indices.items())


def _grid_neighbor_overrides(strategy: str, indices: Mapping[str, int]) -> list[dict[str, float]]:
    """Every grid point exactly one index-step away from ``indices`` on ONE axis
    (all other axes held fixed) — the immediate grid neighbors of a corner."""
    axes = PARAM_BOUNDS[strategy]
    neighbors: list[dict[str, float]] = []
    for name, idx in indices.items():
        axis = axes[name]
        for step in (-1, 1):
            new_idx = idx + step
            if 0 <= new_idx < len(axis):
                nb = {n: axes[n][i] for n, i in indices.items()}
                nb[name] = axis[new_idx]
                neighbors.append(nb)
    return neighbors


def grid_corner_guard(
    rows: Sequence[SurvivorTrial], *, strategy: str, cell_key: str, variant_id: str
) -> GuardVerdict:
    """REJECT a winning variant that sits at a grid corner with no adjacent
    (same-cell) grid neighbor also OOS-passing. Non-corner winners, and
    strategies with no ``PARAM_BOUNDS`` grid, pass trivially (N/A)."""
    winner = next(
        (r for r in rows if r.variant_id == variant_id and r.cell_key == cell_key), None
    )
    if winner is None:
        return GuardVerdict(False, "SURVIVOR_TRIAL_NOT_FOUND")
    indices = _grid_indices(strategy, _parse_overrides(winner.param_json))
    if indices is None or not _is_grid_corner(strategy, indices):
        return GuardVerdict(True, "GRID_CORNER_NOT_APPLICABLE")
    neighbor_sets = _grid_neighbor_overrides(strategy, indices)
    for r in rows:
        if r.cell_key != cell_key or not r.oos_pass or r.variant_id == variant_id:
            continue
        if _parse_overrides(r.param_json) in neighbor_sets:
            return GuardVerdict(True, "GRID_CORNER_ADJACENT_PASS")
    return GuardVerdict(False, "ISOLATED_GRID_CORNER")


# ---------------------------------------------------------------------------
# Guard 2 — k-independent-cell requirement
# ---------------------------------------------------------------------------


def independent_cell_guard(
    rows: Sequence[SurvivorTrial], *, variant_id: str, k: int = K_MIN_INDEPENDENT_CELLS
) -> tuple[GuardVerdict, int]:
    """REJECT a survivor whose OOS-pass is carried by fewer than ``k`` DISTINCT
    ``cell_key`` partitions. Returns ``(verdict, n_independent_cells)``."""
    passing_cells = {r.cell_key for r in rows if r.variant_id == variant_id and r.oos_pass}
    n = len(passing_cells)
    if n >= k:
        return GuardVerdict(True, f"INDEPENDENT_CELLS_OK({n}>={k})"), n
    return GuardVerdict(False, f"INDEPENDENT_CELLS_INSUFFICIENT({n}<{k})"), n


# ---------------------------------------------------------------------------
# Guard 3 — cohort cap (throttle, not a block — see module docstring)
# ---------------------------------------------------------------------------


def cohort_cap_guard(conn: sqlite3.Connection, *, track: str) -> GuardVerdict:
    """Throttle concurrent admission to ``PROBE_CONCURRENCY_CAP[track]`` (the
    SAME budget every live PROVE probe candidate already shares)."""
    cap = PROBE_CONCURRENCY_CAP[track]
    active = count_active_admissions(conn, track=track)
    if active < cap:
        return GuardVerdict(True, f"COHORT_CAP_OK({active}<{cap})")
    return GuardVerdict(False, f"COHORT_CAP_EXHAUSTED({active}>={cap})")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def evaluate_survivor(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    venue: str,
    variant_id: str,
    cell_key: str,
    now_ts: int,
    k_min_independent_cells: int = K_MIN_INDEPENDENT_CELLS,
) -> SurvivorVerdict:
    """Evaluate one P0a survivor against all 3 guards (first REJECT wins) and,
    if admitted, persist it into the virtual PROVE cohort. PROVE ONLY — see
    module docstring; there is no branch in this function (or anywhere in
    this module) that can admit to any class other than PROVE."""
    rows = load_variant_trials(conn, strategy=strategy)
    track = resolve_stream(venue).track

    corner = grid_corner_guard(rows, strategy=strategy, cell_key=cell_key, variant_id=variant_id)
    if not corner.passed:
        return SurvivorVerdict(False, corner.reason, variant_id, strategy, venue, track, 0)

    independent, n_cells = independent_cell_guard(
        rows, variant_id=variant_id, k=k_min_independent_cells
    )
    if not independent.passed:
        return SurvivorVerdict(
            False, independent.reason, variant_id, strategy, venue, track, n_cells
        )

    cohort = cohort_cap_guard(conn, track=track)
    if not cohort.passed:
        return SurvivorVerdict(False, cohort.reason, variant_id, strategy, venue, track, n_cells)

    record_survivor_admission(
        conn,
        SurvivorAdmissionRow(
            venue=venue,
            variant_id=variant_id,
            strategy=strategy,
            track=track,
            cell_key=cell_key,
            n_independent_cells=n_cells,
            reason="ADMITTED_PROVE",
            admitted_ts=now_ts,
        ),
    )
    return SurvivorVerdict(True, "ADMITTED_PROVE", variant_id, strategy, venue, track, n_cells)
