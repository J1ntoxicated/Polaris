"""Layer 2 — Custom asyncio orchestrator (LangGraph-style 8-gate state machine).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q1 orchestrator + Q4 mixed failure)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (8-gate flow)

Each call to ``run_signal_pipeline`` walks gates 1→8 sequentially for one
context, persisting one ``gate_events`` row per gate execution. Gate-specific
fail policy:
- Gate 3/4/5 fail-CLOSED: KILL → pipeline stops, signal lifecycle = KILLED.
- Gate 6/7/8 fail-OPEN: error → caller default (HOLD / current stop / drop lesson).

The orchestrator does not invent inputs — callers must seed ``ctx.payload``
with everything each gate needs. This keeps the orchestrator a pure dispatch
loop (no I/O beyond the ``gate_events`` log).
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from polaris.core.pipeline.agents import (
    adaptive_exit_gate,
    entry_sizer_gate,
    position_monitor_gate,
    post_trade_reflector_gate,
    pre_entry_watcher_gate,
    signal_validator_gate,
    strategy_signal_gate,
    universe_scanner_gate,
)
from polaris.core.pipeline.g1_focus_gate import G1FocusCache
from polaris.core.pipeline.gate_event_log import log_gate_event
from polaris.core.pipeline.gate_state import (
    ENTRY_GATES,
    GATE_ADAPTIVE_EXIT,
    GATE_ENTRY_SIZER,
    GATE_POSITION_MONITOR,
    GATE_POST_TRADE_REFLECTOR,
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GATE_STRATEGY_SIGNAL,
    GATE_UNIVERSE_SCANNER,
    POSITION_GATES,
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)

# One-way lifecycle invariant: each gate may only run when ctx.state is in a
# spec-permitted predecessor set (Q1 + Q8). RAW is only valid at the entry
# gates (G1/G2/G3) — late gates require explicit seeding by the caller, so
# tests / smoke must seed the appropriate predecessor (e.g. WATCHED to start
# at G5). Skipping is intentionally impossible: the orchestrator emits a
# synthetic KILL when the predecessor invariant is violated.
GATE_PREREQ_STATES: dict[int, frozenset[SignalLifecycle]] = {
    GATE_UNIVERSE_SCANNER: frozenset({SignalLifecycle.RAW}),
    GATE_STRATEGY_SIGNAL: frozenset({SignalLifecycle.RAW}),
    GATE_SIGNAL_VALIDATOR: frozenset({SignalLifecycle.RAW}),
    GATE_PRE_ENTRY_WATCHER: frozenset({SignalLifecycle.VALIDATED}),
    GATE_ENTRY_SIZER: frozenset({SignalLifecycle.WATCHED}),
    GATE_POSITION_MONITOR: frozenset(
        {SignalLifecycle.SIZED, SignalLifecycle.ACTIVE, SignalLifecycle.MONITORED}
    ),
    GATE_ADAPTIVE_EXIT: frozenset(
        {SignalLifecycle.ACTIVE, SignalLifecycle.MONITORED, SignalLifecycle.EXIT_PENDING}
    ),
    GATE_POST_TRADE_REFLECTOR: frozenset(
        {
            SignalLifecycle.CLOSED,
            SignalLifecycle.EXIT_PENDING,
        }
    ),
}

logger = logging.getLogger(__name__)

__all__ = ["GateOrchestrator", "log_gate_event", "run_signal_pipeline"]


# Coroutine signature for gate handlers.
GateHandler = Callable[..., Awaitable[GateResult]]


class GateOrchestrator:
    """8-gate sequential orchestrator with mixed failure policy.

    Construct with optional handler overrides for tests. The default mapping
    uses the production agents from ``polaris.core.pipeline.agents``.
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        haiku_client: Any | None = None,
        handlers: dict[int, GateHandler] | None = None,
        phase: str = "P0",
        g1_focus_cache: G1FocusCache | None = None,
    ) -> None:
        """Build a P0/P1 orchestrator.

        ``phase`` selects the model split per spec
        (``vault/30_components/layer-2-per-gate-pipeline.md`` Q3 +
        ``vault/10_decisions/ADR-004-per-gate-ai-pipeline.md`` §Phase):

        - **P0** (default): G1/G3/G4 = GPT, G2/G5/G6/G7/G8 = Python
          template/deterministic.
        - **P1**: G7 adaptive-exit may use ``haiku_client``; G6/G8 are
          deterministic Python at every phase (ai_conductor P2+P3 removed
          their per-signal GPT branches — see the agent module docstrings).
        """
        if phase not in ("P0", "P1"):
            raise ValueError(f"phase must be 'P0' or 'P1', got {phase!r}")
        self.conn = conn
        self.haiku_client = haiku_client
        self.phase = phase
        # G1-EFF: shared on-change-only focus cache. When supplied (production
        # loop), the G1 GPT call is reused across signals/ticks while the
        # universe composition is unchanged (efficiency only; focus DECISION
        # still GPT-chosen). None → G1 calls GPT every cycle (back-compat).
        self.g1_focus_cache = g1_focus_cache
        self.handlers: dict[int, GateHandler] = handlers or {
            GATE_UNIVERSE_SCANNER: self._wrap_universe,
            GATE_STRATEGY_SIGNAL: self._wrap_strategy,
            GATE_SIGNAL_VALIDATOR: self._wrap_validator,
            GATE_PRE_ENTRY_WATCHER: self._wrap_watcher,
            GATE_ENTRY_SIZER: self._wrap_sizer,
            GATE_POSITION_MONITOR: self._wrap_monitor,
            GATE_ADAPTIVE_EXIT: self._wrap_exit,
            GATE_POST_TRADE_REFLECTOR: self._wrap_reflector,
        }

    async def run(self, ctx: GateContext, *, start_gate: int = GATE_UNIVERSE_SCANNER) -> list[GateResult]:
        """Walk the pipeline starting at ``start_gate`` until exhaustion.

        Each gate enforces a predecessor invariant (one-way lifecycle —
        ``GATE_PREREQ_STATES``). A start_gate whose prereq is not satisfied
        terminates the run with a synthetic KILL event so callers cannot
        silently bypass validation.
        """
        results: list[GateResult] = []
        current: int | None = start_gate
        logger.debug(
            "[orchestrator] run start_gate=G%s run_id=%s signal_id=%s symbol=%s/%s state=%s",
            start_gate,
            ctx.run_id,
            ctx.signal_id,
            ctx.venue,
            ctx.symbol,
            ctx.state.value,
        )
        while current is not None:
            handler = self.handlers.get(current)
            if handler is None:
                logger.debug("[orchestrator] no handler for G%s — terminating", current)
                break

            allowed = GATE_PREREQ_STATES.get(current, frozenset({SignalLifecycle.RAW}))
            if ctx.state not in allowed:
                # One-way invariant violation: terminate with synthetic KILL.
                ctx.gate_id = current
                kill = GateResult(
                    decision=GateDecision.KILL,
                    next_gate=None,
                    payload={
                        "reason": "lifecycle_invariant_violation",
                        "expected_predecessor": [s.value for s in allowed],
                        "actual": ctx.state.value,
                    },
                    model_used="python",
                    error="lifecycle_invariant",
                )
                results.append(kill)
                log_gate_event(self.conn, ctx, kill)
                logger.warning(
                    "[G%s] lifecycle invariant violation: state=%s expected=%s — KILL",
                    current,
                    ctx.state.value,
                    [s.value for s in allowed],
                )
                ctx.state = SignalLifecycle.KILLED
                break

            ctx.gate_id = current
            started = time.monotonic()
            try:
                result = await handler(ctx)
            except Exception as exc:  # noqa: BLE001 — capture for failure policy
                latency = int((time.monotonic() - started) * 1000)
                result = self._fallback_result(current, error=repr(exc), latency_ms=latency)
                logger.error(
                    "[G%s] exception in handler: %r (latency=%dms) → fallback decision=%s",
                    current,
                    exc,
                    latency,
                    result.decision.value,
                )

            results.append(result)
            self._stamp_payload(ctx, result)
            prev_state = ctx.state.value
            self._update_lifecycle(ctx, current, result)
            log_gate_event(self.conn, ctx, result)

            # Level discipline: KILL = normal AI filtering (WARNING, not ERROR —
            # an AI gate rejecting a signal is expected flow, not a fault; logging
            # it at ERROR inflated the dashboard error count with ~269 false
            # positives). A genuine handler exception is surfaced as WARNING via
            # result.error. PASS/PROCEED/SIZED/HOLD = milestone (INFO).
            if result.decision == GateDecision.KILL or result.error:
                level = logging.WARNING
            else:
                level = logging.INFO
            logger.log(
                level,
                "[G%s %s/%s] decision=%s reason=%s model=%s latency=%dms "
                "%s→%s next=G%s",
                current,
                ctx.venue,
                ctx.symbol,
                result.decision.value,
                result.payload.get("reason", "-"),
                result.model_used,
                result.latency_ms,
                prev_state,
                ctx.state.value,
                result.next_gate if result.next_gate is not None else "-",
            )

            if result.decision == GateDecision.KILL:
                ctx.state = SignalLifecycle.KILLED
                logger.info(
                    "[orchestrator] pipeline KILL at G%s (run_id=%s signal_id=%s)",
                    current,
                    ctx.run_id,
                    ctx.signal_id,
                )
                break
            current = result.next_gate
        return results

    # ------------------------------------------------------------------
    # Wrappers — bind shared resources (conn, client) to each agent.
    # ------------------------------------------------------------------

    async def _wrap_universe(self, ctx: GateContext) -> GateResult:
        # G1 here = NON-AUTHORITATIVE structural pass-through (B3 #5, audit
        # 2026-06-23): it always PASSes and emits a display-only focus echo that
        # NO downstream gate reads. The authoritative G1 universe/eligibility
        # selection is Layer-0 ``rank_active_universe`` / ``compute_dynamic_focus``
        # (run in the L0 producers, persisted as ``is_active=1``); this gate does
        # not select the universe. Deterministic scored ranker — no GPT client.
        # ``focus_cache`` gates the RECOMPUTE trigger only.
        return await universe_scanner_gate(
            ctx, focus_cache=self.g1_focus_cache,
        )

    async def _wrap_strategy(self, ctx: GateContext) -> GateResult:
        return await strategy_signal_gate(ctx)

    async def _wrap_validator(self, ctx: GateContext) -> GateResult:
        # ai_conductor P0 SHADOW: pass the conn so the G3 deterministic technical
        # rule is computed + logged vs the live GPT decision (behavior 0 — the
        # GPT decision is still what drives the pipeline).
        return await signal_validator_gate(
            ctx, client=self.haiku_client, shadow_conn=self.conn
        )

    async def _wrap_watcher(self, ctx: GateContext) -> GateResult:
        # ai_conductor P0 SHADOW: same as G3 — G4 technical rule logged in
        # parallel (PROCEED default / stale-crossed KILL only); GPT still decides.
        return await pre_entry_watcher_gate(
            ctx, client=self.haiku_client, shadow_conn=self.conn
        )

    async def _wrap_sizer(self, ctx: GateContext) -> GateResult:
        if self.conn is None:
            return GateResult(
                decision=GateDecision.KILL,
                next_gate=None,
                payload={"reason": "no_conn"},
                model_used="python",
            )
        return await entry_sizer_gate(ctx, conn=self.conn)

    async def _wrap_monitor(self, ctx: GateContext) -> GateResult:
        # ai_conductor P3 (2026-05-30): G6 is now deterministic Python — the
        # per-position GPT branch was removed (live GPT was HOLD 99.97%; the
        # hard stop + Q8 swap fast-path already owned every real action).
        return await position_monitor_gate(ctx)

    async def _wrap_exit(self, ctx: GateContext) -> GateResult:
        if self.phase == "P1":
            # G7 divergence SHADOW (instrumentation only): log the Q9 rail
            # decision vs the live GPT decision; the GPT decision still drives.
            return await adaptive_exit_gate(
                ctx, client=self.haiku_client, shadow_conn=self.conn
            )
        return await adaptive_exit_gate(ctx, client=None)

    async def _wrap_reflector(self, ctx: GateContext) -> GateResult:
        # ai_conductor P2 (2026-05-30): G8 is now a deterministic Python
        # template — the P1/GPT lesson branch was removed (the real learning
        # is posterior NIG + cell EWMA consuming pnl_r/won; ``ai_lessons`` has
        # zero readers, so the GPT lesson text was a paid no-op).
        return await post_trade_reflector_gate(ctx, conn=self.conn)

    # ------------------------------------------------------------------
    # Failure policy
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_result(gate_id: int, *, error: str, latency_ms: int) -> GateResult:
        """Mixed failure mode (Q4)."""
        if gate_id in ENTRY_GATES:
            return GateResult(
                decision=GateDecision.KILL,
                next_gate=None,
                payload={"reason": "exception", "error": error},
                model_used="python",
                latency_ms=latency_ms,
                error=error,
            )
        if gate_id == GATE_POST_TRADE_REFLECTOR:
            # Reflector is observability-only (Q4): drop the lesson and
            # finish with REFLECTED — never leave the lifecycle non-terminal.
            return GateResult(
                decision=GateDecision.REFLECTED,
                next_gate=None,
                payload={"reason": "reflector_fail_open_drop_lesson", "error": error},
                model_used="python",
                latency_ms=latency_ms,
                error=error,
            )
        if gate_id in POSITION_GATES:
            # Protective fail-open — keep current state, advance to next gate.
            next_gate = _next_position_gate(gate_id)
            return GateResult(
                decision=GateDecision.HOLD,
                next_gate=next_gate,
                payload={"reason": "exception_fail_open", "error": error},
                model_used="python",
                latency_ms=latency_ms,
                error=error,
            )
        # G1 / G2: fail-open (G1 keeps focus = prev cycle; G2 strategy_local KILL).
        if gate_id == GATE_UNIVERSE_SCANNER:
            return GateResult(
                decision=GateDecision.PASS,
                next_gate=GATE_STRATEGY_SIGNAL,
                payload={"reason": "exception_fail_open", "focus": []},
                model_used="python",
                latency_ms=latency_ms,
                error=error,
            )
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "exception_strategy_local", "error": error},
            model_used="python",
            latency_ms=latency_ms,
            error=error,
        )

    # ------------------------------------------------------------------
    # Lifecycle stamping
    # ------------------------------------------------------------------

    @staticmethod
    def _stamp_payload(ctx: GateContext, result: GateResult) -> None:
        for k, v in result.payload.items():
            ctx.payload[k] = v

    @staticmethod
    def _update_lifecycle(ctx: GateContext, gate_id: int, result: GateResult) -> None:
        """One-way lifecycle transitions (cannot regress)."""
        if result.decision == GateDecision.KILL:
            ctx.state = SignalLifecycle.KILLED
            return
        if gate_id == GATE_SIGNAL_VALIDATOR and result.decision in (
            GateDecision.PASS,
            GateDecision.MODIFY,
        ):
            ctx.state = SignalLifecycle.VALIDATED
        elif gate_id == GATE_PRE_ENTRY_WATCHER and result.decision == GateDecision.PROCEED:
            ctx.state = SignalLifecycle.WATCHED
        elif gate_id == GATE_ENTRY_SIZER and result.decision == GateDecision.SIZED:
            ctx.state = SignalLifecycle.SIZED
        elif gate_id == GATE_POSITION_MONITOR and result.decision in (
            GateDecision.HOLD,
            GateDecision.ADJUST_EXIT,
            GateDecision.SWAP_STRATEGY,
            GateDecision.EXIT_NOW,
        ):
            # First time reaching G6: SIZED → ACTIVE (entry confirmed).
            if ctx.state == SignalLifecycle.SIZED:
                ctx.state = SignalLifecycle.ACTIVE
            # Subsequent monitor passes: ACTIVE/MONITORED → MONITORED.
            elif ctx.state in (SignalLifecycle.ACTIVE, SignalLifecycle.MONITORED):
                ctx.state = SignalLifecycle.MONITORED
            if result.decision == GateDecision.EXIT_NOW:
                ctx.state = SignalLifecycle.EXIT_PENDING
        elif gate_id == GATE_ADAPTIVE_EXIT:
            # EXIT_PENDING → CLOSED at G7 completion (venue adapter's close
            # fill is normalized to CLOSED externally; the in-process pipeline
            # advances here so G8 sees a CLOSED predecessor).
            if ctx.state == SignalLifecycle.EXIT_PENDING:
                ctx.state = SignalLifecycle.CLOSED
            elif (
                result.decision == GateDecision.ADJUST_EXIT
                and ctx.state == SignalLifecycle.ACTIVE
            ):
                ctx.state = SignalLifecycle.MONITORED
        elif gate_id == GATE_POST_TRADE_REFLECTOR and result.decision == GateDecision.REFLECTED:
            # CLOSED → REFLECTED is the canonical end (G8 prereq is
            # {CLOSED, EXIT_PENDING}; EXIT_PENDING is allowed only when the
            # smoke/test path skips G7 and seeds it directly).
            ctx.state = SignalLifecycle.REFLECTED


