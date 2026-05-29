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

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.core.live_recalc.exit_engine import (
    ExitState,
    evaluate_exit,
    init_exit_state,
)

if TYPE_CHECKING:
    from polaris.scripts._production_recalc import ActivePositionRow
    from polaris.scripts.production_paper_loop import ProdLoopState

__all__ = ["persist_exit_state", "run_precise_exit"]


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
    decision = evaluate_exit(
        prev=prev, side=side, entry_price=entry_price, last_price=last_price,
        atr_pct=atr_pct, pnl_r=pnl_r, held_seconds=held_seconds,
    )
    persist_exit_state(conn, position_id=position_id, st=decision.state)
    if not decision.close:
        return False
    await close_specific(
        conn, state=state, position_id=position_id, now_ts=now_ts,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session,
    )
    state.recalc_precise_exit = getattr(state, "recalc_precise_exit", 0) + 1
    return True
