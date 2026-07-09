"""Opposite-aware entry — self-hedge detection at the G4/G5 entry seam.

Forensic (Jin 2026-07-10): US500 held long x2 + short x1 simultaneously —
``session_breakout`` entered long at 14:11 and the SAME strategy flipped
short on the SAME symbol at 14:17 (self-hedge). ``concurrent_same_side_open``
(:mod:`polaris.core.isolation.reentry`) only ever compares ``side = ?``, so an
OPPOSITE-side position was structurally invisible to every existing
entry-gating check — it neither counted against a cap nor triggered anything.

Two cases, both DEMO/PAPER + ``flow_not_block`` (the new entry is NEVER
blocked — this module only ever REACTS to an opposing position, it never
returns a verdict a caller could use to skip an entry):

* SAME strategy + SAME symbol + opposite side -> FLIP. The existing opposite
  position is the SAME strategy talking to itself (a reversed thesis) — the
  wire (:mod:`polaris.scripts._production_tick_opposing`) closes it first via
  the EXISTING close path (``exit_reason='signal_flip'``), then the new entry
  proceeds same-tick regardless of whether the close succeeded.
* DIFFERENT strategy + opposite side -> COEXIST. Two independent edges
  disagree on direction — both stay open (no cross-strategy veto); this is
  ALREADY today's behaviour (``concurrent_same_side_open`` never saw it), so
  the only change is instrumentation.

Both cases stamp a best-effort ``risk_events`` row (frequency/cost analysis
only — never read back into any trading decision). This module is PURE DB
read/write — no ``ProdLoopState``/``close_specific`` orchestration (that
lives in the scripts-layer wire, keeping core->scripts layering clean, same
precedent as ``reentry.py``'s virtual-cooldown factor).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Final, Literal, NamedTuple

logger = logging.getLogger(__name__)

__all__ = [
    "EVENT_COEXIST",
    "EVENT_FLIP",
    "OpposingPosition",
    "opposing_side_open_positions",
    "record_opposing_entry_event",
]

EVENT_FLIP: Final[str] = "opposing_entry_flip"
EVENT_COEXIST: Final[str] = "opposing_entry_coexist"


class OpposingPosition(NamedTuple):
    """A LIVE position on the opposite side of an incoming entry signal."""

    position_id: str
    strategy_id: str


def opposing_side_open_positions(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    side: Literal["long", "short"],
) -> list[OpposingPosition]:
    """LIVE positions on ``(venue, symbol)`` whose side OPPOSES ``side``.

    Mirrors ``concurrent_same_side_open``'s live-position definition
    (``status NOT IN ('closed','cancelled','reconciled')``) so this reads the
    exact same "live" set, just filtered on the opposite side instead of the
    same one. An OKX (long-only) venue never has a 'short' row to begin with,
    so this is a structural no-op there — no venue special-case needed. Any
    DB error is fail-open (empty list — the entry seam proceeds exactly as if
    no opposing position existed rather than wedging on a lookup failure).
    """
    opposite_side = "short" if side == "long" else "long"
    try:
        rows = conn.execute(
            "SELECT position_id, strategy_id FROM positions "
            "WHERE venue = ? AND symbol = ? AND side = ? "
            "AND status NOT IN ('closed','cancelled','reconciled')",
            (venue, symbol, opposite_side),
        ).fetchall()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("opposing-side lookup failed: %s", exc)
        return []
    return [OpposingPosition(str(r[0]), str(r[1])) for r in rows]


def record_opposing_entry_event(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    event_type: str,
    venue: str,
    symbol: str,
    side: str,
    opposing_position_id: str,
    opposing_strategy_id: str,
    now_ts: int,
    closed: bool | None = None,
) -> None:
    """Best-effort ``risk_events`` row for an opposite-side entry encounter.

    ``event_type`` is :data:`EVENT_FLIP` (same strategy, victim closed) or
    :data:`EVENT_COEXIST` (different strategy, both stay open). ``closed`` is
    the FLIP case's close-attempt outcome (``None`` for COEXIST, since no
    close is attempted there). Frequency/cost analysis only — never read back
    into any trading decision. Insert failure is swallowed (matches the
    project-wide best-effort ``risk_events`` insert idiom — see
    ``_production_close_helpers.py``); this NEVER blocks the caller's
    already-in-flight entry/close.
    """
    payload = json.dumps(
        {
            "venue": venue,
            "symbol": symbol,
            "side": side,
            "opposing_position_id": opposing_position_id,
            "opposing_strategy_id": opposing_strategy_id,
            "closed": closed,
        },
        separators=(",", ":"),
    )
    try:
        conn.execute(
            "INSERT INTO risk_events "
            "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, strategy_id, event_type, now_ts, payload),
        )
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("opposing-entry risk_events insert failed: %s", exc)
