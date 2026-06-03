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
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from polaris.core.live_recalc.regime_flip import fetch_regime
from polaris.core.pipeline._sizer_payload import (
    _read_portfolio_state,
    _read_strategy_risk_state,
)
from polaris.core.sizing import SignalIntent, compute_size
from polaris.core.sizing.constants import production_default_equity_usd
from polaris.core.streams import resolve_stream
from polaris.core.ticks.config import TICK_ENGINE_OWNED_VENUES, TickEngineConfig
from polaris.core.ticks.features import compute_tick_features
from polaris.core.ticks.regime_gate import active_signals
from polaris.core.ticks.signals import (
    TickIntent,
    burst_rider,
    flow_pressure,
    micro_reversion,
)
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_indicators import compute_unrealized_pnl_r
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts._production_recalc import ActivePositionRow
from polaris.scripts._production_recalc_exit import run_precise_exit
from polaris.scripts._production_state import ProdLoopState
from polaris.strategies.base import RawSignal

logger = logging.getLogger(__name__)

__all__ = [
    "TickEngineState",
    "run_tick_decision_loop",
]

# Phase 1 = OKX only (24/7, richest, long-only). Phase 2 adds Capital (long &
# short) + Alpaca (RTH). Aliased to the config SSOT ``TICK_ENGINE_OWNED_VENUES``
# so the engine and the bar pipeline read the SAME frozenset (no drift).
PHASE1_VENUES: frozenset[str] = TICK_ENGINE_OWNED_VENUES

# Venues that are LONG-ONLY (spot / cash equity). A 'short' intent on these is
# DROPPED at the risk gate (bidirectional rule) — Capital CFD (cfd) takes both.
_LONG_ONLY_PRODUCT_CLASSES: frozenset[str] = frozenset({"spot", "equity"})

# The three pure signal functions, keyed by signal_id so the regime gate's
# active set selects which ones run this tick.
_SIGNAL_FNS: dict[str, Callable[..., TickIntent | None]] = {
    "burst_rider": burst_rider,
    "flow_pressure": flow_pressure,
    "micro_reversion": micro_reversion,
}

# A reversion (fast-scalp) position closes on the FIRST of: flow reversal (the
# book turned against the fade), a micro-stop (adverse R past this floor), or a
# small-R target (the snap-back banked). EXPECTANCY, not a throttle — it cuts a
# dead scalp fast and banks a small winner fast; it never reduces size or blocks
# an entry. R units share ``compute_unrealized_pnl_r``'s ATR-R basis.
_SCALP_TARGET_R = 0.5
_SCALP_STOP_R = -0.4


@dataclass(slots=True)
class TickEngineState:
    """Engine-owned in-mem state (no DB) carried across loop iterations.

    ``cooldowns`` suppresses a re-fire of the same (symbol, signal_id) inside
    ``cfg.cooldown_sec`` (per-symbol×signal spam guard). ``family_by_position``
    records each opened position's ``signal_family`` so the per-tick exit routes
    momentum positions to the ATR-trail and reversion positions to the scalp
    exit. ``entry_ref_by_position`` is the mid the scalp exit measures its small
    flow reversal / R target against. ``decisions`` / ``orders`` / ``skips`` are
    telemetry only.
    """

    cfg: TickEngineConfig = field(default_factory=TickEngineConfig)
    cooldowns: dict[tuple[str, str], float] = field(default_factory=dict)
    family_by_position: dict[str, str] = field(default_factory=dict)
    entry_ref_by_position: dict[str, float] = field(default_factory=dict)
    decisions: int = 0
    orders: int = 0
    shadow_logs: int = 0
    skips_stale: int = 0
    skips_dedup: int = 0
    skips_cooldown: int = 0
    drops_short: int = 0
    drops_sizing: int = 0
    scalp_exits: int = 0
    # --- eval telemetry (diagnostic: WHY signals do / don't fire) ---------
    # ``evaluated`` = symbols that passed freshness+dedup and reached feature
    # eval; ``thin`` = of those, windows too thin/stale (None features → cannot
    # fire); ``dry`` = sufficient features but no signal armed. The ``max_*`` are
    # peak feature magnitudes seen since the last telemetry emit (reset each
    # interval) so the operator can see how close features get to the thresholds.
    evaluated: int = 0
    thin: int = 0
    dry: int = 0
    max_n_ticks: int = 0
    max_burst_z: float = 0.0
    max_abs_ofi: float = 0.0
    max_abs_overshoot: float = 0.0
    tel_mono: float = 0.0


