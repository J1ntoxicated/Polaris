"""#26 precise-exit wiring — tick-loop adaptive stop / FSM / loser timeout.

Split out of ``_production_recalc.py`` for the ≤500-LOC budget. Owns the two
helpers that wire the pure ``polaris.core.live_recalc.exit_engine`` into the
per-position recalc pass: persist tracked state back to ``positions`` and run
one precise-exit evaluation (close-or-hold of THIS position only).

EXPECTANCY, not a defensive throttle: let winners run (ATR-trailing stop + MFE
harvest FSM), cut dead losers (round-trip break-even + stale-loser timeout).
NEVER reduces size, NEVER blocks an entry, NEVER adds a P&L / strategy / bot
halt. The G6 -1.0R hard ``stop_hit`` rail lives in the G6 gate and is untouched
— these precise exits ADD on top of it.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.core.live_recalc.exit_engine import (
    EXIT_ADAPTIVE_THESIS_ON,
    EXIT_THESIS_BROKEN_TICKS,
    EXIT_THESIS_GIVEBACK_ARM_R,
    EXIT_THESIS_GIVEBACK_FRAC,
    EXIT_THESIS_GIVEBACK_HARD_FRAC,
    ExitState,
    ManagementMode,
    MfeProtectSchedule,
    ThesisGivebackParams,
    assess_thesis,
    evaluate_exit,
    init_exit_state,
)

# Re-exported (move-only) with the redundant-alias idiom so these stay EXPLICIT
# module attributes (mypy no-implicit-reexport) for the external importers — they
# were used by ``_loser_timeout_for_strategy`` before it moved to core.
from polaris.core.live_recalc.exit_engine import (
    EXIT_LOSER_TIMEOUT_SEC as EXIT_LOSER_TIMEOUT_SEC,
)
from polaris.core.live_recalc.loser_timeout import (
    EXIT_LOSER_TIMEOUT_MIN_BARS as EXIT_LOSER_TIMEOUT_MIN_BARS,
)
from polaris.core.live_recalc.loser_timeout import (
    LOSER_TIMEOUT_CAP_SEC as LOSER_TIMEOUT_CAP_SEC,
)
from polaris.core.live_recalc.loser_timeout import (
    loser_timeout_for_strategy as _loser_timeout_for_strategy,
)
from polaris.core.metrics.risk_unit import STOP_ATR_MULT
from polaris.scripts._production_atr import strategy_timeframe

# Re-exported (move-only split) with redundant aliases so these resolve as
# EXPLICIT module attributes (mypy --strict no-implicit-reexport) for the
# existing import paths + run_precise_exit / _assess_mode_for_position global
# lookups — all byte-identical to the pre-split single module.
from polaris.scripts.exit_session_rail import (
    _persist_session_exit_telemetry as _persist_session_exit_telemetry,
)
from polaris.scripts.exit_session_rail import (
    _session_calendar_for_venue as _session_calendar_for_venue,
)
from polaris.scripts.exit_session_rail import (
    run_session_forced_exit as run_session_forced_exit,
)
from polaris.scripts.exit_strategy_config import (
    BAR_TREND_PEAK_LOCK_ARM_R as BAR_TREND_PEAK_LOCK_ARM_R,
)
from polaris.scripts.exit_strategy_config import (
    BAR_TREND_PEAK_LOCK_FRAC as BAR_TREND_PEAK_LOCK_FRAC,
)
from polaris.scripts.exit_strategy_config import (
    BAR_TREND_TRAIL_MULT as BAR_TREND_TRAIL_MULT,
)
from polaris.scripts.exit_strategy_config import (
    _bucket_for_strategy as _bucket_for_strategy,
)
from polaris.scripts.exit_strategy_config import (
    _mfe_protect_for_strategy as _mfe_protect_for_strategy,
)
from polaris.scripts.exit_strategy_config import (
    _stop_atr_mult_for_strategy as _stop_atr_mult_for_strategy,
)
from polaris.scripts.exit_strategy_config import (
    _trail_mult_for_strategy as _trail_mult_for_strategy,
)
from polaris.scripts.exit_venue_stop import arm_okx_venue_stop as arm_okx_venue_stop
from polaris.strategies import STRATEGY_REGISTRY

if TYPE_CHECKING:
    from polaris.scripts._production_recalc import ActivePositionRow
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

__all__ = [
    "arm_okx_venue_stop",
    "assess_mode_for_position",
    "persist_exit_state",
    "run_precise_exit",
    "run_session_forced_exit",
]


def assess_mode_for_position(**kw: Any) -> ManagementMode | None:
    """Public wrapper for the bar/tick callers to resolve the re-map mode."""
    return _assess_mode_for_position(**kw)


def _profit_target_for_strategy(strategy_id: str) -> float | None:
    """Take-profit target (R) for ``strategy_id``, or ``None`` for trend exits.

    Reads the strategy's declared ``metadata.profit_target_r``: a mean-reversion
    strategy (a BB fade) opts in to a fixed take-profit so the precise-exit engine
    harvests at the target instead of letting the wide ATR trail round-trip the
    bounded revert-to-mean. Every momentum strategy leaves it ``None`` →
    let-winners-run unchanged. An unregistered id falls back to ``None``.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return None
    return cls.metadata.profit_target_r


