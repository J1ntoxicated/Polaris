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

``pending_order_id`` (open-confirm fix, mirrors the close leg's
``pending_order_id``/``pending_close_ref``): a prior tick's still-unconfirmed
open order is resolved FIRST — a fill returns that order's ``Fill`` (no
duplicate submit); a terminal/dead order clears + falls through to a fresh
submit; still-LIVE returns ``no_fill`` again with the SAME ``venue_order_id``
so the caller re-persists (not re-submits) the pending ref.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any, Final

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

# Order states after which the order can never fill again (mirrors the close
# leg's ``_ALPACA_TERMINAL_STATES``) — a carried-over pending ref in one of
# these states is dead: clear it and fall through to a fresh submit.
_ALPACA_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {"canceled", "cancelled", "expired", "rejected", "done_for_day", "stopped"}
)

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


async def _resolve_pending_open(
    adapter: Any, *,
    order_id: str,
    client_order_id: str | None,
    strategy_id: str,
    last_price: float | None,
) -> OpenAttempt | None:
    """Settle a prior tick's unconfirmed open order BEFORE firing a new one.

    * FILLED/partially_filled → that order's normalized ``Fill`` (no duplicate
      submit — the shares are already bought).
    * TERMINAL (canceled/rejected/expired/...) or the order has vanished →
      ``None`` (dead, safe to fall through to a fresh submit this tick).
    * Still LIVE (``new``/``pending_new``/...) or a read failure → an
      ``OpenAttempt(no_fill)`` carrying the SAME ``venue_order_id`` so the
      caller re-persists (not re-submits) the pending ref.
    """
    try:
        row = await adapter.fetch_order(order_id=order_id)
    except Exception as exc:  # noqa: BLE001 — venue read is best-effort
        logger.warning(
            "[alpaca/open] pending order %s fetch failed %r — keep pending",
            order_id, exc,
        )
        return OpenAttempt(
            fill=None, reject_code="no_fill", reject_msg="pending fetch failed",
            venue_order_id=order_id, client_order_id=client_order_id,
        )
    state = str((row or {}).get("status") or "").lower()
    if row and state in ALPACA_FILLED_STATES:
        logger.info(
            "[alpaca/open] pending order %s already %s — using its fill "
            "(no duplicate buy)", order_id, state,
        )
        try:
            fill = normalize_alpaca_fill(
                row, strategy_id=strategy_id, expected_price=last_price
            )
        except FillNormalizationError as exc:
            return OpenAttempt(fill=None, reject_code="no_fill", reject_msg=str(exc))
        return OpenAttempt(fill=fill)
    if not row or state in _ALPACA_TERMINAL_STATES:
        logger.info(
            "[alpaca/open] pending order %s terminal (state=%s) — ref cleared, "
            "fresh submit allowed", order_id, state,
        )
        return None
    # Still LIVE — carry the SAME ref forward (no new order placed this tick).
    logger.info(
        "[alpaca/open] pending confirm carryover order=%s state=%s", order_id, state,
    )
    return OpenAttempt(
        fill=None, reject_code="no_fill", reject_msg=f"state={state}",
        venue_order_id=order_id, client_order_id=client_order_id,
    )


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
    pending_order_id: str | None = None,
    pending_client_order_id: str | None = None,
) -> OpenAttempt:
    """Alpaca entry leg: place notional market order → poll → normalize.

    ``pending_order_id``: a prior tick's unconfirmed open order — resolved
    FIRST (see module docstring). Only when it is dead/absent does this
    proceed to submit a fresh order. ``pending_client_order_id`` is carried
    through unchanged (our own ref for that same order, for the caller's
    pending-open record).
    """
    if pending_order_id:
        resolved = await _resolve_pending_open(
            adapter, order_id=pending_order_id,
            client_order_id=pending_client_order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
        if resolved is not None:
            return resolved
        # Pending order is dead with no fill — safe to fire a fresh submit.

    # Clamp the entry to live Alpaca buying power before submit (best-effort;
    # None = prior un-clamped path) so we never re-emit an unfundable order that
    # the venue rejects with insufficient_buying_power.
    clamped = _clamp_to_buying_power(notional_usd, buying_power)
    if clamped is None:
        # audit1 P0-4 ②: this full-vacate branch (fundable notional below the
        # dust floor) previously returned silently — no venue call, no log, so
        # the skip was invisible in the runtime log (indistinguishable from a
        # transport stall). Log the reason (requested vs the live buying_power
        # that produced it) so the skip is diagnosable without a debugger.
        logger.info(
            "[alpaca] %s entry skipped notional=%.2f buying_power=%s — "
            "fundable amount below dust floor (reason=insufficient_buying_power, "
            "no order submitted)",
            symbol, notional_usd, buying_power,
        )
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
            client_order_id=cl_ord_id,
        )
    try:
        fill = normalize_alpaca_fill(
            row, strategy_id=strategy_id, expected_price=last_price
        )
    except FillNormalizationError as exc:
        return OpenAttempt(fill=None, reject_code="no_fill", reject_msg=str(exc))
    return OpenAttempt(fill=fill)
