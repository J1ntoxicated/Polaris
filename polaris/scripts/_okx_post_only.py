"""OKX post-only maker entry leg — split out of ``_okx_limit_open``.

Move-only extraction for the ≤500-LOC budget. ``_okx_post_only_open`` posts a
``post_only`` limit at the passive touch (best bid for a buy) so a fill pays the
maker fee + zero spread-cross, polling up to ``limit_fill_wait_sec`` then
cancelling. ``_okx_limit_open`` calls it and falls back to a market order on
``None`` / raise (flow_not_block).
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


async def _okx_maker_px(adapter: Any, *, inst_id: str) -> float | None:
    """Resolve the passive maker touch for a BUY: best bid (post-only, no cross).

    Returns ``None`` when the ticker has no usable bid so the caller falls back
    to a plain market order (fail-safe — never blocks the entry).
    """
    tk = await adapter.fetch_ticker(inst_id)
    bid = tk.get("bidPx")
    try:
        px = float(bid) if bid not in (None, "") else 0.0
    except (TypeError, ValueError):
        return None
    return px if px > 0.0 else None


async def _okx_post_only_open(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None,
    poll_delay_sec: float,
) -> OpenAttempt | None:
    """Post-only limit buy at the touch, poll for fill, cancel on timeout.

    Returns the filled ``OpenAttempt`` on success (incl. partial fill), or
    ``None`` when the limit could not be placed / did not fill in time (the
    order is cancelled before returning ``None`` so no live maker order leaks).
    Raises only on an unexpected adapter error — the caller treats a raise as a
    fail-safe market fallback.
    """
    maker_px = await _okx_maker_px(adapter, inst_id=inst_id)
    if maker_px is None:
        return None  # no usable bid → market fallback
    resp = await adapter.place_market_order(
        inst_id=inst_id,
        side="buy",
        notional_usd=notional_usd,
        client_order_id=f"polLpo{uuid.uuid4().hex[:8]}",
        ord_type="post_only",
        last_price_hint=maker_px,
    )
    if not resp.ok or not resp.venue_order_id:
        # post_only rejected (e.g. would cross) → cancel n/a, market fallback.
        logger.info(
            "[okx/limit] %s post_only rejected code=%s — market fallback",
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
            logger.info("[okx/limit] %s post_only filled ordId=%s", inst_id, ord_id)
            return attempt
        if time.monotonic() >= deadline:
            break
    # Timed out unfilled — cancel the resting maker order, then poll once more in
    # case a partial filled during the cancel race (orphan guard: a partial
    # cancel still leaves a REAL position that must be tracked).
    try:
        await adapter.cancel_order(inst_id=inst_id, ord_id=ord_id)
    except Exception as exc:  # noqa: BLE001 — cancel best-effort; still re-check
        logger.warning("[okx/limit] %s cancel raised %r", inst_id, exc)
    state = await adapter.fetch_order(inst_id=inst_id, ord_id=ord_id)
    rows = state.get("data", []) or []
    attempt = _normalize_open_rows(
        rows, strategy_id=strategy_id, last_price=last_price,
    )
    if attempt is not None:
        logger.info(
            "[okx/limit] %s post_only partial-fill on cancel ordId=%s — tracked",
            inst_id, ord_id,
        )
    return attempt
