"""Shared OKX entry-leg order-row normalizers — split out of ``_okx_limit_open``.

Move-only extraction for the ≤500-LOC budget. ``_accfill_qty`` /
``_normalize_open_rows`` are the polled-order → ``OpenAttempt`` adapters shared by
the market, post-only, and marketable-limit entry paths. Kept in a leaf module so
every path module imports them without a cycle.
"""

from __future__ import annotations

from typing import Any

from polaris.core.data.fill_normalizer import OKX_FILLED_STATES, normalize_okx_fill
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt


def _accfill_qty(row: dict[str, Any]) -> float:
    """Filled base qty from an OKX order row (accFillSz, fallback fillSz)."""
    for key in ("accFillSz", "fillSz"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _normalize_open_rows(
    rows: list[dict[str, Any]],
    *,
    strategy_id: str,
    last_price: float | None,
) -> OpenAttempt | None:
    """Normalize a polled OKX order row → ``OpenAttempt`` (Fill) or ``None``.

    Returns the filled/partially-filled ``OpenAttempt`` when the order has a
    real fill, or ``None`` when it is still unfilled (caller decides whether to
    keep polling or fall back). A 'canceled' order that left a partial fill
    (accFillSz>0) is a REAL position — normalized so it is tracked and never
    becomes an untracked orphan (codex review 2026-05-29).
    """
    if not rows:
        return None
    order_state = str(rows[0].get("state") or "").lower()
    filled_qty = _accfill_qty(rows[0])
    if order_state not in OKX_FILLED_STATES and filled_qty <= 0.0:
        return None
    row = (
        rows[0] if order_state in OKX_FILLED_STATES
        else {**rows[0], "state": "partially_filled"}
    )
    fill = normalize_okx_fill(
        row, strategy_id=strategy_id, expected_price=last_price,
    )
    return OpenAttempt(fill=fill)
