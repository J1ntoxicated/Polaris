"""G4 frontgate helpers — relocated verbatim into G3's flow (P2a group A).

Gate 4 (Pre-Entry Watcher) is abolished as a decision STEP (measured
1447/1447 PROCEED — 100% no-op, its own GPT call already removed in P2a
group B); G3 wires directly to G5. This module holds G4's side-effect
content, called from ``signal_validator.py`` so nothing is lost:

- ``FastPathContext`` / ``is_fast_path_eligible`` / ``fast_path_context_from_payload``
  — the EFFICIENCY skip of the slow per-tick watch computation (never an
  entry block).
- ``log_g4_frontgate_shadow`` — VWAP/AVWAP timing (frontgate item #6) + TTM
  Squeeze (item #9) watch-only shadow tags.
- ``maybe_judge_timing`` — the #32 AI entry-TIMING judge (a separate,
  out-of-scope call path from G3's own entry-rationale judge; left
  byte-identical, still logs under ``GATE_PRE_ENTRY_WATCHER`` for
  measurement continuity with historical G4 rows).

Split out of ``signal_validator.py`` to keep both files under the 500-LOC
budget (CLAUDE.md).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.core.pipeline.agents._frontgate_bars import fetch_known_bars
from polaris.core.pipeline.agents._shadow_rules import G4ShadowInputs, ShadowDecision
from polaris.core.pipeline.agents._stream_guards import (
    GUARD_CFD,
    GUARD_EQUITY,
    cfd_fast_path_eligible,
    equity_fast_path_eligible,
)
from polaris.core.pipeline.agents.ai_judge import (
    apply_entry_verdict,
    judge_entry,
    log_judge_event,
)
from polaris.core.pipeline.agents.judge_gate import (
    evidence_robustness,
    robust_min,
    should_escalate_entry,
)
from polaris.core.pipeline.agents.shadow_log import log_shadow_event
from polaris.core.pipeline.agents.ttm_squeeze_shadow import compute_squeeze_state
from polaris.core.pipeline.agents.vwap_timing_shadow import log_vwap_timing_shadow
from polaris.core.pipeline.config import ai_judge_mode
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GateContext,
    GateDecision,
    GateResult,
)

if TYPE_CHECKING:
    from polaris.core.streams import StreamProfile

__all__ = [
    "FastPathContext",
    "fast_path_context_from_payload",
    "is_fast_path_eligible",
    "log_g4_frontgate_shadow",
    "maybe_judge_timing",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FastPathContext:
    """Inputs for fast-path eligibility (relocated verbatim from G4, Q7 spec
    + Phase 2 per-stream guards).

    The first block is the legacy crypto (stream A) input set. The trailing
    fields (defaulted so A construction is unchanged) carry the per-stream
    session inputs read by the B (cfd) / C (equity) guards. They are
    eligibility inputs only: a not-eligible signal flows the normal (now G3-
    only) path, never blocked.
    """

    cell_quartile: str  # "top" | "mid" | "bottom" | "cold"
    signal_strength: float  # validated_signal.strength_scalar
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
    """Whether the slow per-tick watch computation below may be skipped
    (relocated verbatim from G4, Q7 spec).

    Dispatches on the ``StreamProfile.guard_hooks`` token: B → CFD
    session-state guard, C → equity RTH/PDT/gap guard. With NO profile (or
    any non-B/C token) the LEGACY crypto (stream A) logic runs verbatim. This
    is an EFFICIENCY/eligibility decision, NEVER an entry block.
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


def fast_path_context_from_payload(
    ctx: GateContext, validated: dict[str, Any]
) -> FastPathContext:
    """Build the default conservative FastPathContext from payload hints
    (relocated verbatim from G4). The per-stream (B/C) session inputs are
    read from the payload when supplied; their defaults (open & calm / rth)
    keep A unchanged."""
    return FastPathContext(
        cell_quartile=str(ctx.payload.get("cell_quartile", "mid")),
        signal_strength=float(validated.get("strength_scalar", 1.0)),
        spread_bps=float(ctx.payload.get("spread_bps", 0.0)),
        baseline_p50_spread_bps=float(ctx.payload.get("baseline_p50_spread_bps", 0.0)),
        recent_reject_in_6h=bool(ctx.payload.get("recent_reject_in_6h", False)),
        listing_age_hours=float(ctx.payload.get("listing_age_hours", 9999.0)),
        session_open_shock_window=bool(
            ctx.payload.get("session_open_shock_window", False)
        ),
        cfd_market_open=bool(ctx.payload.get("cfd_market_open", True)),
        cfd_rollover_window=bool(ctx.payload.get("cfd_rollover_window", False)),
        equity_session_state=str(ctx.payload.get("equity_session_state", "rth")),
        opening_gap_window=bool(ctx.payload.get("opening_gap_window", False)),
        daytrade_count=int(ctx.payload.get("daytrade_count", 0)),
    )


