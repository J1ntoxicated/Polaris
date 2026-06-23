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

__all__ = [
    "ALPACA_OPEN_CONFIRM_POLLS",
    "fetch_alpaca_buying_power",
    "real_alpaca_open_fill",
]

ALPACA_POLL_DELAY_SEC: float = 0.5
# Poll the accepted open order up to this many times (× ``poll_delay_sec``)
# before treating it as a still-LIVE no-fill → handed to ``record_venue_orphan``.
# Mirrors the close leg's ``ALPACA_CLOSE_CONFIRM_POLLS`` (6) cadence: the prior
# SINGLE 0.5s poll dropped market orders still at ``new``/``pending_new`` as
# no_fill, and the released-but-live BUY filled seconds later → orphan leak.
ALPACA_OPEN_CONFIRM_POLLS: int = 6

# Leave a thin margin under the reported buying power so a tick of price drift /
# rounding between the account read and the submit does not re-trip the venue's
# insufficient_buying_power reject. flow-ENABLING (lets SOME order through),
# NOT a defensive size cut.
ALPACA_BP_CLAMP_BUFFER_FRAC: float = 0.02
# Below this fundable notional the order rounds to dust — skip cleanly instead
# of re-submitting an unfundable order forever (the buying-power churn this
# fixes). Alpaca has no hard min notional; $1 is the practical floor.
ALPACA_MIN_NOTIONAL_USD: float = 1.0


async def fetch_alpaca_buying_power(adapter: Any) -> float | None:
    """Best-effort live Alpaca buying power for the entry clamp.

    Reads ``/v2/account`` ``buying_power`` (the cash-equity fundable amount).
    Any failure / missing field → ``None`` so the caller skips the clamp and
    keeps the prior un-clamped path (never blocks an entry on a telemetry miss).
    """
    try:
        body = await adapter.fetch_account()
    except Exception as exc:  # noqa: BLE001 — telemetry best-effort, never block
        logger.debug("[alpaca] buying-power fetch failed: %r", exc)
        return None
    raw = body.get("buying_power") if isinstance(body, dict) else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _clamp_to_buying_power(
    notional_usd: float, buying_power: float | None
) -> float | None:
    """Clamp the entry notional to live Alpaca buying power.

    Alpaca rejects an order larger than the account's buying power with
    ``code=insufficient_buying_power`` (a REAL account constraint). When
    ``buying_power`` is known, cap the order at ``buying_power × (1 - buffer)``
    so SOME order fills instead of a 100%-rejected oversized one (flow_not_block
    — routes the sized edge through the venue's funding limit, never a defensive
    cut). Returns ``None`` when the fundable amount is below the dust floor (skip
    cleanly). ``buying_power=None`` preserves the prior un-clamped behaviour.
    """
    if buying_power is None:
        return notional_usd
    fundable = buying_power * (1.0 - ALPACA_BP_CLAMP_BUFFER_FRAC)
    if fundable < ALPACA_MIN_NOTIONAL_USD:
        return None
    return min(notional_usd, fundable)


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
    buying_power: float | None = None,
) -> OpenAttempt:
    """Alpaca entry leg: place notional market order → poll → normalize."""
    # Clamp the entry to live Alpaca buying power before submit (best-effort;
    # None = prior un-clamped path) so we never re-emit an unfundable order that
    # the venue rejects with insufficient_buying_power.
    clamped = _clamp_to_buying_power(notional_usd, buying_power)
    if clamped is None:
        return OpenAttempt(fill=None, reject_code="insufficient_buying_power")
    if clamped < notional_usd:
        logger.info(
            "[alpaca] %s entry clamped to buying power: %.2f → %.2f",
            symbol, notional_usd, clamped,
        )
    notional_usd = clamped
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
    submitted_qty = 0.0
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
            submitted_qty = float(whole_qty)
    if not resp.ok or not resp.venue_order_id:
        return OpenAttempt(fill=None, reject_code=resp.code, reject_msg=resp.msg)
    venue_order_id = str(resp.venue_order_id)
    # CONFIRM-POLL LOOP (mirrors the CLOSE path's ``ALPACA_CLOSE_CONFIRM_POLLS``):
    # an Alpaca market order sits at ``new`` / ``pending_new`` for a beat, so the
    # prior SINGLE poll dropped late fills as ``no_fill`` and the RELEASED but
    # still-LIVE BUY filled seconds later → a permanent untracked orphan. Poll up
    # to the budget; the FIRST filled/partially_filled state captures the SAME
    # shares the venue actually holds (more confirmed entries — aggressive-aligned).
    row: dict[str, Any] | None = None
    state = ""
    for _poll in range(ALPACA_OPEN_CONFIRM_POLLS):
        await asyncio.sleep(poll_delay_sec)
        row = await adapter.fetch_order(order_id=venue_order_id)
        state = str((row or {}).get("status") or "").lower()
        if state in ALPACA_FILLED_STATES:
            break
    if not row or state not in ALPACA_FILLED_STATES:
        # Accepted but unfilled across the WHOLE budget — the order is still LIVE
        # at the venue. Carry the venue_order_id + submitted qty so the caller
        # records an orphan (idempotent) instead of leaking the live order. NOT a
        # throttle — an order that already left our hands made trackable.
        return OpenAttempt(
            fill=None, reject_code="no_fill", reject_msg=f"state={state}",
            venue_order_id=venue_order_id, unfilled_qty=submitted_qty,
        )
    try:
        fill = normalize_alpaca_fill(
            row, strategy_id=strategy_id, expected_price=last_price
        )
    except FillNormalizationError as exc:
        return OpenAttempt(fill=None, reject_code="no_fill", reject_msg=str(exc))
    return OpenAttempt(fill=fill)
