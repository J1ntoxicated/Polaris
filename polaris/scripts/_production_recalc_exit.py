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
import os
import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.core.isolation.reentry import bar_seconds
from polaris.core.live_recalc.exit_engine import (
    EXIT_ADAPTIVE_THESIS_ON,
    EXIT_BAR_MFE_BEP_R,
    EXIT_BAR_MFE_LOCK_R,
    EXIT_BAR_MFE_PROTECT_R,
    EXIT_EQUITY_MFE_BEP_R,
    EXIT_EQUITY_MFE_LOCK_R,
    EXIT_EQUITY_MFE_PROTECT_R,
    EXIT_LOSER_TIMEOUT_SEC,
    EXIT_THESIS_GIVEBACK_ARM_R,
    EXIT_THESIS_GIVEBACK_FRAC,
    EXIT_THESIS_GIVEBACK_HARD_FRAC,
    Bucket,
    ExitState,
    ManagementMode,
    MfeProtectSchedule,
    ThesisGivebackParams,
    assess_thesis,
    bucket_from_correlation_group,
    evaluate_exit,
    init_exit_state,
)
from polaris.core.live_recalc.session_exit_rail import session_forced_exit
from polaris.core.streams import resolve_stream
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

# A resting venue stop is only cancelled+replaced when the trailing stop tightens
# by at least this fraction of the trigger price — steady ticks where the stop is
# unchanged (or loosens, which the ratchet forbids) are a no-op (no churn). This
# bounds the per-tick venue I/O to symbols whose stop actually moved.
_VENUE_STOP_REPLACE_EPS_FRAC: float = 0.0005  # 5 bps

# When the OKX algo endpoint reports the conditional stop is unavailable for a
# symbol, that result is cached on the trade for this long so the arm does NOT
# re-attempt every ~5s recalc tick (XRP-USDT logged ~2 arm attempts/sec → algo
# rate-limit risk). After the window the arm retries ONCE (the endpoint may have
# recovered); the software-polled stop is the backstop the whole time (fail-open
# UNCHANGED — never blocks the trade, never changes size). The marker lives on
# the per-position trade object, so it clears automatically when the position
# closes / changes (a fresh symbol carries no cooldown and still arms).
_VENUE_STOP_UNAVAIL_COOLDOWN_SEC: float = 300.0


