"""Day 8 production paper loop — per-tick orchestration (one 5s cycle).

Split out of ``production_paper_loop`` to keep both modules ≤500 LOC.
``production_paper_loop`` re-exports ``_run_tick`` + the strategy/regime/swap
helpers it owns so existing import paths (incl. tests) keep working. Shared
loop state lives in ``_production_state``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sqlite3
import time
from typing import Any

from polaris.core.cell_matrix.score import regime_rank_penalty
from polaris.core.data.quote_writer import live_or_bar_price
from polaris.core.data.signal_persist import persist_emitted_signal
from polaris.core.data.technical_store import (
    extract_technicals_from_mv,
    upsert_technicals,
)
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
from polaris.core.pipeline.agents._gpt_client import default_gpt_factory
from polaris.core.sizing.constants import production_default_equity_usd
from polaris.core.streams import resolve_stream
from polaris.core.ticks.config import TICK_ENGINE_OWNED_VENUES, tick_engine_owns_okx
from polaris.core.universe.schema import ALLOWED_TRADE_QUOTE_CCY_OKX
from polaris.scripts import _production_rotation as rotation
from polaris.scripts._production_counterfactual import (
    CF_SWEEP_THROTTLE_SEC,
    sweep_forward_marks,
)
from polaris.scripts._production_indicators import (
    build_real_market_view,
    session_window_now,
)
from polaris.scripts._production_layers import (
    bar_fetch_limit_for,
    compute_and_flip_regime,
    get_focus_targets,
    ingest_bars_per_timeframe,
    read_recent_bars_ondemand,
    run_recalc_for_active_positions,
    staleness_threshold_for,
)
from polaris.scripts._production_pipeline import (
    close_specific_position,
    reserve_and_submit,
    run_pipeline_for_signal,
)
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._production_state import ProdLoopState
from polaris.strategies import (
    BarBreakoutRunStrategy,
    BaseStrategy,
    DonchianTurtleBreakoutStrategy,
    EMACrossoverStrategy,
    Equity52WkHighBreakoutStrategy,
    EquityVolExpansionPocketPivotStrategy,
    FXBreakoutBasketStrategy,
    GoldBreakout1HStrategy,
    GoldRiskoffTrendAmplifyStrategy,
    GoldTrendChandelier1DStrategy,
    Index52WHighMomentumStrategy,
    IndexDualMomentumRotationStrategy,
    MACDEMATrendPullbackStrategy,
    OKXDonchian55BreakoutStrategy,
    RawSignal,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    TSMom12_1MultiAssetStrategy,
    WeekendFundingCapitulationMakerStrategy,
    WeekendThinBookFlushMakerStrategy,
    XAUIndicesTrendStrategy,
)
from polaris.strategies.gold_trend_chandelier_1d import (
    SUPPORTED_SYMBOLS as _GOLD_TREND_SYMBOLS,
)
from polaris.strategies.index_52w_high_momentum import (
    SUPPORTED_SYMBOLS as _IDX_52W_SYMBOLS,
)
from polaris.strategies.index_dual_momentum_rotation import (
    SUPPORTED_SYMBOLS as _IDX_DUALMOM_SYMBOLS,
)
from polaris.strategies.session_breakout import (
    SUPPORTED_SYMBOLS as _SESSION_BREAKOUT_SYMBOLS,
)
from polaris.strategies.xau_indices_trend import (
    SUPPORTED_SYMBOLS as _XAU_INDICES_SYMBOLS,
)
from polaris.venues.alpaca.equity_session_gate import (
    equity_entry_held_for_session,
    pdt_rank_penalty,
    stream_session_gate_active,
)
from polaris.venues.capital.session import CapitalSession

logger = logging.getLogger(__name__)

# Per-cycle bar-ingest WATCH cap (Jin 2026-06-24 — WATCH/TRADE decouple). This is
# the REAL per-cycle watch bottleneck (REST bar fan-out + DB writes — the binding
# resource cost when WATCH widens). Decoupled from the focus window so the bot can
# bar-ingest dozens+ watched names; env-tunable (``POLARIS_FOCUS_CYCLE_TARGET``)
# as the resource guard. Default raised 30 → 120 to match the widened watch set
# (OKX 2 → 100+); lower it via env if REST QPS / DB growth telemetry flags load.
FOCUS_CYCLE_TARGET = 120
_FOCUS_CYCLE_TARGET_ENV = "POLARIS_FOCUS_CYCLE_TARGET"


def _focus_cycle_target() -> int:
    """Per-cycle bar-ingest watch cap (env ``POLARIS_FOCUS_CYCLE_TARGET``; >= 1).

    Resource guard: caps how many watched names get a per-cycle bar pull + quote
    write (the binding REST + DB cost). Invalid/unset → ``FOCUS_CYCLE_TARGET``.
    """
    raw = os.environ.get(_FOCUS_CYCLE_TARGET_ENV)
    if raw is None or raw == "":
        return FOCUS_CYCLE_TARGET
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return FOCUS_CYCLE_TARGET

# P5 coexistence: ``TICK_ENGINE_OWNED_VENUES`` (imported from core.ticks.config)
# is the single SSOT for the venues the engine owns in Phase 1. When
# ``tick_engine_owns_okx()`` is on, the bar entry path yields these venues to the
# engine (no double-trade); the engine reads the SAME frozenset as PHASE1_VENUES.

# Capital non-forex (index/commodity) symbols that an ENABLED Capital bar
# strategy actually supports — the UNION of the FIVE enabled index/commodity bar
# strategies' SUPPORTED_SYMBOLS (xau_indices_trend + session_breakout +
# index_dual_momentum_rotation + index_52w_high_momentum + gold_trend_chandelier_1d).
# Built from the strategy modules so it can never drift from what they accept. The
# routing carve-out below keeps these on the bar pipeline (donchian/momentum/
# session/rotation) so they get a trend/breakout edge COMPLEMENTARY to the tick
# engine's flow micro-structure on the SAME symbol. Symbols are the raw
# live-universe spelling (e.g. ``GOLD`` / ``US100`` / ``J225``), upper-cased like
# the strategy symbol gate. wave2 fix: the two index strategies (dual_momentum +
# 52w_high) were INERT — their wave2 symbols (J225/HK50/AU200/AU200AU) were NOT in
# the prior union, so keep_on_bar_path returned False and the bar fan-out vacated
# them → generate_raw_signal was NEVER reached (live: 0 signals). Phase2 fan-out
# (wajecs9ct): gold_trend_chandelier_1d widened {GOLD,XAUUSD} → metals+energy fan
# (SILVER/PALLADIUM/COPPER/OIL_BRENT/GASOLINE + Tier-2), so its module is now
# unioned to keep those NEW commodity epics reachable (else same vacate-skip INERT
# recurrence). Unioning their SUPPORTED_SYMBOLS restores reachability (flow_not_
# block — purely additive).
CAPITAL_BAR_STRATEGY_SYMBOLS: frozenset[str] = (
    _XAU_INDICES_SYMBOLS
    | _SESSION_BREAKOUT_SYMBOLS
    | _IDX_DUALMOM_SYMBOLS
    | _IDX_52W_SYMBOLS
    | _GOLD_TREND_SYMBOLS
)


def keep_on_bar_path(*, asset_class: str, symbol: str) -> bool:
    """True iff a tick-engine-owned (venue, symbol) should ALSO stay on the bar path.

    PURELY ADDITIVE reachability widen (flow_not_block): the prior asset-class
    routing skip VACATED every non-forex Capital index/commodity symbol from the
    bar fan-out, handing it ONLY to the tick engine — so xau_indices_trend (whole
    universe vacated) had a structural 0%-emit ceiling and session_breakout lost
    its index legs. This keeps a symbol on the bar pipeline when ANY of:

      * it is forex (the carve-out already done — its micro-structure thresholds
        never trip so the tick engine gave it zero entries), OR
      * it is OKX SPOT (asset_class ``crypto`` / ``spot``) — STEP1 multi-horizon
        activation. The asset-class skip vacated the WHOLE OKX spot universe to
        the tick engine, so the OKX 1H swing strategies (tsmom / supertrend /
        spot_donchian / ema_crossover — whole-universe, NO per-symbol whitelist)
        had a structural 0-entry ceiling (live: 0 swing/position closes, every
        close <60m). Keeping OKX spot on the bar pipeline gives those 1H trend
        strategies the SWING horizon while the tick engine still owns the SCALP
        flow edge on the SAME symbol, OR
      * an ENABLED Capital index/commodity bar strategy supports it
        (``CAPITAL_BAR_STRATEGY_SYMBOLS``).

    The tick engine STILL trades these symbols (its flow edge is untouched); the
    bar strategies add a COMPLEMENTARY trend/breakout edge. The cross-producer
    double-open is accounted as INDEPENDENT logical positions (no blind netting):
    ``concurrent_same_side_open`` is strategy-scoped (``WHERE strategy_id = ?``)
    so a tick scalp and a bar swing on the same (venue, symbol, side) coexist as
    distinct positions with per-strategy PnL attribution; OKX SPOT has no forced
    reversal. The risk backstop is the per-symbol RISK cap (sizing
    ``per_symbol_remaining_pct``) which SUMS open_risk_pct over ``(venue,
    symbol)`` across BOTH producers (both persist via the shared
    ``reserve_and_submit`` → ``position_risk_state`` path). NOT a throttle — the
    skip is NARROWED, never widened; no block / size-cut is introduced.
    """
    cls = (asset_class or "").strip().lower()
    if cls in ("forex", "crypto", "spot"):
        return True
    sym = (symbol or "").upper().replace("/", "").replace(".", "")
    return sym in CAPITAL_BAR_STRATEGY_SYMBOLS


def okx_quote_settleable(venue: str, quote_ccy: str) -> bool:
    """True unless ``(venue, quote_ccy)`` is an OKX pair whose quote can't settle.

    The OKX SPOT demo wallet holds only USD-stablecoins, and the order/accounting
    path sizes ``sz = notional_usd`` with ``tgtCcy = quote_ccy`` (i.e. it ASSUMES
    the quote ≈ USD). For an OKX pair whose quote ∉ {USDT, USDC} — a crypto quote
    (e.g. ``SOL-ETH``, quote=ETH) or a nominal-``USD`` pair (#44) — that order 100%
    rejects at the venue (51201 ``sz`` mis-interpreted as N quote-ccy ≈ $millions /
    51008 no quote-ccy balance / 51000 ``tradeQuoteCcy`` error). Non-OKX venues
    (Capital/Alpaca) price venue-side in USD → always settleable. This mirrors the
    structural quote-ccy half of ``entrance._okx_quote_trade_eligible`` (shared SSOT
    ``ALLOWED_TRADE_QUOTE_CCY_OKX``) WITHOUT the opportunity-score floor — so a
    settleable USDT pair is NEVER deferred on a thin score (aggressive bias).
    """
    if venue != "okx":
        return True
    return quote_ccy in ALLOWED_TRADE_QUOTE_CCY_OKX


def okx_unsettleable_set(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Active OKX ``(venue, symbol)`` whose quote ccy can't settle on the demo wallet.

    One indexed read per tick. The bar dispatch defers ENTRY (only) for a name in
    this set — it stays WATCHED/SIGNALED/streamed (flow_not_block: flow is
    redirected to settleable USDT/USDC pairs, not blocked). Held positions are
    unaffected: exits run via the recalc loop, not this entry seam.
    """
    rows = conn.execute(
        "SELECT venue, symbol, quote_ccy FROM universe "
        "WHERE venue = 'okx' AND is_active = 1"
    ).fetchall()
    return {
        (str(r[0]), str(r[1]))
        for r in rows
        if not okx_quote_settleable(str(r[0]), str(r[2] or ""))
    }


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


def equity_entry_inert_for_feed(strategy: BaseStrategy) -> bool:
    """Equity-feed entry gate — RELAXED to a no-op (2026-06-27, equity-gate-relax).

    History: the stop-bleeders build (#56) made the two daily-equity strategies
    (``equity_vol_expansion_pocket_pivot`` / ``equity_52wk_high_breakout``) INERT
    whenever the Alpaca ACTIVE feed was not ``sip`` — premised on "wrong feed =
    bad data = cannot trade" (a data-correctness gate like universe-eligibility).

    RELAX (Jin 2026-06-27): that premise conflated the REALTIME tick feed with the
    SIGNAL data source. Both strategies are DAILY (1D bar-close) — their entry
    signal is computed from yfinance daily bars (#21 PRIMARY: free, full US market,
    feed-agnostic), NOT the Alpaca SIP/IEX realtime quote stream. The SIP/IEX
    distinction only governs intraday realtime quotes, which a 1D-close strategy
    never consumes. So the data-quality concern the gate guarded is ALREADY solved
    by the yfinance daily signal: the strategies can trade correctly on ``iex`` (or
    any feed). This returns ``False`` for EVERY strategy — flow ACTIVATED, not
    blocked (flow_not_block). The -$104.58 prior bleed is now bounded by the small
    shadow validation cap (``equity_shadow_validation_cap``) so the live demo run
    (Alpaca commission-free → clean true-edge P&L) verifies the edge instead.

    Kept as a no-op predicate (not deleted) so the call-site stays a documented
    seam: a future genuine feed-correctness need has a named hook. Entry-emit only;
    A/B venues unaffected. degrade-never-crash.
    """
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
        RSIBBPullbackStrategy(),
        BarBreakoutRunStrategy(),
        OKXDonchian55BreakoutStrategy(),
        TSMom12_1MultiAssetStrategy(),
        MACDEMATrendPullbackStrategy(),
        DonchianTurtleBreakoutStrategy(),
        EMACrossoverStrategy(),
        FXBreakoutBasketStrategy(),
        XAUIndicesTrendStrategy(),
        SessionBreakoutStrategy(),
        # weekend OKX makers — the two VALIDATED weekend edges (#77 thin-book
        # flush +73 bps real-fee, #80 funding capitulation +0.83R shadow-first).
        # Both are OKX SPOT 1H, weekend-gated (Sat/Sun UTC), kept on the bar path by
        # ``keep_on_bar_path`` (crypto/spot). They were in STRATEGY_REGISTRY but
        # MISSING here → ``generate_raw_signal`` was never invoked (silent INERT,
        # feedback_verify_firing_after_build "등록 ≠ 발화"). Adding them restores
        # dispatch; the shadow / maker-economics layer (maker_no_fill_cancel +
        # REVERSION post-only entry mode) is keyed off the registry + metadata
        # DOWNSTREAM of the emit, so it activates automatically — no extra wiring.
        # (The registered-but-unvalidated supertrend / connors_rsi2 / cci_reversion
        # stay OUT of dispatch — no OOS/fee evidence, unvalidated live = churn.)
        WeekendThinBookFlushMakerStrategy(),
        WeekendFundingCapitulationMakerStrategy(),
        # strategy-wave2 — Capital CFD GOLD/index (5, deploy live).
        GoldTrendChandelier1DStrategy(),
        GoldRiskoffTrendAmplifyStrategy(),
        GoldBreakout1HStrategy(),
        Index52WHighMomentumStrategy(),
        IndexDualMomentumRotationStrategy(),
        # strategy-wave2 — Alpaca equity (2, inert until SIP #42 routes bars;
        # degrade-never-crash: un-routed symbol → no bars → no emit).
        Equity52WkHighBreakoutStrategy(),
        EquityVolExpansionPocketPivotStrategy(),
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
    # STAGE 1 tier-cadence: ``tick_idx`` is the cycle index, so the per-cycle bar
    # ingest polls S/A every cycle, B every K, T every M (flow_not_block: every
    # active row is still watched, cadence only governs HOW OFTEN it is bar-pulled).
    focus = get_focus_targets(
        conn, cycle_ts=now_ts, max_n=_focus_cycle_target(), cycle_index=tick_idx
    )
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
    # Venues that have a REAL 1m strategy (consume intra-minute updates, e.g.
    # OKX volume_burst) — captured BEFORE the regime-SSOT widen so we can tell
    # them apart from venues forced into the 1m bucket only for regime.
    strategy_1m_venues = {s.metadata.venue for s in strategies_by_tf.get("1m", [])}
    timeframe_to_venues.setdefault("1m", set()).update(all_focus_venues)
    # Alpaca 429 fix (2026-06-24): the regime-only 1m venues (forced into the 1m
    # bucket but with NO 1m strategy — e.g. Alpaca, whose strategies are all 1D)
    # re-fetched the in-progress 1m bar for every focus symbol every 5s tick →
    # ~99 Alpaca /bars requests/tick → free-tier 429 → 9-50h-stale bars → the
    # US-equity track went dark. Those venues skip the 1m re-fetch when the
    # current minute's bar is already held: the regime read is idempotent over a
    # frozen in-progress bar within the minute, and the skip auto-clears the
    # instant the minute rolls (flow_not_block — missing/rolled always fetches).
    # OKX 1m (volume_burst) is NOT in the skip set → intra-minute freshness kept.
    regime_only_1m_venues = all_focus_venues - strategy_1m_venues
    skip_if_current = {(v, "1m") for v in regime_only_1m_venues}
    ingest_totals = await ingest_bars_per_timeframe(
        conn, focus,
        timeframe_to_venues=timeframe_to_venues,
        last_fetch_monotonic_by_tf=state.last_fetch_monotonic_by_tf,
        bars_persisted_by_tf=state.bars_persisted_by_tf,
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        limit=240, now_mono=now_mono,
        skip_if_current=skip_if_current,
        gpt_client_factory=default_gpt_factory,
    )
    state.bars_persisted += ingest_totals["bars"]
    state.bars_baseline_samples += ingest_totals["baseline_samples"]

    # Gate→outcome counterfactual forward-mark sweep (instrumentation only):
    # resolves pending G3/G4 KILL/PASS cohort rows from the bars just ingested
    # (zero new fetches). 60s throttle + LIMIT batch inside the sweep; never
    # reads/affects any entry/exit/sizing decision.
    if now_mono - state.last_cf_sweep_monotonic >= CF_SWEEP_THROTTLE_SEC:
        state.last_cf_sweep_monotonic = now_mono
        await sweep_forward_marks(conn, now_ts=now_ts)

    await run_recalc_for_active_positions(conn, now_ts=now_ts)
    # Day 9 F1+F2 — live recalc loop with G6/G7 GPT per-position invocation.
    # Replaces the entry-time-only G6 wiring + FIFO-oldest close path with a
    # per-tick AI supervisory pass over every active position. Phase=P1
    # forwards the GPT client; phase=P0 keeps decisions deterministic.
    await recalc_active_positions(
        conn, state=state, now_ts=now_ts, gpt_client=haiku, phase=phase,
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        tick_idx=tick_idx,
    )
    # Regime is computed off 1m bars (Layer 6 SSOT — keep stable across tf
    # buckets so swap predicate doesn't oscillate with strategy timeframe).
    regime_by_group: dict[tuple[str, str], str] = {}
    for venue, symbol, asset_class, group_id in focus:
        if not group_id:
            continue
        # Cooperative yield (event-loop fairness). The per-symbol regime pass
        # (read_recent_bars + compute_and_flip_regime) over the full focus set is
        # otherwise a multi-second SYNC block that starves the ~0.5s tick engine
        # AND the WS recv loop (live: 30-45s loop stalls → WS idle-drop+reconnect
        # → tick engine telemetry every ~50s, 0 real-time opens). Yielding between
        # symbols lets the real-time path interleave — same pattern as the learner
        # tune / WS-recv burst yields. No behavior change: no DB transaction spans
        # this point (each conn.execute here auto-commits).
        await asyncio.sleep(0)
        # Data-proactive (Jin 2026-06-27): a STALE 1m store refetches live instead
        # of skipping the symbol (cooldown-gated; storm/OKX-429 safe). A FRESH read
        # is byte-identical to the prior ``read_recent_bars`` (no fetch).
        bars_1m = await read_recent_bars_ondemand(
            conn, venue=venue, symbol=symbol, asset_class=asset_class,
            bar_interval="1m",
            freshness_threshold_sec=staleness_threshold_for("1m"),
            capital_session=capital_session, alpaca_adapter=alpaca_adapter,
            gpt_client_factory=default_gpt_factory, now_mono=now_mono,
        )
        if not bars_1m:
            continue
        regime_by_group[(venue, group_id)] = compute_and_flip_regime(
            conn, venue=venue, underlying_group_id=group_id,
            bars=bars_1m, now_ts=now_ts, altdata_cache=altdata_cache,
            asset_class=asset_class, hint_stats=state.regime_hint_stats,
        )

    universe_rows: list[dict[str, Any]] = []
    cur = conn.execute(
        "SELECT venue, symbol, vol_24h_usd FROM universe WHERE is_active = 1 LIMIT 50"
    )
    for r in cur.fetchall():
        universe_rows.append(
            {"venue": r[0], "symbol": r[1], "vol_24h_usd": float(r[2] or 0.0)}
        )
    # OKX settle-ability TRADE gate (bar producer) — the twin of the tick engine's
    # ``eligible_set`` (``_production_tick_engine`` :: ``get_focus_targets(
    # eligible_only=True)``). ``entrance._okx_quote_trade_eligible`` correctly forces
    # ``trade_eligible=0`` for an OKX pair whose quote ∉ {USDT, USDC}, but that flag
    # was enforced ONLY on the tick-engine entry path — the bar pipeline dispatched
    # ``bar_breakout_run`` over the FULL watch set and never read it, so crypto-quote
    # names (e.g. SOL-ETH) reached the OKX order path and 100% rejected at the venue
    # (51201 sz mis-unit / 51008 no quote-ccy balance). Build the unsettleable set
    # once per tick; the dispatch loop DEFERS only the ENTRY for a name in it (the
    # signal still emits/persists/streams — flow_not_block: flow is redirected to
    # settleable USDT/USDC pairs, not blocked). ONLY the structural quote-ccy guard
    # (NOT the score floor), so a thin-score USDT breakout still reaches orders.
    okx_unsettleable = okx_unsettleable_set(conn)
    # Day 9 F11 fix: build PipelineTaskSpec list + delegate execution to
    # ``supervise_pipeline_tasks`` (Layer 7 SSOT). Replaces the bare
    # ``asyncio.create_task`` + ``asyncio.gather(..., return_exceptions=True)``
    # site that bypassed ``supervise_strategies``.
    pipeline_specs: list[PipelineTaskSpec] = []
    # P5 coexistence — COMPLEMENTARY-EDGE ROUTING (``keep_on_bar_path``).
    # When the tick engine owns a venue (D3: OKX + Capital) the bar path yields a
    # symbol ONLY when the tick engine is its sole effective producer. Three
    # carve-outs keep a symbol ALSO on the bar pipeline (ADDITIVE — the tick
    # engine still trades it; the bar strategies add a complementary edge):
    #   * FOREX (Capital FX) — its micro-structure thresholds never trip so the
    #     tick engine gave FX ZERO entries; its bar strategies (fx_breakout_basket
    #     / session_breakout) own that edge (carve-out already done pre-Wave-1).
    #   * OKX SPOT (asset_class crypto/spot) — STEP1 multi-horizon. The skip
    #     vacated the WHOLE OKX spot universe to the tick engine (scalp-only), so
    #     the OKX 1H swing strategies (tsmom / supertrend / spot_donchian /
    #     ema_crossover — whole-universe) had a 0-entry ceiling (live: 0 swing/
    #     position closes). Keeping OKX spot on the bar pipeline gives them the
    #     SWING horizon; the tick engine keeps the SCALP flow edge on the SAME
    #     symbol (the two coexist as independent strategy-scoped positions).
    #   * Capital INDEX/COMMODITY symbols an ENABLED bar strategy supports
    #     (``CAPITAL_BAR_STRATEGY_SYMBOLS``) — the prior skip VACATED all of them
    #     to the tick engine, so xau_indices_trend (whole universe vacated → 0%
    #     emit ceiling) sat dead and session_breakout lost US500/US100. The tick
    #     engine's flow edge on these is intact; the bar strategies add the
    #     donchian/momentum/session trend-breakout edge on the SAME symbol.
    # Cross-producer double-open is accounted as INDEPENDENT logical positions (no
    # blind netting): concurrent_same_side_open is strategy-scoped, so a tick scalp
    # + a bar swing on the same (venue, symbol, side) are distinct positions with
    # per-strategy PnL attribution. The risk backstop is the per-symbol RISK cap
    # (sizing ``per_symbol_remaining_pct``) which sums open_risk_pct over (venue,
    # symbol) across BOTH producers (both persist via reserve_and_submit →
    # position_risk_state). flow_not_block: the skip is NARROWED, never widened —
    # no block / size-cut introduced.
    # /DEBATE-FLAGGED (NOT applied this wave): scoping TICK_ENGINE_OWNED_VENUES to
    # crypto/forex-vs-both-with-dedup (routing-ownership reframe) is a separate
    # /debate decision — this wave keeps ownership as-is and only widens the bar
    # carve-out.
    owns_okx = tick_engine_owns_okx()
    for timeframe, strategies_for_tf in strategies_by_tf.items():
        venues_for_tf = {s.metadata.venue for s in strategies_for_tf}
        for venue, symbol, asset_class, group_id in focus:
            if venue not in venues_for_tf:
                continue
            if (
                owns_okx
                and venue in TICK_ENGINE_OWNED_VENUES
                and not keep_on_bar_path(asset_class=asset_class, symbol=symbol)
            ):
                continue
            # Cooperative yield (see the regime loop above). build_real_market_view
            # (indicators) + read_recent_bars per (symbol, timeframe, strategy) is
            # the heaviest SYNC stretch in the tick — yield between symbols so the
            # tick engine + WS are not starved for the duration of the fan-out.
            await asyncio.sleep(0)
            # Data-proactive (Jin 2026-06-27): a STALE strategy-timeframe store
            # refetches live instead of skipping (cooldown-gated; storm/OKX-429
            # safe). A FRESH read is byte-identical to the prior path (no fetch).
            bars = await read_recent_bars_ondemand(
                conn, venue=venue, symbol=symbol, asset_class=asset_class,
                bar_interval=timeframe,
                # Per-tf read depth: 1D reads 260 bars so the deepest 1D warmup
                # (equity_52wk_high_breakout, 253) is satisfied — a 240 read
                # capped the canvas below warmup → that strategy was INERT. The
                # ingest persists the same depth; intraday tf keep 240.
                limit=bar_fetch_limit_for(timeframe),
                freshness_threshold_sec=staleness_threshold_for(timeframe),
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                gpt_client_factory=default_gpt_factory, now_mono=now_mono,
            )
            if len(bars) < 30:
                continue
            regime = regime_by_group.get((venue, group_id), "chop")
            mv = build_real_market_view(
                venue=venue, symbol=symbol, timeframe=timeframe, bars=bars,
                spread_bps=5.0, session_open_window=session_window_now(now_ts),
                asset_class=asset_class,
                # Alt-data wire (SIGNAL only): read the already-populated cache
                # snapshot for this group → MarketView.altdata. No network; stale/
                # absent/keyless → neutral no-op. NOT a block / throttle / size-cut.
                altdata_cache=altdata_cache, underlying_group_id=group_id,
                now_ts=now_ts,
            )
            # ④ #12 technical store — WRITE-AFTER-COMPUTE. Persist the full
            # indicator set just computed in ``mv`` (rsi/adx/bb/donchian/ema/
            # momentum) so the AI judge / probes can read it as evidence (the judge
            # previously saw only the 3-metric atr/size/volume baseline). EXTRACTED
            # from this same mv — never re-computed (no double source of truth) and
            # never a network call (the OKX candles bucket is untouched). Single-row
            # LWW → bounded table. EVIDENCE-ONLY (flow_not_block: no entry/size/exit
            # gated).
            # STALL fix #88 — the WRITE is OFF-LOADED off the loop thread: extract
            # (pure CPU) here, then ``record`` the snapshot into the dedicated
            # ``tech_store_writer`` (in-mem coalesce; its 1Hz flush writes on a
            # dedicated conn). This removes the synchronous ``upsert_technicals``
            # the loop thread used to run on the SHARED tick conn — the WAL-lock
            # contention with the 1Hz quote flush that re-introduced the #74 STALL.
            # When no writer is wired (smoke/replay/direct _run_tick callers) it
            # degrades to the inline upsert (behavior-identical to pre-#88). The
            # store keeps being written either way (judge evidence — flow_not_block).
            # best-effort: a write fault NEVER blocks emit/judge.
            try:
                tech_values = extract_technicals_from_mv(mv)
                if tech_values:
                    tech_writer = getattr(state, "tech_store_writer", None)
                    if tech_writer is not None:
                        tech_writer.record(
                            instrument_id=f"{venue}:{symbol}",
                            bar_interval=timeframe,
                            values=tech_values,
                            computed_ts=now_ts,
                            source_bar_ts=int(bars[-1].ts),
                        )
                    else:
                        upsert_technicals(
                            conn,
                            instrument_id=f"{venue}:{symbol}",
                            bar_interval=timeframe,
                            values=tech_values,
                            computed_ts=now_ts,
                            source_bar_ts=int(bars[-1].ts),
                        )
            except Exception as exc:  # noqa: BLE001 — store is advisory, never blocks
                logger.debug(
                    "[technicals] write skipped %s:%s/%s: %r",
                    venue, symbol, timeframe, exc,
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
                # Equity-feed gate (equity-gate-relax): now a documented no-op
                # seam. The daily-equity strategies derive their entry signal from
                # yfinance daily bars (#21 PRIMARY, feed-agnostic), so the SIP/IEX
                # realtime distinction no longer gates a 1D-close strategy — flow is
                # ACTIVATED on any feed (flow_not_block). The prior -$104.58 bleed is
                # instead bounded by the shadow validation cap in the T4 engine.
                if equity_entry_inert_for_feed(strategy):
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
                    # A strategy that produced no signal this tick is the dominant
                    # normal state (most symbols stay quiet most ticks). The former
                    # per-symbol DEBUG line was 88% of the live log; its count is
                    # already aggregated by the per-tick ``[tick N] focus=…
                    # bars_by_tf=…`` INFO summary below + the per-emit INFO lines
                    # (emit-vs-focus IS the quiet rate), so the individual line
                    # carried zero extra signal. Dropped — log only, flow untouched.
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
                # Persist the EMITTED signal so G2 output is queryable (the table
                # was log-only → empty). Pure observability, FAIL-OPEN: a write
                # error never blocks the tick. A no-emit (handled above) writes
                # nothing. Feeds dashboard G2 visibility + learner expectancy.
                persist_emitted_signal(
                    conn, signal_id=sig.signal_id, venue=venue, symbol=symbol,
                    strategy_id=strategy_id, side=sig.side, strength=sig.strength,
                    timeframe=timeframe, ts=now_ts,
                    correlation_group=sig.correlation_group, thesis=sig.thesis_tag,
                )
                # OKX settle-ability — DEFER the ENTRY (only) for an OKX pair whose
                # quote ccy can't settle on the demo SPOT wallet (quote ∉ {USDT,
                # USDC}). The signal is ALREADY emitted+persisted above (the name
                # stays WATCHED/SIGNALED/streamed); only the order is deferred. The
                # order would 100% reject at the venue (51201/51008/51000), so this
                # REDIRECTS flow to settleable USDT/USDC pairs (live fills were 0) —
                # NOT a defensive throttle, NOT a size dampen, and held positions are
                # untouched (exits run via the recalc loop). Mirrors the tick
                # engine's eligible_set gate on the bar producer (flow_not_block).
                if (venue, symbol) in okx_unsettleable:
                    state.okx_unsettleable_entry_defers += 1
                    logger.debug(
                        "[L1/signal] defer-entry %s:%s strategy=%s side=%s "
                        "reason=okx_quote_unsettleable (watched, order redirected)",
                        venue, symbol, strategy_id, sig.side,
                    )
                    continue
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

                # G2 ticker-tailored assignment — regime-first POOL priority.
                # The audited G2 matched a strategy to a ticker by venue/asset-
                # class ONLY (static). Couple THIS ticker's LIVE ``regime`` into
                # the emit-time ranking (the /debate "regime-first hybrid"): a
                # strategy whose edge fits the ticker's current regime is ranked
                # AHEAD of a mis-regime one via the SAME ``rank_penalty`` channel
                # the PDT gate uses. ``order_specs_by_rank`` sorts the per-tick
                # batch by this penalty, so a better-fit spec is *created first*
                # in the supervised TaskGroup — a structural HEAD-START into the
                # concurrent gate fan-out, NOT a guaranteed reservation order
                # (the gate pipeline awaits interleave, so the actual budget
                # reservation is still gate-completion-ordered — this is a
                # best-effort priority nudge, not a hard precedence). A mis-
                # regime strategy STILL emits, STILL flows, STILL sized normally,
                # just sorted LATER (flow_not_block, exactly like the PDT rank-
                # down). NON-negative, additive with ``pdt_penalty``, and < one
                # PDT step so PDT integrity always dominates. NEVER a T4 sizing
                # multiplier — notional is byte-identical regardless of rank
                # (assignment is not a sizing input; 9-stack untouched). Crisis/
                # unknown → NEUTRAL (an unclassified ticker is not the worst tier).
                regime_penalty = regime_rank_penalty(
                    strategy=strategy_id, regime=regime, exchange=venue
                )
                rank_penalty = pdt_penalty + regime_penalty

                def _factory(
                    *, _strategy: Any = strategy, _sig: Any = sig,
                    _venue: str = venue, _symbol: str = symbol,
                    _asset_class: str = asset_class, _group_id: str = group_id,
                    _regime: str = regime, _atr_pct: float = mv.atr_pct,
                    _last_price: float = live_or_bar_price(
                        state.quote_writer, f"{venue}:{symbol}", mv.last_price,
                    ),
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
                        rank_penalty=rank_penalty,
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
