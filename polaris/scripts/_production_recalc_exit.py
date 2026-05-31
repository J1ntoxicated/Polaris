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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.core.isolation.reentry import bar_seconds
from polaris.core.live_recalc.exit_engine import (
    EXIT_LOSER_TIMEOUT_SEC,
    ExitState,
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
    "persist_exit_state",
    "run_precise_exit",
    "run_session_forced_exit",
]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


# Component B exit horizon ∝ timeframe. A still-OPEN losing position is given at
# least this many bars of its strategy timeframe before the stale-loser timeout
# can fire — so a 1H tsmom thesis is not force-closed at the flat 900s, while a
# 1m strategy keeps the short 900s timeout (max() floor below). EXPECTANCY (give
# the thesis its horizon), NOT a defensive throttle — the ATR-trail / MFE stops
# (the precise exits) are untouched. Env-overridable.
EXIT_LOSER_TIMEOUT_MIN_BARS: int = _env_int("POLARIS_EXIT_LOSER_TIMEOUT_MIN_BARS", 2)


def _loser_timeout_for_strategy(strategy_id: str) -> float:
    """Stale-loser timeout floor for ``strategy_id``, scaled to its timeframe.

    ``max(EXIT_LOSER_TIMEOUT_SEC, MIN_BARS × bar_seconds(timeframe))``: a fast
    strategy (1m) keeps the flat 900s; a slow thesis (tsmom 1H → 2×3600=7200s)
    earns its horizon. An unregistered strategy_id falls back to the flat
    default (bar_seconds itself fails safe to 300s for an unknown timeframe).
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return EXIT_LOSER_TIMEOUT_SEC
    tf_floor = float(EXIT_LOSER_TIMEOUT_MIN_BARS * bar_seconds(cls.metadata.timeframe))
    return max(EXIT_LOSER_TIMEOUT_SEC, tf_floor)


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
) -> bool:
    """Phase 3 per-stream session-close RAIL (CALENDAR INTEGRITY, not a P&L
    throttle). Keyed on the position's stream ``session_calendar``: ``always_on``
    (A) NEVER fires (byte-identical); ``fx_indices_cal`` (B) forces a flat when
    the weekend close is imminent; ``us_equity_cal`` (C) forces a flat when the
    RTH close is imminent (no-overnight). Fires on TIME ONLY — never on pnl /
    drawdown — and routes through the EXISTING ``close_specific_position`` (the
    close path is unchanged; this only ADDS a calendar-driven EXIT_NOW on top of
    the shared #26 FSM / G6 stop / G7 widening). Returns ``True`` when this
    position was force-flattened (caller skips the rest of the exit/G6 pass).
    """
    session_calendar = _session_calendar_for_venue(str(pos["venue"]))
    decision = session_forced_exit(session_calendar, now_ts, pnl_r=pnl_r)
    if not decision.close:
        return False
    await close_specific(
        conn, state=state, position_id=str(pos["position_id"]), now_ts=now_ts,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, close_reason=decision.close_reason,
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
) -> bool:
    """Track excursion + ratchet ATR stop + advance FSM; close if a precise
    exit fired. Returns ``True`` when this position was closed (caller skips
    the G6/G7 pass for it this tick). EXPECTANCY, not a throttle — per-position
    close-or-hold only; size + entry side untouched; G6 -1.0R rail stays.
    """
    position_id = str(pos["position_id"])
    tracked_peak = pos.get("peak_price")
    if tracked_peak is None:
        prev = init_exit_state(entry_price=entry_price, side=side)
    else:
        prev = ExitState(
            peak_price=float(tracked_peak),
            trough_price=float(pos.get("trough_price") or entry_price),
            stop_price=(
                None if pos.get("stop_price") is None
                else float(pos["stop_price"])
            ),
            exit_state=str(pos.get("exit_state") or "open"),
        )
    # Component B exit horizon ∝ timeframe: scale the stale-loser timeout to the
    # position's ACTIVE strategy timeframe so a 1H thesis is not force-closed at
    # 900s. The ATR-trail / protected-BEP precise exits are untouched.
    strategy_id = str(pos.get("active_strategy_id") or pos.get("strategy") or "")
    decision = evaluate_exit(
        prev=prev, side=side, entry_price=entry_price, last_price=last_price,
        atr_pct=atr_pct, pnl_r=pnl_r, held_seconds=held_seconds,
        loser_timeout_sec=_loser_timeout_for_strategy(strategy_id),
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
        return False
    await close_specific(
        conn, state=state, position_id=position_id, now_ts=now_ts,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, close_reason=decision.close_reason,
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