def _open_symbols(state: ProdLoopState) -> set[tuple[str, str]]:
    """The (venue, symbol) set with an OPEN tracked position (dedup source)."""
    return {
        (t.venue, t.symbol)
        for t in state.open_trades
        if not t.closed
    }


def _cooldown_active(
    eng: TickEngineState, *, symbol: str, signal_id: str, now_mono: float
) -> bool:
    """True iff this (symbol, signal_id) fired inside ``cfg.cooldown_sec``."""
    last = eng.cooldowns.get((symbol, signal_id))
    if last is None:
        return False
    return (now_mono - last) < eng.cfg.cooldown_sec


def _conviction_to_strength(conviction: float) -> float:
    """Map signal conviction ``[0,1]`` into the EXISTING ``signal_strength`` space.

    ``compute_size``'s continuous scalar reads ``signal_strength`` in ``[0,2]``
    (1.0 = baseline). A tick conviction of 1.0 maps to 1.5 (the upper continuous
    scalar anchor — a strong, but not absurd, conviction), 0.0 maps to the 0.75
    floor anchor (0.5 strength). This is NOT a new multiplier — it only chooses
    the value of the one existing continuous-scalar input.
    """
    return 0.5 + conviction * 1.0


def _intent_to_raw_signal(intent: TickIntent, *, asset_class: str) -> RawSignal:
    """Adapt a pure ``TickIntent`` into the ``RawSignal`` the entry path reads.

    ``strategy_id`` is the tick ``signal_id`` so the persisted position + lineage
    record which tick signal opened it (the signal_family is tracked separately
    on the engine state). ``strength`` carries the conviction-derived strength so
    ``compute_size`` (via ``reserve_and_submit``'s caps) and the cell matrix see a
    coherent value. A fresh ``signal_id`` per fire keeps the order key unique.
    """
    strength = _conviction_to_strength(intent.conviction)
    return RawSignal(
        signal_id=f"tick_{intent.signal_id}_{uuid.uuid4().hex[:10]}",
        strategy_id=intent.signal_id,
        symbol=intent.symbol,
        side=intent.side,
        strength=strength,
        sizing_hint=strength,
        ttl_bars=1,
        thesis_tag=intent.signal_family,
        correlation_group=intent.symbol,
    )


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


def _drop_for_bidirectional(venue: str, side: str) -> bool:
    """True iff ``side`` is not tradeable on ``venue`` (long-only venue + short).

    OKX (spot) / Alpaca (equity) are long-only → a 'short' intent is DROPPED
    (not invertible — we never silently flip a short into a long). Capital (cfd)
    takes both. An unknown venue degrades to long-only (conservative on the
    direction only — it never blocks a long).
    """
    if side == "long":
        return False
    try:
        product_class = resolve_stream(venue).product_class
    except (KeyError, ValueError):
        return True
    return product_class in _LONG_ONLY_PRODUCT_CLASSES


