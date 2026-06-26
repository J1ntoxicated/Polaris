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
    alpaca_equity_entries_halted,
    derive_leverage,
    resolve_stream,
    resolve_stream_profile,
)
from polaris.scripts._production_counterfactual import (
    backfill_run_position_id,
    record_pipeline_cohort,
)
from polaris.scripts._production_indicators import compute_unrealized_pnl_r

# Re-exported (move-only split) with redundant aliases so the stateless per-signal
# helpers stay reachable as module attributes (mypy --strict no-implicit-reexport)
# and run_pipeline_for_signal's global lookups resolve byte-identically to the
# pre-split single module. ``run_pipeline_for_signal`` itself stays here because
# its gate/orchestrator globals are the test monkeypatch seams.
from polaris.scripts._run_signal_helpers import (
    _assert_stream_asset_class_coherent as _assert_stream_asset_class_coherent,
)
from polaris.scripts._run_signal_helpers import (
    _bar_order_mode as _bar_order_mode,
)
from polaris.scripts._run_signal_helpers import (
    _log_entry_admission_shadow as _log_entry_admission_shadow,
)
from polaris.scripts._run_signal_helpers import (
    _maybe_register_rotation_candidate as _maybe_register_rotation_candidate,
)
from polaris.scripts._run_signal_helpers import (
    _read_universe_state as _read_universe_state,
)
from polaris.scripts._run_signal_helpers import (
    _strategy_recent_reject as _strategy_recent_reject,
)
from polaris.scripts._static_ground import read_ticker_ground
from polaris.strategies import BaseStrategy, RawSignal

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


# T7: the flat CFD_LEVERAGE_DEFAULT (was 30.0) is RETIRED — Capital leverage is
# now per-market via derive_leverage(stream, asset_class) (FX 30 / index 20 /
# commodity 20 / crypto 2; live CapitalMarketConstraint.leverage overrides).
# SSOT = polaris.core.streams.fallback_leverage_for_asset_class. OKX spot stays
# the invariant fixed 1.0.


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
    # Alpaca dead-feed HALT (Jin 2026-06-22): when the Alpaca feed is stale/dead
    # there is no live price to size or exit against, so a NEW equity entry would
    # become an unexitable zombie. Hold NEW Alpaca entries while the feed is dead.
    # DATA-HEALTH gate ("no live price = cannot trade"), NOT a defensive throttle:
    # it touches no sizing, applies only to NEW entries (exits untouched), and
    # auto-clears the instant a fresh Alpaca bar lands. OKX/Capital are no-ops.
    if venue == "alpaca" and alpaca_equity_entries_halted(conn, now_ts=now_ts):
        logger.warning(
            "[alpaca-health] HOLD new entry %s — Alpaca feed stale/dead "
            "(no live price; auto-clears on fresh data). flow_not_block: "
            "data-health gate, not a throttle.",
            instrument_id,
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
    # P4 #3 — feed the G4 watcher the live WS tick window (last ~30 ticks,
    # newest-last) so its stale/crossed-book judgement runs on real microstructure
    # instead of the empty P0 placeholder. No WS history yet (no writer / no ticks)
    # → [] (G4 treats freshness as unknown, never a manufactured stale KILL).
    tick_window = (
        state.quote_writer.recent_ticks(instrument_id)
        if state.quote_writer is not None
        else []
    )
    g4_payload = build_watcher_payload(
        spread_bps=spread_bps_real, baseline_p50_spread_bps=spread_bps_real,
        listing_age_hours=listing_age_h, recent_reject_in_6h=recent_reject,
        session_open_shock_window=False, tick_window=tick_window,
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
    # #32 — stamp the bot's OWN fused alt-data + ground coverage so the per-ticker
    # AI JUDGE (G3/G4) actually SEES the information it judges over, instead of
    # ``n/a``. The fuser output (news / COT / macro / funding / fear-greed) is
    # ALREADY materialized per active ticker by ``refresh_ticker_ground`` (the
    # background ticker-ground producer), so we REUSE it via ``read_ticker_ground``
    # rather than re-fusing on the hot path. Absent ground row (not yet covered) →
    # the keys are simply not stamped (the judge renders ``n/a`` gracefully).
    # EVIDENCE only: no gate branches on these keys (only the judge reads them).
    ground_row = read_ticker_ground(conn, instrument_id)
    if ground_row is not None:
        payload["evidence"] = ground_row["ground"]
        payload["ticker_ground"] = {
            "has_sentiment": ground_row["has_sentiment"],
            "has_event": ground_row["has_event"],
            # #32 axis-B freshness input: thread the ground stamp so the judge CALL
            # gate can score staleness (missing → treated as NOT fresh, fail-safe).
            "updated_ts": ground_row["updated_ts"],
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
        # #32 — the per-ticker AI JUDGE (G3/G4) runs alongside the deterministic
        # decision. None (no client) → byte-identical no-judge loop. Shadow mode
        # (default) logs the verdict; the deterministic decision still acts.
        judge_client=state.judge_client,
    )
    results = await orch.run(ctx, start_gate=GATE_UNIVERSE_SCANNER)
    state.pipeline_runs += len(results)
    if any(r.decision == GateDecision.KILL for r in results):
        state.pipeline_kills += 1
    # Gate→outcome instrumentation (BUILD, behavior 0): one cohort row per
    # G3/G4 GPT decision — a KILL gets forward-mark counterfactuals (resolved
    # by the bar-ingest sweep), a G4-cleared PASS shares the same estimator.
    # Fail-open inside; never branches the pipeline.
    record_pipeline_cohort(
        conn, state=state, ctx=ctx, results=results, sig=sig, venue=venue,
        symbol=symbol, regime=regime, last_price=last_price,
        atr_pct=bars_atr_pct, timeframe=strategy.metadata.timeframe,
        now_ts=now_ts,
    )
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
    # Build B: per-family OKX order mode — breakout/TREND bar strategy crosses
    # the spread (marketable-limit, cap_bps), reversion/range rests post-only.
    # Every mode falls back to market on no-fill/reject (flow_not_block). Capital
    # bar entries are unaffected (market-default; OKX-only wire today).
    bar_prefer_maker, bar_marketable_limit = _bar_order_mode(sig.strategy_id)
    trade = await reserve_and_submit(
        conn=conn, state=state, sig=sig, venue=venue, symbol=symbol,
        asset_class=asset_class, underlying_group_id=underlying_group_id,
        notional_usd=notional_usd, last_price=last_price, now_ts=now_ts,
        real_roundtrip=real_roundtrip, capital_session=capital_session,
        okx_adapter=okx_adapter, alpaca_adapter=alpaca_adapter,
        prefer_maker=bar_prefer_maker, marketable_limit=bar_marketable_limit,
    )
    if trade is None:
        return
    state.open_trades.append(trade)
    # Gate→outcome PASS link (BUILD, behavior 0): stamp this run's pre-position
    # gate_events rows (position_id NULL — G1-G5) with the persisted
    # position_id so the PASS cohort joins gate_events → positions.pnl_r.
    # run_id-scoped UPDATE (indexed) + fail-open — open path untouched.
    backfill_run_position_id(conn, run_id=ctx.run_id, position_id=trade.position_id)
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


