"""Layer 1/2 — emitted-signal persistence to the ``signals`` table.

The ``signals`` table was never written on a real emit: G2 (strategy signal)
output was log-only, so the table sat EMPTY (0 rows) and the dashboard G2
visibility + the learner expectancy keyspace could not see what the strategies
emitted. ``persist_emitted_signal`` records each EMITTED raw signal (the decision
the strategy actually produced) so G2 output is queryable.

This is PURE OBSERVABILITY — it never gates, sizes, blocks, or skips. It is
FAIL-OPEN by contract: any persistence error is swallowed (logged at WARNING) so
a signals-table write can NEVER break the tick. A no-emit calls nothing, so the
table only ever carries real emits.
"""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

__all__ = ["persist_emitted_signal"]


def persist_emitted_signal(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str,
    strength: float,
    timeframe: str,
    ts: int,
    correlation_group: str | None = None,
    thesis: str = "",
) -> None:
    """Persist one EMITTED L1/L2 signal to the ``signals`` table (fail-open).

    Maps the emit onto the existing schema: ``instrument_id`` = ``venue:symbol``,
    ``direction`` = ``side``, ``score`` = ``strength``; ``venue`` / ``symbol`` /
    timeframe (``tf``) ride in ``payload_json`` so the row is fully queryable
    without a schema change. ``INSERT OR REPLACE`` on the PK
    ``(strategy_id, signal_id)`` is idempotent (a re-driven emit overwrites the
    same row, never double-counts). ANY ``sqlite3.Error`` is swallowed — a
    persistence failure logs a WARNING and returns; it NEVER blocks the tick.
    """
    payload = json.dumps(
        {"venue": venue, "symbol": symbol, "tf": timeframe, "side": side}
    )
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO signals (
                strategy_id, signal_id, instrument_id, correlation_group,
                direction, score, thesis, ts, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                signal_id,
                f"{venue}:{symbol}",
                correlation_group,
                side,
                float(strength),
                thesis,
                int(ts),
                payload,
            ),
        )
    except sqlite3.Error as exc:  # fail-open — observability never breaks the tick
        logger.warning(
            "emitted-signal persist failed (%s:%s strategy=%s): %s",
            venue, symbol, strategy_id, exc,
        )
