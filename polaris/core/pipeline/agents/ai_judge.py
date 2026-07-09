"""#32 — Per-ticker AI JUDGE over the bot's OWN information (structurally non-blocking).

Jin vision (2026-06-26): "이 프로브는 그냥 판단을 하게 만드는거지 우리가 가진 정보를
통해서" + "둘 다"(entry + exit). The AI judges per-ticker using the SAME information the
bot already collects — technicals (Yahoo bars), alt-data evidence (news / COT / macro /
funding / fear-greed via ``altdata.fuser``), the regime label, and ground/event coverage —
and emits a verdict that HELPS THE FLOW, never blocks it.

Debate verdict (``vault/50_research/debates/ai_reengage_gate_design_2026-06-26.md``):
SOUND-WITH-FIXES — ① no veto anywhere (incl. "broken premise"); ② delay/refine = one-shot,
time-boxed, missed-entry accounted; ③ single P0 action per gate, causal effect isolated.

STRUCTURAL no-KILL guarantee (the central design invariant)
-----------------------------------------------------------
The judge does NOT reuse the cross-gate ``GateDecision`` enum (which CONTAINS ``KILL`` /
``EXIT_NOW``). It has its OWN closed verdict type — :class:`JudgeVerdict` — whose members are
**exclusively non-blocking**. There is no ``KILL`` / ``BLOCK`` / ``INVALID`` / ``VETO`` /
``EXIT_NOW`` member to emit; the block direction is unrepresentable. Any unrecognized / hostile
model output (incl. the literal strings ``"KILL"`` / ``"BLOCK"``) parses to the per-role SAFE
verdict (``PROCEED`` / ``PROTECT``), never a block. And :func:`apply_entry_verdict` /
:func:`apply_exit_verdict` are TOTAL over the enum: every branch returns either the
deterministic decision UNCHANGED (a no-op refinement) or a flow-ADDITIVE effect — by
construction there is no path that yields ``GateDecision.KILL`` or strips ``next_gate`` on an
entry gate. Objective execution-validity (malformed data / venue-session / duplicate) and the
G6 -1.0R rail stay DETERMINISTIC and are untouched by the judge (the judge never sees them).

The judge is the *measurement / refinement* layer; the deterministic gate is the floor. In
``shadow`` mode the judge only logs (deterministic acts → pass-through preserved); in
``active`` mode the judge's non-blocking refinement is reflected on top of the deterministic
PASS / PROCEED / HOLD.

Cost / safety: gpt-5-mini hot path, bounded budget (per-call timeout), structured JSON,
deterministic fallback on ANY GPT error / timeout / parse-error (the deterministic decision is
returned verbatim and an escalation reason is logged), and every call logs its escalation
reason. AI = OpenAI GPT only (no Anthropic routing — Jin mandate).

9-stack: a ``SIZE_UP`` verdict is an INTENT that is logged + surfaced, NEVER a new multiplier
folded into the T4 sizing chain (the sizing chain is untouched here). The -1.0R rail / BEP /
entry side are never read.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from polaris.core.pipeline.agents._gpt_client import (
    GPT_P0_MODEL,
    GPTCallResult,
    call_gpt,
    make_system_prefix,
)
from polaris.core.pipeline.agents._shadow_rules import ShadowDecision
from polaris.core.pipeline.agents.judge_gate import (
    extend_atr,
    tighten_atr,
)
from polaris.core.pipeline.agents.shadow_log import log_shadow_event
from polaris.core.pipeline.config import AI_JUDGE_MODE_ACTIVE
from polaris.core.pipeline.gate_state import GateContext, GateDecision, GateResult

__all__ = [
    "JUDGE_MAX_TOKENS",
    "JUDGE_TIMEOUT_SEC",
    "JUDGE_WALL_CLOCK_DEADLINE_SEC_DEFAULT",
    "EntryJudgeVerdict",
    "ExitJudgeVerdict",
    "JudgeOutcome",
    "apply_entry_verdict",
    "apply_exit_verdict",
    "judge_entry",
    "judge_exit",
    "judge_wall_clock_deadline_sec",
    "parse_entry_verdict",
    "parse_exit_verdict",
]

logger = logging.getLogger(__name__)

# Hot-path budget (gpt-5-mini). Tighter than the 30 s gate default — the judge is
# an ADD-ON over a decision that already exists, so it must not extend latency.
JUDGE_TIMEOUT_SEC: Final[float] = 8.0
JUDGE_MAX_TOKENS: Final[int] = 180

# [[judge-probe-reality]] audit: httpx's ``timeout=`` kwarg on ``call_gpt`` bounds
# EACH connect/read/write phase, not the TOTAL call — a trickling response can
# occupy the gate far past JUDGE_TIMEOUT_SEC (audited max 495s). This is a
# SEPARATE, outer wall-clock deadline wrapped around the whole ``call_gpt`` await
# via ``asyncio.wait_for``; on expiry the SAME deterministic safe-verdict
# fallback fires immediately (never a block — matches every other failure mode).
# Slightly above JUDGE_TIMEOUT_SEC so a well-behaved (non-trickling) call that
# legitimately needs the full per-op budget is not cut off early.
JUDGE_WALL_CLOCK_DEADLINE_SEC_DEFAULT: Final[float] = 10.0


def judge_wall_clock_deadline_sec() -> float:
    """Env-tunable TOTAL wall-clock deadline for one judge call (no_hardcode_in_plans)."""
    raw = os.getenv("POLARIS_JUDGE_WALL_CLOCK_DEADLINE_SEC")
    if raw is None or raw.strip() == "":
        return JUDGE_WALL_CLOCK_DEADLINE_SEC_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return JUDGE_WALL_CLOCK_DEADLINE_SEC_DEFAULT


# ---------------------------------------------------------------------------
# Verdict types — CLOSED, non-blocking only. No KILL/BLOCK/VETO member exists.
# ---------------------------------------------------------------------------


class EntryJudgeVerdict(StrEnum):
    """Per-ticker ENTRY judgment (G3 rationale + G4 timing). NON-BLOCKING ONLY.

    There is deliberately NO ``KILL`` / ``BLOCK`` / ``INVALID`` member — the entry
    block direction is unrepresentable. The safe default is ``PROCEED`` (flow).
    """

    PROCEED = "PROCEED"  # let the deterministic decision flow (no-op refinement)
    STRENGTHEN_EVIDENCE = "STRENGTHEN_EVIDENCE"  # info corroborates — annotate confidence
    SIZE_UP = "SIZE_UP"  # info strong — INTENT only (never folded into the sizing chain)
    REFINE_TIMING = "REFINE_TIMING"  # G4: one-shot, time-boxed timing nudge (missed-entry logged)


class ExitJudgeVerdict(StrEnum):
    """Per-ticker EXIT judgment (G7 timing). NON-BLOCKING ONLY.

    There is deliberately NO ``EXIT_NOW`` / ``KILL`` member — a forced cut is
    unrepresentable. Exit is TIMING only: protect (let it ride), extend (widen so
    winners run), or tighten on CONFIRMED decay. The safe default is ``PROTECT``.
    """

    PROTECT = "PROTECT"  # thesis intact — hold the current floor (no-op)
    EXTEND = "EXTEND"  # winner running — request a WIDEN (further from price)
    TIGHTEN_ON_CONFIRMED_DECAY = "TIGHTEN_ON_CONFIRMED_DECAY"  # ratchet trail closer


# Compile-time assertions that the block direction is absent from the verdict
# vocabularies (a future careless edit that adds a KILL member trips this).
_FORBIDDEN_VERDICTS: Final[frozenset[str]] = frozenset(
    {"KILL", "BLOCK", "INVALID", "VETO", "REJECT", "EXIT_NOW", "SKIP"}
)
assert _FORBIDDEN_VERDICTS.isdisjoint({v.value for v in EntryJudgeVerdict})
assert _FORBIDDEN_VERDICTS.isdisjoint({v.value for v in ExitJudgeVerdict})


@dataclass(frozen=True, slots=True)
class JudgeOutcome:
    """The judge's verdict + provenance for one gate execution.

    ``escalation_reason`` is ALWAYS set (the debate's "기계적·전부 로그" mandate):
    why the judge was consulted / what happened (``gpt_ok`` / ``gpt_error`` /
    ``gpt_parse_fallback`` / ``no_client``). ``deterministic`` is the live gate
    decision the judge ran ALONGSIDE — in shadow mode it is what acts.
    """

    verdict: str
    deterministic: GateDecision
    escalation_reason: str
    raw: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# Parsing — hostile-input-safe. KILL/BLOCK strings collapse to the SAFE verdict.
# ---------------------------------------------------------------------------


def parse_entry_verdict(raw: Any) -> EntryJudgeVerdict:
    """Coerce arbitrary model output into an :class:`EntryJudgeVerdict`.

    Anything not an exact non-blocking member (incl. ``"KILL"`` / ``"BLOCK"`` /
    garbage / None) → ``PROCEED`` (flow). The judge can NEVER manufacture a block
    by emitting a hostile token.
    """
    token = str(raw or "").strip().upper()
    try:
        return EntryJudgeVerdict(token)
    except ValueError:
        return EntryJudgeVerdict.PROCEED


def parse_exit_verdict(raw: Any) -> ExitJudgeVerdict:
    """Coerce arbitrary model output into an :class:`ExitJudgeVerdict`.

    Anything not an exact non-blocking member (incl. ``"EXIT_NOW"`` / ``"KILL"`` /
    garbage / None) → ``PROTECT`` (hold the floor). No forced-cut token is honored.
    """
    token = str(raw or "").strip().upper()
    try:
        return ExitJudgeVerdict(token)
    except ValueError:
        return ExitJudgeVerdict.PROTECT


# ---------------------------------------------------------------------------
# Prompt — feeds the bot's OWN information (Jin: "우리가 가진 정보를 통해서").
# ---------------------------------------------------------------------------


def _age_suffix_hours(age_h: Any) -> str:
    """`` (age 20.0h)`` for a numeric headline age; ``""`` when absent/unparsable."""
    if isinstance(age_h, (int, float)):
        return f" (age {round(float(age_h), 1)}h)"
    return ""


def _macro_age_suffix(evidence: dict[str, Any]) -> str:
    """`` (vix asof 2026-06-26, 3d old)`` when the macro observation date/age is
    present; ``""`` otherwise (pre-fix payload renders unchanged)."""
    asof = evidence.get("vix_asof") or evidence.get("hy_asof")
    age = evidence.get("macro_age_days")
    if asof is None and age is None:
        return ""
    parts: list[str] = []
    if asof is not None:
        parts.append(f"asof {asof}")
    if isinstance(age, (int, float)):
        parts.append(f"{int(age)}d old")
    return f" (macro {', '.join(parts)})" if parts else ""


def _cot_age_suffix(evidence: dict[str, Any]) -> str:
    """`` (cot report 2026-06-16)`` when the COT report date is present; else ``""``."""
    report = evidence.get("cot_report_date")
    if isinstance(report, str) and report:
        return f" (cot report {report})"
    return ""


# Granular positioning / order-flow keys the altdata expansion adds to the fused
# evidence (Binance derivs / Coinglass liquidations / MyFxBook retail crowd). The
# 5-key allowlist used to DROP these from the judge prompt; now they are rendered
# as read-only context (flow_not_block). Each source's ``*_asof`` carries its
# observation date for inline age labelling (#69 currency).
_POSITIONING_KEYS: tuple[str, ...] = (
    "binance_top_long_short", "binance_taker_buy_sell", "binance_global_long_short",
    "binance_funding", "binance_oi_change_24h",
    "cg_long_liq_usd", "cg_short_liq_usd", "cg_funding_mean", "cg_funding_dispersion",
    "fxb_long_pct", "fxb_short_pct",
)
# Per-source observation-date keys → the suffix label shown after the block.
_POSITIONING_ASOF: tuple[str, ...] = (
    "binance_oi_asof", "cg_liquidation_asof", "fxb_asof",
)

# Curated, DECISION-relevant subset of the FRED 4->32 panel surfaced to the judge.
# The full 32-series panel is evidence-only in the fuser; dumping all 32 numbers
# would bloat the prompt, so only the macro axes that move a trade decision are
# rendered: yield-curve shape, rate level, inflation expectations, the dollar,
# the credit-spread, and the vol term-structure. Off-curate series (housing /
# payrolls / GDP / money …) stay out of the prompt (still in evidence_json).
_FRED_PANEL_CURATED: tuple[str, ...] = (
    "yield_curve", "yield_curve_10y3m", "ust_10y", "ust_2y", "fed_funds",
    "breakeven_5y", "breakeven_10y", "dollar_index", "ig_spread", "vix_3m",
)


def _positioning_line(evidence: dict[str, Any]) -> str | None:
    """`` - positioning (binance/coinglass/myfxbook): {…} (asof …)`` or ``None``.

    Surfaces the granular Binance / Coinglass / MyFxBook positioning keys the
    altdata expansion adds — previously dropped by the hardcoded allowlist. Each
    present source's observation date is appended as a currency label (never a
    drop). Absent on all keys → ``None`` (the line is simply omitted).
    """
    present = {k: evidence[k] for k in _POSITIONING_KEYS if k in evidence}
    if not present:
        return None
    asof_parts = [
        f"{k} {evidence[k]}" for k in _POSITIONING_ASOF
        if isinstance(evidence.get(k), str) and evidence[k]
    ]
    asof = f" (asof {', '.join(asof_parts)})" if asof_parts else ""
    return f"- positioning (binance/coinglass/myfxbook): {present}{asof}"


def _macro_panel_line(evidence: dict[str, Any]) -> str | None:
    """`` - macro panel (curated): {…}`` or ``None``.

    Renders the DECISION-relevant FRED panel subset (yield curve / rates /
    breakevens / dollar / credit / vol term) with each present series' ``*_asof``
    observation date inline. Off-curate panel members are intentionally excluded
    (prompt-bloat guard). Absent on all curated keys → ``None``.
    """
    parts: list[str] = []
    for key in _FRED_PANEL_CURATED:
        val = evidence.get(key)
        if not isinstance(val, (int, float)):
            continue
        asof = evidence.get(f"{key}_asof")
        if isinstance(asof, str) and asof:
            parts.append(f"{key}={val} (asof {asof})")
        else:
            parts.append(f"{key}={val}")
    if not parts:
        return None
    return f"- macro panel (curated): {', '.join(parts)}"


def _evidence_block(payload: dict[str, Any]) -> str:
    """Render the bot's own per-ticker information for the judge prompt.

    Pulls the SAME data the rest of the pipeline already carries: the regime
    label, the fused alt-data ``evidence`` (news / COT / macro / funding /
    fear-greed from ``altdata.fuser``), the ticker baseline (ATR / size / volume
    technicals), cell expectancy, and ground coverage flags. All optional — a
    missing source renders as ``n/a`` (graceful, never a manufactured judgment).
    """
    regime = payload.get("regime", "n/a")
    evidence = payload.get("evidence")
    baseline = payload.get("baseline")
    technicals = payload.get("technicals")
    cell = payload.get("cell_routing")
    ground = payload.get("ticker_ground")
    lines = [f"- regime: {regime}"]
    if isinstance(evidence, dict) and evidence:
        # Consume the keys ``altdata.fuser.fuse_evidence`` ACTUALLY emits (there is
        # no ``news_headline`` key — that was a dead read). ``label``/``scores`` are
        # the fused regime synthesis; ``news_*`` is the headline-tone tilt; and the
        # raw alt-data datapoints (fear-greed / funding / macro / COT) are surfaced
        # so the judge sees the underlying signal strength, not just the verdict.
        label = evidence.get("label")
        scores = evidence.get("scores")
        lines.append(f"- alt-data evidence: label={label} scores={scores}")
        news_sentiment = evidence.get("news_sentiment")
        if news_sentiment is not None:
            # Currency: append the OLDEST contributing headline age so the judge
            # can self-discount a thin-ticker stale-as-current sentiment. Absent
            # age (no timestamp) → no suffix (graceful, no fabricated freshness).
            news_age = _age_suffix_hours(evidence.get("news_max_age_h"))
            lines.append(
                f"- news: sentiment={news_sentiment} "
                f"magnitude={evidence.get('news_magnitude')} "
                f"n={evidence.get('news_n')}{news_age}"
            )
        raw_signals = {
            k: evidence[k]
            for k in (
                "crypto_fg", "avg_funding", "vix", "hy_spread",
                "cot_net_spec_pctile",
            )
            if k in evidence
        }
        if raw_signals:
            # Currency: per-source observation age (macro days / COT report date)
            # is surfaced so a weekend macro print or a 10-day-old weekly COT is
            # visibly stale, never silently treated as 'current'. flow_not_block:
            # an age label, never a drop.
            macro_age = _macro_age_suffix(evidence)
            cot_age = _cot_age_suffix(evidence)
            lines.append(f"- alt-data signals: {raw_signals}{macro_age}{cot_age}")
        # Max-expand altdata: granular positioning (Binance / Coinglass /
        # MyFxBook) + the curated FRED macro panel reach the judge here. Without
        # these the hardcoded 5-key allowlist dropped everything the expansion
        # surfaced as evidence-only. flow_not_block: read-only context, age-labelled.
        positioning = _positioning_line(evidence)
        if positioning is not None:
            lines.append(positioning)
        macro_panel = _macro_panel_line(evidence)
        if macro_panel is not None:
            lines.append(macro_panel)
    if isinstance(baseline, dict) and baseline:
        lines.append(f"- technicals (baseline atr/size/volume): {baseline}")
    if isinstance(technicals, dict) and technicals:
        # ④ #12 — the FULL indicator set from the technical store (rsi/adx/bb/
        # donchian/ema/momentum) the judge could not previously see. EVIDENCE-ONLY:
        # read-only context, never a block/size. ``source_bar_ts`` is the currency
        # stamp (#68/#69) so a stale technical set is visibly stale to the judge.
        lines.append(f"- full technicals (bar-indicators): {technicals}")
    if isinstance(cell, dict) and cell:
        lines.append(
            f"- cell expectancy: quartile={cell.get('quartile')} "
            f"avg_pnl_r={cell.get('avg_pnl_r')} n_eff={cell.get('n_eff')}"
        )
    if isinstance(ground, dict) and ground:
        lines.append(
            f"- ground coverage: has_sentiment={ground.get('has_sentiment')} "
            f"has_event={ground.get('has_event')}"
        )
    return "\n".join(lines)


def _build_entry_prompt(payload: dict[str, Any], *, subject: dict[str, Any]) -> str:
    enum = ", ".join(v.value for v in EntryJudgeVerdict)
    return (
        "# Subject signal\n"
        f"{subject}\n"
        "# Our information (technicals + alt-data + regime + ground)\n"
        f"{_evidence_block(payload)}\n"
        "# Task\n"
        "Judge this ENTRY using the information above. You CANNOT block, kill, or "
        "veto — those are not options. Choose the one verdict that best HELPS the "
        f"flow: {enum}. PROCEED = information is consistent, let it flow. "
        "STRENGTHEN_EVIDENCE = information corroborates the thesis. SIZE_UP = "
        "information is strongly confirming (intent only). REFINE_TIMING = a "
        "one-shot, time-boxed timing nudge would improve the fill.\n"
        f'Output JSON: {{"verdict": "{EntryJudgeVerdict.PROCEED.value}", "reason": "..."}}'
    )


def _build_exit_prompt(payload: dict[str, Any], *, subject: dict[str, Any]) -> str:
    enum = ", ".join(v.value for v in ExitJudgeVerdict)
    exit_context = payload.get("exit_context")
    ctx_line = f"# Exit context\n{exit_context}\n" if isinstance(exit_context, dict) else ""
    return (
        "# Open position\n"
        f"{subject}\n"
        f"{ctx_line}"
        "# Our information (technicals + alt-data + regime + ground)\n"
        f"{_evidence_block(payload)}\n"
        "# Task\n"
        "Judge this EXIT's TIMING using the information above. You CANNOT force an "
        "immediate exit, kill, or cut — those are not options. Choose the one "
        f"verdict that best manages exit timing: {enum}. PROTECT = thesis intact, "
        "hold the floor. EXTEND = winner running with favourable momentum, widen so "
        "it runs further. TIGHTEN_ON_CONFIRMED_DECAY = the thesis is CONFIRMED "
        "decaying, ratchet the trail closer (never below the floor).\n"
        f'Output JSON: {{"verdict": "{ExitJudgeVerdict.PROTECT.value}", "reason": "..."}}'
    )


# ---------------------------------------------------------------------------
# Judge calls — bounded, structured-JSON, deterministic-fallback on any failure.
# ---------------------------------------------------------------------------

# [[judge-probe-reality]] audit: gpt_parse_fallback measured at 10.6%
# (984/9,327) — root cause is JUDGE_MAX_TOKENS=180 cutting the response mid
# the free-text ``reason`` field, which the prompt places AFTER ``verdict``.
# ``reason`` is decorative (never read from ``res.parsed`` by any caller —
# only ``verdict`` drives behaviour), so a truncation that lands inside
# ``reason`` still has a COMPLETE, already-closed ``verdict`` value sitting
# before the cut. This regex salvages just that value instead of discarding
# the whole call. Anchored to the exact ``"verdict": "..."`` shape our own
# prompts request; a merely-different quoting style is never force-matched.
_VERDICT_SALVAGE_RE: Final[re.Pattern[str]] = re.compile(
    r'"verdict"\s*:\s*"([A-Za-z_]+)"'
)


def _salvage_verdict(text: str) -> str | None:
    """Best-effort recovery of a COMPLETE ``verdict`` value from truncated JSON.

    Returns ``None`` when the truncation cut before the verdict value closed
    (nothing to salvage) — the caller falls back exactly as before.
    """
    match = _VERDICT_SALVAGE_RE.search(text)
    return match.group(1) if match else None


async def _call_and_log(
    *,
    client: Any | None,
    system_role: str,
    prompt: str,
    deterministic: GateDecision,
    safe_verdict: str,
    parse: Any,
) -> JudgeOutcome:
    """Shared judge call: GPT (bounded) → parse → escalation-logged outcome.

    Deterministic fallback: ``client is None`` OR any GPT error / timeout /
    parse-error → the SAFE non-blocking verdict + the deterministic decision are
    returned (the live loop falls back to the deterministic decision). The judge
    can never DEGRADE flow on a failure.
    """
    if client is None:
        return JudgeOutcome(
            verdict=safe_verdict,
            deterministic=deterministic,
            escalation_reason="no_client",
        )
    system = make_system_prefix(
        role=system_role,
        decision_enum=[],  # the judge enum lives in the user prompt (non-blocking only)
        cell_summary="",
        baseline_summary="",
        recent_trades_summary="",
    )
    started = time.monotonic()
    try:
        # Outer TOTAL wall-clock deadline — the per-op ``timeout_sec`` below
        # only bounds each connect/read/write phase inside ``call_gpt``/httpx
        # (audited gap: a trickling response can occupy the gate for minutes
        # even though no single phase ever exceeds JUDGE_TIMEOUT_SEC).
        res: GPTCallResult = await asyncio.wait_for(
            call_gpt(
                client=client,
                system_prefix=system,
                user_prompt=prompt,
                max_tokens=JUDGE_MAX_TOKENS,
                model=GPT_P0_MODEL,
                timeout_sec=JUDGE_TIMEOUT_SEC,
            ),
            timeout=judge_wall_clock_deadline_sec(),
        )
    except TimeoutError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "[ai-judge] %s → fallback (det=%s) escalation=gpt_timeout "
            "wall_clock_ms=%d",
            system_role, deterministic.value, elapsed_ms,
        )
        return JudgeOutcome(
            verdict=safe_verdict,
            deterministic=deterministic,
            escalation_reason="gpt_timeout",
            latency_ms=elapsed_ms,
        )
    if res.error or res.parsed is None:
        if res.error is None:
            salvaged = _salvage_verdict(res.text)
            if salvaged is not None:
                verdict = parse(salvaged)
                logger.info(
                    "[ai-judge] %s verdict=%s (det=%s) escalation=gpt_ok_salvaged "
                    "latency=%dms (truncated response, reason field cut)",
                    system_role, verdict.value, deterministic.value, res.latency_ms,
                )
                return JudgeOutcome(
                    verdict=verdict.value,
                    deterministic=deterministic,
                    escalation_reason="gpt_ok_salvaged",
                    raw=res.text[:120],
                    latency_ms=res.latency_ms,
                    input_tokens=res.input_tokens,
                    output_tokens=res.output_tokens,
                )
        reason = "gpt_error" if res.error else "gpt_parse_fallback"
        logger.info(
            "[ai-judge] %s → fallback (det=%s) escalation=%s err=%s",
            system_role, deterministic.value, reason, res.error,
        )
        return JudgeOutcome(
            verdict=safe_verdict,
            deterministic=deterministic,
            escalation_reason=reason,
            raw=res.text[:120],
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        )
    verdict = parse(res.parsed.get("verdict"))
    logger.info(
        "[ai-judge] %s verdict=%s (det=%s) escalation=gpt_ok latency=%dms",
        system_role, verdict.value, deterministic.value, res.latency_ms,
    )
    return JudgeOutcome(
        verdict=verdict.value,
        deterministic=deterministic,
        escalation_reason="gpt_ok",
        raw=res.text[:120],
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )


async def judge_entry(
    ctx: GateContext,
    *,
    deterministic: GateDecision,
    subject: dict[str, Any],
    client: Any | None,
) -> JudgeOutcome:
    """Judge an ENTRY (G3 rationale / G4 timing) over the bot's own information."""
    return await _call_and_log(
        client=client,
        system_role=(
            "You are a Polaris per-ticker ENTRY judge. You assess whether the bot's "
            "OWN collected information supports this entry. You never block or veto."
        ),
        prompt=_build_entry_prompt(ctx.payload, subject=subject),
        deterministic=deterministic,
        safe_verdict=EntryJudgeVerdict.PROCEED.value,
        parse=parse_entry_verdict,
    )


