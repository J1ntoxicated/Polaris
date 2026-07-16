"""Gate 6 — Position Monitor (deterministic Python).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G6 + Q4 protective fail-open + Q8 swap)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Position Monitor)
- .claude/plans/ai_conductor_architecture_2026-05-30.md (P3 — per-position GPT branch removed)

Outputs: HOLD / ADJUST_EXIT / EXIT_NOW / SWAP_STRATEGY.

Decision contract (deterministic, no LLM):
- EXIT_NOW on stop hit (``pnl_r <= -max_loss_r``) — hard loss rail.
- SWAP_STRATEGY when a candidate matches Q8 (correlation_group/side/venue/symbol).
- ADJUST_EXIT in the winner-widen window (``pnl_r > +0.7R``).
- HOLD otherwise.

ai_conductor P3 (2026-05-30): the per-position GPT (P1) branch was removed. In
live telemetry G6 GPT returned HOLD 99.97% (3434 HOLD / 1 EXIT_NOW) — the model
never materially moved the decision, while the deterministic hard stop + Q8 swap
fast-path already owned every real action. Precise *signal* exits remain owned by
the G7 FSM (``evaluate_exit``), which runs downstream. This gate is now pure
Python: cost removed, behaviour preserved. The ``client`` / ``model`` /
``call_cache`` / ``tick_idx`` parameters are retained for caller compatibility
but are inert (no network call is ever made).

Aggressive bias (Jin 2026-05-07): DEMO/PAPER, virtual capital. The deterministic
rules never KILL a position defensively — only the hard loss rail exits, and
winners stay open (HOLD / ADJUST_EXIT widen).

Fail-open (Q4): missing position → HOLD (never KILL the position).

Conviction-pyramid writer (winner add-on judgment, sizing-change ``/debate``'d
— vault/50_research/debates/conviction_pyramid_addon_notional_2026-07-10.md):
when the caller threads ``conn`` AND ``ctx.payload["conviction_stack"]``, a
HOLD or ADJUST_EXIT decision (the position stays open) additionally runs
``conviction_writer.evaluate_conviction_stack`` and attaches its result under
``GateResult.payload["conviction_stack"]`` — a pure SIDE-CHANNEL judgment that
never changes the HOLD/ADJUST_EXIT/EXIT_NOW/SWAP_STRATEGY decision itself
(flow_not_block). Absent ``conn`` or the payload key → byte-identical to the
pre-writer gate (same opt-in shape as ``tighten_enabled``).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from polaris.core.pipeline.config import (
    TIME_STOP_K_DEFAULT,
    g6_probe_tighten_mode,
    time_stop_k_mult,
)
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GateContext,
    GateDecision,
    GateResult,
)
from polaris.core.probes.tighten_intent import PROBE_TIGHTEN_APPLY_ACTIONS

__all__ = [
    "DEFAULT_MAX_LOSS_R",
    "G6_DECISION_ENUM",
    "TIME_STOP_STANDARD_BARS",
    "WIDEN_WINDOW_R",
    "evaluate_strategy_swap",
    "position_monitor_gate",
]

logger = logging.getLogger(__name__)

WIDEN_WINDOW_R: float = 0.7
DEFAULT_MAX_LOSS_R: float = 1.0
G6_DECISION_ENUM: list[str] = ["HOLD", "ADJUST_EXIT", "EXIT_NOW", "SWAP_STRATEGY"]

# Time-stop backstop (stopless zombie cleanup — P1 fix). An unregistered
# strategy (no StrategyMetadata, e.g. a tick-engine id) has no
# expected_holding_bars to read: it falls back to its 1m-default timeframe x
# this many bars — the SAME 10-bar/600s standard `_production_recalc.
# _horizon_seconds_for` already uses for its own unregistered-strategy horizon
# fallback (this mirrors, does not import, that scripts-layer helper — core
# must not depend on scripts).
TIME_STOP_STANDARD_BARS: int = 10


def _time_stop_horizon(strategy_id: str) -> tuple[int, int]:
    """(horizon_seconds, horizon_bars) = expected_holding_bars x timeframe for
    a registered strategy; an unregistered strategy (no metadata — e.g. a
    tick-engine id) falls back to the "1m" timeframe x
    ``TIME_STOP_STANDARD_BARS`` (mirrors
    ``_production_atr.strategy_timeframe``'s own unregistered->"1m" fallback,
    duplicated locally rather than imported — core must not depend on
    scripts)."""
    # Lazy import (layer-cycle guard, mirrors the conviction_writer import
    # below): polaris.strategies / polaris.core.isolation.reentry must not be
    # imported at this module's top level.
    from polaris.core.isolation.reentry import bar_seconds  # noqa: PLC0415
    from polaris.strategies import STRATEGY_REGISTRY  # noqa: PLC0415

    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return bar_seconds("1m") * TIME_STOP_STANDARD_BARS, TIME_STOP_STANDARD_BARS
    bars = max(1, int(cls.metadata.expected_holding_bars))
    return bar_seconds(cls.metadata.timeframe) * bars, bars


def evaluate_strategy_swap(
    *,
    position: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    """Q8 — only allow swap when candidate matches correlation_group, side, venue, symbol."""
    if position.get("correlation_group") != candidate.get("correlation_group"):
        return False
    if position.get("side") != candidate.get("side"):
        return False
    if position.get("venue") != candidate.get("venue"):
        return False
    return position.get("symbol") == candidate.get("symbol")


def _python_decision(
    *,
    pos: dict[str, Any],
    pnl_r: float,
    max_loss_r: float,
    candidate: dict[str, Any] | None,
    probe_action: str | None,
    tighten_enabled: bool,
    time_stop_k: float,
) -> GateResult:
    """Deterministic decision rules (the sole G6 decision path)."""
    if pnl_r <= -abs(max_loss_r):
        return GateResult(
            decision=GateDecision.EXIT_NOW,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "stop_hit", "pnl_r": pnl_r},
            model_used="python",
        )
    # Time-stop backstop (stopless-zombie cleanup — P1 fix, round-3 scope fix):
    # a P&L-agnostic time rail, independent of and NEVER touching the -1.0R
    # rail above. Scoped to ``stop_price is None`` ONLY — that is the actual
    # incident this backstop exists for (a stop_price-null position has no
    # OTHER exit backstop; the ATR-trailing-stop system needs a stop_price to
    # ever trigger). A position that already carries a real trailing
    # stop_price is NOT stopless — it stays governed by that trail / the -1R
    # rail / G7, so a winner's unbounded tail (profit_target_r=None slow-trend
    # edge) is never clipped by a universal holding-period cap
    # (aggressive_always_profit / no_defensive_param_dampen). Missing
    # held_seconds/strategy keys degrade to 0/"" — never fires (fail-open,
    # same contract as the missing-position HOLD above).
    # Bars-seen PREFERRED over wall-clock (mirrors exit_thesis._has_matured's
    # maturity-gate precedent): when the caller threads ``native_bars_seen``
    # into ``pos``, the backstop judges elapsed development in NATIVE BARS the
    # strategy's own timeframe has seen since open — a session close / weekend
    # gap can no longer fake wall-clock "elapsed" into an early force-exit of a
    # still-valid slow-trend winner. Absent key (caller not yet migrated) ->
    # byte-identical wall-clock comparison.
    held_seconds = int(pos.get("held_seconds", 0) or 0)
    horizon_sec, horizon_bars = _time_stop_horizon(str(pos.get("strategy") or ""))
    time_stop_sec = horizon_sec * time_stop_k
    native_bars_seen_raw = pos.get("native_bars_seen")
    if native_bars_seen_raw is not None:
        time_stopped = int(native_bars_seen_raw) > horizon_bars * time_stop_k
    else:
        time_stopped = held_seconds > time_stop_sec
    if time_stopped and pos.get("stop_price") is None:
        return GateResult(
            decision=GateDecision.EXIT_NOW,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={
                "reason": "time_stop",
                "pnl_r": pnl_r,
                "held_seconds": held_seconds,
                "time_stop_sec": time_stop_sec,
            },
            model_used="python",
        )
    if isinstance(candidate, dict) and evaluate_strategy_swap(position=pos, candidate=candidate):
        return GateResult(
            decision=GateDecision.SWAP_STRATEGY,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"swap": candidate, "from_strategy": pos.get("strategy")},
            model_used="python",
        )
    if pnl_r > WIDEN_WINDOW_R:
        return GateResult(
            decision=GateDecision.ADJUST_EXIT,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "widen_window", "pnl_r": pnl_r},
            model_used="python",
        )
    # Probe protect consumer (flag-gated, default OFF pre-spawn / ON via botctl
    # ``_spawn_env`` — P3 promotion, 2026-07-16): in the adverse HOLD band (-1R <
    # pnl_r <= +0.7R, past the stop_hit rail and below the widen window) a latest
    # probe action of TIGHTEN or HARVEST (``PROBE_TIGHTEN_APPLY_ACTIONS`` — an
    # ALLOWLIST, so WIDEN structurally can never reach this path; WIDEN measured
    # -0.115R in the 2026-07-15 probe performance readout, TIGHTEN/HARVEST measured
    # net-positive) escalates the plain HOLD to ADJUST_EXIT carrying a
    # ``tighten_intent``. The recalc caller reads the latest probe_decisions row to
    # synthesise the tighter stop and routes it to G7's deterministic tighten rail.
    # flow_not_block — precise exit TIMING (a tighter trail), NEVER a HOLD->EXIT_NOW
    # block or a size cut; the -1.0R rail / swap / widen window / entry / size are all
    # untouched. Flag OFF → falls through to the byte-identical plain HOLD.
    if tighten_enabled and probe_action in PROBE_TIGHTEN_APPLY_ACTIONS:
        return GateResult(
            decision=GateDecision.ADJUST_EXIT,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "probe_tighten", "tighten_intent": True, "pnl_r": pnl_r},
            model_used="python",
        )
    return GateResult(
        decision=GateDecision.HOLD,
        next_gate=GATE_ADAPTIVE_EXIT,
        payload={"pnl_r": pnl_r},
        model_used="python",
    )


async def position_monitor_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    model: str | None = None,
    call_cache: Any | None = None,
    tick_idx: int = 0,
    tighten_enabled: bool | None = None,
    conn: sqlite3.Connection | None = None,
    conviction_active: bool | None = None,
    time_stop_k: float | None = None,
) -> GateResult:
    """Gate 6 dispatcher — deterministic Python (no LLM).

    Inputs from ``ctx.payload``:
        ``position`` (dict, required), ``unrealized_pnl_r`` (float),
        ``max_loss_r`` (float), ``swap_candidate`` (dict | None),
        ``probe_action`` (str | None — the latest probe engine action for this
        position, surfaced by the recalc caller from the probe_decisions sidecar),
        ``conviction_stack`` (dict | None — see ``conn``/``conviction_active`` below).

    The ``client`` / ``model`` / ``call_cache`` / ``tick_idx`` parameters are
    retained for caller compatibility (ai_conductor P3 removed the GPT branch)
    but are inert — no network call is ever made.

    ``tighten_enabled`` (probe TIGHTEN consumer, ``POLARIS_G6_PROBE_TIGHTEN``,
    default OFF; ``None`` reads the env): when ON an adverse HOLD-band position whose
    ``probe_action`` is TIGHTEN escalates to ADJUST_EXIT carrying ``tighten_intent``
    (flow_not_block — a tighter trail to G7, never a block/size cut). OFF →
    byte-identical to the pre-consumer HOLD.

    ``conn`` + ``ctx.payload["conviction_stack"]`` (conviction-pyramid writer,
    ``POLARIS_CONVICTION_PYRAMID_ACTIVE``, default OFF via ``conviction_active``
    ``None`` reading the env): when BOTH are supplied and the decision is HOLD
    or ADJUST_EXIT (position stays open), evaluates the next conviction layer
    and attaches the result under ``GateResult.payload["conviction_stack"]`` —
    see ``conviction_writer.evaluate_conviction_stack``. Absent either input →
    no-op (byte-identical). Never runs on EXIT_NOW / SWAP_STRATEGY.

    ``time_stop_k`` (stopless-zombie backstop multiplier, ``POLARIS_TIME_STOP_K``,
    default 4.0; ``None`` reads the env): a ``stop_price``-null position held
    past ``K x strategy_horizon_seconds`` is EXIT_NOW ("time_stop") —
    independent of and never touching the -1.0R rail. A position that already
    carries a real ``stop_price`` is not stopless and is never force-exited by
    this rail (winners run unbounded). ``env_value`` injectable for tests.

    Fail-open (Q4): missing position → HOLD (never KILL).
    """
    pos = ctx.payload.get("position", {})
    if not isinstance(pos, dict) or not pos:
        return GateResult(
            decision=GateDecision.HOLD,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "missing_position"},
            model_used="python",
        )
    pnl_r = float(ctx.payload.get("unrealized_pnl_r", 0.0))
    max_loss_r = float(ctx.payload.get("max_loss_r", DEFAULT_MAX_LOSS_R))
    candidate = ctx.payload.get("swap_candidate")
    candidate_dict: dict[str, Any] | None = (
        candidate if isinstance(candidate, dict) else None
    )
    use_tighten = (
        tighten_enabled if tighten_enabled is not None else g6_probe_tighten_mode()
    )
    probe_action_raw = ctx.payload.get("probe_action")
    probe_action = str(probe_action_raw) if isinstance(probe_action_raw, str) else None
    use_time_stop_k = time_stop_k if time_stop_k is not None else time_stop_k_mult()
    # Reject a non-positive injected K -> TIME_STOP_K_DEFAULT (same clamp as
    # time_stop_k_mult's env path — K<=0 would invert the P&L-agnostic time
    # rail into an exit-everything-immediately throttle).
    if use_time_stop_k <= 0.0:
        use_time_stop_k = TIME_STOP_K_DEFAULT
    result = _python_decision(
        pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r, candidate=candidate_dict,
        probe_action=probe_action, tighten_enabled=use_tighten,
        time_stop_k=use_time_stop_k,
    )
    if conn is not None and result.decision in (GateDecision.HOLD, GateDecision.ADJUST_EXIT):
        stack_inputs = ctx.payload.get("conviction_stack")
        if isinstance(stack_inputs, dict):
            # Lazy import (layer-cycle guard): conviction_writer imports back
            # into polaris.core.pipeline.agents.* (shadow_log/config/gate_state)
            # — a module-top-level import here would create a circular-import
            # ordering hazard the FIRST time this file is reached via
            # polaris.core.pipeline.agents.__init__ before conviction_writer has
            # ever been imported. Importing inside the (rarely-hit, opt-in)
            # branch defers it until every top-level module graph has settled.
            from polaris.core.live_recalc.conviction_writer import (  # noqa: PLC0415
                evaluate_conviction_stack,
            )

            try:
                stack_result = evaluate_conviction_stack(
                    conn,
                    position=pos,
                    unrealized_pnl_r=pnl_r,
                    cell_quartile=str(stack_inputs.get("cell_quartile", "")),
                    layer_sum_size_pct=float(stack_inputs.get("layer_sum_size_pct", 0.0)),
                    single_trade_cap_pct=float(stack_inputs.get("single_trade_cap_pct", 0.0)),
                    headroom_pct=float(stack_inputs.get("headroom_pct", 0.0)),
                    run_id=ctx.run_id,
                    regime=str(stack_inputs.get("regime", "")),
                    now_ts=int(ctx.started_ts),
                    active=conviction_active,
                )
            except sqlite3.Error as exc:
                # Shadow/writer failure must never crash the live G6 decision
                # already computed above (mirrors log_shadow_event's own
                # never-crash-the-hot-path contract).
                logger.warning(
                    "[conviction-stack] write failed position_id=%s: %r",
                    ctx.position_id, exc,
                )
                stack_result = None
            if stack_result is not None:
                result.payload["conviction_stack"] = {
                    "can_stack": stack_result.can_stack,
                    "blocked_reason": stack_result.blocked_reason,
                    "layer_index": stack_result.layer_index,
                    "next_layer_mult": stack_result.next_layer_mult,
                    "active": stack_result.active,
                    "layer_id": stack_result.layer_id,
                    "add_on_signal": stack_result.add_on_signal,
                }
    return result
