"""Day 8 — production per-signal G1→G8 pipeline driver.

Drives one validated RawSignal through gates 1-7 with production-shaped payloads
(``build_*_payload``), invoking the injected AllocatorFence submit closure and
real G6/G7 mark-to-market. Split out of ``_production_pipeline`` to keep both
modules ≤500 LOC; ``_production_pipeline`` re-exports ``run_pipeline_for_signal``
so existing import paths keep working.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.pipeline import (
    GateOrchestrator,
    build_exit_payload,
    build_monitor_payload,
    build_sizer_payload,
    build_validator_payload,
    build_watcher_payload,
)
from polaris.core.pipeline.agents import (
    adaptive_exit_gate,
    position_monitor_gate,
)
from polaris.core.pipeline.gate_orchestrator import log_gate_event
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_POSITION_MONITOR,
    GATE_UNIVERSE_SCANNER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.sizing.constants import production_default_equity_usd
from polaris.core.streams import (
    StreamConfig,
    asset_class_allowed_for_venue,
    derive_leverage,
    resolve_stream,
    resolve_stream_profile,
)
from polaris.scripts._production_indicators import compute_unrealized_pnl_r
from polaris.strategies import BaseStrategy, RawSignal

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

# T7: the flat CFD_LEVERAGE_DEFAULT (was 30.0) is RETIRED — Capital leverage is
# now per-market via derive_leverage(stream, asset_class) (FX 30 / index 20 /
# commodity 20 / crypto 2; live CapitalMarketConstraint.leverage overrides).
# SSOT = polaris.core.streams.fallback_leverage_for_asset_class. OKX spot stays
# the invariant fixed 1.0.


def _maybe_register_rotation_candidate(
    state: ProdLoopState,
    *,
    results: list[Any],
    sig: RawSignal,
    venue: str,
) -> None:
    """Register a capital-blocked signal as a rotation candidate (trigger 1).

    Scans the gate results for an entry-sizer ``sizing_zero`` KILL on a binding
    cap and, if found, pushes the signal + its surfaced ``proposed_risk_pct``
    onto ``state.rotation_candidates`` via the rotation wire. Only a
    capital-block (binding cap) qualifies — a missing/zero ``proposed_risk_pct``
    or any other KILL reason is skipped (rotation only fires for a real pending
    deploy, keeping net deploy UP). Import is local to avoid a module-load cycle
    (``_production_rotation`` -> state -> tick -> run_signal).
    """
    from polaris.scripts._production_rotation import register_rotation_candidate

    for r in results:
        if r.decision != GateDecision.KILL:
            continue
        if r.payload.get("reason") != "sizing_zero":
            continue
        proposed = r.payload.get("proposed_risk_pct")
        if proposed is None:
            continue
        register_rotation_candidate(
            state, sig=sig, proposed_risk_pct=float(proposed), venue=venue,
            binding_reason=f"sizing_zero:{r.payload.get('binding_cap', '?')}",
        )
        return


def _assert_stream_asset_class_coherent(stream: StreamConfig, asset_class: str) -> bool:
    """True iff ``asset_class`` belongs on ``stream``'s venue (STEP 4 guard).

    Runtime regression catch for the STEP 2 routing correction: if a crypto tag
    leaks back into the Capital B-stream (e.g. a stale universe row or a future
    nav-tree change re-admits crypto CFDs), this returns ``False`` so the caller
    drops/flags the signal instead of sizing an off-venue (wrong-leverage)
    position. Delegates to the SSOT (``asset_class_allowed_for_venue``) — an
    unregistered venue stays permissive. This is a coherence/routing assertion,
    NOT a defensive throttle (a coherent signal is never touched).
    """
    return asset_class_allowed_for_venue(stream.venue, asset_class)


def _read_universe_state(
    conn: sqlite3.Connection, *, venue: str, symbol: str, now_ts: int,
) -> tuple[float, float]:
    """Return (spread_bps, listing_age_hours) from the universe row.

    Falls back to (5.0 bps, 365×24 h) when the row is missing — the smoke
    seed path may not have populated universe yet for cold-start runs.
    """
    row = conn.execute(
        "SELECT spread_bps, listing_ts FROM universe "
        "WHERE venue = ? AND symbol = ?",
        (venue, symbol),
    ).fetchone()
    if row is None:
        return (5.0, 24 * 365.0)
    spread_bps = float(row[0] or 5.0)
    listing_ts = int(row[1]) if row[1] is not None else (now_ts - 24 * 3600 * 365)
    listing_age_hours = max(0.0, (now_ts - listing_ts) / 3600.0)
    return (spread_bps, listing_age_hours)


def _log_entry_admission_shadow(
    shadow_conn: sqlite3.Connection | None,
    *,
    run_id: str,
    sig: RawSignal,
    venue: str,
    symbol: str,
    regime: str,
    cell_routing: dict[str, Any],
    entry_price: float,
    atr_pct: float,
) -> None:
    """Component C (SHADOW): compute the edge-first admission decision + LOG it.

    Behavior 0 — the decision is logged ONLY; the live pipeline admit/skip below
    is UNCHANGED (this call adds a single ``entry_admission_shadow`` row and
    returns). No-op when ``shadow_conn`` is None so the gated call is
    byte-identical to the legacy path (mirrors ``signal_validator._log_g3_shadow``).

    The cost is the REAL OKX round trip (``fees.real_fee_usd`` ×2 legs) expressed
    in ATR-R (``cost_usd / atr_usd``), where ``atr_usd = entry_price * atr_pct *
    2.0`` mirrors ``_production_close.py`` — the SAME R unit as the cell's
    ``avg_pnl_r`` and the expected-move proxy (no $-basis mismatch). The fee leg
    is sized at a representative $50 risk notional (the cost-R is the per-trade
    fee fraction of an ATR move, independent of the not-yet-sized live notional).
    ``net_edge`` is never consulted. Cold cell (n_eff < CELL_POOL_MIN_N_EFF)
    ALWAYS admits.
    """
    if shadow_conn is None:
        return
    # Local imports keep this module free of an economics dependency unless the
    # shadow wire actually fires (and avoid any load-order surprise).
    from polaris.core.economics.entry_admission import (
        entry_admission_decision,
        expected_move_r_from_cell,
        real_round_trip_cost_r_from_usd,
    )
    from polaris.core.economics.entry_admission_shadow import log_entry_admission
    from polaris.core.economics.fees import real_fee_usd

    n_eff = float(cell_routing.get("n_eff", 0.0) or 0.0)
    avg_pnl_r = float(cell_routing.get("avg_pnl_r", 0.0) or 0.0)
    wins_eff = float(cell_routing.get("wins_eff", 0.0) or 0.0)
    expected_move_r = expected_move_r_from_cell(
        n_eff=n_eff, wins_eff=wins_eff, avg_pnl_r=avg_pnl_r,
    )
    # ATR-R basis: atr_usd mirrors _production_close.py:120 (entry_price * atr_pct
    # * 2.0) so the cost shares the cell-matrix R unit. A degenerate atr_usd <= 0
    # makes the cost helper fail-open to 0.0 (never manufactures a would_suppress).
    from polaris.core.pipeline.payload_builder import PNL_R_USD_DENOM

    atr_usd = entry_price * atr_pct * 2.0
    # One leg's real fee at a representative $50 risk-unit notional; ×2 legs is
    # applied inside real_round_trip_cost_r_from_usd, then divided by atr_usd.
    real_leg_usd = real_fee_usd(venue, PNL_R_USD_DENOM)
    real_round_trip_cost_r = real_round_trip_cost_r_from_usd(
        real_fee_one_leg_usd=real_leg_usd,
        atr_usd=atr_usd,
    )
    decision = entry_admission_decision(
        cell_n_eff=n_eff,
        cell_avg_pnl_r=avg_pnl_r,
        regime=regime,
        expected_move_r=expected_move_r,
        real_round_trip_cost_r=real_round_trip_cost_r,
    )
    log_entry_admission(
        shadow_conn,
        run_id=run_id,
        signal_id=sig.signal_id,
        venue=venue,
        symbol=symbol,
        strategy_id=sig.strategy_id,
        regime=regime,
        cell_n_eff=n_eff,
        cell_avg_pnl_r=avg_pnl_r,
        expected_move_r=expected_move_r,
        real_round_trip_cost_r=real_round_trip_cost_r,
        decision=decision,
    )


def _strategy_recent_reject(
    conn: sqlite3.Connection, *, strategy_id: str, now_ts: int,
    window_sec: int = 6 * 3600,
) -> bool:
    """True if the strategy logged a reject/halt within the last 6h."""
    cutoff = now_ts - window_sec
    row = conn.execute(
        "SELECT 1 FROM strategy_fault_events "
        "WHERE strategy_id = ? AND fault_type IN ('reject', 'idempotency_conflict') "
        "AND event_ts >= ? LIMIT 1",
        (strategy_id, cutoff),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# G1 → G8 production pipeline
# ---------------------------------------------------------------------------


async def run_pipeline_for_signal(
    *,
    conn: sqlite3.Connection,
    haiku: Any,
    state: ProdLoopState,
    strategy: BaseStrategy,
    sig: RawSignal,
    venue: str,
    symbol: str,
    asset_class: str,
    underlying_group_id: str,
    regime: str,
    bars_atr_pct: float,
    last_price: float,
    universe_rows: list[dict[str, Any]],
    now_ts: int,
    reserve_and_submit: Any,
    phase: str = "P0",
    real_roundtrip: bool = False,
    capital_session: Any = None,
    okx_adapter: Any = None,
    alpaca_adapter: Any = None,
) -> None:
    """Run G1 → G2 → G3 → G4 → G5 → G6 → G7 for one validated signal.

    ``reserve_and_submit`` is the AllocatorFence-aware submit closure from
    ``production_paper_loop`` (kept as an injected dep so this module stays
    free of state.fields-binding logic).

    Day 8 spec D: ``start_gate=GATE_UNIVERSE_SCANNER`` so G1/G2 also run.
    Day 8 spec E: G6/G7 use real ``unrealized_pnl_r``; G8 fires on close.
    """
    instrument_id = f"{venue}:{symbol}"
    # Stream SSOT lookup (design §2.1) replaces the venue-binary track branch.
    # Track is identical to the prior literal (A_okx_crypto→A, B_capital_cfd→B).
    stream = resolve_stream(venue)
    # STEP 4 (coherence guard): the symbol's asset_class MUST belong on this
    # stream's venue (Jin 2026-05-30 STEP 0 (a) — crypto on OKX, FX/index/
    # commodity on Capital). If a crypto tag leaks back into the Capital
    # B-stream (stale universe row / future nav change), drop the signal here
    # before it sizes with the wrong leverage. Routing coherence, NOT a throttle.
    if not _assert_stream_asset_class_coherent(stream, asset_class):
        logger.warning(
            "[stream-coherence] DROP off-venue signal %s asset_class=%r not in "
            "stream %s asset_classes=%s (crypto belongs on OKX track A)",
            instrument_id, asset_class, stream.stream_id, sorted(stream.asset_classes),
        )
        return
    track: Any = stream.track
    # T7: per-market leverage. OKX spot stays the INVARIANT fixed 1.0; Capital
    # CFD derives leverage from the symbol's asset_class (FX 30 / index 20 /
    # commodity 20 / crypto 2) instead of the erroneous flat 30 — this CORRECTS
    # the notional down for index/commodity/crypto (a bug fix, not a throttle).
    # (Live CapitalMarketConstraint.leverage overrides the fallback at the
    # constraint_translator layer; here we use the asset_class fallback because
    # the per-symbol constraint is not loaded on this path.)
    leverage = derive_leverage(stream, asset_class)
    equity_usd = production_default_equity_usd()

    # Day 8 codex P1 fix: read spread/listing/recent-reject from real state
    # (universe + bars + strategy_fault_events) instead of hard-coded fixtures.
    spread_bps_real, listing_age_h = _read_universe_state(
        conn, venue=venue, symbol=symbol, now_ts=now_ts,
    )
    recent_reject = _strategy_recent_reject(
        conn, strategy_id=sig.strategy_id, now_ts=now_ts,
    )

    g3_payload = build_validator_payload(
        raw_signal=sig, venue=venue, symbol=symbol, instrument_id=instrument_id,
        regime=regime, conn=conn,
    )
    g4_payload = build_watcher_payload(
        spread_bps=spread_bps_real, baseline_p50_spread_bps=spread_bps_real,
        listing_age_hours=listing_age_h, recent_reject_in_6h=recent_reject,
        session_open_shock_window=False, tick_window=[],
        # T14 net-edge measurement inputs (DISPLAY/LOG-ONLY). Surfacing these
        # adds payload keys + the log line below; it does NOT change control
        # flow — no early return / no skip (SKIP_ON_NEGATIVE_NET_EDGE is False).
        venue=venue, signal_strength=sig.strength, atr_pct=bars_atr_pct,
    )
    # Display-only emit so the dashboard can later read net edge vs cost. The
    # values never gate (cost measurement, not a defensive throttle).
    logger.info(
        "[net_edge] %s:%s cost_model=%s net_edge_r=%.4f "
        "gross_edge_r=%.4f roundtrip_cost_r=%.4f (display-only, no gate)",
        venue, symbol, g4_payload.get("cost_model", "?"),
        g4_payload.get("net_edge_r", 0.0), g4_payload.get("gross_edge_r", 0.0),
        g4_payload.get("roundtrip_cost_r", 0.0),
    )
    g5_payload = build_sizer_payload(
        raw_signal=sig, venue=venue, symbol=symbol,
        instrument_id=instrument_id, underlying_group_id=underlying_group_id,
        asset_class=asset_class, regime=regime, track=track,
        listing_age_hours=listing_age_h, leverage=leverage,
        equity_usd=equity_usd, conn=conn,
    )
    payload: dict[str, Any] = {
        "signal_id": sig.signal_id,
        "universe": universe_rows,
        "cell_summary": "",
        # ai_conductor P0 SHADOW dimension: surface ``regime`` so the G3/G4
        # shadow log can bucket technical-vs-GPT agreement by regime. Display/log
        # key only — no gate branches on it (behavior 0).
        "regime": regime,
        "raw_signal": g3_payload["raw_signal"],
        **g3_payload, **g4_payload, **g5_payload,
    }
    # Gate architecture Phase 0 (Option A): resolve the per-stream seam ONCE and
    # thread it through every GateContext. P0 = structural enabler only — no gate
    # reads it for a decision yet, so A/B/C stay byte-identical (P1+ enriches it).
    stream_profile = resolve_stream_profile(venue)
    ctx = GateContext(
        run_id=uuid.uuid4().hex, signal_id=sig.signal_id, position_id=None,
        gate_id=GATE_UNIVERSE_SCANNER, venue=venue, symbol=symbol,
        strategy_id=strategy.metadata.strategy_id, payload=payload,
        started_ts=now_ts, state=SignalLifecycle.RAW,
        stream_profile=stream_profile,
    )
    # Component C (SHADOW, behavior 0): compute the edge-first entry admission
    # decision (regime-conditioned cell expectancy + cost-aware move vs REAL
    # round-trip fee) and LOG it against this run. This NEVER branches the
    # pipeline — the live admit/skip is owned entirely by the gates below; only a
    # single entry_admission_shadow row is added. Passing ``conn`` as the shadow
    # conn means a None conn (never happens on this production path) would be
    # byte-identical (the helper no-ops). cell_routing lives inside g3_payload.
    _g3_cell = g3_payload.get("cell_routing", {})
    _log_entry_admission_shadow(
        conn,
        run_id=ctx.run_id,
        sig=sig,
        venue=venue,
        symbol=symbol,
        regime=regime,
        cell_routing=_g3_cell if isinstance(_g3_cell, dict) else {},
        # ATR-R basis: last_price is the entry/ref price (same as the live
        # entry_price below) and bars_atr_pct is the bar-derived ATR%. atr_usd =
        # last_price * bars_atr_pct * 2.0 mirrors _production_close.py so the
        # shadow cost shares the cell-matrix R unit.
        entry_price=last_price,
        atr_pct=bars_atr_pct,
    )
    # G1-EFF: share the per-run focus cache so the G1 GPT call is reused across
    # signals/ticks while the universe composition is unchanged (efficiency
    # only — the focus DECISION is still GPT-chosen). G1 still always PASS.
    orch = GateOrchestrator(
        conn=conn, haiku_client=haiku, phase=phase,
        g1_focus_cache=state.g1_focus_cache,
    )
    results = await orch.run(ctx, start_gate=GATE_UNIVERSE_SCANNER)
    state.pipeline_runs += len(results)
    if any(r.decision == GateDecision.KILL for r in results):
        state.pipeline_kills += 1
    state.g1_runs += 1
    # Cost telemetry: count G1 runs that reused the cached focus (no GPT call).
    # ``model_used == "cached"`` is emitted ONLY by the G1-EFF skip path.
    if any(r.model_used == "cached" for r in results):
        state.g1_focus_skipped += 1
    state.g2_emits += 1
    sized_payload: dict[str, Any] | None = None
    for r in results:
        if r.decision == GateDecision.SIZED:
            sized_payload = r.payload.get("sized")
            state.sized_count += 1
            break
    if sized_payload is None:
        # Capital rotation TRIGGER SEAM 1 (Jin 2026-05-30): the signal was
        # KILLed by the entry sizer on a binding cap (``sizing_zero``) — capital
        # is the blocker, not the signal's quality. Register it as a rotation
        # CANDIDATE carrying its conviction-derived ``proposed_risk_pct`` (the
        # capital SCALE only) so a later weak held can be redeployed into it.
        # This is capital EFFICIENCY (a concrete pending entry → net deploy UP),
        # NOT a defensive throttle; it does NOT re-open this entry here and adds
        # NO T4 multiplier. Other KILL reasons are signal-quality rejects (not a
        # capital block) and are intentionally NOT registered.
        _maybe_register_rotation_candidate(
            state, results=results, sig=sig, venue=venue,
        )
        return

    notional_usd = max(
        10.0, min(float(sized_payload.get("final_notional_usd", 50.0)), 5_000.0)
    )
    trade = await reserve_and_submit(
        conn=conn, state=state, sig=sig, venue=venue, symbol=symbol,
        asset_class=asset_class, underlying_group_id=underlying_group_id,
        notional_usd=notional_usd, last_price=last_price, now_ts=now_ts,
        real_roundtrip=real_roundtrip, capital_session=capital_session,
        okx_adapter=okx_adapter, alpaca_adapter=alpaca_adapter,
    )
    if trade is None:
        return
    state.open_trades.append(trade)
    # Component B anti-churn: record the LAST actually-submitted entry per
    # (venue, symbol, strategy_id) so the next tick's novelty test exempts only
    # a NEW strategy-timeframe bar OR a side flip vs this — never raw strength.
    state.last_entry_by_key[(venue, symbol, sig.strategy_id)] = (
        sig.created_at_bar, sig.side,
    )

    # G6 / G7 with real R-multiples.
    pnl_r = compute_unrealized_pnl_r(
        side=sig.side, entry_price=last_price, last_price=last_price,
        atr_pct=max(bars_atr_pct, 1e-4),
    )
    g6_payload = build_monitor_payload(
        position={
            "venue": venue, "symbol": symbol, "side": sig.side,
            "strategy": sig.strategy_id,
            "correlation_group": sig.correlation_group,
            "entry_price": last_price,
            "qty": notional_usd / max(last_price, 1e-6),
        },
        unrealized_pnl_r=pnl_r, max_loss_r=1.0,
    )
    g7_payload = build_exit_payload(
        side=sig.side,
        current_stop_price=last_price * (0.99 if sig.side == "long" else 1.01),
        proposed_stop_price=last_price * (0.985 if sig.side == "long" else 1.015),
        entry_price=last_price, unrealized_pnl_r=pnl_r, max_loss_r=1.0,
        overrides_used=0, seconds_since_last_override=60,
    )
    # Day 8 codex R2 P2 fix: G6/G7/G8 telemetry must use the persisted
    # ``positions.position_id`` so gate_events.position_id joins back to
    # positions for audits + downstream replay.
    persisted_position_id = trade.position_id or f"pos_{sig.signal_id[:10]}"
    g6_ctx = GateContext(
        run_id=ctx.run_id, signal_id=sig.signal_id,
        position_id=persisted_position_id,
        gate_id=GATE_POSITION_MONITOR,
        venue=venue, symbol=symbol, strategy_id=sig.strategy_id,
        payload=g6_payload, started_ts=now_ts, state=SignalLifecycle.SIZED,
        stream_profile=stream_profile,
    )
    # Day 9 F1 wire: forward GPT client at P1 so G6 fires the gpt_p1 branch
    # (entry-time invocation also exercises the LLM path; F2 live recalc
    # then re-invokes G6 per dirty trigger).
    g6_client = haiku if phase == "P1" else None
    g6_result = await position_monitor_gate(g6_ctx, client=g6_client)
    # Day 8 codex R3 P2 fix: persist G6 result to gate_events so audits can
    # join gate_events.position_id back to positions for the full lifecycle.
    log_gate_event(conn, g6_ctx, g6_result)
    g7_ctx = GateContext(
        run_id=ctx.run_id, signal_id=sig.signal_id,
        position_id=g6_ctx.position_id,
        gate_id=GATE_ADAPTIVE_EXIT,
        venue=venue, symbol=symbol, strategy_id=sig.strategy_id,
        payload={
            **g6_payload, **g7_payload,
            "current_stop_price": g7_payload["current_stop_price"],
        },
        started_ts=now_ts,
        state=(
            SignalLifecycle.MONITORED
            if g6_result.decision == GateDecision.HOLD
            else SignalLifecycle.ACTIVE
        ),
        stream_profile=stream_profile,
    )
    g7_client = haiku if phase == "P1" else None
    g7_result = await adaptive_exit_gate(g7_ctx, client=g7_client)
    log_gate_event(conn, g7_ctx, g7_result)


