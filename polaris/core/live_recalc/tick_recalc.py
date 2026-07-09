"""Layer 6 — tick-triggered dirty mark + 5s cadence (P0 stub).

Spec source: vault/30_components/layer-6-live-recalc.md (Q1, Q5).

Per-tick *full* recalc was rejected (state churn > calc cost). Instead:
  - Tick handler marks the position dirty.
  - A 5s cadence sweeps dirty positions → recompute exit params.

This module is the **P0 stub**: ``mark_position_dirty`` and ``should_run_recalc``
are real (so the scheduler can be wired today), but ``recompute_exit_params``
returns a logging stub. Full Universal 3-layer formula = P1.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

LIVE_RECALC_CADENCE_SEC: Final[int] = 5
LIVE_RECALC_MAX_POSITIONS: Final[int] = 50
LIVE_RECALC_BATCH_P95_MS: Final[int] = 250
EXIT_OVERRIDE_COOLDOWN_SEC: Final[int] = 30
EXIT_OVERRIDE_MAX_PER_TRADE: Final[int] = 5
EXIT_DIRTY_PRICE_ATR_MULT: Final[float] = 0.25
EXIT_DIRTY_PNL_BOUNDARIES_R: Final[tuple[float, ...]] = (0.7, 1.5, 2.5)


@dataclass(slots=True)
class RecalcLogEntry:
    position_id: str
    reason: str
    decision: str  # "would_recalc" / "skipped_cooldown" / "skipped_clean"
    last_check_ts: int
    dirty_ts: int


def mark_position_dirty(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    reason: str,
    now_ts: int,
) -> None:
    """Set ``dirty_reason`` + ``dirty_ts`` on the position recalc state row.

    Idempotent: if already dirty with the same reason, no-op. If a different
    reason arrives, the most recent reason wins.
    """
    conn.execute(
        "INSERT INTO position_live_recalc_state "
        "(position_id, last_check_ts, dirty_reason, dirty_ts, cooldown_until_ts) "
        "VALUES (?, ?, ?, ?, 0) "
        "ON CONFLICT(position_id) DO UPDATE SET "
        " dirty_reason = excluded.dirty_reason, "
        " dirty_ts = excluded.dirty_ts",
        (position_id, now_ts, reason, now_ts),
    )


def mark_positions_dirty(
    conn: sqlite3.Connection,
    entries: Sequence[tuple[str, str, int]],
) -> None:
    """Batch form of ``mark_position_dirty`` — one multi-row INSERT for N
    positions instead of N single-row statements (writer-migration-completion
    design #3: collapses ≤50 lock acquisitions/recalc-cycle into 1).

    ``entries`` is a sequence of ``(position_id, reason, now_ts)`` — same
    fields as the individual-call form. Same last-reason-wins semantics,
    including WITHIN one batch: SQLite resolves a multi-row upsert's
    ``ON CONFLICT`` sequentially per row, so a later duplicate ``position_id``
    in ``entries`` overwrites an earlier one (matches calling
    ``mark_position_dirty`` N times in order).
    """
    if not entries:
        return
    placeholders = ", ".join(["(?, ?, ?, ?, 0)"] * len(entries))
    params: list[str | int] = []
    for position_id, reason, now_ts in entries:
        params.extend((position_id, now_ts, reason, now_ts))
    conn.execute(
        "INSERT INTO position_live_recalc_state "
        "(position_id, last_check_ts, dirty_reason, dirty_ts, cooldown_until_ts) "
        f"VALUES {placeholders} "
        "ON CONFLICT(position_id) DO UPDATE SET "
        " dirty_reason = excluded.dirty_reason, "
        " dirty_ts = excluded.dirty_ts",
        params,
    )


def should_run_recalc(
    state: dict[str, Any] | None,
    *,
    now_ts: int,
    cadence_sec: int = LIVE_RECALC_CADENCE_SEC,
) -> bool:
    """Decide whether the cadence allows a recalc *now*.

    Rules:
      - No state row → never run (only positions with dirty mark recalc).
      - dirty_ts unset → no.
      - last_check_ts within cadence and not dirty → no.
      - dirty mark present and outside cooldown → yes.
    """
    if state is None:
        return False
    dirty_ts = int(state.get("dirty_ts") or 0)
    if dirty_ts <= 0:
        return False
    cooldown_until = int(state.get("cooldown_until_ts") or 0)
    if cooldown_until > now_ts:
        return False
    last_check_ts = int(state.get("last_check_ts") or 0)
    if last_check_ts and (now_ts - last_check_ts) < cadence_sec:
        # within cadence floor — but dirty trigger always overrides.
        return True
    return True


def recompute_exit_params(  # P0 stub
    conn: sqlite3.Connection,
    *,
    position: dict[str, Any],
    now_ts: int,
) -> RecalcLogEntry:
    """P0 stub — log "would recalc" + clear dirty mark.

    P1 will implement Universal 3-layer formula:
      ``final = base × ticker_technical(live) × regime_mult``
    """
    pid = str(position["position_id"])
    state = _load_state(conn, pid)
    decision: str
    reason = (state.get("dirty_reason") if state else None) or "no_dirty"
    if not should_run_recalc(state, now_ts=now_ts):
        decision = "skipped_clean" if state and not state.get("dirty_ts") else "skipped_cooldown"
        return RecalcLogEntry(
            position_id=pid,
            reason=reason,
            decision=decision,
            last_check_ts=int((state or {}).get("last_check_ts") or 0),
            dirty_ts=int((state or {}).get("dirty_ts") or 0),
        )
    # mark check + clear dirty (P0 = log only).
    conn.execute(
        "UPDATE position_live_recalc_state "
        "SET last_check_ts = ?, dirty_reason = NULL, dirty_ts = NULL "
        "WHERE position_id = ?",
        (now_ts, pid),
    )
    return RecalcLogEntry(
        position_id=pid,
        reason=reason,
        decision="would_recalc",
        last_check_ts=now_ts,
        dirty_ts=int((state or {}).get("dirty_ts") or 0),
    )


async def run_live_recalc_cycle(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
    active_positions: list[dict[str, Any]],
    max_positions: int = LIVE_RECALC_MAX_POSITIONS,
) -> list[RecalcLogEntry]:
    """Sweep dirty positions, log decisions. P0 stub — no exit override emitted."""
    out: list[RecalcLogEntry] = []
    for pos in active_positions[:max_positions]:
        out.append(recompute_exit_params(conn, position=pos, now_ts=now_ts))
    if active_positions:
        n_recalc = sum(1 for e in out if e.decision == "would_recalc")
        n_clean = sum(1 for e in out if e.decision == "skipped_clean")
        n_cd = sum(1 for e in out if e.decision == "skipped_cooldown")
        logger.info(
            "[recalc] cycle ts=%d positions=%d would=%d clean=%d cooldown=%d",
            now_ts,
            len(out),
            n_recalc,
            n_clean,
            n_cd,
        )
    return out


def _load_state(conn: sqlite3.Connection, position_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT position_id, last_check_ts, last_override_ts, override_count, "
        "       dirty_reason, dirty_ts, cooldown_until_ts, last_seen_mid, "
        "       last_seen_unrealized_pnl_r, last_eval_regime, locked_widen "
        "FROM position_live_recalc_state WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "position_id": row[0],
        "last_check_ts": row[1],
        "last_override_ts": row[2],
        "override_count": row[3],
        "dirty_reason": row[4],
        "dirty_ts": row[5],
        "cooldown_until_ts": row[6],
        "last_seen_mid": row[7],
        "last_seen_unrealized_pnl_r": row[8],
        "last_eval_regime": row[9],
        "locked_widen": row[10],
    }


__all__ = [
    "EXIT_DIRTY_PNL_BOUNDARIES_R",
    "EXIT_DIRTY_PRICE_ATR_MULT",
    "EXIT_OVERRIDE_COOLDOWN_SEC",
    "EXIT_OVERRIDE_MAX_PER_TRADE",
    "LIVE_RECALC_BATCH_P95_MS",
    "LIVE_RECALC_CADENCE_SEC",
    "LIVE_RECALC_MAX_POSITIONS",
    "RecalcLogEntry",
    "mark_position_dirty",
    "mark_positions_dirty",
    "recompute_exit_params",
    "run_live_recalc_cycle",
    "should_run_recalc",
]