async def arm_okx_venue_stop(
    *,
    trade: Any,
    okx_adapter: Any,
    side: str,
    stop_price: float | None,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Place / replace a VENUE-RESTING conditional stop on OKX for ``trade``.

    The software stop fires only on the next ~5s recalc tick; an OKX SPOT alt can
    gap through it in that window and the polled market close then fills far below
    (the -34..-100R orphan). This rests the stop AT the venue so OKX triggers it
    the instant the trigger crosses — closing the inter-tick gap. PRECISE-EXIT
    loss-defence, NOT a throttle: it never changes size, never blocks entry, and
    only mirrors the stop the FSM already computed for THIS position.

    Fully fail-open (flow_not_block): a missing adapter / non-OKX venue / no stop
    yet / any venue error leaves the in-memory ref untouched and the software
    stop as the backstop. Cancel-then-replace fires only when the stop TIGHTENED
    by ``_VENUE_STOP_REPLACE_EPS_FRAC`` (steady ticks are a no-op).
    """
    if okx_adapter is None or stop_price is None or stop_price <= 0.0:
        return
    if getattr(trade, "venue", "") != "okx":
        return
    base_qty = float(getattr(trade, "base_qty", 0.0) or 0.0)
    if base_qty <= 0.0:
        return
    prev_px = getattr(trade, "okx_stop_px", None)
    prev_algo = getattr(trade, "okx_stop_algo_id", None)
    # Replace only on a material tighten (SPOT long stop ratchets UP) — a steady
    # tick where the stop is unchanged (or loosens, which the ratchet forbids) is
    # a no-op so the per-tick venue I/O is bounded to symbols whose stop moved.
    if (
        prev_algo is not None
        and prev_px is not None
        and stop_price <= float(prev_px) * (1.0 + _VENUE_STOP_REPLACE_EPS_FRAC)
    ):
        return
    # Rate-defence: if the algo endpoint was already reported unavailable for this
    # symbol, skip the re-attempt until the cooldown elapses (no per-tick arm spam
    # → no OKX algo-order rate-limit risk). Software stop stays the backstop.
    unavail_until = getattr(trade, "okx_stop_unavail_until", None)
    if unavail_until is not None and now_monotonic() < float(unavail_until):
        return
    # OKX SPOT positions are long-only → the protective stop is a SELL.
    close_side = "sell" if side == "long" else "buy"
    try:
        if prev_algo is not None:
            with contextlib.suppress(Exception):
                await okx_adapter.cancel_algo_order(
                    inst_id=trade.symbol, algo_id=prev_algo
                )
        resp = await okx_adapter.place_conditional_stop(
            inst_id=trade.symbol,
            side=close_side,
            base_qty=base_qty,
            trigger_px=stop_price,
            client_order_id=f"polstop{(trade.position_id or trade.symbol)}",
        )
    except Exception as exc:  # noqa: BLE001 — resting stop is best-effort
        logger.warning(
            "[L6/exit] venue-stop arm failed %s — software stop holds: %r",
            getattr(trade, "symbol", "?"), exc,
        )
        return
    if resp.ok and resp.algo_id:
        trade.okx_stop_algo_id = resp.algo_id
        trade.okx_stop_px = stop_price
        # Endpoint healthy → clear any prior unavailable cooldown.
        trade.okx_stop_unavail_until = None
        logger.info(
            "[L6/exit] venue-stop armed %s algoId=%s slTriggerPx=%.6g",
            trade.symbol, resp.algo_id, stop_price,
        )
    else:
        # Algo endpoint unavailable for this symbol — cache the result so the arm
        # backs off instead of re-attempting every tick; keep the software stop.
        trade.okx_stop_unavail_until = (
            now_monotonic() + _VENUE_STOP_UNAVAIL_COOLDOWN_SEC
        )
        logger.info(
            "[L6/exit] venue-stop unavailable %s (%s) — software stop backstop, "
            "arm backed off %.0fs",
            trade.symbol, resp.code, _VENUE_STOP_UNAVAIL_COOLDOWN_SEC,
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0.0 else default


# Component B exit horizon ∝ timeframe. A still-OPEN losing position is given at
# least this many bars of its strategy timeframe before the stale-loser timeout
# can fire — so a 1H tsmom thesis is not force-closed at the flat 900s, while a
# 1m strategy keeps the short 900s timeout (max() floor below). EXPECTANCY (give
# the thesis its horizon), NOT a defensive throttle — the ATR-trail / MFE stops
# (the precise exits) are untouched. Env-overridable.
EXIT_LOSER_TIMEOUT_MIN_BARS: int = _env_int("POLARIS_EXIT_LOSER_TIMEOUT_MIN_BARS", 2)

# Named drift-backstop CEILING (POLARIS_LOSER_TIMEOUT_SEC, default 3600s = 1hr).
# A sideways-drifting dead-thesis loser is cut faster: the bar-scaled floor for a
# slow strategy (tsmom 1H → 2×3600=7200s) is capped here so a drifter is closed at
# ~1hr instead of sitting 2hr tying up capital. PRECISE-EXIT loss-defense
# (flow_not_block): it only shortens the sideways-drift timeout — the G6 −1R hard
# rail and the ATR-trail (which cut REAL fast losers far earlier) are untouched,
# and fast strategies / tick scalps (≤900s floor, already under the cap) are
# unaffected. Re-tunable via the env var.
LOSER_TIMEOUT_CAP_SEC: float = _env_float("POLARIS_LOSER_TIMEOUT_SEC", 3600.0)


def _loser_timeout_for_strategy(strategy_id: str) -> float:
    """Stale-loser drift-backstop timeout for ``strategy_id``.

    ``min(max(EXIT_LOSER_TIMEOUT_SEC, MIN_BARS × bar_seconds(timeframe)),
    LOSER_TIMEOUT_CAP_SEC)``: a fast strategy (1m) keeps the flat 900s; a slow
    thesis (tsmom 1H → floor 2×3600=7200s) is CAPPED at the named
    ``POLARIS_LOSER_TIMEOUT_SEC`` drift backstop (default 3600s) so a dead-thesis
    drifter is cut faster instead of sitting the full 2hr. An unregistered
    strategy_id falls back to the flat default (bar_seconds itself fails safe to
    300s for an unknown timeframe), also under the cap.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return min(EXIT_LOSER_TIMEOUT_SEC, LOSER_TIMEOUT_CAP_SEC)
    tf_floor = float(EXIT_LOSER_TIMEOUT_MIN_BARS * bar_seconds(cls.metadata.timeframe))
    return min(max(EXIT_LOSER_TIMEOUT_SEC, tf_floor), LOSER_TIMEOUT_CAP_SEC)


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


def _mfe_protect_for_strategy(strategy_id: str) -> MfeProtectSchedule | None:
    """MFE-protect harvest schedule for a REGISTERED bar strategy, or ``None``.

    [[harvest_generalization_2026-06-23]]: the harvest floor started equity-ONLY
    (asset_class=='equity'); the 9 other bar strategies passed
    ``run_precise_exit`` NO schedule, so below EXIT_FSM_PROTECT_R=1.0R the only
    floor was the wide 2-ATR trail. MEASURED on the live ledger: 93.5% of trades
    never reach +1.0R MFE (the dead PROTECT rung), yet 29.2% reach +0.30R and
    19.7% reach +0.45R — those excursions round-tripped (avg MFE +0.278R,
    realized -0.947R, giveback 1.225R). This now routes EVERY registered bar
    strategy — every ``asset_class`` (spot / fx / index / commodity / equity) —
    to a calibrated schedule so the +0.30R / +0.45R excursions become
    break-even-or-better exits:

      * equity keeps its OWN proven, separately-named rungs (byte-identical to
        the prior equity-only behaviour — the 0%-win 1D round-trips it fixed);
      * every other asset_class uses the shared bar default (the SAME proven
        +0.30R BEP / +0.45R protect / +0.20R lock, calibrated to the aggregate
        measured distribution — 29.2% reach +0.30R, 19.7% reach +0.45R).

    EXPECTANCY, not a throttle: it ONLY ratchets the stop toward profit (the
    let-winners-run ATR trail still runs ABOVE the floor; size / entry side / the
    G6 -1.0R rail are untouched). Tick-engine ids (flow_pressure / micro_reversion
    / burst_rider) are NOT registered here — they carry their OWN tick-path
    schedule — so an unregistered id → ``None`` → byte-identical pre-fix exit.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return None
    if cls.metadata.asset_class == "equity":
        return MfeProtectSchedule(
            bep_at_r=EXIT_EQUITY_MFE_BEP_R,
            protect_at_r=EXIT_EQUITY_MFE_PROTECT_R,
            lock_r=EXIT_EQUITY_MFE_LOCK_R,
        )
    return MfeProtectSchedule(
        bep_at_r=EXIT_BAR_MFE_BEP_R,
        protect_at_r=EXIT_BAR_MFE_PROTECT_R,
        lock_r=EXIT_BAR_MFE_LOCK_R,
    )


# Shared give-back thresholds for the adaptive thesis re-map (env-tunable; the
# module defaults are read once at import).
_THESIS_GIVEBACK = ThesisGivebackParams(
    arm_r=EXIT_THESIS_GIVEBACK_ARM_R,
    frac=EXIT_THESIS_GIVEBACK_FRAC,
    hard_frac=EXIT_THESIS_GIVEBACK_HARD_FRAC,
)


def _bucket_for_strategy(strategy_id: str) -> Bucket:
    """Exit archetype (TREND / REVERSION) for ``strategy_id`` from its registered
    ``correlation_group_id``. Unregistered / unknown → TREND (let-winners-run
    default — flow_not_block: never force an unmapped strategy tighter)."""
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return Bucket.TREND
    return bucket_from_correlation_group(cls.metadata.correlation_group_id)


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
) -> ManagementMode | None:
    """Resolve the adaptive thesis re-map mode for THIS position (or ``None``).

    Gathers the entry-thesis-health inputs and calls the pure ``assess_thesis``.
    Returns ``None`` when the re-map is globally OFF
    (POLARIS_EXIT_ADAPTIVE_THESIS) → the caller forwards ``mode=None`` →
    byte-identical. EXIT-timing only; never size / entry / the G6 -1.0R rail.
    Fully fail-open: any unexpected error degrades to ``None`` (no re-map) so a
    bad input can never break the exit pass.
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


def _session_calendar_for_venue(venue: str) -> str:
    """Resolve a venue's ``session_calendar`` (always_on / fx_indices_cal /
    us_equity_cal). An unknown venue degrades to ``always_on`` so the session
    rail NEVER fires for it (no spurious calendar flat) — A's behaviour and any
    unmapped stream stay byte-identical.
    """
    try:
        return resolve_stream(venue).session_calendar
    except (KeyError, ValueError):
        return "always_on"


async def run_session_forced_exit(
    *,
    conn: sqlite3.Connection,
    state: ProdLoopState,
    pos: ActivePositionRow,
    pnl_r: float,
    now_ts: int,
    close_specific: Callable[..., Any],
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    gpt_client: Any | None,
    phase: str,
    real_roundtrip: bool,
    okx_adapter: Any,
    capital_session: Any,
    alpaca_adapter: Any = None,
) -> bool:
    """Phase 3 per-stream session-close RAIL (CALENDAR INTEGRITY, not a P&L
    throttle). Keyed on the position's stream ``session_calendar``: ``always_on``
    (A) NEVER fires (byte-identical); ``fx_indices_cal`` (B) forces a flat when
    the weekend close is imminent OR the position survived a weekend close
    (stale-overnight) and is now in-session; ``us_equity_cal`` (C) forces a flat
    when the RTH close is imminent (no-overnight) OR the position survived an RTH
    close (a restart gap missed the pre-close flatten) and is now in-session.
    Fires on TIME ONLY — never on pnl / drawdown — and routes through the EXISTING
    ``close_specific_position`` (the close path is unchanged; this only ADDS a
    calendar-driven EXIT_NOW on top of the shared #26 FSM / G6 stop / G7
    widening). Returns ``True`` when this position was force-flattened (caller
    skips the rest of the exit/G6 pass).

    ``opened_ts`` (stale-overnight, threaded from the position row): the close
    can fill ONLY while in-session, so a bot-restart gap around the close misses
    the pre-close trigger → the position would hold across ≥1 calendar close. The
    rail re-arms the flatten at the next in-session open.
    """
    session_calendar = _session_calendar_for_venue(str(pos["venue"]))
    opened_ts_raw = pos.get("opened_ts")
    opened_ts = None if opened_ts_raw is None else int(opened_ts_raw)
    decision = session_forced_exit(
        session_calendar, now_ts, pnl_r=pnl_r, opened_ts=opened_ts,
    )
    if not decision.close:
        return False
    await close_specific(
        conn, state=state, position_id=str(pos["position_id"]), now_ts=now_ts,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        close_reason=decision.close_reason,
    )
    state.recalc_session_forced_exit = (
        getattr(state, "recalc_session_forced_exit", 0) + 1
    )
    _persist_session_exit_telemetry(
        conn, now_ts=now_ts, venue=str(pos["venue"]),
        symbol=str(pos.get("symbol", "")),
    )
    # Session-forced close (INFO): CALENDAR INTEGRITY flat (weekend / RTH close
    # imminent), TIME-only — never P&L. Log only; the flatten already happened.
    logger.info(
        "[L6/exit] close %s:%s trade_id=%s reason=%s calendar=%s pnl_r=%.2f",
        pos["venue"], pos.get("symbol", ""), pos["position_id"],
        decision.close_reason, session_calendar, pnl_r,
    )
    return True


def _persist_session_exit_telemetry(
    conn: sqlite3.Connection, *, now_ts: int, venue: str, symbol: str
) -> None:
    """Append one session-forced-exit row to ``loop_session_exit_events``.

    Display-only observability for the read-only dashboard — the flatten already
    happened via ``close_specific`` and ``state.recalc_session_forced_exit`` is
    the authoritative in-memory counter. Best-effort: any sqlite error (older
    schema lacking the table) is swallowed. Touches ONLY this isolated telemetry
    table; never positions/fills/sizing/gating.
    """
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT INTO loop_session_exit_events (ts, venue, symbol) "
            "VALUES (?,?,?)",
            (int(now_ts), str(venue), str(symbol)),
        )


