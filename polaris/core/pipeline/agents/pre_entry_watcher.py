"""Gate 4 — Pre-Entry Watcher (GPT P0 30s loop, fail-closed).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G4 + Q5 30s window + Q7 fast-path)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Pre-Entry Watcher)

Behavior:
- Default: 30s window, sample once per second OR per tick batch; emit
  PROCEED / KILL once per call.
- Fast-path skip: top-quartile cell + strong validated_signal + tight spread
  + no recent reject + listing age ≥ 24h + not session-open shock window →
  caller can short-circuit to PROCEED with model="python_fast_path".
- Fail-closed: any error → KILL.

Migration (2026-05-07): Anthropic Haiku → OpenAI GPT (Jin mandate).
Aggressive bias preserved — fast-path PROCEED stays unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from polaris.core.pipeline.agents._gpt_client import (
    DEFAULT_TIMEOUT_SEC,
    GPTCallResult,
    call_gpt,
    make_system_prefix,
)
from polaris.core.pipeline.agents._stream_guards import (
    GUARD_CFD,
    GUARD_EQUITY,
    cfd_fast_path_eligible,
    equity_fast_path_eligible,
)
from polaris.core.pipeline.agents.post_trade_reflector import (
    LESSON_RECENT_TRADES_MAX,
)
from polaris.core.pipeline.gate_state import (
    GATE_ENTRY_SIZER,
    GateContext,
    GateDecision,
    GateResult,
)

if TYPE_CHECKING:
    from polaris.core.streams import StreamProfile

__all__ = [
    "FastPathContext",
    "WATCHER_MAX_TOKENS",
    "is_fast_path_eligible",
    "pre_entry_watcher_gate",
]


_DECISION_TOKENS = {"PROCEED", "KILL"}
# Q6 G4 token guidance — PROCEED|KILL response stays under ~150 tokens.
WATCHER_MAX_TOKENS: Final[int] = 150


@dataclass(frozen=True, slots=True)
class FastPathContext:
    """Inputs for fast-path eligibility (Q7 spec + Phase 2 per-stream guards).

    The first block is the legacy crypto (stream A) input set. The trailing
    fields (defaulted so A construction is unchanged) carry the per-stream
    session inputs read by the B (cfd) / C (equity) guards — supplied by
    ``build_watcher_payload`` from the reused session modules. They are
    eligibility inputs only: a not-eligible signal flows to the slow GPT path,
    never blocked.
    """

    cell_quartile: str  # "top" | "mid" | "bottom" | "cold"
    signal_strength: float  # validated_signal.strength
    spread_bps: float
    baseline_p50_spread_bps: float
    recent_reject_in_6h: bool
    listing_age_hours: float
    session_open_shock_window: bool
    # B (cfd) session-state inputs (default = open & calm → no effect on A/C).
    cfd_market_open: bool = True
    cfd_rollover_window: bool = False
    # C (equity) RTH / PDT / opening-gap inputs (defaults → no effect on A/B).
    equity_session_state: str = "rth"
    opening_gap_window: bool = False
    daytrade_count: int = 0


def is_fast_path_eligible(
    fp: FastPathContext, stream_profile: StreamProfile | None = None
) -> bool:
    """Whether G4 may skip the slow GPT watcher (per-stream guard, Phase 2).

    Dispatches on the ``StreamProfile.guard_hooks`` token (resolved once via
    ``guard_token_for_product_class``): B → CFD session-state guard, C → equity
    RTH/PDT/gap guard. With NO profile (or any non-B/C token) the LEGACY crypto
    (stream A) logic runs verbatim — byte-identical, so A is unaffected. This is
    an EFFICIENCY/eligibility decision, NEVER an entry block.
    """
    hooks = stream_profile.guard_hooks if stream_profile is not None else frozenset()
    if GUARD_CFD in hooks:
        return cfd_fast_path_eligible(fp)
    if GUARD_EQUITY in hooks:
        return equity_fast_path_eligible(fp)
    # A (crypto/spot) — legacy logic, byte-identical (Q7 spec).
    if fp.cell_quartile != "top":
        return False
    if fp.signal_strength < 1.25:
        return False
    if fp.baseline_p50_spread_bps <= 0.0:
        return False
    if fp.spread_bps > fp.baseline_p50_spread_bps * 0.9:
        return False
    if fp.recent_reject_in_6h:
        return False
    if fp.listing_age_hours < 24.0:
        return False
    return not fp.session_open_shock_window


def _build_user_prompt(
    *,
    validated_signal: dict[str, Any],
    tick_window: list[dict[str, Any]],
) -> str:
    tail = "\n".join(
        f"- {t.get('ts')}: bid={t.get('bid')} ask={t.get('ask')} mid={t.get('mid')}"
        for t in tick_window[-30:]
    )
    return (
        f"# Validated signal\n{validated_signal}\n"
        f"# Last 30s ticks\n{tail}\n"
        'Output JSON: {"decision": "PROCEED|KILL", "reason": "..."}'
    )


def _validate_decision(parsed: dict[str, Any]) -> GateDecision | None:
    decision = str(parsed.get("decision", "")).upper()
    if decision not in _DECISION_TOKENS:
        return None
    return GateDecision.PROCEED if decision == "PROCEED" else GateDecision.KILL


async def pre_entry_watcher_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    fast_path_ctx: FastPathContext | None = None,
) -> GateResult:
    """Gate 4 dispatcher.

    Inputs from ``ctx.payload``: ``validated_signal``, ``tick_window``.
    """
    validated = ctx.payload.get("validated_signal", {})
    tick_window = list(ctx.payload.get("tick_window", []))
    if not isinstance(validated, dict) or not validated:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_validated_signal"},
            model_used="python",
        )

    # Fast-path
    fp = fast_path_ctx
    if fp is None:
        # Build a default conservative FastPathContext from payload hints. The
        # per-stream (B/C) session inputs are read from the payload when
        # build_watcher_payload supplied them; their defaults (open & calm /
        # rth) keep A unchanged.
        fp = FastPathContext(
            cell_quartile=str(ctx.payload.get("cell_quartile", "mid")),
            signal_strength=float(validated.get("strength_scalar", 1.0)),
            spread_bps=float(ctx.payload.get("spread_bps", 0.0)),
            baseline_p50_spread_bps=float(ctx.payload.get("baseline_p50_spread_bps", 0.0)),
            recent_reject_in_6h=bool(ctx.payload.get("recent_reject_in_6h", False)),
            listing_age_hours=float(ctx.payload.get("listing_age_hours", 9999.0)),
            session_open_shock_window=bool(ctx.payload.get("session_open_shock_window", False)),
            cfd_market_open=bool(ctx.payload.get("cfd_market_open", True)),
            cfd_rollover_window=bool(ctx.payload.get("cfd_rollover_window", False)),
            equity_session_state=str(ctx.payload.get("equity_session_state", "rth")),
            opening_gap_window=bool(ctx.payload.get("opening_gap_window", False)),
            daytrade_count=int(ctx.payload.get("daytrade_count", 0)),
        )
    if is_fast_path_eligible(fp, ctx.stream_profile):
        return GateResult(
            decision=GateDecision.PROCEED,
            next_gate=GATE_ENTRY_SIZER,
            payload={"watched_signal": validated, "fast_path": True},
            model_used="python_fast_path",
            latency_ms=0,
            skipped=True,
        )

    # Slow-path GPT
    if client is None:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "no_gpt_client"},
            model_used="python",
        )

    system = make_system_prefix(
        role="Polaris Pre-Entry Watcher — PROCEED or KILL only (30s window).",
        decision_enum=["PROCEED", "KILL"],
        cell_summary=str(ctx.payload.get("cell_routing", {})),
        baseline_summary=str(ctx.payload.get("baseline", {})),
        recent_trades_summary=str(
            ctx.payload.get("recent_trades", [])[:LESSON_RECENT_TRADES_MAX]
        ),
    )
    prompt = _build_user_prompt(validated_signal=validated, tick_window=tick_window)
    res: GPTCallResult = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=prompt,
        max_tokens=WATCHER_MAX_TOKENS,
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
    decision = _validate_decision(res.parsed)
    if decision is None or decision == GateDecision.KILL:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "watcher_kill", "raw": res.text[:200]},
            model_used="gpt",
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        )
    return GateResult(
        decision=GateDecision.PROCEED,
        next_gate=GATE_ENTRY_SIZER,
        payload={"watched_signal": validated, "fast_path": False},
        model_used="gpt",
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )
