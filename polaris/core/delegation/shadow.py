"""Delegation-gate SHADOW log (P2b v1) — fit-score vs actual dispatch, behavior 0.

DEMO/PAPER only. Design SSOT: vault/50_research/delegation-gate-blueprint.md.
Hooked at the G2 signal-emit point (after ``persist_emitted_signal`` in
``_production_tick.py``'s dispatch loop): for the (venue, symbol) that just
emitted, ranks every SAME-VENUE registered strategy by
``polaris.core.delegation.fit_score`` and records whether the #1 pick matches
the strategy that ACTUALLY fired (dumb asset_class routing, unchanged this
version). INSTRUMENTATION ONLY — no gate/size/route decision reads this
table; the live dispatch that already happened is completely unaffected.

Promotion criteria (blueprint's "섀도우-후-승격" 2-stage plan): once
``delegation_shadow`` accumulates enough rows, a later /debate judges whether
the "fit-1위≠현재배정" slices' counterfactual performance (via
``score_f_events``, the same historical-edge source this module reads) beats
the dumb-routed baseline — ONLY THEN does fit-score graduate to live routing,
and only the AMBIGUOUS slice (margin < AMBIGUOUS_MARGIN_THRESHOLD) escalates
to an AI tie-break (gpt-5-mini) at that point. v1 makes ZERO AI calls.

Domain split: reads (``universe`` + the ``score_f_events``/``positions``
historical-edge join) are TRADING, read-only. The WRITE
(``delegation_shadow`` row) is MARKETDATA-domain, fire-and-forget via
``db_writer`` when wired — mirrors
``polaris.core.economics.price_through_shadow.log_price_through_entry``'s
dual direct-conn/db_writer path exactly.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.delegation.fit_score import (
    AMBIGUOUS_MARGIN_THRESHOLD,
    margin_of,
    rank_strategies,
)
from polaris.core.delegation.historical_edge import fetch_historical_edge_bps
from polaris.storage.db_writer import dbwriter_enabled
from polaris.strategies import STRATEGY_REGISTRY

if TYPE_CHECKING:
    from polaris.storage.db_writer import DBWriter

logger = logging.getLogger(__name__)

__all__ = ["fetch_delegation_shadow", "log_delegation_shadow"]

_DELEGATION_SHADOW_SQL = (
    "INSERT INTO delegation_shadow "
    "(event_id, run_id, signal_id, venue, symbol, dispatched_strategy_id, "
    " fit_top_strategy_id, fit_top_score, dispatched_score, margin, "
    " mismatch, ambiguous, regime, atr_pct, vol_24h_usd, spread_bps, "
    " candidate_n, created_ts) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_COLUMNS: tuple[str, ...] = (
    "event_id", "run_id", "signal_id", "venue", "symbol",
    "dispatched_strategy_id", "fit_top_strategy_id", "fit_top_score",
    "dispatched_score", "margin", "mismatch", "ambiguous", "regime",
    "atr_pct", "vol_24h_usd", "spread_bps", "candidate_n", "created_ts",
)


def _universe_row(
    conn: sqlite3.Connection, *, venue: str, symbol: str
) -> tuple[str, float, float]:
    """(asset_class, vol_24h_usd, spread_bps) off ``universe``; degrades to
    ("", 0.0, 0.0) on a missing row or a query fault (universe is trading-
    domain and indexed on its own (venue, symbol) primary key)."""
    try:
        row = conn.execute(
            "SELECT asset_class, vol_24h_usd, spread_bps FROM universe "
            "WHERE venue = ? AND symbol = ?",
            (venue, symbol),
        ).fetchone()
    except sqlite3.Error:
        return ("", 0.0, 0.0)
    if row is None:
        return ("", 0.0, 0.0)
    return (str(row[0] or ""), float(row[1] or 0.0), float(row[2] or 0.0))


def log_delegation_shadow(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    signal_id: str | None,
    venue: str,
    symbol: str,
    dispatched_strategy_id: str,
    regime: str,
    atr_pct: float,
    now_ts: int,
    write_conn: sqlite3.Connection | None,
    db_writer: DBWriter | None = None,
) -> None:
    """Compute the fit-score ranking for THIS (venue, symbol) and append ONE
    ``delegation_shadow`` row comparing it against ``dispatched_strategy_id``
    (the strategy that ACTUALLY fired). No-op when ``conn`` is None or
    neither a write path is wired (``write_conn`` nor ``db_writer``); a read
    or write fault is logged and swallowed — this must never crash the G2
    dispatch loop it is hooked into (degrade-never-crash, same contract as
    every other shadow logger in this codebase)."""
    if conn is None or (write_conn is None and db_writer is None):
        return
    candidates = [
        s.metadata for s in STRATEGY_REGISTRY.values() if s.metadata.venue == venue
    ]
    if not candidates:
        return
    try:
        ticker_asset_class, vol_24h_usd, spread_bps = _universe_row(
            conn, venue=venue, symbol=symbol
        )
        historical_edge_bps = fetch_historical_edge_bps(
            conn, venue=venue, symbol=symbol
        )
    except sqlite3.Error as exc:
        logger.warning(
            "[delegation_shadow] feature read dropped %s:%s: %r", venue, symbol, exc,
        )
        return
    ranked = rank_strategies(
        candidates,
        ticker_asset_class=ticker_asset_class,
        regime=regime,
        historical_edge_bps=historical_edge_bps,
    )
    if not ranked:
        return
    top = ranked[0]
    margin = margin_of(ranked)
    dispatched_score = next(
        (e.score for e in ranked if e.strategy_id == dispatched_strategy_id),
        0.0,
    )
    args: tuple[Any, ...] = (
        uuid.uuid4().hex, run_id, signal_id, venue, symbol,
        dispatched_strategy_id, top.strategy_id, float(top.score),
        float(dispatched_score), margin,
        1 if top.strategy_id != dispatched_strategy_id else 0,
        1 if (margin is not None and margin < AMBIGUOUS_MARGIN_THRESHOLD) else 0,
        regime, float(atr_pct), float(vol_24h_usd), float(spread_bps),
        len(ranked), int(now_ts),
    )
    try:
        if db_writer is not None and dbwriter_enabled():

            def _job(
                c: sqlite3.Connection,
                sql: str = _DELEGATION_SHADOW_SQL,
                a: tuple[object, ...] = args,
            ) -> None:
                c.execute(sql, a)

            db_writer.submit(_job, label="delegation_shadow")
        elif write_conn is not None:
            write_conn.execute(_DELEGATION_SHADOW_SQL, args)
    except sqlite3.Error as exc:
        logger.warning(
            "[delegation_shadow] write dropped %s:%s: %r", venue, symbol, exc,
        )


def fetch_delegation_shadow(
    conn: sqlite3.Connection, *, mismatch: bool | None = None
) -> list[dict[str, Any]]:
    """Read shadow rows (newest first); optional mismatch filter. Test/
    analysis use — mirrors every other ``fetch_*_shadow`` reader."""
    sql = f"SELECT {', '.join(_COLUMNS)} FROM delegation_shadow"
    params: tuple[Any, ...] = ()
    if mismatch is not None:
        sql += " WHERE mismatch = ?"
        params = (1 if mismatch else 0,)
    sql += " ORDER BY created_ts DESC, event_id DESC"
    return [dict(zip(_COLUMNS, row, strict=True)) for row in conn.execute(sql, params)]
