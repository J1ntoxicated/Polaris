"""Layer 7 — per-strategy circuit breaker (4-state, deterministic).

Spec source: vault/30_components/layer-7-strategy-isolation.md (Q2).

States:
  - ACTIVE: normal.
  - SOFT_HALT: new entries blocked; auto-unblock after 15min (configurable).
  - HARD_HALT: new entries blocked; manual reset only.
  - RISK_ONLY: HALT with active positions, exit/stop-update only.

Triggers:
  - exception fault: 3 / 300s rolling window → HARD_HALT.
  - reject fault:    3 / 600s rolling window → SOFT_HALT.
  - NaN sizing:      1 occurrence            → HARD_HALT.
  - stale data:      timeframe-specific      → SOFT_HALT.

When a HALT fires while the strategy still owns open positions the mode is
recorded as RISK_ONLY so the executor knows it can manage exits but not entries.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE",
    "CB_EXCEPTION_THRESHOLD",
    "CB_HARD_HALT_AUTO_UNBLOCK_SEC",
    "CB_NAN_THRESHOLD",
    "CB_REJECT_THRESHOLD",
    "CB_SOFT_HALT_AUTO_UNBLOCK_SEC",
    "CB_STALE_DATA_BY_TIMEFRAME",
    "FAULT_EXCEPTION",
    "FAULT_NAN",
    "FAULT_REJECT",
    "FAULT_STALE_DATA",
    "HARD_HALT",
    "RISK_ONLY",
    "SOFT_HALT",
    "StrategyMode",
    "compute_4_state_transition",
    "current_strategy_mode",
    "record_fault",
    "reset_strategy_halt",
    "resume_stale_permanent_halts",
    "should_allow_new_entry",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

StrategyMode = Literal["ACTIVE", "SOFT_HALT", "HARD_HALT", "RISK_ONLY"]

ACTIVE: Final[StrategyMode] = "ACTIVE"
SOFT_HALT: Final[StrategyMode] = "SOFT_HALT"
HARD_HALT: Final[StrategyMode] = "HARD_HALT"
RISK_ONLY: Final[StrategyMode] = "RISK_ONLY"

FAULT_EXCEPTION: Final[str] = "exception"
FAULT_REJECT: Final[str] = "reject"
FAULT_NAN: Final[str] = "nan_sizing"
FAULT_STALE_DATA: Final[str] = "stale_data"

CB_EXCEPTION_THRESHOLD: Final[tuple[int, int]] = (3, 300)
CB_REJECT_THRESHOLD: Final[tuple[int, int]] = (3, 600)
CB_NAN_THRESHOLD: Final[tuple[int, int]] = (1, 0)
CB_STALE_DATA_BY_TIMEFRAME: Final[dict[str, int]] = {"1m": 75, "15m": 300, "1H": 900}
CB_SOFT_HALT_AUTO_UNBLOCK_SEC: Final[int] = 900
# HARD_HALT is a *bounded* halt, never a permanent block (flow_not_block, Jin
# 2026-06-22): after this cooldown the strategy auto-resumes and retries on the
# next signal. Loss-defense stays as precise exit — this is NOT a throttle.
# RISK_ONLY (open positions) is intentionally excluded: it is exit-only and stays
# manual-reset so we never re-arm NEW entries while a position is still live.
CB_HARD_HALT_AUTO_UNBLOCK_SEC: Final[int] = 1800


# ---------------------------------------------------------------------------
# Pure-function transition (property-test friendly)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FaultEvent:
    fault_type: str
    event_ts: int


def compute_4_state_transition(
    *,
    fault_type: str,
    rolling_faults: list[FaultEvent],
    now_ts: int,
    has_open_positions: bool = False,
    exception_threshold: tuple[int, int] = CB_EXCEPTION_THRESHOLD,
    reject_threshold: tuple[int, int] = CB_REJECT_THRESHOLD,
    nan_threshold: tuple[int, int] = CB_NAN_THRESHOLD,
) -> StrategyMode:
    """Pure transition: return the next mode given the latest fault and history.

    ``rolling_faults`` should already be filtered to the strategy in question
    and ordered by ``event_ts``. Threshold semantics: ``(count, window_sec)``;
    triggers when ``count`` events appear within the last ``window_sec``
    (inclusive). ``window_sec=0`` means "single event triggers".
    """
    if fault_type == FAULT_NAN:
        if _matches_threshold(rolling_faults, FAULT_NAN, nan_threshold, now_ts=now_ts):
            return RISK_ONLY if has_open_positions else HARD_HALT
        return ACTIVE

    if fault_type == FAULT_EXCEPTION:
        if _matches_threshold(rolling_faults, FAULT_EXCEPTION, exception_threshold, now_ts=now_ts):
            return RISK_ONLY if has_open_positions else HARD_HALT
        return ACTIVE

    if fault_type == FAULT_REJECT:
        if _matches_threshold(rolling_faults, FAULT_REJECT, reject_threshold, now_ts=now_ts):
            return RISK_ONLY if has_open_positions else SOFT_HALT
        return ACTIVE

    if fault_type == FAULT_STALE_DATA:
        # CONTRACT: callers (data freshness watcher in L1) MUST pre-threshold
        # stale-data faults using ``CB_STALE_DATA_BY_TIMEFRAME`` (75 s / 300 s /
        # 900 s by timeframe). When a fault reaches this function the staleness
        # window is already breached, so one event is sufficient to trip
        # SOFT_HALT (or RISK_ONLY when active positions exist).
        return RISK_ONLY if has_open_positions else SOFT_HALT

    # Unknown fault types stay ACTIVE — the supervisor is the only thing that
    # should be raising new fault types and silent ACTIVE here is safer than
    # spurious HARD_HALT churn.
    return ACTIVE


def _matches_threshold(
    history: list[FaultEvent],
    fault_type: str,
    threshold: tuple[int, int],
    *,
    now_ts: int,
) -> bool:
    count, window = threshold
    if count <= 0:
        return False
    if window <= 0:
        return sum(1 for f in history if f.fault_type == fault_type) >= count
    cutoff = now_ts - window
    relevant = [f for f in history if f.fault_type == fault_type and f.event_ts >= cutoff]
    return len(relevant) >= count


# ---------------------------------------------------------------------------
# SQLite-backed entry points
# ---------------------------------------------------------------------------


def record_fault(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    fault_type: str,
    now_ts: int,
    detail: dict[str, Any] | None = None,
    has_open_positions: bool = False,
) -> dict[str, Any]:
    """Record a fault event and (if thresholds tripped) open a HALT row.

    Severity is monotonic: a SOFT_HALT cannot downgrade an open HARD_HALT or
    RISK_ONLY (those are manual-reset only per the spec). A new HARD_HALT or
    RISK_ONLY *can* upgrade an existing SOFT_HALT (auto-resets the soft row).

    Returns ``{"mode": <StrategyMode>, "halt_id": Optional[str]}``.
    """
    detail = detail or {}
    event_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO strategy_fault_events
            (event_id, strategy_id, fault_type, event_ts, detail_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, strategy_id, fault_type, now_ts, json.dumps(detail, sort_keys=True)),
    )

    history = _load_recent_faults(conn, strategy_id, now_ts=now_ts)
    next_mode = compute_4_state_transition(
        fault_type=fault_type,
        rolling_faults=history,
        now_ts=now_ts,
        has_open_positions=has_open_positions,
    )

    if next_mode == ACTIVE:
        return {"mode": current_strategy_mode(conn, strategy_id, now_ts=now_ts), "halt_id": None}

    # Monotonic severity guard: SOFT cannot downgrade an open HARD/RISK_ONLY.
    current = current_strategy_mode(conn, strategy_id, now_ts=now_ts)
    if next_mode == SOFT_HALT and current in (HARD_HALT, RISK_ONLY):
        return {"mode": current, "halt_id": None}

    # When upgrading SOFT → HARD/RISK_ONLY, auto-close the soft halt so
    # `current_strategy_mode` resolves cleanly to the more severe row.
    if next_mode in (HARD_HALT, RISK_ONLY) and current == SOFT_HALT:
        conn.execute(
            """
            UPDATE strategy_halts SET reset_ts = ?, reset_by = 'upgrade'
            WHERE strategy_id = ? AND reset_ts IS NULL AND mode = ?
            """,
            (now_ts, strategy_id, SOFT_HALT),
        )

    halt_id = _open_halt(
        conn,
        strategy_id=strategy_id,
        mode=next_mode,
        reason_code=fault_type,
        now_ts=now_ts,
        detail=detail,
    )
    logger.warning(
        "[circuit] strategy=%s fault=%s → mode=%s halt_id=%s",
        strategy_id,
        fault_type,
        next_mode,
        halt_id,
    )
    return {"mode": next_mode, "halt_id": halt_id}


def current_strategy_mode(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    now_ts: int,
) -> StrategyMode:
    """Resolve the live strategy mode with monotonic severity.

    Walk every open halt row for the strategy, auto-expire stale SOFT_HALT
    rows, then return the most-severe mode still open. Severity ordering:
    HARD_HALT == RISK_ONLY > SOFT_HALT > ACTIVE. RISK_ONLY is treated as a
    HARD-tier mode for entry-block decisions but is reported separately so
    callers can run the exit-only path.
    """
    rows = conn.execute(
        """
        SELECT halt_id, mode, unblock_ts
        FROM strategy_halts
        WHERE strategy_id = ? AND reset_ts IS NULL
        """,
        (strategy_id,),
    ).fetchall()
    if not rows:
        return ACTIVE

    has_hard = False
    has_risk_only = False
    has_soft = False
    for halt_id, mode, unblock_ts in rows:
        mode_str = str(mode)
        if mode_str == SOFT_HALT:
            if unblock_ts is not None and now_ts >= int(unblock_ts):
                conn.execute(
                    "UPDATE strategy_halts SET reset_ts = ?, reset_by = 'auto' WHERE halt_id = ?",
                    (now_ts, halt_id),
                )
                continue
            has_soft = True
        elif mode_str == HARD_HALT:
            # Bounded HARD_HALT auto-resume (flow_not_block): a HARD_HALT now
            # carries an unblock_ts; once it elapses the strategy resumes and
            # retries on the next signal — never a permanent block. A legacy
            # row with unblock_ts=None stays blocked until the startup migration
            # (resume_stale_permanent_halts) makes it resumable.
            if unblock_ts is not None and now_ts >= int(unblock_ts):
                conn.execute(
                    "UPDATE strategy_halts SET reset_ts = ?, reset_by = 'auto' WHERE halt_id = ?",
                    (now_ts, halt_id),
                )
                continue
            has_hard = True
        elif mode_str == RISK_ONLY:
            has_risk_only = True
        # Unknown mode strings are skipped — schema invariant guards the writer.

    if has_hard:
        return HARD_HALT
    if has_risk_only:
        return RISK_ONLY
    if has_soft:
        return SOFT_HALT
    return ACTIVE


def should_allow_new_entry(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    now_ts: int,
) -> bool:
    """True only when the strategy is ACTIVE — every halt mode blocks new entries."""
    return current_strategy_mode(conn, strategy_id, now_ts=now_ts) == ACTIVE


def resume_stale_permanent_halts(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
    cooldown_sec: int = CB_HARD_HALT_AUTO_UNBLOCK_SEC,
) -> int:
    """One-time migration: make legacy permanent HARD_HALT rows resumable.

    Before the bounded-auto-resume change, a HARD_HALT was written with
    ``unblock_ts=None`` (permanent block, manual reset only) — which silently
    stalled entries forever (the tsmom/THETA-USDT & rsi_bb_pullback/CRV-USDT
    stall). This converts every still-open HARD_HALT with ``unblock_ts IS NULL``
    to ``unblock_ts = opened_ts + cooldown_sec`` so it auto-resumes like a
    freshly-written bounded halt (flow_not_block). Idempotent: rows that already
    carry an unblock_ts, are reset, or are RISK_ONLY (exit-only, manual) are
    left untouched. Returns the number of rows converted.
    """
    cur = conn.execute(
        """
        UPDATE strategy_halts
        SET unblock_ts = opened_ts + ?
        WHERE mode = ? AND reset_ts IS NULL AND unblock_ts IS NULL
        """,
        (cooldown_sec, HARD_HALT),
    )
    rows = cur.rowcount or 0
    if rows:
        logger.warning(
            "[circuit] migration: converted %d permanent HARD_HALT row(s) to "
            "bounded auto-resume (cooldown=%ds)",
            rows,
            cooldown_sec,
        )
    return rows


def reset_strategy_halt(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    operator: str,
    now_ts: int,
) -> int:
    """Manually reset every open HALT row for the strategy. Returns rows touched."""
    cur = conn.execute(
        """
        UPDATE strategy_halts
        SET reset_ts = ?, reset_by = ?
        WHERE strategy_id = ? AND reset_ts IS NULL
        """,
        (now_ts, operator, strategy_id),
    )
    rows = cur.rowcount or 0
    if rows:
        logger.info(
            "[circuit] strategy=%s reset by=%s rows=%d",
            strategy_id,
            operator,
            rows,
        )
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_recent_faults(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    now_ts: int,
) -> list[FaultEvent]:
    """Load the last 30 minutes of fault events for the strategy."""
    cutoff = now_ts - 30 * 60
    rows = conn.execute(
        """
        SELECT fault_type, event_ts FROM strategy_fault_events
        WHERE strategy_id = ? AND event_ts >= ?
        ORDER BY event_ts ASC
        """,
        (strategy_id, cutoff),
    ).fetchall()
    return [FaultEvent(fault_type=str(r[0]), event_ts=int(r[1])) for r in rows]


def _open_halt(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    mode: StrategyMode,
    reason_code: str,
    now_ts: int,
    detail: dict[str, Any],
) -> str:
    halt_id = str(uuid.uuid4())
    unblock_ts: int | None = None
    if mode == SOFT_HALT:
        unblock_ts = now_ts + CB_SOFT_HALT_AUTO_UNBLOCK_SEC
    elif mode == HARD_HALT:
        # Bounded auto-resume (flow_not_block). RISK_ONLY stays manual (None):
        # it is exit-only with a live position and must not re-arm NEW entries.
        unblock_ts = now_ts + CB_HARD_HALT_AUTO_UNBLOCK_SEC
    conn.execute(
        """
        INSERT INTO strategy_halts
            (halt_id, strategy_id, mode, reason_code,
             opened_ts, unblock_ts, reset_by, reset_ts, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
        """,
        (
            halt_id,
            strategy_id,
            mode,
            reason_code,
            now_ts,
            unblock_ts,
            json.dumps(detail, sort_keys=True),
        ),
    )
    return halt_id