def _collect_intents(
    eng: TickEngineState,
    *,
    venue: str,
    symbol: str,
    window: list[Any],
    regime: str | None,
    now_ts: int,
) -> list[TickIntent]:
    """Run the regime-active signal fns over the feature window → intents.

    PURE / in-mem: ``compute_tick_features`` + the signal fns read only the
    window (no DB, no I/O). A safe-sentinel (thin / stale) window yields ``None``
    features so no signal fires. ``ref_price`` is the newest mid in the window.
    ``now_ts`` is epoch seconds (same domain as ``TickSample.ts``) for the
    feature freshness gate.
    """
    feat = compute_tick_features(window, now_ts, eng.cfg)
    # --- eval telemetry: window sufficiency + peak feature magnitudes -----
    eng.evaluated += 1
    if feat.burst_z is None:
        eng.thin += 1  # thin/stale window → safe-sentinel (no signal possible)
    else:
        eng.max_n_ticks = max(eng.max_n_ticks, feat.n_ticks)
        eng.max_burst_z = max(eng.max_burst_z, feat.burst_z)
        if feat.ofi is not None:
            eng.max_abs_ofi = max(eng.max_abs_ofi, abs(feat.ofi))
        if feat.overshoot_z is not None:
            eng.max_abs_overshoot = max(eng.max_abs_overshoot, abs(feat.overshoot_z))
    active = active_signals(regime)
    ref_price = float(window[-1].mid) if window else 0.0
    intents: list[TickIntent] = []
    for signal_id in active:
        fn = _SIGNAL_FNS.get(signal_id)
        if fn is None:
            continue
        intent = fn(
            feat, regime, venue=venue, symbol=symbol,
            ref_price=ref_price, cfg=eng.cfg,
        )
        if intent is not None:
            intents.append(intent)
    if not intents and feat.burst_z is not None:
        eng.dry += 1  # had real features but no signal armed (calm / sub-θ)
    return intents


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
    if notional <= 0.0:
        eng.drops_sizing += 1
        logger.debug(
            "[tick-engine] sizing-zero %s:%s sig=%s (binding cap — capital "
            "headroom, not a throttle)",
            intent.venue, intent.symbol, intent.signal_id,
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
) -> None:
    """Entry pass: for each FRESH tick-eligible symbol, decide + (maybe) open.

    Steps 1-4 of the spec. ``focus`` is the (venue, symbol, asset_class,
    group_id) set; Phase 1 keeps OKX only. Freshness, feature/signal eval, and
    sizing are all in-mem (the only DB read is the per-loop-cached
    ``fetch_regime``). A ``sleep(0)`` guard yields between symbols so a wide focus
    cannot starve the loop.
    """
    writer = state.quote_writer
    if writer is None:
        return
    open_syms = _open_symbols(state)
    for venue, symbol, asset_class, group_id in focus:
        await asyncio.sleep(0)  # M1/M2 starvation guard (between symbols)
        if venue not in PHASE1_VENUES:
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


def _scalp_exit_decision(
    *,
    side: str,
    entry_price: float,
    last_mid: float,
    ofi: float | None,
    pnl_r: float,
) -> str | None:
    """Fast-scalp exit for a reversion position — close-reason or None (hold).

    Closes on the FIRST of:
      - ``flow_reversal``: order-flow imbalance turned to favour the side the
        fade was betting AGAINST continuing (a long fade wanted the down-push to
        exhaust; if ``ofi`` is now firmly negative again, the snap-back failed).
      - ``scalp_stop``: adverse excursion past ``_SCALP_STOP_R`` (micro-stop).
      - ``scalp_target``: the snap-back banked ``_SCALP_TARGET_R`` (small R).
    EXPECTANCY (cut a dead scalp fast / bank a small winner fast), NOT a throttle.
    """
    if pnl_r <= _SCALP_STOP_R:
        return "scalp_stop"
    if pnl_r >= _SCALP_TARGET_R:
        return "scalp_target"
    if ofi is not None:
        # A long reversion bet wants flow to stop selling; if ofi is firmly
        # bid-negative (still selling) the fade is failing → exit. Symmetric for
        # a short reversion (ofi firmly bid-positive = still buying).
        if side == "long" and ofi < -0.0:
            return "flow_reversal"
        if side == "short" and ofi > 0.0:
            return "flow_reversal"
    return None


