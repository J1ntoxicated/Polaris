"""OKX marketable-limit entry leg — split out of ``_okx_limit_open``.

Move-only extraction for the ≤500-LOC budget. ``_okx_marketable_limit_open``
posts a limit at ``ask × (1 + cap_bps/1e4)`` so a momentum/breakout entry crosses
the spread (fills like a taker) but never WORSE than ``cap_bps`` past the touch,
polling then cancelling. ``_okx_limit_open`` calls it and falls back to a market
order on ``None`` / raise (flow_not_block).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from polaris.scripts._limit_exec_constants import limit_fill_wait_sec
from polaris.scripts._okx_open_shared import _normalize_open_rows
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt

logger = logging.getLogger(__name__)


async def _okx_marketable_ask(adapter: Any, *, inst_id: str) -> float | None:
    """Resolve the aggressive touch for a BUY: best ASK (the marketable side).

    Returns ``None`` when the ticker has no usable ask so the caller falls back
    to a plain market order (fail-safe — never blocks the entry).
    """
    tk = await adapter.fetch_ticker(inst_id)
    ask = tk.get("askPx")
    try:
        px = float(ask) if ask not in (None, "") else 0.0
    except (TypeError, ValueError):
        return None
    return px if px > 0.0 else None


async def _okx_marketable_limit_open(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None,
    poll_delay_sec: float,
    cap_bps: float,
) -> OpenAttempt | None:
    """Marketable-limit BUY: a limit at ``ask × (1 + cap_bps/1e4)``, poll, cancel.

    A momentum/breakout entry crosses the spread (so it fills like a taker and
    keeps the trade) but with an adverse-fill cap — it can never fill WORSE than
    ``cap_bps`` past the best ask on a fast tape. Returns the filled
    ``OpenAttempt`` (incl. a partial fill), or ``None`` when the limit could not
    be placed / did not fill in time (the order is cancelled before returning
    ``None`` so no live order leaks). Raises only on an unexpected adapter error
    — the caller treats a raise as a fail-safe market fallback.
    """
    ask_px = await _okx_marketable_ask(adapter, inst_id=inst_id)
    if ask_px is None:
        return None  # no usable ask → market fallback
    cap_px = ask_px * (1.0 + cap_bps / 10_000.0)
    resp = await adapter.place_market_order(
        inst_id=inst_id,
        side="buy",
        notional_usd=notional_usd,
        client_order_id=f"polLmk{uuid.uuid4().hex[:8]}",
        ord_type="limit",
        last_price_hint=cap_px,
    )
    if not resp.ok or not resp.venue_order_id:
        logger.info(
            "[okx/limit] %s marketable-limit rejected code=%s — market fallback",
            inst_id, resp.code,
        )
        return None
    ord_id = resp.venue_order_id
    deadline = time.monotonic() + limit_fill_wait_sec()
    while True:
        await asyncio.sleep(poll_delay_sec)
        state = await adapter.fetch_order(inst_id=inst_id, ord_id=ord_id)
        rows = state.get("data", []) or []
        attempt = _normalize_open_rows(
            rows, strategy_id=strategy_id, last_price=last_price,
        )
        if attempt is not None:
            logger.info(
                "[okx/limit] %s marketable-limit filled ordId=%s cap_bps=%.1f",
                inst_id, ord_id, cap_bps,
            )
            return attempt
        if time.monotonic() >= deadline:
            break
    # Timed out unfilled — cancel, then re-poll once for a cancel-race partial
    # (orphan guard: a partial cancel still leaves a REAL position to track).
    try:
        await adapter.cancel_order(inst_id=inst_id, ord_id=ord_id)
    except Exception as exc:  # noqa: BLE001 — cancel best-effort; still re-check
        logger.warning("[okx/limit] %s marketable cancel raised %r", inst_id, exc)
    state = await adapter.fetch_order(inst_id=inst_id, ord_id=ord_id)
    rows = state.get("data", []) or []
    attempt = _normalize_open_rows(
        rows, strategy_id=strategy_id, last_price=last_price,
    )
    if attempt is not None:
        logger.info(
            "[okx/limit] %s marketable partial-fill on cancel ordId=%s — tracked",
            inst_id, ord_id,
        )
    return attempt