async def judge_exit(
    ctx: GateContext,
    *,
    deterministic: GateDecision,
    subject: dict[str, Any],
    client: Any | None,
) -> JudgeOutcome:
    """Judge an EXIT's TIMING (G7) over the bot's own information."""
    return await _call_and_log(
        client=client,
        system_role=(
            "You are a Polaris per-ticker EXIT-timing judge. You assess whether the "
            "thesis is intact / running / confirmed-decaying. You never force a cut."
        ),
        prompt=_build_exit_prompt(ctx.payload, subject=subject),
        deterministic=deterministic,
        safe_verdict=ExitJudgeVerdict.PROTECT.value,
        parse=parse_exit_verdict,
    )


# ---------------------------------------------------------------------------
# Verdict application — TOTAL over the enum, flow-additive only (no KILL path).
# ---------------------------------------------------------------------------


def apply_entry_verdict(
    outcome: JudgeOutcome,
    *,
    deterministic_result: GateResult,
    mode: str,
) -> GateResult:
    """Reflect an ENTRY verdict onto the deterministic result — NON-BLOCKING.

    In ``shadow`` mode (default) the deterministic result is returned VERBATIM
    (pass-through preserved). In ``active`` mode the verdict ANNOTATES the result
    payload (judge provenance) but NEVER changes ``decision`` / ``next_gate`` away
    from flow: SIZE_UP tags an intent flag, STRENGTHEN_EVIDENCE tags a
    record-only confidence flag, and every verdict (incl. REFINE_TIMING) is
    recorded verbatim in the generic ``ai_judge`` annotation — the
    deterministic decision still acts.

    By construction this function NEVER returns a KILL and NEVER strips
    ``next_gate``: it only ever copies the deterministic result and adds keys.
    The sizing chain is untouched — SIZE_UP is an intent flag, not a multiplier.
    """
    if mode != AI_JUDGE_MODE_ACTIVE:
        return deterministic_result
    verdict = parse_entry_verdict(outcome.verdict)
    payload = dict(deterministic_result.payload)
    payload["ai_judge"] = {
        "verdict": verdict.value,
        "escalation": outcome.escalation_reason,
        "applied": "annotate",
    }
    if verdict is EntryJudgeVerdict.SIZE_UP:
        # INTENT ONLY — consumed by the entry sizer as ``judge_conviction`` folded
        # into the ONE continuous scalar + re-clamped (NOT a new multiplier stacked
        # here; 9-stack stays sealed). Absent/False → conviction 1.0 (byte-identical).
        payload["ai_judge_size_up_intent"] = True
    if verdict is EntryJudgeVerdict.STRENGTHEN_EVIDENCE:
        # PURE annotation — telemetry/dashboard + G8 reflector context. NO behavioral
        # consumer; deliberately does NOT bump the sizing scalar (SIZE_UP owns the
        # amplification — STRENGTHEN is the weaker, record-only verdict; avoids
        # double-counting). Zero size / stop / decision effect.
        payload["ai_judge_confidence"] = {"strengthened": True}
    # decision + next_gate are copied from the deterministic result UNCHANGED.
    return GateResult(
        decision=deterministic_result.decision,
        next_gate=deterministic_result.next_gate,
        payload=payload,
        model_used=deterministic_result.model_used,
        latency_ms=deterministic_result.latency_ms,
        error=deterministic_result.error,
        skipped=deterministic_result.skipped,
        input_tokens=deterministic_result.input_tokens,
        output_tokens=deterministic_result.output_tokens,
    )


