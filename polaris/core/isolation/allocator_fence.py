"""Layer 7 — central allocator fence (single global asyncio.Lock + SQLite reservation ledger).

Spec source: vault/30_components/layer-7-strategy-isolation.md (Q3).

Pattern: ``check_and_reserve → submit → confirm | release``.

The lock is a *single* ``asyncio.Lock`` because the design intentionally
serialises the read-modify-write of the reservation ledger across all 7 P0
strategies — fine-grained locking would invite deadlocks for negligible
contention savings (codex round 1 D3).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOCATOR_RESERVATION_TTL_SEC",
    "AllocationRequest",
    "AllocatorFence",
    "ReservationConflictError",
    "get_process_fence",
    "reset_process_fence",
]

ALLOCATOR_RESERVATION_TTL_SEC: Final[int] = 5


class ReservationConflictError(RuntimeError):
    """Raised when an order_key already has a live reservation by another caller."""


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    strategy_id: str
    venue: str
    symbol: str
    side: str
    correlation_group: str
    underlying_group_id: str
    requested_notional: float
    requested_risk: float
    signal_id: str
    order_key: str
    ttl_sec: int = ALLOCATOR_RESERVATION_TTL_SEC


class AllocatorFence:
    """Process-wide allocator fence over a SQLite reservation ledger.

    Construct **once** per process. Pass the same instance to every strategy
    worker so they share the global lock.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        """Expose the underlying lock — useful for test introspection."""
        return self._lock

    async def check_and_reserve(self, req: AllocationRequest, *, now_ts: int) -> dict[str, Any]:
        """Reserve capacity for ``req`` if no live reservation collides.

        Returns ``{"reservation_id": str, "expires_ts": int}``.
        Raises :class:`ReservationConflictError` on duplicate live order_key.
        """
        async with self._lock:
            return self._check_and_reserve_locked(req, now_ts=now_ts)

    async def confirm_reservation(
        self,
        reservation_id: str,
        *,
        venue_order_ref: str,
        now_ts: int,
    ) -> None:
        """Mark a pending reservation as confirmed (after venue ack)."""
        async with self._lock:
            cur = self._conn.execute(
                """
                UPDATE allocator_reservations
                SET status = 'confirmed', confirmed_ts = ?, venue_order_ref = ?
                WHERE reservation_id = ? AND status = 'pending'
                """,
                (now_ts, venue_order_ref, reservation_id),
            )
            if (cur.rowcount or 0) == 0:
                raise ReservationConflictError(
                    f"reservation {reservation_id!r} not pending (already confirmed/released?)"
                )

    async def release_reservation(
        self,
        reservation_id: str,
        *,
        reason: str,
        now_ts: int,
    ) -> None:
        """Release a reservation (success path = after fill, failure path = after reject)."""
        async with self._lock:
            self._conn.execute(
                """
                UPDATE allocator_reservations
                SET status = 'released', released_ts = ?
                WHERE reservation_id = ? AND status IN ('pending', 'confirmed')
                """,
                (now_ts, reservation_id),
            )
            _ = reason  # accepted for ledger payload; written via fault events upstream.

    async def expire_stale_reservations(self, *, now_ts: int) -> int:
        """Mark every overdue ``pending`` reservation as ``expired``. Returns rows touched."""
        async with self._lock:
            cur = self._conn.execute(
                """
                UPDATE allocator_reservations
                SET status = 'expired', released_ts = ?
                WHERE status = 'pending' AND expires_ts <= ?
                """,
                (now_ts, now_ts),
            )
            return cur.rowcount or 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_and_reserve_locked(
        self,
        req: AllocationRequest,
        *,
        now_ts: int,
    ) -> dict[str, Any]:
        if req.requested_notional < 0 or req.requested_risk < 0:
            raise ValueError("requested_notional / requested_risk must be ≥ 0")

        # Sweep stale rows so a long-dead pending row doesn't lock out a retry.
        self._conn.execute(
            """
            UPDATE allocator_reservations
            SET status = 'expired', released_ts = ?
            WHERE status = 'pending' AND expires_ts <= ?
            """,
            (now_ts, now_ts),
        )

        existing = self._conn.execute(
            """
            SELECT reservation_id, strategy_id, status
            FROM allocator_reservations
            WHERE order_key = ? AND status IN ('pending', 'confirmed')
            ORDER BY created_ts DESC
            LIMIT 1
            """,
            (req.order_key,),
        ).fetchone()
        if existing is not None:
            res_id, owner, status = existing
            if owner != req.strategy_id:
                raise ReservationConflictError(
                    f"order_key {req.order_key!r} already reserved by strategy={owner} status={status}"
                )
            # Same strategy retrying with the same order_key — return the live row.
            return {
                "reservation_id": str(res_id),
                "expires_ts": _fetch_expiry(self._conn, str(res_id)),
                "dedupe_hit": True,
            }

        reservation_id = str(uuid.uuid4())
        expires_ts = now_ts + max(1, int(req.ttl_sec))
        self._conn.execute(
            """
            INSERT INTO allocator_reservations
                (reservation_id, strategy_id, venue, symbol,
                 correlation_group, underlying_group_id, order_key,
                 requested_notional, requested_risk,
                 status, created_ts, expires_ts,
                 confirmed_ts, released_ts, venue_order_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL)
            """,
            (
                reservation_id,
                req.strategy_id,
                req.venue,
                req.symbol,
                req.correlation_group,
                req.underlying_group_id,
                req.order_key,
                float(req.requested_notional),
                float(req.requested_risk),
                now_ts,
                expires_ts,
            ),
        )
        logger.debug(
            "[fence] reserve strategy=%s order_key=%s notional=%.2f risk=%.4f "
            "reservation_id=%s expires_ts=%d",
            req.strategy_id,
            req.order_key,
            req.requested_notional,
            req.requested_risk,
            reservation_id,
            expires_ts,
        )
        return {"reservation_id": reservation_id, "expires_ts": expires_ts, "dedupe_hit": False}


def _fetch_expiry(conn: sqlite3.Connection, reservation_id: str) -> int:
    row = conn.execute(
        "SELECT expires_ts FROM allocator_reservations WHERE reservation_id = ?",
        (reservation_id,),
    ).fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Process-wide singleton (P1 codex round 2 patch — guards against accidental
# multi-instance bypass of the "single global asyncio.Lock" guarantee).
# ---------------------------------------------------------------------------

_PROCESS_FENCE: AllocatorFence | None = None


def get_process_fence(conn: sqlite3.Connection) -> AllocatorFence:
    """Return the process-wide AllocatorFence, creating it on first call.

    Subsequent calls MUST pass the same ``conn`` — passing a different
    connection raises ``ReservationConflictError`` because that would split
    the fence across two ledgers and re-introduce the race we are guarding
    against.
    """
    global _PROCESS_FENCE  # noqa: PLW0603 — intentional process-wide singleton
    if _PROCESS_FENCE is None:
        _PROCESS_FENCE = AllocatorFence(conn)
        return _PROCESS_FENCE
    if _PROCESS_FENCE._conn is not conn:  # noqa: SLF001 — singleton invariant check
        raise ReservationConflictError(
            "AllocatorFence singleton is bound to a different sqlite connection; "
            "call reset_process_fence() before reseating with a new conn."
        )
    return _PROCESS_FENCE


def reset_process_fence() -> None:
    """Reset the singleton (tests / process restart only)."""
    global _PROCESS_FENCE  # noqa: PLW0603
    _PROCESS_FENCE = None
