"""Gate 3 — Signal Validator (GPT P0, fail-closed).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G3 + Q4 fail-closed + Q6 prompt)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Signal Validator)

Behavior:
- Input: raw_signal + cell_matrix routing summary + ticker baseline + recent same-symbol trades.
- Output: PASS / KILL / MODIFY. MODIFY carries ``strength_scalar in [0.5, 1.5]``.
- Fail-closed: timeout / parse error → KILL.

Migration (2026-05-07): Anthropic Haiku → OpenAI GPT (Jin mandate).
Aggressive bias preserved — no defensive throttle on PASS rate.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Final

from polaris.core.cell_matrix.routing import _quantile
from polaris.core.cell_matrix.schema import CELL_MIN_LIVE_N
from polaris.core.pipeline.agents._gpt_client import (
    DEFAULT_TIMEOUT_SEC,
    GPTCallResult,
    call_gpt,
    make_system_prefix,
)
from polaris.core.pipeline.agents._shadow_rules import (
    CELL_WARM_MIN_N_EFF,
    G3ShadowInputs,
    ShadowDecision,
    g3_shadow_inputs_from_payload,
    technical_validate_decision,
)
from polaris.core.pipeline.agents.shadow_log import log_shadow_event
from polaris.core.pipeline.config import ai_free_mode
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "MODIFY_MAX",
    "MODIFY_MIN",
    "VALIDATOR_MAX_TOKENS",
    "VALIDATOR_RECENT_TRADES_MAX",
    "signal_validator_gate",
]

logger = logging.getLogger(__name__)

MODIFY_MIN: Final[float] = 0.5
MODIFY_MAX: Final[float] = 1.5
# Q6 G3 token budget for PASS/KILL/MODIFY response.
VALIDATOR_MAX_TOKENS: Final[int] = 250
# Recent same-symbol trades cap surfaced to the validator prompt.
VALIDATOR_RECENT_TRADES_MAX: Final[int] = 5

# Warm-pool floor for the NEW warm-pool-local-bottom discriminator (mirrors
# ``payload_builder.CELL_POOL_MIN_N_EFF`` / ``CELL_MIN_LIVE_N`` = 5.0 — the
# quartile-eligibility n_eff floor). A cell counts toward the local warm pool
# only at/above this n_eff.
WARM_POOL_FLOOR_N_EFF: Final[float] = CELL_MIN_LIVE_N
# Minimum warm-pool size needed to define a bottom quartile locally. Below this
# the discriminator never fires (no thin-sample quartile is manufactured).
WARM_POOL_MIN_MEMBERS: Final[int] = 4
# Bottom-quartile threshold percentile of the warm pool's scores.
WARM_POOL_BOTTOM_PCT: Final[float] = 0.25

_DECISION_TOKENS = {"PASS", "KILL", "MODIFY"}


def _build_user_prompt(
    *,
    raw_signal: dict[str, Any],
    cell_routing: dict[str, Any],
    baseline: dict[str, Any],
    recent_trades: list[dict[str, Any]],
) -> str:
    rt_lines = "\n".join(
        f"- {t.get('ts')}: pnl_r={t.get('pnl_r')} won={t.get('won')}"
        for t in recent_trades[:VALIDATOR_RECENT_TRADES_MAX]
    )
    return (
        f"# Raw signal\n{raw_signal}\n"
        f"# Cell routing\n{cell_routing}\n"
        f"# Baseline\n{baseline}\n"
        f"# Recent same-symbol\n{rt_lines}\n"
        'Output JSON: {"decision": "PASS|KILL|MODIFY", "strength_scalar": 1.0, '
        '"thesis": "..."}'
    )


def _validate_decision(parsed: dict[str, Any]) -> tuple[GateDecision, float] | None:
    """Schema-validate the GPT output. None = invalid → fail-closed KILL."""
    decision = str(parsed.get("decision", "")).upper()
    if decision not in _DECISION_TOKENS:
        return None
    raw_scalar = parsed.get("strength_scalar", 1.0)
    try:
        scalar = float(raw_scalar)
    except (TypeError, ValueError):
        scalar = 1.0
    # Clamp to [MODIFY_MIN, MODIFY_MAX] (GPT is allowed only the 0.5-1.5 range).
    scalar = max(MODIFY_MIN, min(MODIFY_MAX, scalar))
    if decision == "PASS":
        return GateDecision.PASS, 1.0
    if decision == "KILL":
        return GateDecision.KILL, 0.0
    return GateDecision.MODIFY, scalar


def _warm_pool_local_bottom(
    conn: sqlite3.Connection,
    *,
    regime: str,
    cell_score: float,
    cell_n_eff: float,
) -> bool:
    """NEW G3 shadow discriminator — is THIS cell in the bottom quartile of the
    *current warm pool* of its regime?

    Computes the bottom-quartile threshold (25th percentile) of the scores of
    warm cells (``n_eff >= WARM_POOL_FLOOR_N_EFF``) in the SAME ``regime`` from
    ``cell_matrix_p0``. Flags True when:

    - the warm pool has ``>= WARM_POOL_MIN_MEMBERS`` members (enough to define a
      quartile — no thin-sample quartile manufactured), AND
    - THIS cell is itself warm (``cell_n_eff >= WARM_POOL_FLOOR_N_EFF``), AND
    - ``cell_score <= threshold`` (bottom quartile of the warm pool).

    This is measured INDEPENDENT of the global ``CELL_MIN_POOL_SIZE=20``
    cardinality gate (which holds the global quartile label at ``cold`` while
    < 20 warm cells exist). SHADOW path only — read-only, never writes, and the
    production payload never carries the result. Any read error → False
    (behavior-safe: the discriminator simply does not fire).
    """
    if cell_n_eff < WARM_POOL_FLOOR_N_EFF:
        return False
    try:
        rows = conn.execute(
            "SELECT score FROM cell_matrix_p0 WHERE regime = ? AND n_eff >= ?",
            (regime, WARM_POOL_FLOOR_N_EFF),
        ).fetchall()
    except sqlite3.Error:
        return False
    pool = sorted(float(r[0]) for r in rows)
    if len(pool) < WARM_POOL_MIN_MEMBERS:
        return False
    threshold = _quantile(pool, WARM_POOL_BOTTOM_PCT)
    return cell_score <= threshold


def _g3_technical(
    ctx: GateContext, conn: sqlite3.Connection | None
) -> tuple[G3ShadowInputs, ShadowDecision]:
    """Compute the G3 deterministic technical decision from ``ctx.payload``.

    The ``warm_pool_local_bottom`` discriminator reads the live warm pool in
    this regime from ``conn`` (read-only); ``conn=None`` → the discriminator
    does not fire. Shared by the legacy shadow logger AND the AI-free primary
    path so the two can never drift.
    """
    cell = ctx.payload.get("cell_routing", {})
    cell = cell if isinstance(cell, dict) else {}
    regime = str(ctx.payload.get("regime", ""))
    local_bottom = (
        _warm_pool_local_bottom(
            conn,
            regime=regime,
            cell_score=float(cell.get("score", 0.0) or 0.0),
            cell_n_eff=float(cell.get("n_eff", 0.0) or 0.0),
        )
        if conn is not None
        else False
    )
    inp = g3_shadow_inputs_from_payload(
        ctx.payload, warm_pool_local_bottom=local_bottom
    )
    return inp, technical_validate_decision(inp)


def _log_g3_shadow(
    ctx: GateContext,
    shadow_conn: sqlite3.Connection | None,
    gpt_decision: GateDecision | None,
) -> None:
    """Compute the G3 deterministic technical rule + log it vs the GPT decision.

    AI-conductor P0 SHADOW (behavior 0): the technical decision is logged for the
    acceptance gate and NEVER returned. ``cold cell = pass-through`` and
    ``net_edge`` is not consulted (both enforced inside the rule). No-op when
    ``shadow_conn`` is None.

    The NEW ``warm_pool_local_bottom`` discriminator is computed here from
    ``shadow_conn`` (the live cell pool in this regime) and fed into the rule —
    entirely in the shadow path, so ``build_validator_payload`` / the production
    payload are untouched (behavior 0).
    """
    if shadow_conn is None:
        return
    inp, technical = _g3_technical(ctx, shadow_conn)
    regime = str(ctx.payload.get("regime", ""))
    log_shadow_event(
        shadow_conn,
        run_id=ctx.run_id,
        signal_id=ctx.signal_id,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue=ctx.venue,
        symbol=ctx.symbol,
        regime=regime,
        technical=technical,
        gpt_decision=gpt_decision,
        cell_warm=inp.n_eff >= CELL_WARM_MIN_N_EFF,
    )


async def signal_validator_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    shadow_conn: sqlite3.Connection | None = None,
    ai_free: bool | None = None,
) -> GateResult:
    """Gate 3 dispatcher (GPT validator / W3 AI-free deterministic).

    Reads inputs from ``ctx.payload``:
        ``raw_signal``, ``cell_routing``, ``baseline``, ``recent_trades``.
    Fail-closed: any unhandled error / non-conformant output → KILL.

    ``ai_free`` (W3 cutover — ``POLARIS_AI_FREE``, default ON; ``None`` reads
    the env): the deterministic technical rule (former shadow) IS the primary
    decision — zero GPT calls, ``model_used="python"``, and no shadow row
    (GPT absent → nothing to compare). ``shadow_conn`` doubles as the
    read-only conn for the warm-pool-local-bottom discriminator. flag=0 →
    the legacy GPT path below runs byte-identical.

    ``shadow_conn`` (legacy AI-conductor P0 SHADOW): when supplied on the GPT
    path, a deterministic technical rule is computed in parallel and logged
    against the live GPT decision (behavior 0 — the GPT decision is what this
    gate returns). None = legacy behavior, byte-identical.
    """
    raw_signal = ctx.payload.get("raw_signal", {})
    cell_routing = ctx.payload.get("cell_routing", {})
    baseline = ctx.payload.get("baseline", {})
    recent = ctx.payload.get("recent_trades", [])

    if not isinstance(raw_signal, dict) or not raw_signal:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_raw_signal"},
            model_used="python",
        )

    use_ai_free = ai_free if ai_free is not None else ai_free_mode()
    if use_ai_free:
        # W3 AI-FREE primary: technical decision drives the pipeline. PASS →
        # scalar 1.0 / MODIFY → conservative scalar — same semantics the GPT
        # path emitted (validated_signal carries strength_scalar downstream).
        _inp, technical = _g3_technical(ctx, shadow_conn)
        logger.info(
            "[gate/G3-validator] AI-free decision=%s scalar=%.2f regime=%s "
            "reason=%s sym=%s (deterministic primary, GPT=0)",
            technical.decision.name,
            technical.scalar,
            str(ctx.payload.get("regime", "")),
            technical.reason,
            str(raw_signal.get("symbol", raw_signal.get("ticker", "?"))),
        )
        if technical.decision == GateDecision.KILL:
            return GateResult(
                decision=GateDecision.KILL,
                next_gate=None,
                payload={"reason": technical.reason},
                model_used="python",
            )
        return GateResult(
            decision=technical.decision,
            next_gate=GATE_PRE_ENTRY_WATCHER,
            payload={
                "validated_signal": {
                    **raw_signal,
                    "strength_scalar": technical.scalar,
                },
                "reason": technical.reason,
            },
            model_used="python",
        )

    if client is None:
        # Fail-closed without LLM → KILL (entry-side gate, Q4 spec).
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "no_gpt_client"},
            model_used="python",
        )

    system = make_system_prefix(
        role=(
            "You are a Signal Validator gate in Polaris v2 paper trading "
            "system."
        ),
        decision_enum=["PASS", "KILL", "MODIFY"],
        cell_summary=str(cell_routing),
        baseline_summary=str(baseline),
        recent_trades_summary=str(recent[:VALIDATOR_RECENT_TRADES_MAX]),
    )
    prompt = _build_user_prompt(
        raw_signal=raw_signal,
        cell_routing=cell_routing,
        baseline=baseline,
        recent_trades=recent,
    )
    res: GPTCallResult = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=prompt,
        max_tokens=VALIDATOR_MAX_TOKENS,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )

    if res.error or res.parsed is None:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "gpt_error", "error": res.error},
            model_used="gpt",
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            error=res.error,
        )

    decoded = _validate_decision(res.parsed)
    if decoded is None:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "gpt_schema_violation", "raw": res.text[:200]},
            model_used="gpt",
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        )
    decision, scalar = decoded
    # AI-conductor P0 SHADOW: log the technical rule vs the live GPT decision.
    # Behavior 0 — the GPT ``decision`` is returned unchanged below.
    _log_g3_shadow(ctx, shadow_conn, decision)
    if decision == GateDecision.KILL:
        # Mirror G4's raw-reason capture (pre_entry_watcher ``watcher_kill``):
        # persist the model's own KILL rationale so a G3 reject is auditable
        # instead of an opaque {reason: validator_kill}. (BUILD_SCHEMA #3.)
        return GateResult(
            decision=decision,
            next_gate=None,
            payload={"reason": "validator_kill", "raw": res.text[:200]},
            model_used="gpt",
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        )
    return GateResult(
        decision=decision,
        next_gate=GATE_PRE_ENTRY_WATCHER,
        payload={
            "validated_signal": {
                **raw_signal,
                "strength_scalar": scalar,
            }
        },
        model_used="gpt",
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )
