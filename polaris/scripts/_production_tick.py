"""Day 8 production paper loop — per-tick orchestration (one 5s cycle).

Split out of ``production_paper_loop`` to keep both modules ≤500 LOC.
``production_paper_loop`` re-exports ``_run_tick`` + the strategy/regime/swap
helpers it owns so existing import paths (incl. tests) keep working. Shared
loop state lives in ``_production_state``.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from typing import Any

from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    FAULT_NAN,
    record_fault,
    should_allow_new_entry,
)
from polaris.core.isolation.reentry import (
    bar_seconds,
    concurrent_same_side_open,
    is_novel_reentry,
    reentry_cooldown_active,
)
from polaris.core.isolation.worker import (
    PipelineTaskSpec,
    supervise_pipeline_tasks,
)
from polaris.core.live_recalc.strategy_swap import (
    SwapCandidate,
    evaluate_strategy_swap,
)
from polaris.core.sizing.constants import production_default_equity_usd
from polaris.core.streams import resolve_stream
from polaris.core.ticks.config import TICK_ENGINE_OWNED_VENUES, tick_engine_owns_okx
from polaris.scripts import _production_rotation as rotation
from polaris.scripts._production_indicators import (
    build_real_market_view,
    session_window_now,
)
from polaris.scripts._production_layers import (
    compute_and_flip_regime,
    get_focus_targets,
    ingest_bars_per_timeframe,
    read_recent_bars,
    run_recalc_for_active_positions,
)
from polaris.scripts._production_pipeline import (
    close_specific_position,
    reserve_and_submit,
    run_pipeline_for_signal,
)
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._production_state import ProdLoopState
from polaris.strategies import (
    BaseStrategy,
    EquityGapGoStrategy,
    EquityRSIBBPullbackStrategy,
    EquityTSMOMStrategy,
    FXBreakoutBasketStrategy,
    RawSignal,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    SpotDonchianStrategy,
    TSMOMStrategy,
    VolumeBurstStrategy,
    XAUIndicesTrendStrategy,
)
from polaris.venues.alpaca.equity_session_gate import (
    equity_entry_held_for_session,
    pdt_rank_penalty,
    stream_session_gate_active,
)
from polaris.venues.capital.session import CapitalSession

logger = logging.getLogger(__name__)

FOCUS_CYCLE_TARGET = 30

# P5 coexistence: ``TICK_ENGINE_OWNED_VENUES`` (imported from core.ticks.config)
# is the single SSOT for the venues the engine owns in Phase 1. When
# ``tick_engine_owns_okx()`` is on, the bar entry path yields these venues to the
# engine (no double-trade); the engine reads the SAME frozenset as PHASE1_VENUES.


def equity_session_entry_hold(
    venue: str, *, now_ts: int, state: ProdLoopState
) -> bool:
    """T13 — hold a NEW equity entry outside US RTH (INTEGRITY, not P&L).

    Applies ONLY to the us_equity_cal stream (Track C / Alpaca equity). For
    OKX (always_on / track A) and Capital (fx_indices_cal / track B) this is a
    no-op returning ``False`` — A/B stay byte-identical (no gate, no counter).

    For the equity stream, returns ``True`` when the US market is closed
    (outside 13:30-20:00 UTC RTH): the venue would reject a closed-market
    order, so the new entry is HELD until RTH. This is an integrity constraint
    (same class as the circuit-breaker integrity halt) — NOT a defensive size
    throttle, NOT a P&L halt. It decides only NEW entries; existing positions
    are never force-closed. A hold bumps ``state.equity_session_holds``
    (telemetry only). Unknown venue → no hold (fail-open, flow_not_block).
    """
    try:
        calendar = resolve_stream(venue).session_calendar
    except KeyError:
        return False
    if not stream_session_gate_active(calendar):
        return False
    held = equity_entry_held_for_session(now_ts)
    # Session on↔off transition (INFO): emit a one-shot record when the equity
    # trading session flips open↔closed (RTH boundary), observability only. The
    # gate decision below is UNCHANGED — this only watches state.* and logs.
    session_open = not held
    prev_open = state.equity_session_open_by_venue.get(venue)
    if prev_open != session_open:
        state.equity_session_open_by_venue[venue] = session_open
        if prev_open is not None:
            logger.info(
                "[session] %s calendar=%s session=%s (RTH boundary)",
                venue, calendar, "open" if session_open else "closed",
            )
    if held:
        state.equity_session_holds += 1
        return True
    return False


def apply_equity_pdt_rank_down(venue: str, *, state: ProdLoopState) -> float:
    """T13 — PDT ranking-down for an equity entry (RANK DOWN, NEVER a block).

    Applies ONLY to the us_equity_cal stream (Track C / Alpaca equity). A/B
    venues return ``0.0`` (no PDT concept — byte-identical). For the equity
    stream, returns :func:`pdt_rank_penalty` of ``state.pdt_daytrade_count``: a
    finite, non-negative ranking number that lowers a day-trade-style entry's
    priority in the existing universe/signal ranking when daytrade_count >= 3.
    It NEVER blocks the entry (flow_not_block), never halts on P&L, and never
    touches notional (not a T4 multiplier). A positive penalty bumps
    ``state.equity_pdt_rank_downs`` (telemetry); the caller proceeds with the
    entry regardless. Overnight holds are unaffected (entry-ranking only).
    """
    try:
        calendar = resolve_stream(venue).session_calendar
    except KeyError:
        return 0.0
    if not stream_session_gate_active(calendar):
        return 0.0
    penalty = pdt_rank_penalty(state.pdt_daytrade_count)
    if penalty > 0.0:
        state.equity_pdt_rank_downs += 1
    return penalty


def _all_strategies() -> list[BaseStrategy]:
    return [
        VolumeBurstStrategy(),
        TSMOMStrategy(),
        RSIBBPullbackStrategy(),
        SpotDonchianStrategy(),
        FXBreakoutBasketStrategy(),
        XAUIndicesTrendStrategy(),
        SessionBreakoutStrategy(),
        EquityTSMOMStrategy(),
        EquityRSIBBPullbackStrategy(),
        EquityGapGoStrategy(),
    ]


def _lookup_regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    """Read the Layer 6 SSOT regime for a (venue, symbol). Defaults to 'chop'."""
    instrument_row = conn.execute(
        "SELECT underlying_group_id FROM universe WHERE venue = ? AND symbol = ?",
        (venue, symbol),
    ).fetchone()
    if instrument_row is None:
        return "chop"
    group_id = str(instrument_row[0])
    row = conn.execute(
        "SELECT regime FROM regime_state WHERE venue = ? AND underlying_group_id = ?",
        (venue, group_id),
    ).fetchone()
    if row is None:
        return "chop"
    return str(row[0])


def order_specs_by_rank(
    specs: list[PipelineTaskSpec],
) -> list[PipelineTaskSpec]:
    """T13/H3 — order pipeline specs by their ranking-down penalty (NOT a block).

    Stable-sorts ascending by ``spec.rank_penalty`` so specs with a higher
    penalty (e.g. a PDT-flagged equity entry, penalty > 0) are RANKED BELOW
    unflagged specs (penalty 0.0). Within an equal penalty the original signal
    order is preserved (stable), so A/B venues — which always carry penalty
    ``0.0`` — keep their byte-identical ordering.

    This is the consumer of :func:`apply_equity_pdt_rank_down`'s return value:
    the penalty actually demotes the entry's priority in the per-tick signal
    ranking. It NEVER drops a spec (flow_not_block) — the demoted entry still
    runs through the pipeline, just later in the supervised batch — and it never
    touches notional (not a T4 multiplier).
    """
    return sorted(specs, key=lambda s: s.rank_penalty)


def _is_finite_signal(sig: RawSignal) -> bool:
    """Reject a signal whose strength / sizing_hint is non-finite."""
    return math.isfinite(sig.strength) and math.isfinite(sig.sizing_hint)


def _evaluate_swaps(conn: sqlite3.Connection, *, now_ts: int) -> None:
    """Day 8 spec E + cumulative #81 X3: Layer 6 SSOT swap predicate."""
    rows = conn.execute(
        "SELECT position_id, active_strategy_id, venue, symbol, side, "
        "       underlying_group_id "
        "FROM positions WHERE status NOT IN ('closed', 'cancelled', 'reconciled') "
        "LIMIT 10"
    ).fetchall()
    for r in rows:
        pos_id, active_strat, venue, symbol, side, group = (
            str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5] or ""),
        )
        candidate = SwapCandidate(
            position_id=pos_id,
            from_strategy_id=active_strat, to_strategy_id=active_strat,
            venue=venue, symbol=symbol, side=side,
            from_correlation_group=group, to_correlation_group=group,
        )
        evaluate_strategy_swap(conn, candidate=candidate, now_ts=now_ts, apply=False)


def _strategies_by_timeframe(
    strategies: list[BaseStrategy],
) -> dict[str, list[BaseStrategy]]:
    """Bucket strategies by their ``metadata.timeframe`` (F10 — Day 9).

    Each bucket runs against a venue/symbol MarketView built from bars at the
    matching ``bar_interval``. This eliminates the 1m hardcode that silently
    starved Capital strategies of usable history.
    """
    out: dict[str, list[BaseStrategy]] = {}
    for s in strategies:
        out.setdefault(s.metadata.timeframe, []).append(s)
    return out


# F10 R2/R3 — ``_is_fetch_due`` was removed (codex R3 P2 nit). The
# orchestrator delegates cadence gating to ``ingest_bars_per_timeframe``,
# which now keys cadence per ``(timeframe, venue)``. A standalone "by
# timeframe only" helper would re-introduce the partial-bucket starvation
# bug if a caller reused it.


async def _run_tick(
    *,
    conn: sqlite3.Connection,
    haiku: Any,
    state: ProdLoopState,
    capital_session: CapitalSession | None,
    tick_idx: int,
    phase: str = "P0",
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    alpaca_adapter: Any = None,
    altdata_cache: Any = None,
) -> None:
    """One 5-second cycle (Day 8 spec B + D + E + F + Day 9 F10).

    F10 contract: bars ingest + market view + strategy eval are partitioned
    by ``strategy.metadata.timeframe``. The hardcoded ``timeframe="1m"``
    that previously starved Capital strategies of their 1H history is gone.
    """
    now_ts = int(time.time())
    now_mono = time.monotonic()
    focus = get_focus_targets(conn, cycle_ts=now_ts, max_n=FOCUS_CYCLE_TARGET)
    if not focus:
        logger.warning(
            "[tick %d] focus empty — falling back to BTC/ETH (universe not yet "
            "populated?)", tick_idx,
        )
        focus = [
            ("okx", "BTC-USDT", "crypto", "crypto:BTC"),
            ("okx", "ETH-USDT", "crypto", "crypto:ETH"),
        ]

    strategies = _all_strategies()
    strategies_by_tf = _strategies_by_timeframe(strategies)

    # F10 — fetch bars per timeframe at the appropriate cadence (delegated
    # to ``ingest_bars_per_timeframe`` so the orchestrator stays light).
    #
    # Codex F10 R1 P1-1 fix: Layer 6 regime SSOT is computed from 1m bars
    # for *every* focus venue, not just venues that have a 1m strategy.
    # Capital has no 1m strategy, but its symbols still need fresh 1m bars
    # so ``compute_and_flip_regime`` doesn't fall through to "chop". Force
    # the 1m bucket to cover every focus venue.
    timeframe_to_venues = {
        tf: {s.metadata.venue for s in strats}
        for tf, strats in strategies_by_tf.items()
    }
    all_focus_venues = {t[0] for t in focus}
    timeframe_to_venues.setdefault("1m", set()).update(all_focus_venues)
    ingest_totals = await ingest_bars_per_timeframe(
        conn, focus,
        timeframe_to_venues=timeframe_to_venues,
        last_fetch_monotonic_by_tf=state.last_fetch_monotonic_by_tf,
        bars_persisted_by_tf=state.bars_persisted_by_tf,
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        limit=240, now_mono=now_mono,
    )
    state.bars_persisted += ingest_totals["bars"]
    state.bars_baseline_samples += ingest_totals["baseline_samples"]

    await run_recalc_for_active_positions(conn, now_ts=now_ts)
    # Day 9 F1+F2 — live recalc loop with G6/G7 GPT per-position invocation.
    # Replaces the entry-time-only G6 wiring + FIFO-oldest close path with a
    # per-tick AI supervisory pass over every active position. Phase=P1
    # forwards the GPT client; phase=P0 keeps decisions deterministic.
    await recalc_active_positions(
        conn, state=state, now_ts=now_ts, gpt_client=haiku, phase=phase,
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, tick_idx=tick_idx,
    )
    # Regime is computed off 1m bars (Layer 6 SSOT — keep stable across tf
    # buckets so swap predicate doesn't oscillate with strategy timeframe).
    regime_by_group: dict[tuple[str, str], str] = {}
    for venue, symbol, _ac, group_id in focus:
        if not group_id:
            continue
        bars_1m = read_recent_bars(conn, venue=venue, symbol=symbol, bar_interval="1m")
        if not bars_1m:
            continue
        regime_by_group[(venue, group_id)] = compute_and_flip_regime(
            conn, venue=venue, underlying_group_id=group_id,
            bars=bars_1m, now_ts=now_ts, altdata_cache=altdata_cache,
        )

    universe_rows: list[dict[str, Any]] = []
    cur = conn.execute(
        "SELECT venue, symbol, vol_24h_usd FROM universe WHERE is_active = 1 LIMIT 50"
    )
    for r in cur.fetchall():
        universe_rows.append(
            {"venue": r[0], "symbol": r[1], "vol_24h_usd": float(r[2] or 0.0)}
        )
    # Day 9 F11 fix: build PipelineTaskSpec list + delegate execution to
    # ``supervise_pipeline_tasks`` (Layer 7 SSOT). Replaces the bare
    # ``asyncio.create_task`` + ``asyncio.gather(..., return_exceptions=True)``
    # site that bypassed ``supervise_strategies``.
    pipeline_specs: list[PipelineTaskSpec] = []
    # P5 coexistence (no double-trade): when the tick-decision engine owns the
    # Phase-1 OKX symbols it is the SOLE opener for them, so the bar entry path
    # yields those venues here (keyed by venue ownership). The open-dedup remains
    # the always-on backstop; this gate just prevents the two producers from
    # racing the SAME symbol. Flag-gated (default OFF) so the bar pipeline is
    # byte-identical until the engine is turned on. NOT a throttle — it routes
    # ownership of a symbol to one producer, never reduces size or blocks edge.
    owns_okx = tick_engine_owns_okx()
    for timeframe, strategies_for_tf in strategies_by_tf.items():
        venues_for_tf = {s.metadata.venue for s in strategies_for_tf}
        for venue, symbol, asset_class, group_id in focus:
            if venue not in venues_for_tf:
                continue
            if owns_okx and venue in TICK_ENGINE_OWNED_VENUES:
                continue
            bars = read_recent_bars(
                conn, venue=venue, symbol=symbol, bar_interval=timeframe,
            )
            if len(bars) < 30:
                continue
            regime = regime_by_group.get((venue, group_id), "chop")
            mv = build_real_market_view(
                venue=venue, symbol=symbol, timeframe=timeframe, bars=bars,
                spread_bps=5.0, session_open_window=session_window_now(now_ts),
            )
            for strategy in strategies_for_tf:
                if strategy.metadata.venue != venue:
                    continue
                strategy_id = strategy.metadata.strategy_id
                # Day 9 F11 fix: respect the Layer 7 circuit breaker — skip
                # HALTed strategies (HARD_HALT / SOFT_HALT / RISK_ONLY) so
                # the per-cycle fan-out cannot bypass the 4-state machine.
                if not should_allow_new_entry(conn, strategy_id, now_ts=now_ts):
                    continue
                # T13 — us_equity_cal RTH integrity hold (Track C only; OKX
                # always_on + Capital fx_indices_cal are no-ops → A/B identical).
                # Outside 13:30-20:00 UTC the equity venue rejects orders, so a
                # NEW entry is HELD until RTH (integrity, not a P&L throttle).
                # Existing positions are untouched (this skips only the entry).
                if equity_session_entry_hold(venue, now_ts=now_ts, state=state):
                    continue
                try:
                    sig = strategy.generate_raw_signal(mv)
                except Exception as exc:  # noqa: BLE001 — must isolate
                    record_fault(
                        conn, strategy_id=strategy_id,
                        fault_type=FAULT_EXCEPTION, now_ts=now_ts,
                        detail={"phase": "generate_raw_signal", "exc": str(exc)[:200]},
                    )
                    state.fault_events += 1
                    continue
                if sig is None:
                    logger.debug(
                        "[L1/signal] no-emit %s:%s strategy=%s tf=%s",
                        venue, symbol, strategy_id, timeframe,
                    )
                    continue
                if not _is_finite_signal(sig):
                    record_fault(
                        conn, strategy_id=strategy_id,
                        fault_type=FAULT_NAN, now_ts=now_ts,
                        detail={"phase": "raw_signal_nan", "signal_id": sig.signal_id},
                    )
                    state.fault_events += 1
                    continue
                state.signals_by_tf[timeframe] = (
                    state.signals_by_tf.get(timeframe, 0) + 1
                )
                # Signal emit (INFO): a finite raw signal cleared generation —
                # the decision-visibility surface. The downstream churn skips +
                # the G1-G8 pipeline still own the lifecycle; this records ONLY
                # that the strategy emitted. Log only — no gate/sizing change.
                logger.info(
                    "[L1/signal] emit %s:%s strategy=%s tf=%s side=%s "
                    "strength=%.3f sizing_hint=%.3f",
                    venue, symbol, strategy_id, timeframe, sig.side,
                    sig.strength, sig.sizing_hint,
                )
                # Component B anti-churn (2026-05-31). The entry key + the last
                # actually-submitted entry on it (created_at_bar, side).
                entry_key = (venue, symbol, strategy_id)
                last_entry = state.last_entry_by_key.get(entry_key)
                last_bar = None if last_entry is None else last_entry[0]
                last_side = None if last_entry is None else last_entry[1]
                # No concurrent duplicate: one live position per
                # (venue, symbol, strategy_id, side). A time cooldown alone
                # misses the 12-simultaneous-BTC stacking (each clone is on a
                # distinct, novel bar), so refuse a clone while one same-side
                # position is open. PRECISION (surgical-strike), not a size
                # dampen / P&L halt — a side flip / different name is unaffected.
                if concurrent_same_side_open(
                    conn, venue=venue, symbol=symbol, strategy_id=strategy_id,
                    side=sig.side,
                ):
                    state.reentry_skips += 1
                    logger.debug(
                        "[L1/signal] skip %s:%s strategy=%s side=%s "
                        "reason=concurrent_same_side_open",
                        venue, symbol, strategy_id, sig.side,
                    )
                    continue
                # Re-entry cooldown — suppress duplicate opens on the same
                # (venue, symbol, strategy_id) within ONE bar of the strategy's
                # timeframe (tsmom 1H → 3600s) so the 5s fan-out can't compound
                # fees (forensic: SOL 20x re-buy / BTC 12-stack). Exempt ONLY on
                # NOVELTY — a NEW strategy-timeframe bar OR a side flip; raw
                # ``strength`` NEVER exempts (it is momentum, not conviction, and
                # spikes in chop → the old exemption stacked every tick). A
                # genuine new opportunity (new bar / flip) still flows.
                if reentry_cooldown_active(
                    conn, venue=venue, symbol=symbol, strategy_id=strategy_id,
                    now_ts=now_ts,
                    cooldown_sec=bar_seconds(strategy.metadata.timeframe),
                    exempt=is_novel_reentry(
                        created_at_bar=sig.created_at_bar, side=sig.side,
                        last_entry_bar=last_bar, last_entry_side=last_side,
                    ),
                ):
                    state.reentry_skips += 1
                    logger.debug(
                        "[L1/signal] skip %s:%s strategy=%s side=%s "
                        "reason=reentry_cooldown",
                        venue, symbol, strategy_id, sig.side,
                    )
                    continue
                # Capital rotation VACATED-SIDE anti-churn (Jin 2026-05-30): a
                # JUST-rotated-out name cannot re-enter immediately — NO strong-
                # signal exemption (backdoor CLOSED), not a P&L halt / size dampen.
                if rotation.rotation_vacated_cooldown_active(
                    state, venue=venue, symbol=symbol, strategy=strategy_id,
                    now_ts=now_ts,
                ):
                    state.reentry_skips += 1
                    logger.debug(
                        "[L1/signal] skip %s:%s strategy=%s side=%s "
                        "reason=rotation_vacated_cooldown",
                        venue, symbol, strategy_id, sig.side,
                    )
                    continue

                # T13/H3 — PDT ranking-down (equity only; A/B no-op). When the
                # rolling daytrade_count >= 3 this surfaces a finite, positive
                # rank penalty — it RANKS DOWN a day-trade-style equity entry in
                # the per-tick signal ranking but NEVER blocks it (flow_not_block)
                # and never halts on P&L. The penalty is CONSUMED below: it is
                # attached to the spec's ``rank_penalty`` and ``order_specs_by_rank``
                # demotes the flagged entry BELOW unflagged ones before the
                # supervised batch runs. The entry still runs (just later). Not a
                # T4 multiplier — notional is untouched.
                pdt_penalty = apply_equity_pdt_rank_down(venue, state=state)

                def _factory(
                    *, _strategy: Any = strategy, _sig: Any = sig,
                    _venue: str = venue, _symbol: str = symbol,
                    _asset_class: str = asset_class, _group_id: str = group_id,
                    _regime: str = regime, _atr_pct: float = mv.atr_pct,
                    _last_price: float = mv.last_price,
                ) -> Any:
                    return run_pipeline_for_signal(
                        conn=conn, haiku=haiku, state=state, strategy=_strategy,
                        sig=_sig, venue=_venue, symbol=_symbol,
                        asset_class=_asset_class,
                        underlying_group_id=_group_id, regime=_regime,
                        bars_atr_pct=_atr_pct, last_price=_last_price,
                        universe_rows=universe_rows, now_ts=now_ts,
                        reserve_and_submit=reserve_and_submit,
                        phase=phase, real_roundtrip=real_roundtrip,
                        capital_session=capital_session, okx_adapter=okx_adapter,
                        alpaca_adapter=alpaca_adapter,
                    )

                pipeline_specs.append(
                    PipelineTaskSpec(
                        strategy_id=strategy_id, coro_factory=_factory,
                        rank_penalty=pdt_penalty,
                    )
                )
    if pipeline_specs:
        # T13/H3 — consume the PDT rank-down: demote PDT-flagged equity specs
        # BELOW unflagged ones (stable; A/B carry penalty 0.0 → byte-identical
        # order). Ranking-down only — no spec is dropped (flow_not_block).
        pipeline_specs = order_specs_by_rank(pipeline_specs)
        results = await supervise_pipeline_tasks(
            pipeline_specs, conn=conn, now_ts=now_ts,
            fault_phase="pipeline_supervisor",
        )
        for r in results:
            if r["exception"] is not None:
                state.fault_events += 1
        state.supervised_tasks_total += len(pipeline_specs)
        state.supervised_tasks_failed += sum(
            1 for r in results if r["exception"] is not None
        )

    # Capital rotation HOOK SEAM (Jin 2026-05-30): rotate one per-venue from the
    # capital-blocked candidates the fan-out populated (close weakest loser,
    # winner re-proposed next tick — no same-tick reopen, MAX_PER_TICK=1). Capital
    # EFFICIENCY (net deploy UP), NOT a throttle; before _evaluate_swaps so a
    # closed victim is not swap-eval'd. See _production_rotation for the contract.
    await rotation.evaluate_capital_rotation(
        conn, state=state, now_ts=now_ts,
        close_specific=close_specific_position, lookup_regime=_lookup_regime,
        equity=production_default_equity_usd(),
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, gpt_client=haiku, phase=phase,
    )

    _evaluate_swaps(conn, now_ts=now_ts)

    # FIX-4 (2026-05-30): the unconditional tail-of-tick FIFO-oldest drain
    # (``if state.open_trades:`` then a bare close of trade[0]) was REMOVED.
    # It force-closed ``state.open_trades[0]`` at the END of EVERY
    # tick with NO exit predicate (no stop, no PnL, no holding gate), so a
    # position opened during the pipeline fan-out was closed within the same/
    # next tick — every position died at holding_bars=0 and the multi-bar
    # adaptive exit (G6/G7) never managed a live position. Closes now happen
    # EXCLUSIVELY through the G6/G7 path (``recalc_active_positions`` ->
    # ``close_specific_position`` on a genuine EXIT_NOW), so a position lives
    # across ticks until a real exit reason fires. This STRENGTHENS precise
    # exits (Jin's loss-defense) — it is NOT a throttle and NOT a size dampen.

    tf_summary = ",".join(
        f"{tf}={state.bars_persisted_by_tf.get(tf, 0)}"
        for tf in sorted(strategies_by_tf)
    )
    logger.info(
        "[tick %d] focus=%d bars_by_tf=%s open=%d closed=%d sized=%d "
        "kills=%d reentry_skips=%d",
        tick_idx, len(focus), tf_summary, len(state.open_trades),
        len(state.closed_trades), state.sized_count, state.pipeline_kills,
        state.reentry_skips,
    )
