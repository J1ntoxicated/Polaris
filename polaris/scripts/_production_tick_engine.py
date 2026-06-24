"""P5 tick-decision engine — the fast (~500ms) live-WS decision loop.

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md`` §"scripts".

Jin decision: the live tick IS the primary decision-maker; bars are context.
This loop runs alongside the bar pipeline (``_run_tick``) and trades ONLY the
symbols that have a FRESH WebSocket tick (``state.quote_writer.live_px``). It is
NOT a new sizing stack — it reuses the existing ``compute_size`` (T4, 9-stack
ban intact) for notional and the existing ``reserve_and_submit`` /
``_real_open_fill`` for the entry leg, and the existing precise-exit engine for
momentum exits. The only new behaviour it adds is the per-tick microstructure
DECISION (which signal fires, which side, how convinced) and a fast-scalp exit
for reversion positions.

Invariants honoured:
  - DEMO/PAPER only — the entry leg is the same demo adapter path the bar
    pipeline uses; nothing here touches a real-money venue.
  - AGGRESSIVE / degrade-never-halt — a stale WS feed simply skips that symbol
    (no manufactured signal), a sizing-zero is a capital-headroom result (not a
    defensive throttle), and no path halts the loop on P&L.
  - 9-stack collapse forbidden — sizing is ``compute_size`` ONLY; conviction
    maps into the EXISTING ``signal_strength`` input, never a new multiplier.
  - No double-trade — for the symbols this engine owns (Phase-1 OKX, gated by
    ``cfg`` in the bar path) the bar pipeline does not also open, and an
    open-position dedup skips any symbol already holding a position.

M1/M2 compliance: steps 1-4 (freshness → features → signals → risk-gate) read
ONLY in-mem state (``live_px`` / ``feature_window`` + cached regime); the sole
DB touch in the hot path is ``fetch_regime`` (a cheap indexed read, cached per
loop). DB WRITES happen ONLY on an actual order (entry via ``reserve_and_submit``
/ close via ``close_specific_position``) — identical to the bar pipeline. An
``asyncio.sleep(0)`` starvation guard sits in every inner batch.

Module layout (move-only split, each ≤500 LOC — behaviour byte-identical):
``_production_tick_state`` (state), ``_production_tick_mfe`` (signal-ids +
harvest helpers), ``_production_tick_decision`` (dedup / intent-collect /
window math), ``_production_tick_exit`` (the per-tick EXIT pass); this module
keeps the sizing + order entry chain (``_sized_notional`` / ``_try_open``,
monkeypatch-pinned here) + the entry pass + the loop orchestrator.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from polaris.core.live_recalc.regime_flip import fetch_regime
from polaris.core.pipeline._sizer_payload import (
    _read_portfolio_state,
    _read_strategy_risk_state,
)
from polaris.core.sizing import SignalIntent, compute_size
from polaris.core.sizing.constants import production_default_equity_usd
from polaris.core.streams import resolve_stream
from polaris.core.ticks.config import (
    TICK_ENGINE_OWNED_VENUES,
    TickEngineConfig,
)
from polaris.core.ticks.signals import TickIntent
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick_decision import (
    _LONG_ONLY_PRODUCT_CLASSES,
    _SIGNAL_FNS,
    _collect_intents,
    _conviction_to_strength,
    _cooldown_active,
    _drop_for_bidirectional,
    _intent_to_raw_signal,
    _open_symbols,
)
from polaris.scripts._production_tick_exit import _run_exits
from polaris.scripts._production_tick_mfe import (
    _BURST_RIDER,
    _BURST_RIDER_MFE_RUNGS,
    _FLOW_PRESSURE,
    _FLOW_PRESSURE_TRAIL_MULT_DEFAULT,
    _MICRO_REVERSION,
    _MICRO_REVERSION_TARGET_R,
    _SCALP_STOP_R,
    _SCALP_TARGET_R,
    _env_pos_float,
    _flow_decay_exit,
    _mfe_protect_base_rungs,
    _mfe_protect_schedule,
    _momentum_trail_mult,
    _scalp_exit_decision,
    _scalp_target_for_strategy,
)
from polaris.scripts._production_tick_state import STALL_SLACK_SEC, TickEngineState
from polaris.scripts._smoke_roundtrip_shared import MIN_OKX_NOTIONAL_USD
from polaris.strategies.base import RawSignal

logger = logging.getLogger(__name__)

__all__ = [
    "TickEngineState",
    "run_tick_decision_loop",
]

# Re-exported (move-only) so existing imports + test monkeypatch targets keep
# resolving on this module namespace. The sizing + order chain below
# (``_sized_notional`` / ``_try_open``) deliberately stays HERE: tests patch
# ``reserve_and_submit`` / ``_sized_notional`` on this module, so moving them out
# would break that patch resolution (a behaviour change). The per-tick EXIT pass
# (``_run_exits``) + the pure helpers move to sibling ``_production_tick_*``
# modules and are re-exported here. Silence unused-import lint on the pure
# re-exports the engine body does not itself reference.
_REEXPORT = (
    RawSignal, SignalIntent, TickEngineConfig, _BURST_RIDER,
    _BURST_RIDER_MFE_RUNGS, _FLOW_PRESSURE_TRAIL_MULT_DEFAULT,
    _LONG_ONLY_PRODUCT_CLASSES, _MICRO_REVERSION_TARGET_R, _SCALP_STOP_R,
    _SCALP_TARGET_R, _SIGNAL_FNS, _collect_intents, _conviction_to_strength,
    _drop_for_bidirectional, _env_pos_float, _flow_decay_exit,
    _intent_to_raw_signal, _mfe_protect_base_rungs, _mfe_protect_schedule,
    _momentum_trail_mult, _run_exits, _scalp_exit_decision,
    _scalp_target_for_strategy,
)

# Phase 1 = OKX only (24/7, richest, long-only). Phase 2 adds Capital (long &
# short) + Alpaca (RTH). Aliased to the config SSOT ``TICK_ENGINE_OWNED_VENUES``
# so the engine and the bar pipeline read the SAME frozenset (no drift).
PHASE1_VENUES: frozenset[str] = TICK_ENGINE_OWNED_VENUES

# Tick signals routed MAKER-FIRST (post-only at touch → TAKER fallback) on OKX:
# neither is latency-critical to the tick, so resting at the touch pays the
# cheaper maker fee when it can rest, NEVER a missed trade (the post-only path
# always falls back to taker on a would-cross/reject/timeout). flow_pressure
# (OFI momentum) + micro_reversion (overshoot fade — you are fading an EXHAUSTED
# move, so resting at the touch is the natural fill) both clear the same honest
# fee-net diagnosis (gross edge POSITIVE, eaten by the ~20bps round-trip taker
# cost). flow_not_block: cheaper when possible, never a skipped entry. Other tick
# signals keep the strength-gated default.
_MAKER_FIRST_SIGNALS: frozenset[str] = frozenset({_FLOW_PRESSURE, _MICRO_REVERSION})

# Tick signals routed MARKETABLE-LIMIT (cross the spread with a cap_bps-capped
# limit → market fallback) on OKX ([[ab_letrun_maker_2026-06-24]] Build B):
# burst_rider is a MOMENTUM tick — the price runs while a passive post-only would
# wait at the touch (non-fill), so it must cross to keep the trade, but with an
# adverse-fill cap (cap_bps past the ask). MUST NOT be in the post-only
# (_MAKER_FIRST) set — a momentum entry that cannot rest would mostly non-fill →
# late market fallback (worse). flow_not_block: a no-fill/reject still market-
# falls back, so the entry is never missed, only (rarely) a worse taker.
_MARKETABLE_LIMIT_SIGNALS: frozenset[str] = frozenset({_BURST_RIDER})

# Minimum tradeable notional (USD) for a tick entry. compute_size returns
# ``final_risk_pct × equity`` which, on an almost-exhausted binding daily/cap
# headroom, can clip to a POSITIVE-but-sub-minimum residual (e.g. $0.0001). Such
# an order is unfundable at EVERY venue — OKX rejects it 51020 "below minimum
# order amount" and Alpaca rejects a $0 notional on validation. Submitting it
# produced the 4000+ ADA/XRP 51020 flood. The floor is the OKX SPOT minimum (the
# lowest venue minimum), so a notional below it is treated as a sizing/headroom
# drop — NOT submitted (flow_not_block at the source: never fire a 100%-reject
# order). NOT a throttle: flooring up would BREACH the very cap that clipped it.
_MIN_TICK_NOTIONAL_USD: float = MIN_OKX_NOTIONAL_USD


def _sized_notional(
    conn: Any,
    *,
    intent: TickIntent,
    asset_class: str,
    underlying_group_id: str,
    regime: str,
    now_ts: int,
) -> float:
    """Run the EXISTING T4 ``compute_size`` and return its final notional (USD).

    Reuses the bar pipeline's risk/portfolio readers (gap-b: the open-position
    risk rows persisted on fill bind the per-symbol / underlying / cluster / track
    caps). Returns ``0.0`` when a binding cap clipped to zero — a capital-headroom
    result (opportunity-cost ceiling), NOT a defensive throttle. The conviction
    feeds the EXISTING ``signal_strength`` input; no new multiplier is introduced.
    """
    stream = resolve_stream(intent.venue)
    strength = _conviction_to_strength(intent.conviction)
    sizing_intent = SignalIntent(
        signal_id=intent.signal_id,
        venue=intent.venue,
        symbol=intent.symbol,
        instrument_id=f"{intent.venue}:{intent.symbol}",
        underlying_group_id=underlying_group_id,
        asset_class=asset_class,
        strategy=intent.signal_id,
        track=stream.track,
        regime=regime,
        direction=intent.side,
        signal_strength=strength,
        listing_age_hours=24 * 365.0,
        leverage=1.0,
        product_class=stream.product_class,
        stream_id=stream.stream_id,
        signal_family=intent.signal_family,
    )
    equity = production_default_equity_usd()
    risk_state = _read_strategy_risk_state(
        conn, venue=intent.venue, strategy=intent.signal_id, now_ts=now_ts
    )
    portfolio = _read_portfolio_state(conn, equity_usd=equity, track=stream.track)
    sized = compute_size(
        conn, intent=sizing_intent, risk_state=risk_state,
        portfolio=portfolio, now_ts=now_ts,
    )
    return float(sized.final_notional_usd)


async def _try_open(
    conn: Any,
    state: ProdLoopState,
    eng: TickEngineState,
    *,
    intent: TickIntent,
    asset_class: str,
    underlying_group_id: str,
    regime: str,
    now_ts: int,
    now_mono: float,
    okx_adapter: Any,
    capital_session: Any,
    alpaca_adapter: Any,
    real_roundtrip: bool,
) -> bool:
    """Risk-gate → executor for ONE intent. Returns True iff an order was placed.

    Risk gate: bidirectional venue rule (drop a short on a long-only venue) +
    ``compute_size`` (capital-headroom caps, gap-b). Executor: shadow → LOG only;
    live → ``reserve_and_submit`` (the EXISTING entry path — fence + idempotent
    register + ``_real_open_fill`` + position/risk/lineage persist). The opened
    position is tagged with the tick ``signal_id`` (via ``strategy_id``) and the
    ``signal_family`` (tracked on the engine state) so the per-tick exit routes it.
    """
    eng.decisions += 1
    # --- bidirectional venue rule ---------------------------------------
    if _drop_for_bidirectional(intent.venue, intent.side):
        eng.drops_short += 1
        logger.debug(
            "[tick-engine] drop short %s:%s sig=%s (long-only venue)",
            intent.venue, intent.symbol, intent.signal_id,
        )
        return False
    # --- sizing (existing T4; conviction → existing strength input) ------
    notional = _sized_notional(
        conn, intent=intent, asset_class=asset_class,
        underlying_group_id=underlying_group_id, regime=regime, now_ts=now_ts,
    )
    if notional < _MIN_TICK_NOTIONAL_USD:
        # <=0 OR positive-but-sub-minimum: the binding daily/cap headroom clipped
        # final_risk_pct to a residual too small to fund a tradeable order. SKIP
        # cleanly — submitting it would 100%-reject (OKX 51020 / Alpaca $0
        # validation). flow_not_block: not a throttle, just never firing an order
        # that cannot fund (flooring up would breach the cap that clipped it).
        eng.drops_sizing += 1
        logger.debug(
            "[tick-engine] sizing-sub-min %s:%s sig=%s notional=%.4f<%.2f "
            "(binding cap — capital headroom, not a throttle)",
            intent.venue, intent.symbol, intent.signal_id,
            notional, _MIN_TICK_NOTIONAL_USD,
        )
        return False
    # Cooldown is armed at the decision point so a shadow decision also gates
    # re-log spam (the operator sees one decision per cooldown, not a flood).
    eng.cooldowns[(intent.symbol, intent.signal_id)] = now_mono
    # --- executor -------------------------------------------------------
    if eng.cfg.shadow:
        eng.shadow_logs += 1
        logger.info(
            "[tick-engine/shadow] DECISION %s:%s side=%s sig=%s family=%s "
            "conviction=%.3f notional=%.2f (NO order — shadow)",
            intent.venue, intent.symbol, intent.side, intent.signal_id,
            intent.signal_family, intent.conviction, notional,
        )
        return False
    raw = _intent_to_raw_signal(intent, asset_class=asset_class)
    trade = await reserve_and_submit(
        conn=conn, state=state, sig=raw, venue=intent.venue, symbol=intent.symbol,
        asset_class=asset_class, underlying_group_id=underlying_group_id,
        notional_usd=notional, last_price=intent.ref_price, now_ts=now_ts,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        # flow_pressure OFI momentum + micro_reversion overshoot fade are not
        # latency-critical to the tick → route their OKX entry maker-first
        # (post-only at touch → TAKER fallback) to pay 8 bps when they can rest,
        # NEVER a missed trade (the post-only path always falls back to taker on a
        # would-cross/reject/timeout). flow_not_block: cheaper when possible, never
        # a skipped entry. Other tick signals keep the strength-gated default.
        prefer_maker=(intent.signal_id in _MAKER_FIRST_SIGNALS),
        # burst_rider (momentum tick) crosses the spread with a cap_bps-capped
        # limit → market fallback (Build B): it cannot rest passively, so it fills
        # like a taker but never worse than the cap. NOT in the post-only set.
        marketable_limit=(intent.signal_id in _MARKETABLE_LIMIT_SIGNALS),
    )
    if trade is None:
        return False
    state.open_trades.append(trade)
    eng.orders += 1
    if trade.position_id is not None:
        eng.family_by_position[trade.position_id] = intent.signal_family
        eng.entry_ref_by_position[trade.position_id] = intent.ref_price
    logger.info(
        "[tick-engine/open] %s:%s side=%s sig=%s family=%s conviction=%.3f "
        "notional=%.2f trade_id=%s",
        intent.venue, intent.symbol, intent.side, intent.signal_id,
        intent.signal_family, intent.conviction, notional, trade.position_id,
    )
    return True


async def _run_entries(
    conn: Any,
    state: ProdLoopState,
    eng: TickEngineState,
    *,
    now_ts: int,
    now_mono: float,
    okx_adapter: Any,
    capital_session: Any,
    alpaca_adapter: Any,
    real_roundtrip: bool,
    focus: list[tuple[str, str, str, str]],
    regime_cache: dict[tuple[str, str], str | None],
    eligible_set: set[tuple[str, str]] | None = None,
) -> None:
    """Entry pass: for each FRESH tick-eligible symbol, decide + (maybe) open.

    Steps 1-4 of the spec. ``focus`` is the (venue, symbol, asset_class,
    group_id) set; Phase 1 keeps OKX only. Freshness, feature/signal eval, and
    sizing are all in-mem (the only DB read is the per-loop-cached
    ``fetch_regime``). A ``sleep(0)`` guard yields between symbols so a wide focus
    cannot starve the loop.

    Increment 1 DECOUPLE: ``eligible_set`` (the EntranceJudge trade set, when
    supplied) gates the ORDER-OPEN — a candidate NOT in it is skipped to-watch
    (``skips_ineligible``, ``continue``) BEFORE feature/signal eval. This is NOT a
    size cut, NOT a hard block: the symbol still streams, still bar-ingests, still
    shows on the dashboard — only the order-open is deferred (flow_not_block). A
    HELD symbol is force-seated into the trade set upstream, so it is never
    starved. ``None`` = no gating (every focus symbol is trade-eligible — the
    pre-Increment-1 behaviour, kept for the direct-call tests).
    """
    writer = state.quote_writer
    if writer is None:
        return
    open_syms = _open_symbols(state)
    for venue, symbol, asset_class, group_id in focus:
        await asyncio.sleep(0)  # M1/M2 starvation guard (between symbols)
        if venue not in PHASE1_VENUES:
            continue
        # DECOUPLE: defer the order-open for a NOT-trade-eligible candidate (still
        # watched/streamed — only the entry is deferred). flow_not_block.
        if eligible_set is not None and (venue, symbol) not in eligible_set:
            eng.skips_ineligible += 1
            continue
        # FOREX → bar pipeline (its dedicated fx_breakout_basket / session_breakout
        # strategies). The tick engine's micro-structure signals (burst/ofi) never
        # trip on low-vol FX, so owning it here only starved it; yield it cleanly.
        if asset_class == "forex":
            continue
        instrument_id = f"{venue}:{symbol}"
        px = writer.live_px(instrument_id)
        # --- (1) freshness gate: trade only live-WS symbols -------------
        if px is None or (now_mono - px[1]) > eng.cfg.fresh_sec:
            eng.skips_stale += 1
            continue
        # --- open-position dedup (skip if already holding this symbol) --
        if (venue, symbol) in open_syms:
            eng.skips_dedup += 1
            continue
        # --- (2) features → regime-active signals → intents -------------
        window = writer.feature_window(instrument_id)
        regime = regime_cache.get((venue, group_id))
        if (venue, group_id) not in regime_cache:
            regime = fetch_regime(
                conn, venue=venue, underlying_group_id=group_id
            ) if group_id else None
            regime_cache[(venue, group_id)] = regime
        intents = _collect_intents(
            eng, venue=venue, symbol=symbol, window=window,
            regime=regime, now_ts=now_ts,
        )
        for intent in intents:
            # --- (3) cooldown (per symbol × signal) ---------------------
            if _cooldown_active(
                eng, symbol=symbol, signal_id=intent.signal_id, now_mono=now_mono
            ):
                eng.skips_cooldown += 1
                continue
            opened = await _try_open(
                conn, state, eng, intent=intent, asset_class=asset_class,
                underlying_group_id=group_id or "", regime=regime or "chop",
                now_ts=now_ts, now_mono=now_mono, okx_adapter=okx_adapter,
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                real_roundtrip=real_roundtrip,
            )
            if opened:
                # One open per symbol per tick — re-dedup so a second intent on
                # the same symbol this tick cannot stack a position.
                open_syms.add((venue, symbol))
                break


def _lookup_regime_str(conn: Any, venue: str, symbol: str) -> str:
    """Adapter to the close path's ``lookup_regime`` signature (returns 'chop'
    when unseeded — never raises, so a close never blocks on a missing regime).
    """
    from polaris.scripts._production_tick import _lookup_regime

    return _lookup_regime(conn, venue, symbol)


async def run_tick_decision_loop(
    conn: Any,
    state: ProdLoopState,
    stop_evt: asyncio.Event,
    *,
    okx_adapter: Any = None,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
    phase: str = "P0",
    real_roundtrip: bool = False,
    tick_engine_state: TickEngineState | None = None,
    cadence_sec: float = 0.5,
) -> TickEngineState:
    """The fast ~500ms tick-decision loop (runs alongside the bar pipeline).

    Each iteration: read the focus set (DB, cheap) → for each FRESH live-WS
    OKX symbol, compute tick features → regime-active signals → risk-gate →
    execute (shadow=log / live=reserve_and_submit); then a per-tick exit pass
    (momentum→precise-exit, reversion→fast-scalp). Cooperative stop via
    ``stop_evt``. Returns the engine state (telemetry) on teardown.

    DEGRADE-NEVER-HALT: a per-iteration exception is logged + the loop continues
    (a transient feature/sizing error never silences the engine). No P&L path
    halts it; a stale feed simply skips that symbol.
    """
    eng = tick_engine_state if tick_engine_state is not None else TickEngineState()
    # Local import avoids a module-load cycle (tick → layers → tick body).
    from polaris.core.universe.schema import universe_watch_max
    from polaris.scripts._production_layers import get_focus_targets

    # The focus cycle persists the full watch breadth (watch-all: OKX active ~120);
    # read the tick path's WATCH/TRADE sets up to that ceiling instead of the
    # default 30 so the wide watch is bar-ingested + WS-subscribed + eligibility-
    # gated (flow_not_block: more symbols OBSERVED; the order-open is still gated
    # by ``eligible_set``, so trade volume is unchanged).
    watch_ceiling = universe_watch_max()

    logger.info(
        "[tick-engine] start phase=%s real_roundtrip=%s shadow=%s cadence=%.2fs",
        phase, real_roundtrip, eng.cfg.shadow, cadence_sec,
    )
    while not stop_evt.is_set():
        loop_start = time.monotonic()
        # --- per-iteration heartbeat (stall visibility) --------------------
        # Always bump the counter + stamp BEFORE the body so a freeze in the
        # body is detectable on the NEXT resume. A gap beyond cadence+slack
        # since the prior iteration means the loop was starved (shared-conn
        # blocker) — log a WARNING the instant it resumes.
        prev_loop_mono = eng.last_loop_mono
        eng.loop_count += 1
        eng.last_loop_mono = loop_start
        if prev_loop_mono > 0.0:
            gap = loop_start - prev_loop_mono
            if gap > cadence_sec + STALL_SLACK_SEC:
                logger.warning(
                    "[tick-engine] STALL detected — iteration gap=%.2fs "
                    "(cadence=%.2fs) at loop=%d; the loop was starved "
                    "(shared-conn blocker?)",
                    gap, cadence_sec, eng.loop_count,
                )
        logger.debug(
            "[tick-engine] heartbeat loop=%d mono=%.3f", eng.loop_count, loop_start
        )
        try:
            now_ts = int(time.time())
            now_mono = time.monotonic()
            # STAGE 1 tier-cadence: ``eng.loop_count`` is the cycle index, so the
            # per-loop tick eval set is the tiers that fire this cycle (S/A every
            # loop; B every K; T every M). flow_not_block: every active row is
            # still watched/persisted — cadence only governs HOW OFTEN it is eval'd.
            cycle_idx = eng.loop_count
            focus = get_focus_targets(
                conn, cycle_ts=now_ts, max_n=watch_ceiling, cycle_index=cycle_idx
            )
            # Increment 1 DECOUPLE: the TRADE set (entrance-judge eligible subset)
            # gates the order-open; the watch ``focus`` above still streams/ingests
            # every valid symbol. Same cadence applies (an order-open only fires on
            # a name whose tier is being eval'd this cycle).
            eligible_set = {
                (v, s)
                for v, s, _ac, _g in get_focus_targets(
                    conn, cycle_ts=now_ts, max_n=watch_ceiling,
                    eligible_only=True, cycle_index=cycle_idx,
                )
            }
            # Regime is read once per (venue, group) per loop (M1/M2: a cheap
            # indexed read, cached so the hot path does not re-hit the DB).
            regime_cache: dict[tuple[str, str], str | None] = {}
            await _run_entries(
                conn, state, eng, now_ts=now_ts, now_mono=now_mono,
                okx_adapter=okx_adapter, capital_session=capital_session,
                alpaca_adapter=alpaca_adapter, real_roundtrip=real_roundtrip,
                focus=focus, regime_cache=regime_cache, eligible_set=eligible_set,
            )
            await _run_exits(
                conn, state, eng, now_ts=now_ts, now_mono=now_mono, phase=phase,
                real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                lookup_regime=_lookup_regime_str,
            )
            # --- periodic eval telemetry (~30s): diagnose fire/no-fire ------
            if now_mono - eng.tel_mono >= 30.0:
                logger.info(
                    "[tick-engine/telemetry] focus_okx_eval=%d thin=%d dry=%d "
                    "decisions=%d shadow=%d orders=%d | max_n=%d "
                    "burst_z=%.2f(θ%.2f) |ofi|=%.3f(θ%.2f) |overshoot|=%.2f(θ%.2f) "
                    "| skips(stale=%d dedup=%d cool=%d inelig=%d) "
                    "drops(short=%d sizing=%d)",
                    eng.evaluated, eng.thin, eng.dry, eng.decisions,
                    eng.shadow_logs, eng.orders, eng.max_n_ticks,
                    eng.max_burst_z, eng.cfg.theta_burst, eng.max_abs_ofi,
                    eng.cfg.theta_ofi, eng.max_abs_overshoot, eng.cfg.theta_revert,
                    eng.skips_stale, eng.skips_dedup, eng.skips_cooldown,
                    eng.skips_ineligible, eng.drops_short, eng.drops_sizing,
                )
                eng.tel_mono = now_mono
                eng.max_burst_z = 0.0
                eng.max_abs_ofi = 0.0
                eng.max_abs_overshoot = 0.0
        except Exception as exc:  # noqa: BLE001 — degrade-never-halt
            logger.error("[tick-engine] iteration error: %r — continuing", exc)
        # Sleep the remaining cadence; wake early on stop_evt (teardown 0-lag).
        elapsed = time.monotonic() - loop_start
        remaining = max(0.0, cadence_sec - elapsed)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_evt.wait(), timeout=remaining)
    logger.info(
        "[tick-engine] stop decisions=%d orders=%d shadow_logs=%d scalp_exits=%d "
        "skips(stale=%d dedup=%d cooldown=%d inelig=%d) drops(short=%d sizing=%d)",
        eng.decisions, eng.orders, eng.shadow_logs, eng.scalp_exits,
        eng.skips_stale, eng.skips_dedup, eng.skips_cooldown,
        eng.skips_ineligible, eng.drops_short, eng.drops_sizing,
    )
    return eng