def apply_exit_verdict(
    outcome: JudgeOutcome,
    *,
    deterministic_result: GateResult,
    mode: str,
) -> GateResult:
    """Reflect an EXIT verdict onto the deterministic G7 result — NON-BLOCKING.

    In ``shadow`` mode the deterministic result is returned VERBATIM. In ``active``
    mode the verdict ANNOTATES provenance; the decision stays on the deterministic
    G7 rail (HOLD / ADJUST_EXIT). A PROTECT / EXTEND / TIGHTEN verdict NEVER becomes
    an EXIT_NOW or a forced cut here — the exit-timing rails (widen / tighten) are
    owned by G7's deterministic ``can_widen_exit`` / ``can_tighten_exit`` rails; this
    only records which timing direction the judge preferred. The -1.0R rail is never
    touched.
    """
    if mode != AI_JUDGE_MODE_ACTIVE:
        return deterministic_result
    verdict = parse_exit_verdict(outcome.verdict)
    payload = dict(deterministic_result.payload)
    payload["ai_judge"] = {
        "verdict": verdict.value,
        "escalation": outcome.escalation_reason,
        "applied": "annotate",
    }
    if verdict is ExitJudgeVerdict.EXTEND:
        # A REQUEST routed THROUGH the deterministic Q9 widen rail (never around it):
        # adaptive_exit_gate pushes the stop one extra ATR farther and RE-RUNS
        # can_widen_exit, whose (allowed, reason) is FINAL (further-from-price,
        # max_loss/-1.0R floor, pnl_r>0.7R, override cap, cooldown). Reject → stop
        # UNCHANGED. flow_not_block: widen only moves the stop FARTHER, never tightens,
        # never forces an exit (the verdict enum has no EXIT_NOW).
        payload["ai_judge_widen_request"] = {"extend_atr": extend_atr()}
    if verdict is ExitJudgeVerdict.TIGHTEN_ON_CONFIRMED_DECAY:
        # A REQUEST routed THROUGH the ratchet-safe tighten rail (can_tighten_exit):
        # adaptive_exit_gate synthesises a stop one ATR-step CLOSER and routes it;
        # the rail forbids LOOSENING + enforces override cap / cooldown. TIGHTEN moves
        # the stop TOWARD price = AWAY from the -1.0R floor (strictly safe); it can
        # never force a cut (no EXIT_NOW member). Reject → stop UNCHANGED.
        payload["ai_judge_tighten_request"] = {"tighten_atr": tighten_atr()}
    return GateResult(
        decision=deterministic_result.decision,
        next_gate=deterministic_result.next_gate,
        payload=payload,
        model_used=deterministic_result.model_used,
        latency_ms=deterministic_result.latency_ms,
        error=deterministic_result.error,
        skipped=deterministic_result.skipped,
        input_tokens=deterministic_result.input_tokens,
        output_tokens=deterministic_result.output_tokens,
    )


