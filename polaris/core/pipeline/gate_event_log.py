"""Layer 2 — gate_events durable log (append-only).

The orchestrator's durability layer: one ``gate_events`` row per gate execution.
Split out of ``gate_orchestrator.py`` to keep each module ≤500 LOC;
``gate_orchestrator`` re-exports ``log_gate_event`` so existing import paths
(``from polaris.core.pipeline.gate_orchestrator import log_gate_event``) keep
working. Spec: vault/30_components/layer-2-per-gate-pipeline.md (Q2).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from polaris.core.pipeline.gate_state import GateContext, GateResult

logger = logging.getLogger(__name__)

__all__ = ["log_gate_event"]


def log_gate_event(
    conn: sqlite3.Connection | None,
    ctx: GateContext,
    result: GateResult,
) -> None:
    """Append one ``gate_events`` row.

    No-op when ``conn`` is None (test harness without DB) or when the schema
    doesn't include the table — the row is structured so the orchestrator
    keeps running even on log failure (Q2 — log is durability layer, not hot
    path).
    """
    if conn is None:
        return
    event_id = uuid.uuid4().hex
    phase = "fail" if result.error else "success"
    payload_json = json.dumps(_safe_payload(result.payload), separators=(",", ":"))
    try:
        conn.execute(
            """
            INSERT INTO gate_events
                (event_id, run_id, signal_id, position_id, gate_id, phase,
                 decision, model_used, latency_ms, payload_json, error_text, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                ctx.run_id,
                ctx.signal_id,
                ctx.position_id,
                int(ctx.gate_id),
                phase,
                str(result.decision.value),
                result.model_used,
                int(result.latency_ms),
                payload_json,
                result.error,
                int(time.time()),
            ),
        )
    except sqlite3.Error as exc:
        # Durable log must not crash the hot path. The schema may not include
        # gate_events yet (e.g. legacy DB). Caller has the in-memory results.
        # Surface the drop (was silent) so a missing-table / locked-DB issue is
        # visible instead of swallowed.
        logger.warning(
            "[gate_events] log dropped gate_id=%s signal_id=%s: %r",
            ctx.gate_id, ctx.signal_id, exc,
        )
        return


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip non-JSON-serializable values from payload (best-effort)."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = repr(v)[:200]
    return out
