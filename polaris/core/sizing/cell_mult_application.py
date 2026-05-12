"""Layer 3 — cell-mult application step of the T4 sizing formula.

Spec source:
- vault/30_components/layer-3-sizing-risk.md
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md

ADR-005 T4 placement:

    notional = base × continuous_scalar(0.75-1.5×) × tier_amplifier(1.5/2/3×)
               × cell_routing_mult       ← THIS MODULE
    clipped  = min(notional, hard_caps)
    final    = clipped × leverage(venue)

The cell mult is multiplied **before** the hard-cap clip so loser cells get
suppression first and winner cells reach the cap (`composition` rule from the
L3 round-1 codex agreement). Day 2 only wires the cell-mult application; the
upstream T4 components (base/scalar/amplifier) and the downstream hard-cap
clip land with the rest of Layer 3 in Day 3+.

This module is the **only** production caller of
:func:`polaris.core.cell_matrix.resolve_routing_for_cell` — that is by design,
so the Layer 4 SSOT cannot be bypassed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from polaris.core.cell_matrix import (
    ROUTING_MID_MULT,
    CellKeyP0,
    resolve_routing_for_cell,
)

__all__ = [
    "SizingInput",
    "apply_cell_routing_mult",
    "resolve_cell_routing_mult",
]


@dataclass(frozen=True, slots=True)
class SizingInput:
    """Pre-cell-mult notional + cell-key context.

    ``pre_cell_notional`` is the product of base × continuous_scalar ×
    tier_amplifier from earlier T4 stages (Day 3+ scope).
    """

    pre_cell_notional: float
    cell_key: CellKeyP0
    now_ts: int


def resolve_cell_routing_mult(
    conn: sqlite3.Connection,
    cell_key: CellKeyP0,
    *,
    now_ts: int,
) -> float:
    """Production resolver: dispatches to Layer 4 SSOT.

    Wraps :func:`resolve_routing_for_cell` so the rest of Polaris can adjust
    cell mult sourcing (e.g. shadow context override) at this seam without
    every caller having to import from ``polaris.core.cell_matrix.routing``.
    """
    return resolve_routing_for_cell(conn, cell_key, now_ts=now_ts)


def apply_cell_routing_mult(
    conn: sqlite3.Connection,
    sizing: SizingInput,
) -> dict[str, float | str]:
    """Apply ``cell_routing_mult`` to the pre-cell notional (T4 step).

    Returns a structured dict so callers can audit the routing decision:
    ``{"pre_cell_notional", "cell_routing_mult", "post_cell_notional",
       "cell_key"}``.

    Negative or non-finite ``pre_cell_notional`` is rejected — sizing must
    propagate cleanly upstream rather than silently zeroing a corrupt input.
    """
    if not (sizing.pre_cell_notional == sizing.pre_cell_notional):  # NaN check
        raise ValueError("pre_cell_notional must not be NaN")
    if sizing.pre_cell_notional < 0.0:
        raise ValueError("pre_cell_notional must be ≥ 0")

    mult = resolve_cell_routing_mult(conn, sizing.cell_key, now_ts=sizing.now_ts)
    if mult <= 0.0:
        # Defensive: a cell mult of 0 would silently kill the trade. Spec
        # bottom is 0.5 (×0.5 suppress) — anything ≤ 0 is a bug.
        mult = ROUTING_MID_MULT

    post = sizing.pre_cell_notional * mult
    return {
        "pre_cell_notional": sizing.pre_cell_notional,
        "cell_routing_mult": mult,
        "post_cell_notional": post,
        "cell_key": _key_repr(sizing.cell_key),
    }


def _key_repr(key: CellKeyP0) -> str:
    return f"{key.exchange}:{key.strategy}:{key.ticker}:{key.regime}"
