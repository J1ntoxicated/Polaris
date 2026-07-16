"""Gate 5 — Entry Sizer (Python P0, Sonnet P1).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G5)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4)

P0: dispatch the validated signal through ``polaris.core.sizing.compute_size``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

from polaris.core.learners.calibration_shadow import record_entry_snapshot
from polaris.core.pipeline.agents.judge_gate import size_up_boost
from polaris.core.pipeline.gate_state import (
    GATE_POSITION_MONITOR,
    GateContext,
    GateDecision,
    GateResult,
)
from polaris.core.sizing import (
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
    compute_size,
)
from polaris.core.sizing.probe_notional import resolve_strategy_class

__all__ = ["entry_sizer_gate"]


async def entry_sizer_gate(
    ctx: GateContext,
    *,
    conn: sqlite3.Connection,
    md_conn: sqlite3.Connection | None = None,
) -> GateResult:
    """Gate 5 dispatcher (Python deterministic T4).

    Inputs from ``ctx.payload``:
        ``signal_intent`` (SignalIntent | dict),
        ``risk_state`` (StrategyRiskState),
        ``portfolio`` (PortfolioState).

    ``md_conn`` (storage-split, 2026-07-16 final review MED): calibration_pairs
    is a MARKETDATA-domain table with a two-phase write — the entry INSERT
    here and the close UPDATE in ``_production_close_effects`` (already on
    ``state.md_conn``). Both phases must land in the SAME file or every
    (predicted, realized) pair splits across DBs and no reader ever sees a
    complete row. ``None`` falls back to ``conn`` (tests / single-DB callers).

    Fail-closed: any error → KILL (entry-side gate, Q4).
    """
    intent_raw = ctx.payload.get("signal_intent")
    risk_state = ctx.payload.get("risk_state")
    portfolio = ctx.payload.get("portfolio")
    if not isinstance(intent_raw, SignalIntent):
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_signal_intent"},
            model_used="python",
        )
    if not isinstance(risk_state, StrategyRiskState) or not isinstance(portfolio, PortfolioState):
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_risk_or_portfolio"},
            model_used="python",
        )

    # #32 axis-C SIZE_UP: the A+B-gated entry judge (G3/G4) stamps
    # ``ai_judge_size_up_intent`` into the result payload (merged into ctx.payload by
    # the orchestrator) ONLY in active mode + robust evidence. Thread its conviction
    # boost into the SAME single continuous scalar via ``judge_conviction`` — NOT a
    # post-hoc multiply, NOT a new T4 chain element (the fold + re-clamp happen inside
    # compute_size, mirroring regime_scalar; the 9-stack count is unchanged). Absent /
    # shadow / False → conviction 1.0 → byte-identical sizing (flow_not_block: never a
    # cut). The downstream tier_amp / cell_mult / single-trade cap / headroom_min() all
    # still bind and clip after, so SIZE_UP can never breach the absolute ceiling.
    if ctx.payload.get("ai_judge_size_up_intent") and intent_raw.judge_conviction == 1.0:
        intent_raw = replace(intent_raw, judge_conviction=size_up_boost())

    # G3 MODIFY strength_scalar: the SignalIntent in ctx.payload was built by
    # build_sizer_payload BEFORE the pipeline ran (the orchestrator primes
    # ctx.payload once, up front, then walks G1→G8 over the SAME ctx) — so it
    # cannot have known G3's verdict at construction time. G3's actual decision
    # (signal_validator_gate) is stamped separately into
    # ctx.payload["validated_signal"]["strength_scalar"] (PASS→1.0, MODIFY→
    # [0.5, 1.5], KILL never reaches this gate). Thread it onto the SAME single
    # continuous scalar here — the SAME payload→intent replace() seam as
    # judge_conviction above (NOT a new T4 multiplier slot; fold_strength_scalar
    # in compute_size does the ONE clamp). Absent / == 1.0 → no-op, byte-identical.
    validated_signal = ctx.payload.get("validated_signal")
    if isinstance(validated_signal, dict):
        try:
            g3_scalar = float(validated_signal.get("strength_scalar", 1.0))
        except (TypeError, ValueError):
            g3_scalar = 1.0
        if g3_scalar != intent_raw.strength_scalar:
            intent_raw = replace(intent_raw, strength_scalar=g3_scalar)

    # pts-classes (group D): thread the live EARN/PROVE/BENCH class for this
    # (venue, strategy) — a missing row (bootstrap hasn't run yet) resolves to
    # EARN, byte-identical to pre-pts-classes sizing.
    strategy_class = resolve_strategy_class(
        conn, venue=intent_raw.venue, strategy_id=intent_raw.strategy
    )
    intent_raw = replace(intent_raw, strategy_class=strategy_class)

    try:
        sized = compute_size(
            conn,
            intent=intent_raw,
            risk_state=risk_state,
            portfolio=portfolio,
            now_ts=int(ctx.started_ts),
        )
    except (ValueError, sqlite3.Error) as exc:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "sizing_error", "error": repr(exc)},
            model_used="python",
        )

    if sized.final_risk_pct <= 0.0:
        # Capital rotation (Jin 2026-05-30): surface the conviction-derived
        # ``proposed_risk_pct`` (the capital SCALE the sizer asked for BEFORE a
        # binding cap clipped final_risk_pct to 0) so the rotation trigger seam
        # can register this capital-blocked signal as a rotation candidate.
        # Display/telemetry only on the KILL — it does NOT re-open the entry and
        # adds NO T4 multiplier (9-stack ban intact).
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={
                "reason": "sizing_zero",
                "binding_cap": sized.binding_cap,
                # Observability (2026-07-10 ghost-row RCA gap-fix): a
                # human-readable detail (e.g. "track_headroom_exhausted") +
                # the RAW used/cap pct for the winning constraint, so a
                # sizing_zero KILL shows HOW oversubscribed it is at a glance
                # — display/RCA only, no sizing effect.
                "binding_reason": sized.binding_reason,
                "binding_used_pct": sized.binding_used_pct,
                "binding_cap_pct": sized.binding_cap_pct,
                "proposed_risk_pct": sized.proposed.proposed_risk_pct,
            },
            model_used="python",
        )

    # Probability calibration shadow (#4, G5) — snapshot THIS cell's predicted
    # p_pos at sizing time, for the SAME (venue, strategy, ticker, regime) key
    # compute_size just resolved cell_routing_mult from. Self-contained
    # fail-open (see calibration_shadow module docstring for the look-ahead
    # guard + why this lives here and not inside core/sizing). Measurement
    # only, never read by sizing (9-stack unaffected).
    # storage-split: calibration_pairs lives in the marketdata DB — write the
    # entry snapshot to the same file the close UPDATE targets (split-brain
    # guard, final review MED 2026-07-16).
    record_entry_snapshot(
        md_conn if md_conn is not None else conn,
        signal_id=intent_raw.signal_id, exchange=intent_raw.venue,
        strategy=intent_raw.strategy, ticker=intent_raw.symbol,
        regime=intent_raw.regime, now_ts=int(ctx.started_ts),
    )

    payload: dict[str, Any] = {
        "sized": {
            "final_risk_pct": sized.final_risk_pct,
            "final_notional_usd": sized.final_notional_usd,
            "leverage": sized.leverage,
            "binding_cap": sized.binding_cap,
            "proposal": {
                "base_risk_pct": sized.proposed.base_risk_pct,
                "continuous_scalar": sized.proposed.continuous_scalar,
                "tier_amplifier": sized.proposed.tier_amplifier,
                "cell_routing_mult": sized.proposed.cell_routing_mult,
                "listing_watchdog_mult": sized.proposed.listing_watchdog_mult,
                "proposed_risk_pct": sized.proposed.proposed_risk_pct,
            },
        }
    }
    return GateResult(
        decision=GateDecision.SIZED,
        next_gate=GATE_POSITION_MONITOR,
        payload=payload,
        model_used="python",
    )
