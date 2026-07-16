"""Historical (venue,strategy) x (symbol) realised edge — score_f_events read.

TRADING-domain, READ-ONLY: ``score_f_events`` + ``positions`` both live in
the trading DB (storage-split's domain boundary,
``polaris/storage/schema_marketdata.py`` module docstring — neither table is
in the marketdata list). One grouped query per (venue, symbol) shadow
evaluation; not cached (v1 — see delegation-gate-blueprint.md, revisit if
this becomes measurably hot, mirrors every other per-emission SHADOW read
already in the G2 dispatch loop, e.g. ``loss_cooldown_active``).
"""

from __future__ import annotations

import sqlite3

__all__ = ["fetch_historical_edge_bps"]


def fetch_historical_edge_bps(
    conn: sqlite3.Connection, *, venue: str, symbol: str
) -> dict[str, float]:
    """{strategy_id: gross_bps} realised over every closed lifecycle of this
    (venue, symbol) pair.

    ``score_f_events`` carries no symbol column (position_id-keyed only), so
    this joins ``positions`` for ``symbol``; ``score_f_events.strategy_id`` is
    used directly (no need for ``positions.strategy_id``, which can drift on
    a swapped position — the EVENT's own recorded strategy is the correct
    attribution). ``gross_bps = SUM(gross_usd) / SUM(notional_usd) * 10_000``;
    a strategy with no notional data (legacy pre fee-split-v0 rows — see
    schema_ddl_classes.py's DDL_SCORE_F_EVENTS docstring — or simply no
    closed lifecycle yet) is OMITTED from the dict. Callers treat a missing
    key as 0.0 / no-data (never fabricated); a query fault degrades to `{}`
    (degrade-never-crash — this is instrumentation, not the live entry path,
    but it still must never raise into the G2 dispatch loop)."""
    try:
        rows = conn.execute(
            """
            SELECT e.strategy_id, SUM(e.gross_usd), SUM(e.notional_usd)
            FROM score_f_events e
            JOIN positions p ON e.position_id = p.position_id
            WHERE e.venue = ? AND p.symbol = ?
            GROUP BY e.strategy_id
            """,
            (venue, symbol),
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, float] = {}
    for strategy_id, sum_gross, sum_notional in rows:
        if sum_gross is None or sum_notional is None or sum_notional <= 0:
            continue
        out[str(strategy_id)] = float(sum_gross) / float(sum_notional) * 10_000.0
    return out