def persist_exit_state(
    conn: sqlite3.Connection, *, position_id: str, st: ExitState,
) -> None:
    """Persist the tracked precise-exit state back to the ``positions`` row.

    Measurement + adaptive-stop state only — these columns NEVER gate sizing,
    NEVER block an entry, and NEVER trigger a P&L / strategy / bot halt.
    """
    conn.execute(
        "UPDATE positions SET stop_price = ?, peak_price = ?, "
        "trough_price = ?, mfe_r = ?, mae_r = ?, exit_state = ? "
        "WHERE position_id = ?",
        (
            st.stop_price, st.peak_price, st.trough_price,
            st.mfe_r, st.mae_r, st.exit_state, position_id,
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
    trail_mult: float | None = None,
    mfe_protect: MfeProtectSchedule | None = None,
    mode: ManagementMode | None = None,
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
    decision = evaluate_exit(
        prev=prev, side=side, entry_price=entry_price, last_price=last_price,
        atr_pct=atr_pct, pnl_r=pnl_r, held_seconds=held_seconds,
        loser_timeout_sec=_loser_timeout_for_strategy(strategy_id),
        profit_target_r=_profit_target_for_strategy(strategy_id),
        entry_atr_pct=entry_atr_pct,
        trail_mult=trail_mult,
        mfe_protect=effective_mfe_protect,
        mode=mode,
        thesis_bucket=_bucket_for_strategy(strategy_id),
        thesis_giveback=_THESIS_GIVEBACK,
    )
    persist_exit_state(conn, position_id=position_id, st=decision.state)
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
        close_reason=decision.close_reason,
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
