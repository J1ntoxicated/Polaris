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
import uuid
from dataclasses import dataclass
from typing import Any, Final

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


def build_stack_signal(position: dict[str, Any], size_mult: float) -> dict[str, Any]:
    """Build the add-on order's SIGNAL shape for the next conviction layer.

    9-stack guard (permanent — [[conviction_pyramid_addon_notional_2026-07-10]]
    debate D1): ``signal_strength`` is pinned to ``1.0`` (baseline) — ``size_mult``
    travels ONLY in the separate ``layer_size_mult`` field, NEVER folded into
    signal_strength / continuous_scalar / judge_conviction / strength_scalar or
    any other T4 pre-clip term.

    This function is intentionally side-effect-free and does NOT compute a
    notional. The (deferred, later-slice) live dispatcher is responsible for
    re-running the add-on through ``polaris.core.sizing.compute_size`` — with
    ``signal_strength=1.0`` / ``judge_conviction=1.0`` / ``strength_scalar=1.0``
    so the FULL hard-cap chain (per-symbol/per-strategy/per-track/cluster/
    daily/headroom_min) is re-verified against current portfolio state — and
    THEN applying ``layer_size_mult`` to the resulting (already-capped)
    ``final_notional_usd`` as a pure post-hoc shrink (``layer_size_mult <= 1.0``
    always, so this can only shrink the capped result, never breach it).
    ``compute_size()`` has caller-visible side effects (R-budget shadow
    observation, Alpaca ladder draw, PROVE/BENCH probe-fee accrual /
    shadow-fill recording) — the dispatcher MUST isolate or account for those
    before firing an add-on quote through it (debate D1 blind spot).
    """
    return {
        "venue": position.get("venue", ""),
        "symbol": position.get("symbol", ""),
        "side": position.get("side", ""),
        "strategy": position.get("strategy") or position.get("strategy_id", ""),
        "underlying_group_id": position.get("underlying_group_id", ""),
        "correlation_group": position.get("correlation_group", ""),
        "parent_position_id": position.get("position_id", ""),
        "signal_strength": 1.0,
        "layer_size_mult": size_mult,
    }


def record_conviction_layer(
    conn: sqlite3.Connection,
    *,
    position: dict[str, Any],
    layer_index: int,
    size_mult: float,
    now_ts: int,
) -> str | None:
    """INSERT one ``position_conviction_layers`` row; returns the new ``layer_id``.

    Idempotent-insert race guard (debate D3/round-2 blind spot): a second call
    for the SAME ``(position_id, layer_index)`` is a no-op (returns ``None``,
    no duplicate row) — ``layer_id`` is a random uuid primary key so a naive
    INSERT would otherwise happily create two rows claiming the same layer
    slot under concurrent access.
    """
    layer_id = uuid.uuid4().hex
    position_id = str(position.get("position_id", ""))
    cur = conn.execute(
        "INSERT INTO position_conviction_layers "
        "(layer_id, position_id, layer_index, size_mult, opened_ts, "
        " strategy_id, venue, symbol, side) "
        "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE NOT EXISTS ("
        "  SELECT 1 FROM position_conviction_layers "
        "  WHERE position_id = ? AND layer_index = ?"
        ")",
        (
            layer_id,
            position_id,
            layer_index,
            size_mult,
            now_ts,
            str(position.get("strategy") or position.get("strategy_id", "")),
            str(position.get("venue", "")),
            str(position.get("symbol", "")),
            str(position.get("side", "")),
            position_id,
            layer_index,
        ),
    )
    return layer_id if cur.rowcount > 0 else None


__all__ = [
    "CONVICTION_GROUP_CAP_MULT",
    "CONVICTION_LAYER_MULTS",
    "CONVICTION_MAX_LAYERS",
    "CONVICTION_MIN_PNL_R",
    "ConvictionDecision",
    "ConvictionGateInputs",
    "build_stack_signal",
    "can_stack_conviction",
    "compute_stack_size_mult",
    "count_layers",
    "record_conviction_layer",
]
