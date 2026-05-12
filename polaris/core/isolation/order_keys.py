"""Layer 7 — idempotent order keys + payload hashing + duplicate resolution.

Spec source: vault/30_components/layer-7-strategy-isolation.md (Q5).

Canonical key: ``strategy_id:venue:symbol:timeframe:signal_ts:side``.

Resolution rules:
  - same key + **same payload** → ``dedupe_hit=True``, reuse the existing intent.
  - same key + **different payload** → raise ``IdempotencyConflictError`` and
    record a strategy fault event (caller is expected to escalate to HALT).
  - missing → caller registers fresh.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

__all__ = [
    "IdempotencyConflictError",
    "build_order_key",
    "payload_hash",
    "register_order_intent",
    "resolve_duplicate_intent",
]


class IdempotencyConflictError(RuntimeError):
    """Raised when the same order_key is reused with a different payload."""

    def __init__(self, order_key: str, expected: str, got: str) -> None:
        super().__init__(
            f"order_key={order_key!r} payload_hash mismatch: expected={expected[:12]} got={got[:12]}"
        )
        self.order_key = order_key
        self.expected = expected
        self.got = got


def build_order_key(
    *,
    strategy_id: str,
    venue: str,
    symbol: str,
    timeframe: str,
    signal_ts: int,
    side: str,
) -> str:
    """Compose the canonical idempotent key (P0 spec)."""
    if not strategy_id or not venue or not symbol or not timeframe or not side:
        raise ValueError("all order-key fields required")
    side_norm = side.lower()
    if side_norm not in {"buy", "sell", "long", "short"}:
        raise ValueError(f"invalid side: {side!r}")
    return f"{strategy_id}:{venue}:{symbol}:{timeframe}:{int(signal_ts)}:{side_norm}"


def payload_hash(order_payload: dict[str, Any]) -> str:
    """Stable SHA-256 over a canonical-JSON serialisation of the payload."""
    blob = json.dumps(order_payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def register_order_intent(
    conn: sqlite3.Connection,
    *,
    order_key: str,
    strategy_id: str,
    venue: str,
    symbol: str,
    timeframe: str,
    signal_ts: int,
    side: str,
    payload_hash_value: str,
    now_ts: int,
) -> dict[str, Any]:
    """Insert (or de-duplicate) an order intent row.

    Returns ``{"dedupe_hit": bool, "order_key": str, "payload_hash": str}``.
    Raises ``IdempotencyConflictError`` on same-key + different-payload.
    """
    existing = conn.execute(
        "SELECT payload_hash, status FROM order_intents WHERE order_key = ?",
        (order_key,),
    ).fetchone()
    if existing is not None:
        existing_hash = str(existing[0])
        if existing_hash != payload_hash_value:
            _record_fault(
                conn,
                strategy_id=strategy_id,
                fault_type="idempotency_conflict",
                detail={
                    "order_key": order_key,
                    "expected_hash": existing_hash,
                    "got_hash": payload_hash_value,
                },
                now_ts=now_ts,
            )
            raise IdempotencyConflictError(order_key, existing_hash, payload_hash_value)
        return {"dedupe_hit": True, "order_key": order_key, "payload_hash": existing_hash}

    conn.execute(
        """
        INSERT INTO order_intents
            (order_key, strategy_id, venue, symbol, timeframe, signal_ts, side,
             payload_hash, status, venue_client_id, venue_order_id,
             created_ts, updated_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', NULL, NULL, ?, ?)
        """,
        (
            order_key, strategy_id, venue, symbol, timeframe, int(signal_ts), side.lower(),
            payload_hash_value, now_ts, now_ts,
        ),
    )
    return {"dedupe_hit": False, "order_key": order_key, "payload_hash": payload_hash_value}


def resolve_duplicate_intent(
    conn: sqlite3.Connection,
    *,
    order_key: str,
    payload_hash_value: str,
) -> dict[str, Any]:
    """Look up an existing intent and decide if it can be retried as-is.

    Returns ``{"exists": bool, "dedupe_hit": bool, "status": str | None}``.
    """
    row = conn.execute(
        "SELECT payload_hash, status FROM order_intents WHERE order_key = ?",
        (order_key,),
    ).fetchone()
    if row is None:
        return {"exists": False, "dedupe_hit": False, "status": None}
    existing_hash, status = str(row[0]), str(row[1])
    if existing_hash == payload_hash_value:
        return {"exists": True, "dedupe_hit": True, "status": status}
    return {"exists": True, "dedupe_hit": False, "status": status}


def _record_fault(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    fault_type: str,
    detail: dict[str, Any],
    now_ts: int,
) -> None:
    conn.execute(
        """
        INSERT INTO strategy_fault_events
            (event_id, strategy_id, fault_type, event_ts, detail_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            strategy_id,
            fault_type,
            now_ts,
            json.dumps(detail, sort_keys=True),
        ),
    )
