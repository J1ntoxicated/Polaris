"""Layer 6 — conviction stacking gate (P0 stub).

Spec source: vault/30_components/layer-6-live-recalc.md (Q4).

4-gate check, max 3 layers (size mult 1.0 / 0.7 / 0.5), wait when full
(rotate forbidden — winner cuts kill aggressive bias).

P0: ``can_stack_conviction()`` enforces gates 2 + (max-layer count) and
``compute_stack_size_mult()`` returns the next layer's mult. Cell-quartile
gate + L3 headroom gate are caller responsibilities (real logic lives in
those layers).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

CONVICTION_MAX_LAYERS: Final[int] = 3
CONVICTION_LAYER_MULTS: Final[tuple[float, ...]] = (1.0, 0.7, 0.5)
CONVICTION_MIN_PNL_R: Final[float] = 0.5
CONVICTION_GROUP_CAP_MULT: Final[float] = 2.2


@dataclass(slots=True)
class ConvictionGateInputs:
    position_id: str
    cell_quartile: str  # "top" / "mid" / "bottom" / "cold"
    unrealized_pnl_r: float
    existing_layer_count: int
    layer_sum_size_pct: float
    single_trade_cap_pct: float
    headroom_pct: float


@dataclass(slots=True)
class ConvictionDecision:
    can_stack: bool
    next_layer_mult: float
    next_layer_index: int
    blocked_reason: str  # empty when can_stack=True


def can_stack_conviction(inputs: ConvictionGateInputs) -> ConvictionDecision:
    """Apply the 4-gate stacking rule.

    Gate order (vault Q4):
      1. cell quartile == top
      2. layer_sum ≤ single_trade_cap × 2.2
      3. unrealized_pnl_r ≥ +0.5R
      4. headroom min() > 0
    """
    if inputs.existing_layer_count >= CONVICTION_MAX_LAYERS:
        return ConvictionDecision(
            can_stack=False,
            next_layer_mult=0.0,
            next_layer_index=inputs.existing_layer_count,
            blocked_reason="max_layers",
        )
    if inputs.cell_quartile != "top":
        return ConvictionDecision(
            can_stack=False,
            next_layer_mult=0.0,
            next_layer_index=inputs.existing_layer_count,
            blocked_reason="not_top_quartile",
        )
    cap_check = inputs.single_trade_cap_pct * CONVICTION_GROUP_CAP_MULT
    if inputs.layer_sum_size_pct > cap_check:
        return ConvictionDecision(
            can_stack=False,
            next_layer_mult=0.0,
            next_layer_index=inputs.existing_layer_count,
            blocked_reason="group_cap_exceeded",
        )
    if inputs.unrealized_pnl_r < CONVICTION_MIN_PNL_R:
        return ConvictionDecision(
            can_stack=False,
            next_layer_mult=0.0,
            next_layer_index=inputs.existing_layer_count,
            blocked_reason="pnl_below_min",
        )
    if inputs.headroom_pct <= 0.0:
        return ConvictionDecision(
            can_stack=False,
            next_layer_mult=0.0,
            next_layer_index=inputs.existing_layer_count,
            blocked_reason="no_headroom",
        )
    next_idx = inputs.existing_layer_count
    mult = CONVICTION_LAYER_MULTS[next_idx]
    return ConvictionDecision(
        can_stack=True,
        next_layer_mult=mult,
        next_layer_index=next_idx,
        blocked_reason="",
    )


def compute_stack_size_mult(existing_layers: int) -> float:
    """Return the next layer's size multiplier (or 0.0 if max reached)."""
    if existing_layers >= CONVICTION_MAX_LAYERS:
        return 0.0
    return CONVICTION_LAYER_MULTS[existing_layers]


def count_layers(conn: sqlite3.Connection, *, position_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM position_conviction_layers WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return 0 if row is None else int(row[0])


__all__ = [
    "CONVICTION_GROUP_CAP_MULT",
    "CONVICTION_LAYER_MULTS",
    "CONVICTION_MAX_LAYERS",
    "CONVICTION_MIN_PNL_R",
    "ConvictionDecision",
    "ConvictionGateInputs",
    "can_stack_conviction",
    "compute_stack_size_mult",
    "count_layers",
]
