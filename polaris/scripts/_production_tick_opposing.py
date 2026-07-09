"""Opposite-aware entry WIRE — the ``_run_tick`` orchestration layer.

Split out (mirrors ``_production_tick_decision.py`` / ``_production_tick_exit.py``)
so ``_production_tick.py`` gets only a short call at the entry seam.

Reacts to a LIVE opposite-side position on ``(venue, symbol)`` right before a
new entry proceeds (see :mod:`polaris.core.isolation.opposing_entry` for the
forensic + the pure DB helpers). ``flow_not_block``: :func:`handle_opposing_entries`
NEVER returns a verdict the caller could use to skip the entry — it only
reacts (close a same-strategy victim, or instrument a cross-strategy coexist)
and always lets the caller's entry continue.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any, Literal

from polaris.core.isolation.opposing_entry import (
    EVENT_COEXIST,
    EVENT_FLIP,
    opposing_side_open_positions,
    record_opposing_entry_event,
)
from polaris.scripts._production_state import ProdLoopState

logger = logging.getLogger(__name__)

__all__ = ["handle_opposing_entries"]


async def handle_opposing_entries(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: Literal["long", "short"],
    now_ts: int,
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    close_specific: Callable[..., Any],
    gpt_client: Any = None,
    phase: str = "P0",
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
) -> None:
    """React to LIVE opposite-side positions on ``(venue, symbol)``.

    SAME strategy -> FLIP: close the victim via ``close_specific``
    (``close_reason='signal_flip'``, the EXISTING close path — no new close
    mechanism). DIFFERENT strategy -> COEXIST: no close, instrumentation
    only (both positions stay open — this is already today's behaviour).
    Both cases stamp a best-effort ``risk_events`` row. This function never
    raises on a DB/close error (fail-open, matches the entry seam's other
    guards) and never blocks the caller's entry — it is a pure side-effect
    step the caller always falls through past.
    """
    for victim in opposing_side_open_positions(
        conn, venue=venue, symbol=symbol, side=side
    ):
        # The WHOLE per-victim reaction (close attempt + instrumentation) is
        # one fail-open unit — an exception ANYWHERE in here must never
        # propagate into ``_run_tick``'s signal loop, which has no wrapping
        # try/except of its own at this seam and would otherwise abort the
        # REST of this tick's signal processing, not just this one entry
        # (flow_not_block: this feature never widens the blast radius of a
        # failure beyond "this one reaction didn't complete"). The close
        # attempt gets its OWN inner guard so a close failure still lands a
        # ``closed=False`` telemetry row instead of silently dropping it.
        try:
            if victim.strategy_id == strategy_id:
                closed = False
                try:
                    closed = bool(
                        await close_specific(
                            conn, state=state, position_id=victim.position_id,
                            now_ts=now_ts, lookup_regime=lookup_regime,
                            gpt_client=gpt_client, phase=phase,
                            real_roundtrip=real_roundtrip,
                            okx_adapter=okx_adapter,
                            capital_session=capital_session,
                            alpaca_adapter=alpaca_adapter,
                            close_reason="signal_flip",
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — still instrument.
                    logger.warning(
                        "signal_flip close failed for %s (%s:%s strategy=%s): %s",
                        victim.position_id, venue, symbol, strategy_id, exc,
                    )
                record_opposing_entry_event(
                    conn, strategy_id=strategy_id, event_type=EVENT_FLIP,
                    venue=venue, symbol=symbol, side=side,
                    opposing_position_id=victim.position_id,
                    opposing_strategy_id=victim.strategy_id, now_ts=now_ts,
                    closed=closed,
                )
            else:
                record_opposing_entry_event(
                    conn, strategy_id=strategy_id, event_type=EVENT_COEXIST,
                    venue=venue, symbol=symbol, side=side,
                    opposing_position_id=victim.position_id,
                    opposing_strategy_id=victim.strategy_id, now_ts=now_ts,
                    closed=None,
                )
        except Exception as exc:  # noqa: BLE001 — never block the new entry.
            logger.warning(
                "opposing-entry reaction failed for %s (%s:%s strategy=%s): %s",
                victim.position_id, venue, symbol, strategy_id, exc,
            )
