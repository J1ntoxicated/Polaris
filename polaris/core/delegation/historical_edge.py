"""Historical (venue,strategy) x (symbol) realised edge — score_f_events read.

TRADING-domain, READ-ONLY: ``score_f_events`` + ``positions`` both live in
the trading DB (storage-split's domain boundary,
``polaris/storage/schema_marketdata.py`` module docstring — neither table is
in the marketdata list). One grouped query per (venue, symbol) shadow
evaluation; not cached (v1 — see delegation-gate-blueprint.md, revisit if
this becomes measurably hot, mirrors every other per-emission SHADOW read
already in the G2 dispatch loop, e.g. ``loss_cooldown_active``).

Small-sample shrinkage: raw ``SUM(gross_usd)/SUM(notional_usd)`` lets a
SINGLE closed lifecycle saturate ``fit_score``'s history term (its largest
weight, W_HISTORY=0.5) — one +300bps trade would read identically to 1000
trades averaging +300bps. ``fetch_historical_edge_bps`` shrinks toward 0
(neutral, same "표본 없으면 중립" prior the module already applies at n=0) by
``n / (n + HISTORY_SHRINKAGE_K)`` before returning, so a thin sample can only
ever contribute a fraction of its raw magnitude.
"""

from __future__ import annotations

import sqlite3
from typing import Final

__all__ = ["HISTORY_SHRINKAGE_K", "fetch_historical_edge_bps"]

HISTORY_SHRINKAGE_K: Final[int] = 20
"""Shrinkage strength: at n=K closed lifecycles, raw bps is halved; n->inf
approaches the raw (unshrunk) value. Reuses the codebase's own existing
"not enough samples yet" boundary (Cold Start CS-3: n<20 -> Kelly off) rather
than introducing an unrelated fresh constant for the same underlying
small-sample-confidence concept."""


def fetch_historical_edge_bps(
    conn: sqlite3.Connection, *, venue: str, symbol: str
) -> dict[str, float]:
    """{strategy_id: shrunk_gross_bps} realised over every closed lifecycle of
    this (venue, symbol) pair.

    ``score_f_events`` carries no symbol column (position_id-keyed only), so
    this joins ``positions`` for ``symbol``; ``score_f_events.strategy_id`` is
    used directly (no need for ``positions.strategy_id``, which can drift on
    a swapped position — the EVENT's own recorded strategy is the correct
    attribution). ``raw_bps = SUM(gross_usd) / SUM(notional_usd) * 10_000``,
    then shrunk by ``n / (n + HISTORY_SHRINKAGE_K)`` (``n`` = COUNT of closed
    lifecycles / rows in the group — ``score_f_events.position_id`` is a
    PRIMARY KEY, i.e. exactly one row per lifecycle, so COUNT(*) is a true
    trade count, not an inflated per-fill count). A strategy with no notional
    data (legacy pre fee-split-v0 rows — see schema_ddl_classes.py's
    DDL_SCORE_F_EVENTS docstring — or simply no closed lifecycle yet) is
    OMITTED from the dict. Callers treat a missing key as 0.0 / no-data
    (never fabricated); a query fault degrades to `{}` (degrade-never-crash —
    this is instrumentation, not the live entry path, but it still must never
    raise into the G2 dispatch loop)."""
    try:
        rows = conn.execute(
            """
            SELECT e.strategy_id, SUM(e.gross_usd), SUM(e.notional_usd), COUNT(*)
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
    for strategy_id, sum_gross, sum_notional, n in rows:
        if sum_gross is None or sum_notional is None or sum_notional <= 0:
            continue
        raw_bps = float(sum_gross) / float(sum_notional) * 10_000.0
        shrink = float(n) / (float(n) + HISTORY_SHRINKAGE_K)
        out[str(strategy_id)] = raw_bps * shrink
    return out
