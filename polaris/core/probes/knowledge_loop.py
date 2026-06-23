"""Knowledge-loop READ-consumer — offline digest of the probe tuning-log.

DEMO/PAPER · AGGRESSIVE · flow_not_block · in-loop GPT = 0.

The write side of the knowledge loop already exists: the ``probe_decisions``
sidecar (ADR-012) records the engine's would-be exit decision per tick with the
realized outcome backfilled at close, and the ``ai_lessons`` ledger records the
G8 cell-matrix delta per closed trade. Both were *write-only* — zero SELECT
readers, inert telemetry. This module is the missing READ side: it aggregates the
closed decisions per regime so an OFFLINE ``/debate`` calibration can MEASURE
would-be-tighten vs realized-giveback BEFORE any knob is touched.

Hard mandate guarantees (every one asserted by the tests):

  * **Read-only** — only ``SELECT``; no INSERT/UPDATE/DELETE. The loop observes;
    it never mutates the bot's state.
  * **Never auto-applied** — every :class:`KnowledgeHint` carries
    ``auto_apply=False`` and ``debate_gated=True`` (ADR-011 deterministic core;
    ADR-012 never-auto-applied). A human + ``/debate`` decides whether a knob
    moves; this module only surfaces the evidence.
  * **A digest, not a gate** — the hint is pure data (regime, sample count, mean
    realized R, mean giveback R). It has no ``tradeable`` / ``block`` field and
    cannot skip / size-cut / veto anything. flow_not_block preserved.

PURE read-side: no GPT, no live-DB writes, no network.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_MIN_CLOSED",
    "KnowledgeHint",
    "consume_probe_tuning_log",
]

# The default surfacing floor: a regime needs at least this many CLOSED
# decisions before the digest reports it. Below the floor the mean is too noisy
# to debate, so it is held back from the SURFACE only — this gates what the
# offline reader DISPLAYS, never a live trade (no entry/size effect whatsoever).
DEFAULT_MIN_CLOSED: Final[int] = 2


@dataclass(frozen=True, slots=True)
class KnowledgeHint:
    """One per-regime row of the offline knowledge digest (surface-only).

    Carries the measured would-be-decision-vs-outcome aggregate for a regime.
    ``auto_apply`` is always ``False`` and ``debate_gated`` always ``True`` — this
    is evidence for a ``/debate``, NEVER a knob the runtime applies on its own.
    """

    regime: str
    n_closed: int
    avg_realized_r: float
    avg_giveback_r: float
    auto_apply: bool = False
    debate_gated: bool = True


def consume_probe_tuning_log(
    conn: sqlite3.Connection,
    *,
    min_closed: int = DEFAULT_MIN_CLOSED,
) -> list[KnowledgeHint]:
    """Read the closed probe decisions and digest them per regime (read-only).

    Returns one :class:`KnowledgeHint` per regime that has at least
    ``min_closed`` CLOSED (``realized_pnl_r IS NOT NULL``) decisions, ordered by
    descending sample count then regime. FAIL-OPEN: a missing table / column or
    any ``sqlite3.Error`` yields ``[]`` (the digest is best-effort telemetry, not
    a runtime dependency).

    This function only ``SELECT``s — it never writes. The returned hints are
    surface-only (``auto_apply=False`` / ``debate_gated=True``); applying any of
    them is a separate, ``/debate``-gated human decision (ADR-011 / ADR-012).
    """
    try:
        rows = conn.execute(
            """
            SELECT regime,
                   COUNT(*)             AS n_closed,
                   AVG(realized_pnl_r)  AS avg_realized_r,
                   AVG(giveback_r)      AS avg_giveback_r
            FROM probe_decisions
            WHERE realized_pnl_r IS NOT NULL
            GROUP BY regime
            HAVING COUNT(*) >= ?
            ORDER BY n_closed DESC, regime ASC
            """,
            (min_closed,),
        ).fetchall()
    except sqlite3.Error:
        return []

    return [
        KnowledgeHint(
            regime=regime if regime is not None else "unknown",
            n_closed=int(n_closed),
            avg_realized_r=float(avg_realized_r) if avg_realized_r is not None else 0.0,
            avg_giveback_r=float(avg_giveback_r) if avg_giveback_r is not None else 0.0,
        )
        for regime, n_closed, avg_realized_r, avg_giveback_r in rows
    ]
