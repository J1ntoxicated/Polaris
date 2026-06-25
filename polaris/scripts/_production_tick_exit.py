"""Tick per-position EXIT pass (extracted, move-only).

Split out of ``_production_tick_engine`` to keep each module ≤500 LOC. This is
the per-tick exit pass (step 6): reversion-family → fast-scalp exit, momentum
family → the EXISTING ``run_precise_exit`` (ATR trail / protected-BEP /
loser-timeout) with the flow_pressure microstructure decay-gate + MFE-protect
harvest + wider let-winners-run trail threaded in. No monkeypatch-pinned symbol
is referenced here (the sizing/order entry chain stays in the orchestrator), so
this moves out byte-for-byte. Re-exported by ``_production_tick_engine``; the
logger NAME is pinned to the engine module so existing ``caplog`` captures and
log greps are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

from polaris.core.live_recalc.exit_engine import tick_micro_broken
from polaris.core.regime_fit import exit_tightness, regime_fit
from polaris.core.ticks.features import compute_tick_features
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_indicators import compute_unrealized_pnl_r
from polaris.scripts._production_probe_attach import observe_probes
from polaris.scripts._production_recalc import ActivePositionRow
from polaris.scripts._production_recalc_exit import (
    assess_mode_for_position,
    run_precise_exit,
)
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick_decision import (
    _window_atr_pct,
    _window_mid_drift,
)
from polaris.scripts._production_tick_mfe import (
    _FLOW_PRESSURE,
    _flow_decay_exit,
    _mfe_protect_schedule,
    _momentum_trail_mult,
    _scalp_exit_decision,
)
from polaris.scripts._production_tick_state import TickEngineState

logger = logging.getLogger("polaris.scripts._production_tick_engine")


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
            # G7 part 2 ([[g7_reversion_scalp_ruler_2026-06-25]]): re-anchor the
            # scalp pnl_r on the entry-time bar ATR — the SAME ruler the momentum
            # branch uses below (and the SAME the DB pnl_r/mfe_r record). The
            # seconds-scale _window_atr_pct above is ~90-190x smaller than the
            # entry-bar ATR, so the shared window pnl_r tripped scalp_target/
            # scalp_stop at ~0.35R on a real ~0.002R move — banking winners at
            # crumbs (flow_not_block violation). NULL anchor / missing row → keep
            # the live-window pnl_r (legacy-graceful, byte-identical pre-anchor).
            rev_anchor_row = conn.execute(
                "SELECT entry_atr_pct FROM positions WHERE position_id = ?",
                (position_id,),
            ).fetchone()
            rev_anchor_raw = (
                rev_anchor_row[0] if rev_anchor_row is not None else None
            )
            scalp_pnl_r = pnl_r
            if rev_anchor_raw is not None:
                scalp_pnl_r = compute_unrealized_pnl_r(
                    side=trade.side, entry_price=entry_price,
                    last_price=last_mid, atr_pct=max(float(rev_anchor_raw), 1e-4),
                )
            # #19 peak give-back: track the running max favourable R (the engine's
            # scalp exit is stateless) and feed it as ``peak_r`` so a reversion that
            # peaked near the target but reverses banks the locked fraction instead
            # of round-tripping. Monotone (only ratchets up), seeded at this tick's R.
            scalp_peak_r = max(
                eng.scalp_peak_r_by_position.get(position_id, scalp_pnl_r),
                scalp_pnl_r,
            )
            eng.scalp_peak_r_by_position[position_id] = scalp_peak_r
            reason = _scalp_exit_decision(
                side=trade.side, entry_price=entry_price, last_mid=last_mid,
                ofi=feat.ofi, pnl_r=scalp_pnl_r, strategy_id=trade.strategy_id,
                peak_r=scalp_peak_r,
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
                eng.scalp_peak_r_by_position.pop(position_id, None)
                logger.info(
                    "[tick-engine/scalp-exit] %s:%s trade_id=%s reason=%s "
                    "pnl_r=%.2f peak_r=%.2f", trade.venue, trade.symbol,
                    position_id, reason, scalp_pnl_r, scalp_peak_r,
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
            # G7 fix ([[g7_tick_trail_atr_scale_2026-06-25]]): anchor the TRAIL
            # WIDTH to the entry-time bar ATR (not the seconds-scale window range
            # passed as atr_pct) so the let-winners-run trail sits at the calibrated
            # bar scale instead of clipping a fresh winner at ~flat on the next tick.
            # NULL anchor (legacy rows) → None → the live-window trail (graceful).
            trail_atr_pct=entry_atr_pct,
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
