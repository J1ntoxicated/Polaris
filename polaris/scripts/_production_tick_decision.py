"""Tick per-symbol decision + adapter + window helpers (extracted, move-only).

Split out of ``_production_tick_engine`` to keep each module ≤500 LOC. PURE /
in-mem helpers (no DB writes, no order I/O): the dedup/cooldown reads, the
conviction→strength map, the ``TickIntent``→``RawSignal`` adapter, the
long-only short gate, the feature→signal intent collector, and the live-tick
window math (ATR proxy + mid drift). Re-exported by ``_production_tick_engine``
so every existing import keeps working byte-for-byte.

NOTE: the SIZING + ORDER chain (``_sized_notional`` / ``_try_open``) stays in the
orchestrator on purpose — tests monkeypatch ``reserve_and_submit`` /
``_sized_notional`` on that module namespace, so moving them would break that
patch resolution (a behaviour change). Only no-patch-dependency helpers move here.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from polaris.core.regime_fit import regime_fit
from polaris.core.streams import resolve_stream
from polaris.core.ticks.config import (
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
from polaris.datastream import emit as datastream_emit
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick_mfe import (
    _BURST_RIDER,
    _FLOW_PRESSURE,
    _MICRO_REVERSION,
)
from polaris.scripts._production_tick_state import TickEngineState
from polaris.strategies.base import RawSignal

logger = logging.getLogger("polaris.scripts._production_tick_engine")

# Venues that are LONG-ONLY (spot / cash equity). A 'short' intent on these is
# DROPPED at the risk gate (bidirectional rule) — Capital CFD (cfd) takes both.
_LONG_ONLY_PRODUCT_CLASSES: frozenset[str] = frozenset({"spot", "equity"})

# The three pure signal functions, keyed by signal_id so the regime gate's
# active set selects which ones run this tick.
_SIGNAL_FNS: dict[str, Callable[..., TickIntent | None]] = {
    _BURST_RIDER: burst_rider,
    _FLOW_PRESSURE: flow_pressure,
    _MICRO_REVERSION: micro_reversion,
}


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
    # DATA → datastream sink, log-on-CHANGE: the confirm cfg only changes when
    # the regime changes, so emit once per (venue, symbol) regime transition
    # instead of once per tick (firehose). Logging-only; ``feat_cfg`` is already
    # computed and used unchanged below.
    if eng.ds_last_seam2_regime.get((venue, symbol)) != regime:
        eng.ds_last_seam2_regime[(venue, symbol)] = regime
        datastream_emit(
            "regime-fit/seam2-confirm",
            venue=venue,
            symbol=symbol,
            regime=regime,
            momo_fit=round(regime_fit("momentum", regime), 4),
            confirm_ticks_base=eng.cfg.confirm_ticks,
            confirm_ticks_eff=feat_cfg.confirm_ticks,
            confirm_ofi_frac_base=round(eng.cfg.confirm_ofi_frac, 4),
            confirm_ofi_frac_eff=round(feat_cfg.confirm_ofi_frac, 4),
            note="entry confirmation DELAY, not a veto",
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
    # DATA → datastream sink, log-on-CHANGE: the active-signal membership is
    # stable per (venue, symbol) regime, so re-logging it every tick is pure
    # firehose. Emit only when (regime, active set) changes. Logging-only —
    # ``active`` is the same set used by the loop below either way.
    active_sorted = tuple(sorted(active))
    if eng.ds_last_regime_active.get((venue, symbol)) != (regime, active_sorted):
        eng.ds_last_regime_active[(venue, symbol)] = (regime, active_sorted)
        datastream_emit(
            "tick-gate/regime-active",
            venue=venue,
            symbol=symbol,
            regime=regime,
            active_signals=list(active_sorted),
            note="membership only — regime is NOT a tradeable yes/no gate",
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
            # DATA → datastream sink, dedup on verdict CHANGE: a calm symbol
            # repeats NO_FIRE every tick (firehose). Emit only on the FIRE→
            # NO_FIRE transition. Logging-only; the ``continue`` is unchanged.
            if eng.ds_last_signal_verdict.get((venue, symbol, signal_id)) != "NO_FIRE":
                eng.ds_last_signal_verdict[(venue, symbol, signal_id)] = "NO_FIRE"
                datastream_emit(
                    "tick-gate/signal",
                    venue=venue,
                    symbol=symbol,
                    sig=signal_id,
                    verdict="NO_FIRE",
                    regime=regime,
                    note="calm / sub-threshold / unconfirmed — DELAY, not a veto",
                )
            continue
        # FIRE is rare + operationally salient (a tradeable intent) → keep on the
        # runtime log (operator surface). Record the verdict change for dedup.
        eng.ds_last_signal_verdict[(venue, symbol, signal_id)] = "FIRE"
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
