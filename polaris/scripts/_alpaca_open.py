"""Alpaca US-equity real demo entry leg (Track C).

Mirrors ``_okx_limit_open.real_okx_open_fill`` but simpler: a single
``notional`` market order (no maker/limit path — US equity notional always
fills at best available; ``flow_not_block``), polled once, then normalized into
a canonical ``Fill`` carried by an ``OpenAttempt``. The adapter is injected so
the production loop and unit tests can pass a mock; no network happens unless a
live adapter is handed in.

Returns the venue reject ``code``/``msg`` on a rejected order or the
``"no_fill"`` sentinel when accepted-but-unfilled, so the caller can classify
an EXTERNAL venue reject apart from an internal fault.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any

from polaris.core.data.fill_normalizer import (
    ALPACA_FILLED_STATES,
    FillNormalizationError,
    normalize_alpaca_fill,
)
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt

logger = logging.getLogger(__name__)

__all__ = ["real_alpaca_open_fill"]

ALPACA_POLL_DELAY_SEC: float = 0.5


def _is_not_fractionable_reject(resp: Any) -> bool:
    """Alpaca rejects a $-notional order on a whole-share-only asset with
    ``code=insufficient_buying_power`` and ``msg='asset "X" is not fractionable'``
    (the code is reused — it is NOT genuine out-of-buying-power)."""
    return "not fractionable" in (getattr(resp, "msg", "") or "").lower()


async def real_alpaca_open_fill(
    adapter: Any,
    *,
    symbol: str,
    notional_usd: float,
    strategy_id: str,
    side: str = "buy",
    last_price: float | None = None,
    poll_delay_sec: float = ALPACA_POLL_DELAY_SEC,
) -> OpenAttempt:
    """Alpaca entry leg: place notional market order → poll → normalize."""
    cl_ord_id = f"polA{uuid.uuid4().hex[:18]}"
    resp = await adapter.place_market_order(
        symbol=symbol,
        side=side,
        notional_usd=notional_usd,
        client_order_id=cl_ord_id,
    )
    # WHOLE-SHARE RETRY. A huge share of the live Alpaca universe is whole-share-
    # only (CXAI/LASE/YMAT/... small-caps); a $-notional order on them is rejected
    # ('not fractionable') so EVERY equity entry on those names was lost (live:
    # 609 of 612 venue rejects → the dashboard showed almost no entries). Convert
    # the sized notional to whole shares (floor) and retry by qty. Skip only when
    # it rounds below one share (notional < price). This is flow_not_block: it
    # routes the SAME sized order through the constraint the venue requires.
    if (
        (not resp.ok or not resp.venue_order_id)
        and _is_not_fractionable_reject(resp)
        and last_price
        and last_price > 0.0
    ):
        whole_qty = math.floor(notional_usd / last_price)
        if whole_qty >= 1:
            logger.info(
                "[alpaca] %s not fractionable — retry whole-share qty=%d "
                "(notional=%.2f price=%.4f)",
                symbol, whole_qty, notional_usd, last_price,
            )
            cl_ord_id = f"polA{uuid.uuid4().hex[:18]}"
            resp = await adapter.place_market_order(
                symbol=symbol,
                side=side,
                qty=float(whole_qty),
                client_order_id=cl_ord_id,
            )
    if not resp.ok or not resp.venue_order_id:
        return OpenAttempt(fill=None, reject_code=resp.code, reject_msg=resp.msg)
    await asyncio.sleep(poll_delay_sec)
    row = await adapter.fetch_order(order_id=resp.venue_order_id)
    if not row:
        return OpenAttempt(fill=None, reject_code="no_fill")
    state = str(row.get("status") or "").lower()
    if state not in ALPACA_FILLED_STATES:
        # Accepted but not yet filled — EXTERNAL no-fill, not a fault.
        return OpenAttempt(
            fill=None, reject_code="no_fill", reject_msg=f"state={state}"
        )
    try:
        fill = normalize_alpaca_fill(
            row, strategy_id=strategy_id, expected_price=last_price
        )
    except FillNormalizationError as exc:
        return OpenAttempt(fill=None, reject_code="no_fill", reject_msg=str(exc))
    return OpenAttempt(fill=fill)
