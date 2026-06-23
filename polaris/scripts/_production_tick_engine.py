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
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from polaris.core.live_recalc.exit_engine import (
    MfeProtectSchedule,
    tick_micro_broken,
)
from polaris.core.live_recalc.regime_flip import fetch_regime
from polaris.core.pipeline._sizer_payload import (
    _read_portfolio_state,
    _read_strategy_risk_state,
)
from polaris.core.regime_fit import exit_tightness, regime_fit
from polaris.core.sizing import SignalIntent, compute_size
from polaris.core.sizing.constants import production_default_equity_usd
from polaris.core.streams import resolve_stream
from polaris.core.ticks.config import (
    TICK_ENGINE_OWNED_VENUES,
    TickEngineConfig,
    regime_aware_confirm_cfg,
    venue_allowed_signals,
)
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
from polaris.scripts._production_probe_attach import observe_probes
from polaris.scripts._production_recalc import ActivePositionRow
from polaris.scripts._production_recalc_exit import (
    assess_mode_for_position,
    run_precise_exit,
)
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._smoke_roundtrip_shared import MIN_OKX_NOTIONAL_USD
from polaris.strategies.base import RawSignal

logger = logging.getLogger(__name__)

__all__ = [
    "TickEngineState",
    "run_tick_decision_loop",
]