# ---------------------------------------------------------------------------
# Measurement — judge-vs-deterministic + pass-through, via gate_shadow_events.
# ---------------------------------------------------------------------------


def log_judge_event(
    conn: sqlite3.Connection | None,
    *,
    ctx: GateContext,
    gate_id: int,
    outcome: JudgeOutcome,
    mode: str,
) -> None:
    """Persist one judge row into ``gate_shadow_events`` (measurement, behavior 0).

    Reuses the existing shadow log so the acceptance analysis can compare the
    judge verdict against the deterministic decision AND track pass-through
    preservation (the debate's "pass-through preservation을 추적 metric으로"). The
    judge VERDICT is recorded in ``technical_flags`` (``judge:<verdict>`` +
    ``mode:<mode>`` + ``escalation:<reason>``); ``technical_decision`` is the
    deterministic decision that the live loop actually acts on in shadow mode —
    so a row where the judge logged but the deterministic decision flowed IS the
    pass-through evidence. No-op when ``conn`` is None / table absent.
    """
    if conn is None:
        return
    log_shadow_event(
        conn,
        run_id=ctx.run_id,
        signal_id=ctx.signal_id,
        gate_id=gate_id,
        venue=ctx.venue,
        symbol=ctx.symbol,
        regime=str(ctx.payload.get("regime", "")),
        technical=ShadowDecision(
            decision=outcome.deterministic,
            reason="ai_judge",
            flags=(
                f"judge:{outcome.verdict}",
                f"mode:{mode}",
                f"escalation:{outcome.escalation_reason}",
            ),
        ),
        # No GPT GateDecision to compare (the judge has its own verdict type) —
        # gpt_decision stays empty so the legacy agreement query excludes it; the
        # judge verdict + mode live in technical_flags for the judge-specific read.
        gpt_decision=None,
        cell_warm=False,
    )
