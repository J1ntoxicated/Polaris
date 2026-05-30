"""AI-conductor P0 SHADOW — durable shadow-decision log (append-only).

One ``gate_shadow_events`` row per gate execution that ran a deterministic
technical rule in parallel with the live GPT decision. The row captures both
decisions + a ``mismatch`` flag + the ``cell_warm`` / ``regime`` dimensions so
the next-session acceptance gate can analyse KILL-rate / PASS-rate / agreement
by regime and by cell warmth.

This is INSTRUMENTATION ONLY — it never influences the pipeline (behavior 0).
No-op when ``conn`` is None or the table is absent (mirrors
``gate_event_log.log_gate_event``); the live path must never crash on a log
failure.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import Any

from polaris.core.pipeline.agents._shadow_rules import ShadowDecision
from polaris.core.pipeline.gate_state import GateDecision

logger = logging.getLogger(__name__)

__all__ = ["fetch_shadow_events", "log_shadow_event"]


def log_shadow_event(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    signal_id: str | None,
    gate_id: int,
    venue: str,
    symbol: str,
    regime: str,
    technical: ShadowDecision,
    gpt_decision: GateDecision | None,
    cell_warm: bool,
) -> None:
    """Append one ``gate_shadow_events`` row (technical vs GPT + mismatch).

    ``mismatch`` is 1 when the technical decision differs from the GPT decision
    (and the GPT decision is known). A None ``gpt_decision`` (GPT error / no
    client) records mismatch=0 with an empty gpt label — the acceptance-gate
    query can exclude those.
    """
    if conn is None:
        return
    gpt_label = str(gpt_decision.value) if gpt_decision is not None else ""
    mismatch = (
        1 if (gpt_decision is not None and technical.decision != gpt_decision) else 0
    )
    flags = ",".join(technical.flags)
    try:
        conn.execute(
            """
            INSERT INTO gate_shadow_events
                (event_id, run_id, signal_id, gate_id, venue, symbol, regime,
                 technical_decision, technical_scalar, technical_reason,
                 technical_flags, gpt_decision, mismatch, cell_warm, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                run_id,
                signal_id,
                int(gate_id),
                venue,
                symbol,
                regime,
                str(technical.decision.value),
                float(technical.scalar),
                technical.reason,
                flags,
                gpt_label,
                mismatch,
                1 if cell_warm else 0,
                int(time.time()),
            ),
        )
    except sqlite3.Error as exc:
        # Shadow log must never crash the hot path; the live decision is GPT and
        # already returned. Surface the drop so a missing-table issue is visible.
        logger.warning(
            "[gate_shadow_events] log dropped gate_id=%s signal_id=%s: %r",
            gate_id, signal_id, exc,
        )
        return


def fetch_shadow_events(
    conn: sqlite3.Connection, *, gate_id: int | None = None
) -> list[dict[str, Any]]:
    """Read shadow rows (newest first); optional gate filter. Test/analysis use."""
    sql = (
        "SELECT event_id, run_id, signal_id, gate_id, venue, symbol, regime, "
        "technical_decision, technical_scalar, technical_reason, technical_flags, "
        "gpt_decision, mismatch, cell_warm, created_ts FROM gate_shadow_events"
    )
    params: tuple[Any, ...] = ()
    if gate_id is not None:
        sql += " WHERE gate_id = ?"
        params = (int(gate_id),)
    sql += " ORDER BY created_ts DESC, event_id DESC"
    cols = [
        "event_id", "run_id", "signal_id", "gate_id", "venue", "symbol", "regime",
        "technical_decision", "technical_scalar", "technical_reason",
        "technical_flags", "gpt_decision", "mismatch", "cell_warm", "created_ts",
    ]
    return [dict(zip(cols, row, strict=True)) for row in conn.execute(sql, params)]