def _next_position_gate(gate_id: int) -> int | None:
    """Successor for protective fail-open in 6/7/8."""
    return {
        GATE_POSITION_MONITOR: GATE_ADAPTIVE_EXIT,
        GATE_ADAPTIVE_EXIT: GATE_POST_TRADE_REFLECTOR,
        GATE_POST_TRADE_REFLECTOR: None,
    }.get(gate_id)


# ---------------------------------------------------------------------------
# Functional API (one-shot call)
# ---------------------------------------------------------------------------


async def run_signal_pipeline(
    *,
    run_id: str | None = None,
    venue: str,
    symbol: str,
    strategy_id: str | None,
    payload: dict[str, Any],
    conn: sqlite3.Connection | None = None,
    haiku_client: Any | None = None,
    start_gate: int = GATE_UNIVERSE_SCANNER,
    initial_state: SignalLifecycle | None = None,
    phase: str = "P0",
) -> tuple[GateContext, list[GateResult]]:
    """One-shot pipeline run helper for tests / smoke / scripts.

    ``initial_state`` defaults to ``RAW`` for entry-side gates (G1/G2/G3) and
    must be set explicitly for late gates so the lifecycle invariant cannot
    be bypassed (codex round-1 P0). E.g. starting at G5 requires
    ``initial_state=SignalLifecycle.WATCHED``.

    ``phase`` (default ``"P0"``) drives the model split — see
    ``GateOrchestrator.__init__``. P0 forces G8 to Python template even
    when ``haiku_client`` is supplied for G1/G3/G4.

    Returns the final ``GateContext`` and the list of ``GateResult`` objects.
    """
    if initial_state is None:
        # Pick the canonical predecessor for ``start_gate`` so the orchestrator
        # invariant is satisfied without the caller having to know the map.
        # Tests may still pass any state explicitly.
        canonical = {
            GATE_UNIVERSE_SCANNER: SignalLifecycle.RAW,
            GATE_STRATEGY_SIGNAL: SignalLifecycle.RAW,
            GATE_SIGNAL_VALIDATOR: SignalLifecycle.RAW,
            GATE_PRE_ENTRY_WATCHER: SignalLifecycle.VALIDATED,
            GATE_ENTRY_SIZER: SignalLifecycle.WATCHED,
            GATE_POSITION_MONITOR: SignalLifecycle.SIZED,
            GATE_ADAPTIVE_EXIT: SignalLifecycle.MONITORED,
            GATE_POST_TRADE_REFLECTOR: SignalLifecycle.CLOSED,
        }
        initial_state = canonical.get(start_gate, SignalLifecycle.RAW)

    ctx = GateContext(
        run_id=run_id or uuid.uuid4().hex,
        signal_id=payload.get("signal_id"),
        position_id=payload.get("position_id"),
        gate_id=start_gate,
        venue=venue,
        symbol=symbol,
        strategy_id=strategy_id,
        payload=dict(payload),
        started_ts=int(time.time()),
        state=initial_state,
    )
    orch = GateOrchestrator(conn=conn, haiku_client=haiku_client, phase=phase)
    results = await orch.run(ctx, start_gate=start_gate)
    return ctx, results
