"""Component C (SHADOW) — entry-admission shadow log writer + reader.

One ``entry_admission_shadow`` row per entry-evaluation capturing the edge-first
admit / would_suppress decision computed net of the REAL round-trip fee. This is
INSTRUMENTATION ONLY — it never influences the pipeline (behavior 0). The live
admit/skip is unchanged; we add a log row so the next step can measure that the
rule would_suppress ONLY proven -EV warm setups before any cutover.

No-op when ``conn`` is None or the table is absent (mirrors
``pipeline.agents.shadow_log.log_shadow_event``); the live entry path must never
crash on a log failure.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import Any

from polaris.core.economics.entry_admission import EntryAdmissionDecision

logger = logging.getLogger(__name__)

__all__ = ["fetch_entry_admission_shadow", "log_entry_admission"]


def log_entry_admission(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    signal_id: str | None,
    venue: str,
    symbol: str,
    strategy_id: str,
    regime: str,
    cell_n_eff: float,
    cell_avg_pnl_r: float,
    expected_move_r: float,
    real_round_trip_cost_r: float,
    decision: EntryAdmissionDecision,
) -> None:
    """Append one ``entry_admission_shadow`` row (the admit / would_suppress decision).

    No-op when ``conn`` is None. A sqlite error (e.g. missing table on a legacy
    DB that has not been re-init'd) is logged and swallowed — the live entry
    decision already ran and must never crash on a shadow-log failure.
    """
    if conn is None:
        return
    try:
        conn.execute(
            """
            INSERT INTO entry_admission_shadow
                (event_id, run_id, signal_id, venue, symbol, strategy_id, regime,
                 cell_n_eff, cell_avg_pnl_r, expected_move_r,
                 real_round_trip_cost_r, admit, would_suppress, reason, basis,
                 created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                run_id,
                signal_id,
                venue,
                symbol,
                strategy_id,
                regime,
                float(cell_n_eff),
                float(cell_avg_pnl_r),
                float(expected_move_r),
                float(real_round_trip_cost_r),
                1 if decision.admit else 0,
                1 if decision.would_suppress else 0,
                decision.reason,
                decision.basis,
                int(time.time()),
            ),
        )
    except sqlite3.Error as exc:
        logger.warning(
            "[entry_admission_shadow] log dropped signal_id=%s: %r",
            signal_id, exc,
        )
        return


def fetch_entry_admission_shadow(
    conn: sqlite3.Connection, *, would_suppress: bool | None = None
) -> list[dict[str, Any]]:
    """Read admission shadow rows (newest first); optional would_suppress filter."""
    cols = [
        "event_id", "run_id", "signal_id", "venue", "symbol", "strategy_id",
        "regime", "cell_n_eff", "cell_avg_pnl_r", "expected_move_r",
        "real_round_trip_cost_r", "admit", "would_suppress", "reason", "basis",
        "created_ts",
    ]
    sql = f"SELECT {', '.join(cols)} FROM entry_admission_shadow"
    params: tuple[Any, ...] = ()
    if would_suppress is not None:
        sql += " WHERE would_suppress = ?"
        params = (1 if would_suppress else 0,)
    sql += " ORDER BY created_ts DESC, event_id DESC"
    return [dict(zip(cols, row, strict=True)) for row in conn.execute(sql, params)]