async def _run_exits(
    conn: Any,
    state: ProdLoopState,
    eng: TickEngineState,
    *,
    now_ts: int,
    now_mono: float,
    phase: str,
    real_roundtrip: bool,
    okx_adapter: Any,
    capital_session: Any,
    lookup_regime: Callable[..., str],
) -> None:
    """Per-tick exit pass over the engine's positions (step 6).

    Momentum-family positions route to the EXISTING ``run_precise_exit`` (ATR
    trail / protected-BEP / loser-timeout). Reversion-family positions route to
    the fast-scalp exit (flow reversal / micro-stop / small-R target) and close
    through the EXISTING ``close_specific_position``. Only positions THIS engine
    opened (tracked in ``family_by_position``) are touched — the bar pipeline's
    own recalc still manages its positions. ``sleep(0)`` guards the batch.
    """
    writer = state.quote_writer
    # Snapshot — close_specific_position mutates state.open_trades.
    targets = [
        t for t in list(state.open_trades)
        if not t.closed and t.position_id in eng.family_by_position
    ]
    for trade in targets:
        await asyncio.sleep(0)  # starvation guard
        position_id = trade.position_id
        if position_id is None:
            continue
        instrument_id = f"{trade.venue}:{trade.symbol}"
        px = writer.live_px(instrument_id) if writer is not None else None
        if px is None:
            # No fresh tick → leave it for the bar pipeline's recalc (graceful
            # degrade; never force a flat on a stale mark).
            continue
        last_mid = float(px[0])
        family = eng.family_by_position.get(position_id, "momentum")
        # ATR proxy from the feature window's recent mid range (in-mem); a tiny
        # floor keeps the R denominator finite. This is the SAME ATR-R basis the
        # bar recalc uses (compute_unrealized_pnl_r), just fed a live-tick ATR.
        atr_pct = _window_atr_pct(writer, instrument_id) if writer is not None else 0.0
        entry_price = float(trade.entry_price)
        pnl_r = compute_unrealized_pnl_r(
            side=trade.side, entry_price=entry_price,
            last_price=last_mid, atr_pct=max(atr_pct, 1e-4),
        )
        if family == "reversion":
            window = (
                writer.feature_window(instrument_id) if writer is not None else []
            )
            feat = compute_tick_features(window, now_mono, eng.cfg)
            reason = _scalp_exit_decision(
                side=trade.side, entry_price=entry_price, last_mid=last_mid,
                ofi=feat.ofi, pnl_r=pnl_r,
            )
            if reason is None:
                continue
            closed = await close_specific_position(
                conn, state=state, position_id=position_id, now_ts=now_ts,
                lookup_regime=lookup_regime, gpt_client=None, phase=phase,
                real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
                capital_session=capital_session, close_reason=reason,
            )
            if closed:
                eng.scalp_exits += 1
                eng.family_by_position.pop(position_id, None)
                eng.entry_ref_by_position.pop(position_id, None)
                logger.info(
                    "[tick-engine/scalp-exit] %s:%s trade_id=%s reason=%s "
                    "pnl_r=%.2f", trade.venue, trade.symbol, position_id,
                    reason, pnl_r,
                )
            continue
        # Momentum family → existing precise-exit engine (ATR trail / BEP /
        # loser-timeout). Build the ActivePositionRow it reads from the tracked
        # trade + a live-tick mark.
        pos = ActivePositionRow()
        pos.update(
            {
                "position_id": position_id,
                "venue": trade.venue,
                "symbol": trade.symbol,
                "side": trade.side,
                "active_strategy_id": trade.strategy_id,
                "strategy": trade.strategy_id,
                "peak_price": None,
                "trough_price": None,
                "stop_price": None,
                "exit_state": "open",
            }
        )
        held_seconds = max(0, now_ts - int(trade.open_ts))
        closed = await run_precise_exit(
            conn=conn, state=state, pos=pos, side=trade.side,
            entry_price=entry_price, last_price=last_mid, atr_pct=max(atr_pct, 1e-4),
            pnl_r=pnl_r, held_seconds=held_seconds, now_ts=now_ts,
            close_specific=close_specific_position, lookup_regime=lookup_regime,
            gpt_client=None, phase=phase, real_roundtrip=real_roundtrip,
            okx_adapter=okx_adapter, capital_session=capital_session,
        )
        if closed:
            eng.family_by_position.pop(position_id, None)
            eng.entry_ref_by_position.pop(position_id, None)