# Shared give-back thresholds for the adaptive thesis re-map (env-tunable; the
# module defaults are read once at import).
_THESIS_GIVEBACK = ThesisGivebackParams(
    arm_r=EXIT_THESIS_GIVEBACK_ARM_R,
    frac=EXIT_THESIS_GIVEBACK_FRAC,
    hard_frac=EXIT_THESIS_GIVEBACK_HARD_FRAC,
)

# Default consecutive-broken streak for callers that do NOT thread a per-tick
# count (the bar pipeline). A bar-close break is one observation on the slower
# bar cadence, so it counts as CONFIRMED — set to the broken-ticks floor so an
# AGED bar position with a broken thesis still CUTs (the SUSTAINED gate only
# guards the fast 500ms tick path from single-tick noise). The tick engine passes
# its own live streak and overrides this.
_DEFAULT_BROKEN_STREAK: int = EXIT_THESIS_BROKEN_TICKS


def _assess_mode_for_position(
    *,
    strategy_id: str,
    side: str,
    mfe_r: float,
    mae_r: float,
    pnl_r: float,
    momentum_drift: float,
    atr_slope: float,
    ofi: float | None,
    flow_confirmed: bool | None,
    regime: str | None,
    entry_regime: str | None,
    held_seconds: int,
    horizon_seconds: int,
    broken_streak: int = _DEFAULT_BROKEN_STREAK,
    native_bars_seen: int | None = None,
    native_bar_interval_seconds: int | None = None,
    horizon_bars: int | None = None,
    position_id: str | None = None,
) -> ManagementMode | None:
    """Resolve the adaptive thesis re-map mode for THIS position (or ``None``).

    Gathers the entry-thesis-health inputs and calls the pure ``assess_thesis``.
    Returns ``None`` when the re-map is globally OFF
    (POLARIS_EXIT_ADAPTIVE_THESIS) → the caller forwards ``mode=None`` →
    byte-identical. EXIT-timing only; never size / entry / the G6 -1.0R rail.
    Fully fail-open: any unexpected error degrades to ``None`` (no re-map) so a
    bad input can never break the exit pass.

    ``broken_streak``: the caller's consecutive-broken tick count for the SUSTAINED
    gate (a single noisy tick never flips a fresh winner to BROKEN). The tick engine
    threads its per-position streak; the bar-pipeline caller (no per-tick streak)
    leaves the default ``_DEFAULT_BROKEN_STREAK`` so a confirmed bar-close break
    still cuts an AGED position.

    ``timeframe`` ([[1d_exit_horizon_fix_2026-07-02]]): resolved from
    ``strategy_timeframe(strategy_id)`` (SAME lookup the ATR ruler uses) and
    forwarded so the horizon drift-materiality floor scales to THIS strategy's own
    bar cadence — unregistered/tick-engine ids resolve to "1m" (the pre-fix
    calibration, byte-identical).

    ``native_bars_seen`` / ``native_bar_interval_seconds`` / ``horizon_bars``
    ([[waveB_sizing_params_2026-07-02]] agenda 3): the bars-seen maturity gate —
    see ``exit_thesis._has_matured``. ``None`` (default) leaves that gate on the
    pre-existing wall-clock fallback → byte-identical for a caller that has not
    migrated yet.
    """
    if not EXIT_ADAPTIVE_THESIS_ON:
        return None
    try:
        return assess_thesis(
            side=side,
            bucket=_bucket_for_strategy(strategy_id),
            mfe_r=mfe_r,
            mae_r=mae_r,
            pnl_r=pnl_r,
            momentum_drift=momentum_drift,
            atr_slope=atr_slope,
            ofi=ofi,
            flow_confirmed=flow_confirmed,
            regime=regime,
            entry_regime=entry_regime,
            held_seconds=held_seconds,
            horizon_seconds=horizon_seconds,
            giveback=_THESIS_GIVEBACK,
            broken_streak=broken_streak,
            timeframe=strategy_timeframe(strategy_id),
            native_bars_seen=native_bars_seen,
            native_bar_interval_seconds=native_bar_interval_seconds,
            horizon_bars=horizon_bars,
            position_id=position_id,
        )
    except Exception as exc:  # noqa: BLE001 — re-map must never break the exit
        logger.warning("[L6/exit] thesis re-map assess failed — no re-map: %r", exc)
        return None


