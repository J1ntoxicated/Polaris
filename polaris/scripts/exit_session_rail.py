"""Per-stream session-close forced-exit rail — split out of ``_production_recalc_exit``.

Move-only extraction for the ≤500-LOC budget. ``run_session_forced_exit`` is the
Phase 3 calendar-integrity rail (weekend / RTH close imminent → force flat, TIME
only). ``_production_recalc_exit`` re-exports it so existing import paths keep
working.

CALENDAR INTEGRITY, not a P&L throttle: it fires on TIME ONLY (never on
pnl / drawdown) and routes through the EXISTING ``close_specific`` path.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.core.live_recalc.session_exit_rail import session_forced_exit
from polaris.core.streams import resolve_stream
from polaris.strategies import STRATEGY_REGISTRY

if TYPE_CHECKING:
    from polaris.scripts._production_recalc import ActivePositionRow
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


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


def _hold_overnight_for_strategy(strategy_id: str) -> bool:
    """Resolve a position's ``StrategyMetadata.hold_overnight`` (DEFAULT False —
    flatten). Mirrors ``reconcile_orphans._is_held_overnight``: True ONLY if the
    strategy is registered AND its metadata opts in; an unknown strategy_id
    degrades to False (flatten — the directive's base case, same as eod_flatten).
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return False
    return bool(getattr(getattr(cls, "metadata", None), "hold_overnight", False))


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

    ``hold_overnight`` (resolved from the position's ACTIVE strategy metadata,
    mirrors ``reconcile_orphans._is_held_overnight``'s eod_flatten opt-out): a
    strategy that opts IN to holding across the close (e.g. a 1D swing) is
    exempt from the ``us_equity_cal`` RTH-close pre-close + stale-overnight
    triggers — see ``session_forced_exit``. Default-False strategies keep the
    existing flatten behaviour byte-identical.
    """
    session_calendar = _session_calendar_for_venue(str(pos["venue"]))
    opened_ts_raw = pos.get("opened_ts")
    opened_ts = None if opened_ts_raw is None else int(opened_ts_raw)
    strategy_id = str(pos.get("active_strategy_id") or pos.get("strategy") or "")
    decision = session_forced_exit(
        session_calendar, now_ts, pnl_r=pnl_r, opened_ts=opened_ts,
        hold_overnight=_hold_overnight_for_strategy(strategy_id),
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
