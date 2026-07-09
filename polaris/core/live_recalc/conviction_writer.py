"""Layer 6 — conviction stacking WRITER (G6 caller-side glue).

Bridges the pure ``conviction.py`` gate (``can_stack_conviction`` +
``compute_stack_size_mult``) to (a) a durable ``position_conviction_layers``
row and (b) an add-on order SIGNAL — the piece ``conviction.py``'s own
docstring named a caller responsibility ("Cell-quartile gate + L3 headroom
gate are caller responsibilities").

Sizing-change ``/debate`` consensus (2 rounds — Jin conviction-pyramiding
mandate): ``vault/50_research/debates/conviction_pyramid_addon_notional_2026-07-10.md``

- **D1 (notional formula)**: the add-on's notional is NEVER computed here.
  ``compute_size()`` (T4) has caller-visible side effects (R-budget shadow
  observation, Alpaca ladder draw, PROVE/BENCH probe-fee accrual / shadow-fill
  recording) that a naive re-invocation from this writer would duplicate. This
  slice only builds the SIGNAL SHAPE (``conviction.build_stack_signal`` —
  ``signal_strength`` pinned to baseline 1.0); re-running it through T4 (with
  ``layer_size_mult`` applied post-hoc to the already-capped
  ``final_notional_usd``) and actually dispatching the order is the explicit
  responsibility of a LATER dispatcher slice.
- **D2 (rollout)**: shadow-first. Every judgment (can_stack True/False) is
  ALWAYS logged to ``gate_shadow_events`` (mirrors the existing G3/G4
  technical-vs-GPT shadow pattern; G6 P3 has no GPT counterpart so
  ``gpt_decision=None``, ``mismatch`` always 0). The real DB write (a new
  layer row) is gated behind ``POLARIS_CONVICTION_PYRAMID_ACTIVE`` (default
  OFF) — mirrors the ``core/sizing/r_budget_sizer.py`` shadow-verify
  precedent.
- **D3 (group-cap integrity)**: ``can_stack_conviction`` itself is NOT
  modified (already implemented + tested; reuse mandate). Instead this
  writer passes a PROJECTED ``layer_sum_size_pct`` (current committed sum +
  this prospective layer's own conservative upper-bound share,
  ``single_trade_cap_pct * compute_stack_size_mult(existing_layer_count)``)
  so a layer that would PUSH the running total past
  ``single_trade_cap_pct * CONVICTION_GROUP_CAP_MULT`` is blocked before it
  is recorded, not one tick late. The projected value is a gate-input-only
  upper-bound approximation — it is NEVER persisted as a real risk/notional
  figure (``position_conviction_layers.size_mult`` still records the plain
  ``1.0/0.7/0.5`` layer multiplier).

9-stack guard: this module never touches the T4 continuous-scalar chain — it
does not import ``polaris.core.sizing`` at all (statically verified by
``tests/test_conviction_pyramid_writer.py``).

flow_not_block / aggressive_always_profit: conviction stacking only ever ADDS
to a proven winner (``pnl_r >= +0.5R``); it never trims, exits, or blocks the
base position's own decision (G6's HOLD/ADJUST_EXIT/EXIT_NOW/SWAP_STRATEGY
enum is untouched by this module).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from polaris.core.live_recalc.conviction import (
    ConvictionGateInputs,
    build_stack_signal,
    can_stack_conviction,
    compute_stack_size_mult,
    count_layers,
    record_conviction_layer,
)
from polaris.core.pipeline.agents._shadow_rules import ShadowDecision
from polaris.core.pipeline.agents.shadow_log import log_shadow_event
from polaris.core.pipeline.config import conviction_pyramid_active
from polaris.core.pipeline.gate_state import GATE_POSITION_MONITOR, GateDecision

__all__ = ["ConvictionStackWrite", "evaluate_conviction_stack"]


@dataclass(frozen=True, slots=True)
class ConvictionStackWrite:
    """Result of one G6-side conviction-stack evaluation."""

    can_stack: bool
    blocked_reason: str
    layer_index: int
    next_layer_mult: float
    active: bool  # True iff a real INSERT + add-on signal were produced
    layer_id: str | None = None
    add_on_signal: dict[str, Any] | None = None


def evaluate_conviction_stack(
    conn: sqlite3.Connection | None,
    *,
    position: dict[str, Any],
    unrealized_pnl_r: float,
    cell_quartile: str,
    layer_sum_size_pct: float,
    single_trade_cap_pct: float,
    headroom_pct: float,
    run_id: str,
    regime: str = "",
    now_ts: int = 0,
    active: bool | None = None,
) -> ConvictionStackWrite | None:
    """Judge + (ACTIVE mode only) record the next conviction layer.

    Fail-open: a missing ``conn`` returns ``None`` (no judgment possible, no
    crash) — mirrors ``log_shadow_event``'s own no-op-on-None-conn contract;
    the caller's core G6 HOLD/ADJUST_EXIT decision is never affected either
    way.

    ``layer_sum_size_pct`` is the CALLER's currently-committed sum (debate
    D3): this function projects it forward by the prospective layer's own
    conservative upper-bound share before handing it to
    ``can_stack_conviction`` — see module docstring.
    """
    if conn is None:
        return None
    position_id = str(position.get("position_id", ""))
    existing_layer_count = count_layers(conn, position_id=position_id)
    prospective_mult = compute_stack_size_mult(existing_layer_count)
    projected_layer_sum_size_pct = layer_sum_size_pct + single_trade_cap_pct * prospective_mult

    inputs = ConvictionGateInputs(
        position_id=position_id,
        cell_quartile=cell_quartile,
        unrealized_pnl_r=unrealized_pnl_r,
        existing_layer_count=existing_layer_count,
        layer_sum_size_pct=projected_layer_sum_size_pct,
        single_trade_cap_pct=single_trade_cap_pct,
        headroom_pct=headroom_pct,
    )
    decision = can_stack_conviction(inputs)
    is_active = conviction_pyramid_active() if active is None else active

    # Always observe (shadow-first, D2) — SIZED stands in for "would stack a
    # new layer", HOLD for "stays flat". No GPT counterpart exists for G6 P3
    # (mismatch always 0). Reuses the existing gate_shadow_events shape
    # rather than inventing a new table.
    log_shadow_event(
        conn,
        run_id=run_id,
        signal_id=position_id,
        gate_id=GATE_POSITION_MONITOR,
        venue=str(position.get("venue", "")),
        symbol=str(position.get("symbol", "")),
        regime=regime,
        technical=ShadowDecision(
            decision=GateDecision.SIZED if decision.can_stack else GateDecision.HOLD,
            scalar=decision.next_layer_mult,
            reason=decision.blocked_reason or f"would_stack_layer_{decision.next_layer_index}",
        ),
        gpt_decision=None,
        cell_warm=(cell_quartile == "top"),
    )

    if not decision.can_stack or not is_active:
        return ConvictionStackWrite(
            can_stack=decision.can_stack,
            blocked_reason=decision.blocked_reason,
            layer_index=decision.next_layer_index,
            next_layer_mult=decision.next_layer_mult,
            active=False,
        )

    # ACTIVE + can_stack — max-3 clamp is already enforced inside
    # can_stack_conviction (existing_layer_count >= CONVICTION_MAX_LAYERS →
    # blocked_reason="max_layers", handled by the branch above).
    layer_id = record_conviction_layer(
        conn,
        position=position,
        layer_index=decision.next_layer_index,
        size_mult=decision.next_layer_mult,
        now_ts=now_ts,
    )
    if layer_id is None:
        # Idempotent-insert race guard fired (concurrent duplicate at this
        # layer_index) — treat as shadow-only for THIS call, no add-on signal.
        return ConvictionStackWrite(
            can_stack=decision.can_stack,
            blocked_reason="duplicate_layer_index",
            layer_index=decision.next_layer_index,
            next_layer_mult=decision.next_layer_mult,
            active=False,
        )
    add_on_signal = build_stack_signal(position, decision.next_layer_mult)
    return ConvictionStackWrite(
        can_stack=True,
        blocked_reason="",
        layer_index=decision.next_layer_index,
        next_layer_mult=decision.next_layer_mult,
        active=True,
        layer_id=layer_id,
        add_on_signal=add_on_signal,
    )