def _find_open_trade(state: Any, position_id: str) -> Any | None:
    """Return the live ``SimulatedTrade`` in ``state.open_trades`` whose
    ``position_id`` matches, or ``None``. The trade carries the in-memory
    venue-stop refs (algoId / px) the resting-stop arming reads + writes.
    """
    for t in getattr(state, "open_trades", ()):
        if getattr(t, "position_id", None) == position_id:
            return t
    return None


def persist_exit_state(
    conn: sqlite3.Connection, *, position_id: str, st: ExitState,
    stop_atr_mult: float | None = None,
) -> None:
    """Persist the tracked precise-exit state back to the ``positions`` row.

    Measurement + adaptive-stop state only — these columns NEVER gate sizing,
    NEVER block an entry, and NEVER trigger a P&L / strategy / bot halt.

    ``stop_atr_mult`` ([P1-8] observability, [[trade_mess_full_audit_2026-07-02]]):
    the resolved R-unit ATR multiplier THIS position's exit ruler is bound to
    (``_stop_atr_mult_for_strategy``) — stamped so a floor-bound / wide-ruler
    position is readable directly off the row. ``None`` (a caller that never
    resolves it) leaves the column untouched — byte-identical.
    """
    if stop_atr_mult is None:
        conn.execute(
            "UPDATE positions SET stop_price = ?, peak_price = ?, "
            "trough_price = ?, mfe_r = ?, mae_r = ?, exit_state = ? "
            "WHERE position_id = ?",
            (
                st.stop_price, st.peak_price, st.trough_price,
                st.mfe_r, st.mae_r, st.exit_state, position_id,
            ),
        )
        return
    conn.execute(
        "UPDATE positions SET stop_price = ?, peak_price = ?, "
        "trough_price = ?, mfe_r = ?, mae_r = ?, exit_state = ?, "
        "stop_atr_mult = ? WHERE position_id = ?",
        (
            st.stop_price, st.peak_price, st.trough_price,
            st.mfe_r, st.mae_r, st.exit_state, stop_atr_mult, position_id,
        ),
    )