def _env_pos_float(name: str, default: float) -> float:
    """Positive-float env override (``default`` for unset / non-positive / bad)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0.0 else default

# Phase 1 = OKX only (24/7, richest, long-only). Phase 2 adds Capital (long &
# short) + Alpaca (RTH). Aliased to the config SSOT ``TICK_ENGINE_OWNED_VENUES``
# so the engine and the bar pipeline read the SAME frozenset (no drift).
PHASE1_VENUES: frozenset[str] = TICK_ENGINE_OWNED_VENUES

# Venues that are LONG-ONLY (spot / cash equity). A 'short' intent on these is
# DROPPED at the risk gate (bidirectional rule) — Capital CFD (cfd) takes both.
_LONG_ONLY_PRODUCT_CLASSES: frozenset[str] = frozenset({"spot", "equity"})

# Tick signal_id of the order-flow-imbalance momentum signal (also the
# strategy_id stamped on the positions it opens) — the retune target.
_FLOW_PRESSURE = "flow_pressure"
_MICRO_REVERSION = "micro_reversion"
_BURST_RIDER = "burst_rider"

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

# The three pure signal functions, keyed by signal_id so the regime gate's
# active set selects which ones run this tick.
_SIGNAL_FNS: dict[str, Callable[..., TickIntent | None]] = {
    _BURST_RIDER: burst_rider,
    _FLOW_PRESSURE: flow_pressure,
    _MICRO_REVERSION: micro_reversion,
}

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

# A reversion (fast-scalp) position closes on the FIRST of: flow reversal (the
# book turned against the fade), a micro-stop (adverse R past this floor), or a
# small-R target (the snap-back banked). EXPECTANCY, not a throttle — it cuts a
# dead scalp fast and banks a small winner fast; it never reduces size or blocks
# an entry. R units share ``compute_unrealized_pnl_r``'s ATR-R basis.
_SCALP_TARGET_R = 0.5
_SCALP_STOP_R = -0.4

# Per-strategy reversion TAKE-PROFIT override (R) — PROFIT SIDE ONLY.
# [[harvest_generalization_2026-06-23]]: micro_reversion is the top harvest target
# (avg MFE +0.523R over 120 trades, realized only -0.148R). Its bounded
# revert-to-mean reached +0.30R for 57% and +0.45R for 44% of trades, yet the
# shared +0.50R scalp target sat ABOVE that mass — so the revert peaked and gave
# back before banking. A LOWER per-strategy target harvests the measured revert
# (BB-fade enters at the band extreme, targets the middle — a bounded gain, NOT
# let-winners-run). This ONLY moves the take-profit DOWN (banks sooner); the loss
# side (``_SCALP_STOP_R``) and the flow-reversal exit are UNTOUCHED — NOT a tighter
# loss cut / defensive throttle. Env-tunable; an unmapped reversion id keeps the
# shared ``_SCALP_TARGET_R`` default (byte-identical).
_MICRO_REVERSION_TARGET_R: float = _env_pos_float(
    "POLARIS_TICK_MICRO_REVERSION_TARGET_R", 0.35
)


def _scalp_target_for_strategy(strategy_id: str) -> float:
    """Reversion take-profit (R) for ``strategy_id`` — PROFIT side only.

    micro_reversion harvests its measured bounded revert at the lower
    ``_MICRO_REVERSION_TARGET_R``; every other reversion id keeps the shared
    ``_SCALP_TARGET_R`` default (byte-identical). Never touches the loss side.
    """
    if strategy_id == _MICRO_REVERSION:
        return _MICRO_REVERSION_TARGET_R
    return _SCALP_TARGET_R

# Per-strategy momentum exit trail width (ATR units) override for the precise
# exit, by tick signal_id. flow_pressure's favourable OFI drift was being closed
# too fast by the default 2.0-ATR Chandelier trail (honest fee-net retune
# w02ccvq0q: the longer-hold cohort was the only gross-cost-positive bucket), so
# it runs on a WIDER trail to capture the drift past the old ~75s scalp.
# EXPECTANCY, not a throttle: it only LOOSENS the running trail (lets the winner
# run); the ratchet, protected-BEP, loser-timeout and the G6 -1.0R hard rail (the
# loss-defence) are all untouched, and HARVEST still tightens. Env-tunable
# (``POLARIS_TICK_FLOW_PRESSURE_TRAIL_MULT``) so it can be re-aimed after the
# honest-slate re-measure. An unlisted signal_id → None → module default.
_FLOW_PRESSURE_TRAIL_MULT_DEFAULT = 4.0


def _momentum_trail_mult(strategy_id: str) -> float | None:
    """Let-winners-run ATR-trail override (ATR units) for a tick momentum strategy.

    flow_pressure runs on a WIDER trail so its favourable OFI drift is captured
    past the old fast scalp; every other tick momentum strategy (burst_rider)
    returns ``None`` → the module-default trail (byte-identical). Env-overridable.
    """
    if strategy_id != _FLOW_PRESSURE:
        return None
    raw = os.getenv("POLARIS_TICK_FLOW_PRESSURE_TRAIL_MULT")
    if raw is None or raw.strip() == "":
        return _FLOW_PRESSURE_TRAIL_MULT_DEFAULT
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _FLOW_PRESSURE_TRAIL_MULT_DEFAULT
    return val if val > 0.0 else _FLOW_PRESSURE_TRAIL_MULT_DEFAULT


# Tick MOMENTUM signals routed through ``run_precise_exit`` — BOTH now carry the
# MFE-protect harvest schedule. [[harvest_generalization_2026-06-23]]: the harvest
# was wired ONLY to flow_pressure; burst_rider (also momentum) passed
# mfe_protect=None, so its measured +0.3R excursions (27% reach +0.30R)
# round-tripped on the wide ATR trail. micro_reversion is REVERSION family (scalp
# exit, not run_precise_exit) → it is NOT here; it harvests via its scalp target.
# Per-strategy MFE-protect BASE rungs (bep_at_r, protect_at_r, lock_r) BEFORE the
# Seam3 regime-fit transform. Each momentum signal is calibrated to its OWN
# excursion mass, so the rungs are strategy-SCOPED — a re-aim of one MUST NOT
# bleed onto the other.
#   - flow_pressure: reads the cfg fields (the env-tunable SSOT). RE-AIMED to the
#     measured flow_pressure +MFE band 0.20/0.30/0.15 ([[flow_pressure_
#     continuation_gate_2026-06-24]]).
#   - burst_rider: its ORIGINAL rungs 0.35/0.50/0.25, calibrated to burst_rider's
#     own measured +0.3R excursion mass ([[harvest_generalization_2026-06-23]]);
#     pinned here so the flow_pressure re-aim leaves it byte-identical.
_BURST_RIDER_MFE_RUNGS: tuple[float, float, float] = (0.35, 0.50, 0.25)


def _mfe_protect_base_rungs(
    strategy_id: str, cfg: TickEngineConfig
) -> tuple[float, float, float] | None:
    """Per-strategy base MFE-protect rungs (bep_at_r, protect_at_r, lock_r), or None.

    flow_pressure → the env-tunable ``cfg`` SSOT; burst_rider → its own pinned
    original rungs. An unmapped id → ``None`` (no schedule). Scoped so a re-aim of
    one strategy's rungs leaves the other byte-identical.
    """
    if strategy_id == _FLOW_PRESSURE:
        return (cfg.mfe_bep_r, cfg.mfe_protect_r, cfg.mfe_protect_lock_r)
    if strategy_id == _BURST_RIDER:
        return _BURST_RIDER_MFE_RUNGS
    return None


def _mfe_protect_schedule(
    strategy_id: str, cfg: TickEngineConfig, *, regime: str | None = None
) -> MfeProtectSchedule | None:
    """MFE-protect harvest schedule for a tick MOMENTUM strategy, or ``None``.

    Both momentum tick signals (flow_pressure + burst_rider, routed through
    ``run_precise_exit``) ratchet the stop to BEP and lock positive R at their
    OWN per-strategy rungs (``_mfe_protect_base_rungs``) — capturing the measured
    +MFE excursion and cutting the give-back. The rungs are strategy-SCOPED:
    flow_pressure reads the cfg SSOT (re-aimed to its measured band), burst_rider
    keeps its own original 0.35/0.50/0.25 (calibrated to ITS excursion mass), so a
    re-aim of one leaves the other byte-identical. burst_rider was previously
    uncovered (→ None) so its +0.3R excursions round-tripped on the wide ATR trail;
    it now has its own schedule. micro_reversion (reversion family → scalp exit) →
    ``None`` here (it harvests via its scalp profit target). An unmapped id →
    ``None`` → byte-identical module-default exit. EXPECTANCY, not a throttle: the
    schedule only tightens the stop toward profit.

    Seam3 (regime-fit): a BAD momentum fit (chop churn = -1) TIGHTENS the harvest
    — the BEP/protect thresholds are pulled IN (divided by ``exit_tightness > 1``)
    so a mis-regime trade banks its excursion sooner; a GOOD fit LOOSENS them
    (LET_RUN). This only ratchets the protective stop TOWARD profit — it never
    reduces size, never blocks/vetoes entry, never loosens below the G6 -1.0R rail
    (run_precise_exit takes the protection-tighter of trail vs floor). The lock_r
    floor is preserved (a tightened schedule never locks LESS positive R).
    """
    rungs = _mfe_protect_base_rungs(strategy_id, cfg)
    if rungs is None:
        return None
    bep_r, protect_r, lock_r = rungs
    # Momentum family (only momentum signals reach here). exit_tightness > 1 on a
    # bad fit, < 1 on a good fit, 1.0 neutral / unknown regime → byte-identical.
    t = exit_tightness(regime_fit("momentum", regime))
    return MfeProtectSchedule(
        bep_at_r=bep_r / t,
        protect_at_r=protect_r / t,
        # Lock AT LEAST the configured positive R — a tighter schedule never banks
        # LESS (ratchet-toward-profit invariant); a looser fit keeps the base lock.
        lock_r=lock_r * max(1.0, t),
    )


def _flow_decay_exit(
    *, side: str, ofi: float | None, flow_confirmed: bool | None, pnl_r: float,
    mfe_gate_r: float,
) -> bool:
    """True iff a GREEN flow_pressure position should exit on OFI decay / failure.

    Once favourable excursion has appeared (``pnl_r >= mfe_gate_r``), trail on the
    MICROSTRUCTURE — not just price distance — so the +0.67R MFE is banked near the
    peak instead of round-tripping the wide ATR trail (the 17% give-back). Exits
    when the order-flow that drove the entry DECAYS or REVERSES against the side:
    the book imbalance flips against the position (``ofi`` sign opposes) OR the
    post-spike follow-through has FAILED (``flow_confirmed`` is False — bid
    withdrawal / microprice failure / spread widening). EXPECTANCY, not a throttle:
    a per-position close once green; never a size-cut, an entry-block, or a halt —
    and it never fires while the trade is red (the G6 -1.0R rail owns that).
    """
    if pnl_r < mfe_gate_r:
        return False
    if ofi is not None:
        if side == "long" and ofi < 0.0:
            return True
        if side == "short" and ofi > 0.0:
            return True
    return flow_confirmed is False

# Loop-heartbeat stall threshold: an iteration gap beyond ``cadence + this``
# means the loop was starved (e.g. a multi-minute learner full-DB backup /
# baseline rescan on the shared conn) and is logged as a WARNING on resume.
STALL_SLACK_SEC: float = 1.0


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
    # Consecutive-broken tick count per position for the adaptive-thesis SUSTAINED
    # gate: a single noisy tick must not flip a fresh winner to BROKEN, so the
    # re-map only treats the thesis as broken once the streak clears the floor.
    # Incremented when this tick's micro signal opposes the position, reset to 0
    # otherwise; popped on close (alongside family/entry_ref).
    thesis_broken_streak_by_position: dict[str, int] = field(default_factory=dict)
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
    # --- loop heartbeat (stall visibility) --------------------------------
    # ``loop_count`` increments every iteration; ``last_loop_mono`` is the
    # monotonic stamp at the top of the previous iteration. A gap between
    # successive ``last_loop_mono`` values that exceeds the cadence by more
    # than ``STALL_SLACK_SEC`` means the loop was starved (e.g. a 3-min
    # learner full-DB backup / baseline 7d rescan on the shared conn) and is
    # surfaced as a WARNING the moment the loop resumes.
    loop_count: int = 0
    last_loop_mono: float = 0.0


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
    # Seam2 (regime-fit): the flow_pressure ENTRY confirmation bar is regime-aware
    # — a bad momentum fit (chop churn) raises the post-spike follow-through floor
    # (a DELAY, not a veto); only ``flow_confirmed`` reads these knobs so the other
    # signals are untouched. flow_not_block: a strong genuine imbalance still clears.
    feat_cfg = regime_aware_confirm_cfg(eng.cfg, regime)
    logger.info(
        "[regime-fit/seam2-confirm] %s:%s regime=%s momo_fit=%+.2f "
        "confirm_ticks %d->%d confirm_ofi_frac %.3f->%.3f "
        "(entry confirmation DELAY, not a veto)",
        venue,
        symbol,
        regime,
        regime_fit("momentum", regime),
        eng.cfg.confirm_ticks,
        feat_cfg.confirm_ticks,
        eng.cfg.confirm_ofi_frac,
        feat_cfg.confirm_ofi_frac,
    )
    feat = compute_tick_features(window, now_ts, feat_cfg)
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
    # D3 routing: a signal must be BOTH regime-active AND structurally live on
    # this venue's feed. OKX (full depth) runs all three; Capital (price quotes
    # only) runs the overshoot fade only — the flow signals would just return
    # None there anyway (sizes/tape zeroed), so this only skips dead evals, never
    # blocks an edge (flow_not_block). An unlisted venue keeps the full set.
    active = active_signals(regime) & venue_allowed_signals(venue)
    logger.info(
        "[tick-gate/regime-active] %s:%s regime=%s active_signals=%s "
        "(membership only — regime is NOT a tradeable yes/no gate)",
        venue,
        symbol,
        regime,
        sorted(active),
    )
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
        if intent is None:
            logger.info(
                "[tick-gate/signal] %s:%s sig=%s verdict=NO_FIRE regime=%s "
                "(calm / sub-threshold / unconfirmed — DELAY, not a veto)",
                venue, symbol, signal_id, regime,
            )
            continue
        logger.info(
            "[tick-gate/signal] %s:%s sig=%s verdict=FIRE side=%s family=%s "
            "regime=%s",
            venue, symbol, signal_id, intent.side,
            intent.signal_family, regime,
        )
        # --- upstream long-only short gate (dead-path hygiene) ----------
        # A 'short' whose only candidate venue is long-only (OKX spot /
        # Alpaca equity) is UNEXECUTABLE — never build it. Moving the check
        # here (upstream of construction) stops the loop generating-then-
        # dropping it (the per-decision drop at _try_open is kept as a
        # backstop). Direction-neutral: removes no executable trade (spot/
        # equity shorts cannot be placed); a long is never gated. NOT a
        # flow_not_block violation — no tradeable edge is suppressed.
        if _drop_for_bidirectional(venue, intent.side):
            eng.drops_short += 1
            logger.debug(
                "[tick-engine] drop short %s:%s sig=%s (long-only venue, "
                "pre-construction)",
                venue, symbol, intent.signal_id,
            )
            continue
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


def _scalp_exit_decision(
    *,
    side: str,
    entry_price: float,
    last_mid: float,
    ofi: float | None,
    pnl_r: float,
    strategy_id: str = "",
) -> str | None:
    """Fast-scalp exit for a reversion position — close-reason or None (hold).

    Closes on the FIRST of:
      - ``flow_reversal``: order-flow imbalance turned to favour the side the
        fade was betting AGAINST continuing (a long fade wanted the down-push to
        exhaust; if ``ofi`` is now firmly negative again, the snap-back failed).
      - ``scalp_stop``: adverse excursion past ``_SCALP_STOP_R`` (micro-stop).
      - ``scalp_target``: the snap-back banked the strategy's take-profit (small
        R). ``strategy_id`` selects a per-strategy PROFIT target — micro_reversion
        harvests its measured bounded revert at the lower
        ``_MICRO_REVERSION_TARGET_R`` so the +0.30-0.45R excursion is banked
        before it gives back; every other reversion id keeps the shared
        ``_SCALP_TARGET_R`` (byte-identical). PROFIT side only.
    EXPECTANCY (cut a dead scalp fast / bank a small winner fast), NOT a throttle.
    The loss side (``_SCALP_STOP_R``) is UNCHANGED regardless of strategy_id.
    """
    if pnl_r <= _SCALP_STOP_R:
        return "scalp_stop"
    if pnl_r >= _scalp_target_for_strategy(strategy_id):
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
    alpaca_adapter: Any = None,
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
                ofi=feat.ofi, pnl_r=pnl_r, strategy_id=trade.strategy_id,
            )
            if reason is None:
                continue
            closed = await close_specific_position(
                conn, state=state, position_id=position_id, now_ts=now_ts,
                lookup_regime=lookup_regime, gpt_client=None, phase=phase,
                real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                close_reason=reason, cadence="tick",
            )
            if closed:
                eng.scalp_exits += 1
                eng.family_by_position.pop(position_id, None)
                eng.entry_ref_by_position.pop(position_id, None)
                eng.thesis_broken_streak_by_position.pop(position_id, None)
                logger.info(
                    "[tick-engine/scalp-exit] %s:%s trade_id=%s reason=%s "
                    "pnl_r=%.2f", trade.venue, trade.symbol, position_id,
                    reason, pnl_r,
                )
            continue
        # Momentum family → existing precise-exit engine (ATR trail / BEP /
        # loser-timeout). ``run_precise_exit`` resumes stop/peak/trough/FSM
        # from the freshest persisted positions row itself (never loosens the
        # bar recalc's ratchet); here we only read the entry-time ATR anchor
        # so the R-unit denominator (pnl_r/mfe_r/mae_r + FSM thresholds)
        # matches the bar recalc's ruler. NULL anchor / missing row → the
        # live tick-window denominator (legacy-graceful, pre-anchor).
        anchor_row = conn.execute(
            "SELECT entry_atr_pct, mfe_r, mae_r, entry_regime "
            "FROM positions WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        anchor_raw = anchor_row[0] if anchor_row is not None else None
        entry_atr_pct = None if anchor_raw is None else max(float(anchor_raw), 1e-4)
        row_mfe_r = (
            float(anchor_row[1]) if anchor_row and anchor_row[1] is not None else 0.0
        )
        row_mae_r = (
            float(anchor_row[2]) if anchor_row and anchor_row[2] is not None else 0.0
        )
        entry_regime = (
            str(anchor_row[3]) if anchor_row and anchor_row[3] is not None else None
        )
        if entry_atr_pct is not None:
            pnl_r = compute_unrealized_pnl_r(
                side=trade.side, entry_price=entry_price,
                last_price=last_mid, atr_pct=entry_atr_pct,
            )
        # flow_pressure EXIT precision: once GREEN, trail on the MICROSTRUCTURE —
        # exit near the peak the instant the OFI that drove the entry decays /
        # reverses (or the follow-through fails: bid withdrawal / microprice
        # failure / spread widening), instead of round-tripping the wide ATR
        # trail (the 17% give-back). EXPECTANCY, not a throttle — a per-position
        # close once green; the schedule-floored ATR stop below still owns the
        # price-distance + protected-R exit. Other momentum strategies skip this.
        if trade.strategy_id == _FLOW_PRESSURE:
            fp_window = (
                writer.feature_window(instrument_id) if writer is not None else []
            )
            fp_feat = compute_tick_features(fp_window, now_mono, eng.cfg)
            if _flow_decay_exit(
                side=trade.side, ofi=fp_feat.ofi,
                flow_confirmed=fp_feat.flow_confirmed, pnl_r=pnl_r,
                # Gate the OFI-decay banking at the PROTECT rung (0.30, near the
                # achievable peak) rather than BEP — [[flow_pressure_continuation_
                # gate_2026-06-24]]. The continuation entry gate keeps the trade
                # from buying a plateau top, so the decay exit can wait for a
                # genuine peak before banking on OFI failure. EXPECTANCY, not a
                # throttle: a per-position close once green; never a size/entry cut.
                mfe_gate_r=eng.cfg.mfe_protect_r,
            ):
                closed = await close_specific_position(
                    conn, state=state, position_id=position_id, now_ts=now_ts,
                    lookup_regime=lookup_regime, gpt_client=None, phase=phase,
                    real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
                    capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                    close_reason="flow_decay", cadence="tick",
                )
                if closed:
                    eng.family_by_position.pop(position_id, None)
                    eng.entry_ref_by_position.pop(position_id, None)
                    eng.thesis_broken_streak_by_position.pop(position_id, None)
                    logger.info(
                        "[tick-engine/flow-decay-exit] %s:%s trade_id=%s "
                        "pnl_r=%.2f ofi=%s confirmed=%s",
                        trade.venue, trade.symbol, position_id, pnl_r,
                        fp_feat.ofi, fp_feat.flow_confirmed,
                    )
                continue
        pos = ActivePositionRow()
        pos.update(
            {
                "position_id": position_id,
                "venue": trade.venue,
                "symbol": trade.symbol,
                "side": trade.side,
                "active_strategy_id": trade.strategy_id,
                "strategy": trade.strategy_id,
                # State resume is owned by run_precise_exit's fresh row read;
                # these caller fields only seed the no-row (orphan) fallback.
                "peak_price": None,
                "trough_price": None,
                "stop_price": None,
                "exit_state": "open",
            }
        )
        held_seconds = max(0, now_ts - int(trade.open_ts))
        # Adaptive thesis re-map (tick path): gather the FULL microstructure inputs
        # the tick engine has — ofi / flow_confirmed / burst_z + the live regime —
        # plus the persisted excursion + entry-regime anchor, and resolve a
        # ManagementMode. re-map OFF → None → byte-identical. EXIT timing only.
        mode_window = (
            writer.feature_window(instrument_id) if writer is not None else []
        )
        mode_feat = compute_tick_features(mode_window, now_mono, eng.cfg)
        try:
            tick_regime: str | None = lookup_regime(conn, trade.venue, trade.symbol)
        except Exception:  # noqa: BLE001 — regime read must never break the exit
            tick_regime = None
        # SUSTAINED gate: maintain the consecutive-broken streak so a single noisy
        # tick never flips a fresh winner to BROKEN. Increment when THIS tick's
        # micro signal opposes the position (deadband-gated), reset to 0 otherwise.
        mode_drift = _window_mid_drift(mode_window)
        if tick_micro_broken(
            side=trade.side, momentum_drift=mode_drift, ofi=mode_feat.ofi
        ):
            row_streak = eng.thesis_broken_streak_by_position.get(position_id, 0) + 1
        else:
            row_streak = 0
        eng.thesis_broken_streak_by_position[position_id] = row_streak
        mode = assess_mode_for_position(
            strategy_id=trade.strategy_id, side=trade.side,
            mfe_r=row_mfe_r, mae_r=row_mae_r, pnl_r=pnl_r,
            # Directional momentum = signed mid drift over the feature window
            # (newest-oldest)/oldest — a STABLE direction, not the noisy
            # instantaneous burst_z sign (an oscillation downtick must not flip a
            # drifting winner to BROKEN). 0.0 on an empty window → no break.
            momentum_drift=mode_drift,
            atr_slope=0.0,
            ofi=mode_feat.ofi, flow_confirmed=mode_feat.flow_confirmed,
            regime=tick_regime, entry_regime=entry_regime,
            held_seconds=held_seconds,
            horizon_seconds=max(held_seconds, 60),
            broken_streak=row_streak,
        )
        _ex_family = eng.family_by_position.get(position_id, "momentum")
        logger.info(
            "[regime-fit/seam3-exit] %s:%s trade_id=%s family=%s tick_regime=%s "
            "fit=%+.2f exit_tightness=%.3f mode=%s pnl_r=%.2f mfe_r=%.2f "
            "broken_streak=%d (harvest tighten = precise exit, ratchet-to-profit)",
            trade.venue, trade.symbol, position_id, _ex_family, tick_regime,
            regime_fit(_ex_family, tick_regime),
            exit_tightness(regime_fit(_ex_family, tick_regime)),
            mode, pnl_r, row_mfe_r, row_streak,
        )
        # ADR-012 / hardening #2 — PROBE → ENGINE → TUNING-LOG (OBSERVE-ONLY) on
        # the TICK exit pass, mirroring the bar recalc attach. Pure/sync, called
        # INLINE here (no await) so the FSM read-modify-write atomicity in
        # run_precise_exit below is untouched. Threads NOTHING into the exit
        # (mark_source='tick' is the only difference vs the bar attach) and is
        # fully fail-open — a None sidecar / raising probe / fail-open writer
        # never breaks the tick. The hard rails BYPASS this framework. This is
        # the dataset the rank-4 / rank-16 calibration readers consume.
        observe_probes(
            state=state, pos=pos, side=trade.side, entry_price=entry_price,
            last_price=last_mid, atr_pct=max(atr_pct, 1e-4),
            entry_atr_pct=entry_atr_pct, pnl_r=pnl_r, held_seconds=held_seconds,
            regime=tick_regime or "chop", now_ts=now_ts, run_id=uuid.uuid4().hex,
            mark_source="tick",
        )
        closed = await run_precise_exit(
            conn=conn, state=state, pos=pos, side=trade.side,
            entry_price=entry_price, last_price=last_mid, atr_pct=max(atr_pct, 1e-4),
            pnl_r=pnl_r, held_seconds=held_seconds, now_ts=now_ts,
            close_specific=close_specific_position, lookup_regime=lookup_regime,
            gpt_client=None, phase=phase, real_roundtrip=real_roundtrip,
            okx_adapter=okx_adapter, capital_session=capital_session,
            alpaca_adapter=alpaca_adapter, entry_atr_pct=entry_atr_pct, mode=mode,
            # flow_pressure rides a WIDER let-winners-run trail so favourable OFI
            # drift is captured past the old ~75s scalp; every other tick momentum
            # strategy → None → module-default trail (byte-identical).
            trail_mult=_momentum_trail_mult(trade.strategy_id),
            # flow_pressure also ratchets the stop to BEP at +MFE and locks
            # positive R (capturing the +0.67R avg MFE, cutting the give-back);
            # other momentum strategies → None → byte-identical. Seam3 (regime-fit):
            # a bad momentum fit (chop) tightens the harvest (bank sooner = precise
            # exit); good fit loosens (LET_RUN). Ratchets toward profit only.
            mfe_protect=_mfe_protect_schedule(
                trade.strategy_id, eng.cfg, regime=tick_regime
            ),
            # Hardening #7: this is the sub-second tick exit pass — tag the close
            # 'tick' so the close_reason × cadence rollup measures the bar-vs-tick
            # thesis-cut asymmetry. Measurement only; no exit-decision change.
            cadence="tick",
        )
        if closed:
            eng.family_by_position.pop(position_id, None)
            eng.entry_ref_by_position.pop(position_id, None)
            eng.thesis_broken_streak_by_position.pop(position_id, None)


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


def _window_mid_drift(window: Any) -> float:
    """Signed mid drift over the feature window: (newest - oldest) / oldest.

    The adaptive thesis re-map's ``momentum_drift`` input on the tick path — a
    STABLE directional signal (positive = price rose over the window) rather than
    the noisy instantaneous ``burst_z`` sign. Empty / single-tick / non-positive
    oldest → 0.0 (degrade safe → the re-map sees no momentum, never a break).
    """
    if not window or len(window) < 2:
        return 0.0
    try:
        first = float(window[0].mid)
        last = float(window[-1].mid)
    except (AttributeError, TypeError, ValueError):
        return 0.0
    if first <= 0.0:
        return 0.0
    return (last - first) / first


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
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                lookup_regime=_lookup_regime_str,
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