def _window_atr_pct(writer: Any, instrument_id: str) -> float:
    """A live-tick ATR% proxy: mid range over the feature window / latest mid.

    In-mem only (reads the writer's ring). Used solely as the R-unit denominator
    for the per-tick exit pnl_r so a live tick — not a delayed bar — drives the
    exit. Returns 0.0 on an empty window (the caller floors the denominator).
    """
    window = writer.feature_window(instrument_id)
    if not window:
        return 0.0
    mids = [float(t.mid) for t in window]
    last = mids[-1]
    if last <= 0.0:
        return 0.0
    return (max(mids) - min(mids)) / last


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
    from polaris.scripts._production_layers import get_focus_targets

    logger.info(
        "[tick-engine] start phase=%s real_roundtrip=%s shadow=%s cadence=%.2fs",
        phase, real_roundtrip, eng.cfg.shadow, cadence_sec,
    )
    while not stop_evt.is_set():
        loop_start = time.monotonic()
        try:
            now_ts = int(time.time())
            now_mono = time.monotonic()
            focus = get_focus_targets(conn, cycle_ts=now_ts)
            # Regime is read once per (venue, group) per loop (M1/M2: a cheap
            # indexed read, cached so the hot path does not re-hit the DB).
            regime_cache: dict[tuple[str, str], str | None] = {}
            await _run_entries(
                conn, state, eng, now_ts=now_ts, now_mono=now_mono,
                okx_adapter=okx_adapter, capital_session=capital_session,
                alpaca_adapter=alpaca_adapter, real_roundtrip=real_roundtrip,
                focus=focus, regime_cache=regime_cache,
            )
            await _run_exits(
                conn, state, eng, now_ts=now_ts, now_mono=now_mono, phase=phase,
                real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
                capital_session=capital_session, lookup_regime=_lookup_regime_str,
            )
            # --- periodic eval telemetry (~30s): diagnose fire/no-fire ------
            if now_mono - eng.tel_mono >= 30.0:
                logger.info(
                    "[tick-engine/telemetry] focus_okx_eval=%d thin=%d dry=%d "
                    "decisions=%d shadow=%d orders=%d | max_n=%d "
                    "burst_z=%.2f(θ%.2f) |ofi|=%.3f(θ%.2f) |overshoot|=%.2f(θ%.2f) "
                    "| skips(stale=%d dedup=%d cool=%d) drops(short=%d sizing=%d)",
                    eng.evaluated, eng.thin, eng.dry, eng.decisions,
                    eng.shadow_logs, eng.orders, eng.max_n_ticks,
                    eng.max_burst_z, eng.cfg.theta_burst, eng.max_abs_ofi,
                    eng.cfg.theta_ofi, eng.max_abs_overshoot, eng.cfg.theta_revert,
                    eng.skips_stale, eng.skips_dedup, eng.skips_cooldown,
                    eng.drops_short, eng.drops_sizing,
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
        "skips(stale=%d dedup=%d cooldown=%d) drops(short=%d sizing=%d)",
        eng.decisions, eng.orders, eng.shadow_logs, eng.scalp_exits,
        eng.skips_stale, eng.skips_dedup, eng.skips_cooldown,
        eng.drops_short, eng.drops_sizing,
    )
    return eng