def log_g4_frontgate_shadow(
    ctx: GateContext,
    shadow_conn: sqlite3.Connection | None,
    *,
    md_conn: sqlite3.Connection | None = None,
    inp: G4ShadowInputs,
) -> None:
    """VWAP/AVWAP timing (frontgate item #6) + TTM Squeeze (item #9) SHADOW tags.

    Relocated verbatim from G4 (P2a group A). Fired once per PROCEED path
    (behavior-0 — never touches ``det_result``/payload; see
    ``vwap_timing_shadow.py`` / ``ttm_squeeze_shadow.py`` for the full
    design). VWAP/AVWAP writes its OWN table (``vwap_timing_shadow`` — the
    columns don't fit ``gate_shadow_events``); TTM Squeeze reuses the
    EXISTING ``gate_shadow_events`` table via ``log_shadow_event`` with
    ``gpt_decision=None`` (mismatch pinned to 0 — this is a watch tag, not a
    technical-vs-GPT comparison). Fail-open: any error here is swallowed so a
    bars-fetch/compute fault can never crash the gate.

    ``shadow_conn`` is the master enable/disable toggle for this whole shadow
    cluster. ``md_conn`` (storage-split round 4 fix): ``bars``/
    ``vwap_timing_shadow`` are marketdata-domain while ``gate_shadow_events``
    (the TTM squeeze tag below) stays trading-domain — so the bars read + the
    vwap_timing_shadow write route to ``md_conn`` (falling back to
    ``shadow_conn`` when unwired), while the TTM squeeze ``log_shadow_event``
    call keeps using ``shadow_conn``.
    """
    if shadow_conn is None:
        return
    _bars_conn = md_conn if md_conn is not None else shadow_conn
    try:
        decision_ts = ctx.started_ts if ctx.started_ts > 0 else int(time.time())
        bar_views = fetch_known_bars(
            _bars_conn, venue=ctx.venue, symbol=ctx.symbol, decision_ts=decision_ts,
        )
        current_price = (
            (inp.best_bid + inp.best_ask) / 2.0
            if inp.best_bid > 0.0 and inp.best_ask > 0.0
            else 0.0
        )
        log_vwap_timing_shadow(
            _bars_conn,
            run_id=ctx.run_id,
            signal_id=ctx.signal_id,
            venue=ctx.venue,
            symbol=ctx.symbol,
            decision_ts=decision_ts,
            current_price=current_price,
            bar_views=bar_views,
        )
        squeeze = compute_squeeze_state(bar_views)
        cell = ctx.payload.get("cell_routing", {})
        n_eff = float(cell.get("n_eff", 0.0) or 0.0) if isinstance(cell, dict) else 0.0
        log_shadow_event(
            shadow_conn,
            run_id=ctx.run_id,
            signal_id=ctx.signal_id,
            gate_id=GATE_PRE_ENTRY_WATCHER,
            venue=ctx.venue,
            symbol=ctx.symbol,
            regime=str(ctx.payload.get("regime", "")),
            technical=ShadowDecision(
                decision=GateDecision.PROCEED,
                reason=squeeze.reason,
                flags=(squeeze.reason,),
            ),
            gpt_decision=None,
            cell_warm=n_eff >= 5.0,
        )
    except Exception as exc:  # noqa: BLE001 — instrumentation must never crash the gate
        logger.warning(
            "[g4-frontgate-shadow] tag dropped run_id=%s signal_id=%s: %r",
            ctx.run_id, ctx.signal_id, exc,
        )


async def maybe_judge_timing(
    ctx: GateContext,
    *,
    det_result: GateResult,
    judge_client: Any | None,
    shadow_conn: sqlite3.Connection | None,
) -> GateResult:
    """Run the #32 AI entry-TIMING judge over the watch result (non-blocking).

    Relocated verbatim from G4 (P2a group A) — still logs under
    ``GATE_PRE_ENTRY_WATCHER`` for measurement continuity with historical G4
    rows. No-op when ``judge_client`` is None (deterministic result returned
    byte-identical). The judge reads the bot's own information + tick context
    and can REFINE_TIMING (one-shot, time-boxed) but NEVER KILL — its verdict
    type has no block member, so a PASS/MODIFY can never become a KILL. Only
    the objective crossed-book KILL (deterministic microstructure validity)
    reaches here as a non-PASS/MODIFY, and the caller does NOT route that
    through the judge. Active mode annotates; shadow logs.

    A+B CALL GATE (#32 axes): only a FLAGGED result (watch_flags ∈
    {spread_wide, drift, stale_book}) with robust evidence escalates; a clean
    non-flagged result skips the GPT call (anti-flooding). ``escalate=False``
    NEVER blocks — the result flows unchanged (flow_not_block). The
    fast-path result never reaches here.
    """
    if judge_client is None:
        return det_result
    watch_flags = det_result.payload.get("watch_flags", ())
    esc_a, _reason_a = should_escalate_entry(
        gate="G4",
        decision=det_result.decision.name,
        scalar=1.0,
        n_eff=0.0,
        watch_flags=tuple(watch_flags) if isinstance(watch_flags, (list, tuple)) else (),
        recent_reject_in_6h=bool(ctx.payload.get("recent_reject_in_6h", False)),
    )
    rob = evidence_robustness(ctx.payload, now_ts=int(ctx.started_ts))
    if not (esc_a and rob.score >= robust_min()):
        return det_result
    outcome = await judge_entry(
        ctx,
        deterministic=det_result.decision,
        subject=det_result.payload.get("watched_signal", {}),
        client=judge_client,
    )
    mode = ai_judge_mode()
    log_judge_event(
        shadow_conn, ctx=ctx, gate_id=GATE_PRE_ENTRY_WATCHER, outcome=outcome, mode=mode
    )
    return apply_entry_verdict(outcome, deterministic_result=det_result, mode=mode)