async def run_precise_exit(
    *,
    conn: sqlite3.Connection,
    state: ProdLoopState,
    pos: ActivePositionRow,
    side: str,
    entry_price: float,
    last_price: float,
    atr_pct: float,
    pnl_r: float,
    held_seconds: int,
    now_ts: int,
    close_specific: Callable[..., Any],
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    gpt_client: Any | None,
    phase: str,
    real_roundtrip: bool,
    okx_adapter: Any,
    capital_session: Any,
    alpaca_adapter: Any = None,
    entry_atr_pct: float | None = None,
    trail_atr_pct: float | None = None,
    trail_mult: float | None = None,
    mfe_protect: MfeProtectSchedule | None = None,
    mode: ManagementMode | None = None,
    cadence: str = "bar",
) -> bool:
    """Track excursion + ratchet ATR stop + advance FSM; close if a precise
    exit fired. Returns ``True`` when this position was closed (caller skips
    the G6/G7 pass for it this tick). EXPECTANCY, not a throttle — per-position
    close-or-hold only; size + entry side untouched; G6 -1.0R rail stays.

    ``mode``: the adaptive thesis re-map ManagementMode (from
    ``_assess_mode_for_position``) forwarded to ``evaluate_exit``. ``None``
    (default / re-map OFF) → byte-identical; when set it re-maps the exit
    schedule (LET_RUN widens, HARVEST/CUT precise exits) EXIT-timing only.

    ``entry_atr_pct``: entry-time R anchor forwarded to ``evaluate_exit`` (the
    mfe/mae denominator). ``None`` (legacy NULL-anchor rows) keeps the
    current-ATR denominator — byte-identical pre-anchor.

    ``trail_atr_pct``: the stable bar-scale ATR for the TRAIL WIDTH forwarded to
    ``evaluate_exit`` ([[g7_tick_trail_atr_scale_2026-06-25]]). ``None`` (the bar
    recalc + every legacy caller) keeps the trail on ``atr_pct`` — byte-identical.
    The tick exit pass passes the entry-time anchor so the trail distance is
    measured on the bar risk, not the seconds-scale window range that clipped fresh
    winners at ~flat. EXIT-timing only — size / entry / the G6 -1.0R rail untouched.

    ``trail_mult``: per-position let-winners-run ATR-trail width forwarded to
    ``evaluate_exit``. ``None`` keeps the module default (every existing caller is
    byte-identical); the tick engine passes a WIDER value for flow_pressure
    momentum positions so favourable OFI drift runs past the old fast scalp. Only
    loosens the running trail — the ratchet, protected-BEP, loser-timeout and G6
    -1.0R rail (the loss-defence) are untouched.

    ``mfe_protect``: per-position MFE-protect schedule forwarded to
    ``evaluate_exit`` (flow_pressure EXIT precision). ``None`` keeps every existing
    caller byte-identical; for flow_pressure the stop ratchets to BEP at +MFE and
    locks positive R — capturing the +0.67R avg MFE and cutting the 17% give-back.
    Only TIGHTENS the stop (EXPECTANCY, not a throttle).

    ``cadence`` (hardening #7, measurement-only): the exit PASS that owns this
    call — 'bar' (the ~5s recalc, default) or 'tick' (the sub-second tick pass).
    Forwarded to ``close_specific`` → stamped on ``positions.exit_cadence`` for
    the close_reason × cadence rollup. Never alters any close/exit decision.
    """
    position_id = str(pos["position_id"])
    # Resume from the FRESHEST persisted state, not the caller's row: the bar
    # cycle snapshots ALL positions at cycle start and awaits G6/G7/close
    # network calls between positions, while the 500ms tick pass ratchets the
    # same row concurrently — evaluating from the stale snapshot would roll
    # the ratchet back. There is NO await between this read and the
    # persist_exit_state below, so the read-modify-write is atomic on the
    # event loop and the ratchet can never loosen. A missing row (synthetic /
    # replay callers) keeps the caller-supplied fields — prior behaviour.
    fresh = conn.execute(
        "SELECT stop_price, peak_price, trough_price, exit_state "
        "FROM positions WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if fresh is not None:
        stop_src, peak_src, trough_src, exit_state_src = fresh
    else:
        stop_src = pos.get("stop_price")
        peak_src = pos.get("peak_price")
        trough_src = pos.get("trough_price")
        exit_state_src = pos.get("exit_state")
    if peak_src is None:
        prev = init_exit_state(entry_price=entry_price, side=side)
    else:
        prev = ExitState(
            peak_price=float(peak_src),
            trough_price=float(trough_src or entry_price),
            stop_price=None if stop_src is None else float(stop_src),
            exit_state=str(exit_state_src or "open"),
        )
    # Component B exit horizon ∝ timeframe: scale the stale-loser timeout to the
    # position's ACTIVE strategy timeframe so a 1H thesis is not force-closed at
    # 900s. The ATR-trail / protected-BEP precise exits are untouched.
    strategy_id = str(pos.get("active_strategy_id") or pos.get("strategy") or "")
    # The bar-pipeline caller passes mfe_protect=None; the registered strategy
    # then routes into its harvest schedule by asset_class (equity keeps its own
    # proven rungs; every other asset_class uses the shared bar default — the
    # harvest floor the +0.3R/+0.45R round-trips lacked). An explicit caller
    # schedule (the tick engine's flow_pressure / burst_rider) is NEVER overridden.
    effective_mfe_protect = (
        mfe_protect
        if mfe_protect is not None
        else _mfe_protect_for_strategy(strategy_id)
    )
    # Bar TREND family widens the running trail (the tick engine threads its own
    # explicit trail_mult, never overridden here). A None caller trail → the
    # registered bar strategy's let-winners-run width (TREND 3.0, else module
    # default) ([[ab_letrun_maker_2026-06-24]]).
    effective_trail_mult = (
        trail_mult
        if trail_mult is not None
        else _trail_mult_for_strategy(strategy_id)
    )
    # FIX-EXIT ([[weekend_maker_honest_rerun_2026-06-28]]): the R-unit ATR
    # multiplier the mfe_r / mae_r excursion FSM is denominated in. The weekend OKX
    # makers widen to 3.0 (wider R-unit distance → a fixed-$ maker fee shrinks in R,
    # the bounded revert gets room); every other strategy keeps the SSOT 2.0 →
    # byte-identical. 🚨 The −1.0R rail coefficient (G6 monitor) is UNTOUCHED — only
    # the measurement unit widens (size / entry / the rail are never changed here).
    resolved_stop_atr_mult = _stop_atr_mult_for_strategy(
        strategy_id,
        atr_pct=atr_pct if entry_atr_pct is None else entry_atr_pct,
    )
    # [P1-8] observability ([[trade_mess_full_audit_2026-07-02]]): a non-SSOT
    # binding (weekend maker / any future widened ruler) is logged once per tick
    # so a floor-bound / wide-ruler position is traceable in the runtime log, not
    # just re-derivable from the stamped column below. Log-only — never gates.
    if resolved_stop_atr_mult != STOP_ATR_MULT:
        logger.debug(
            "[L6/exit] stop_atr_mult bind %s:%s trade_id=%s strategy=%s mult=%.2f "
            "(SSOT=%.2f)",
            pos["venue"], pos["symbol"], position_id, strategy_id,
            resolved_stop_atr_mult, STOP_ATR_MULT,
        )
    decision = evaluate_exit(
        prev=prev, side=side, entry_price=entry_price, last_price=last_price,
        atr_pct=atr_pct, pnl_r=pnl_r, held_seconds=held_seconds,
        loser_timeout_sec=_loser_timeout_for_strategy(strategy_id),
        profit_target_r=_profit_target_for_strategy(strategy_id),
        entry_atr_pct=entry_atr_pct,
        trail_atr_pct=trail_atr_pct,
        trail_mult=effective_trail_mult,
        mfe_protect=effective_mfe_protect,
        mode=mode,
        thesis_bucket=_bucket_for_strategy(strategy_id),
        thesis_giveback=_THESIS_GIVEBACK,
        stop_atr_mult=resolved_stop_atr_mult,
    )
    persist_exit_state(
        conn, position_id=position_id, st=decision.state,
        stop_atr_mult=resolved_stop_atr_mult,
    )
    # FSM state transition (DEBUG): surface the per-tick exit-state advance so
    # the dashboard can trace open→touched→protected→harvest. Logging only —
    # the persisted state above is the authority; this never gates anything.
    if decision.state.exit_state != prev.exit_state:
        logger.debug(
            "[L6/exit] fsm %s:%s trade_id=%s %s→%s mfe_r=%.2f mae_r=%.2f pnl_r=%.2f",
            pos["venue"], pos["symbol"], position_id,
            prev.exit_state, decision.state.exit_state,
            decision.state.mfe_r, decision.state.mae_r, pnl_r,
        )
    if not decision.close:
        # Loss-hole fix: rest the freshly-ratcheted stop AT the OKX venue so a
        # gap through it triggers venue-side in the inter-tick gap (not on the
        # next ~5s poll). Best-effort + fail-open; software stop is the backstop.
        if real_roundtrip and okx_adapter is not None:
            held = _find_open_trade(state, position_id)
            if held is not None:
                await arm_okx_venue_stop(
                    trade=held, okx_adapter=okx_adapter,
                    side=side, stop_price=decision.state.stop_price,
                )
        return False
    # Closing via the software path — cancel any resting venue stop FIRST so it
    # cannot fire a duplicate sell after this close fills (double-sell guard).
    if real_roundtrip and okx_adapter is not None:
        closing = _find_open_trade(state, position_id)
        if closing is not None and getattr(closing, "okx_stop_algo_id", None):
            with contextlib.suppress(Exception):
                await okx_adapter.cancel_algo_order(
                    inst_id=closing.symbol, algo_id=closing.okx_stop_algo_id
                )
            closing.okx_stop_algo_id = None
            closing.okx_stop_px = None
    await close_specific(
        conn, state=state, position_id=position_id, now_ts=now_ts,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        close_reason=decision.close_reason, cadence=cadence,
    )
    state.recalc_precise_exit = getattr(state, "recalc_precise_exit", 0) + 1
    # Precise-exit close (INFO): the decision/거동 visibility Jin asked for —
    # close_reason (protected_bep / atr_trail_stop / loser_timeout) + the R-unit
    # PnL the exit fired on, the venue/ticker, and how long it was held. The
    # realised pnl_usd/exit_price are logged in close_specific (the close path
    # owns the actual fill); this is the EXIT-TRIGGER reason record. Log only.
    logger.info(
        "[L6/exit] close %s:%s trade_id=%s reason=%s pnl_r=%.2f held=%ds "
        "fsm=%s side=%s",
        pos["venue"], pos["symbol"], position_id,
        decision.close_reason, pnl_r, held_seconds,
        decision.state.exit_state, side,
    )
    return True
