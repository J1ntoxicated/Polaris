"""Alpaca US-equity real demo CLOSE leg (Track C).

Mirrors ``real_okx_close_fill`` (``_smoke_real_roundtrip``) and
``real_capital_close_fill`` (``_smoke_roundtrip_capital``): a single-leg close
helper that drives one venue exit on an *already-constructed* adapter, normalized
into a canonical ``Fill``. The adapter is injected so the production loop and
unit tests can pass a mock; no network happens unless a live adapter is handed in.

ROOT CAUSE this fixes: Alpaca had NO venue close path — an Alpaca position fell
through to the Capital branch of ``_real_close_fill`` (no deal_id → CloseOrphan)
→ reconciled in our DB but the shares were NEVER sold. This SELLS the shares.

Reject / orphan semantics (never raises — flow_not_block):
* MARKET CLOSED (``fetch_clock().is_open`` False) → return ``None`` (TRANSIENT —
  retry at the next open; do NOT fabricate a fill or falsely reconcile). The US
  equity venue cannot fill a sell while the session is closed.
* OVER-COUNT (``fetch_positions`` shows fewer shares than ``base_qty`` — the
  wallet does not hold the tracked qty) → ``CloseOrphan(available=<actual>)`` so
  the caller marks ``status='reconciled'`` (state-drift recovery, NOT a throttle),
  mirroring the OKX over-count path. Never oversell.
* Otherwise SELL the position's shares via ``place_market_order(side='sell',
  qty=base_qty, ...)`` → poll ``fetch_order`` → normalize. A non-fill venue state
  returns ``None`` (transient retry).

DEMO/PAPER only; virtual funds. The close is the position's own exit leg —
AGGRESSIVE bias intact, never a defensive size dampen / P&L halt.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any, Final

from polaris.core.data.fill_normalizer import (
    Fill,
    FillNormalizationError,
    normalize_alpaca_fill,
)
from polaris.scripts._smoke_roundtrip_shared import CloseOrphan, PendingClose

logger = logging.getLogger(__name__)

__all__ = ["ALPACA_CLOSE_CONFIRM_POLLS", "real_alpaca_close_fill"]

ALPACA_CLOSE_POLL_DELAY_SEC: float = 0.5
# BUG E-c: poll the SELL up to this many times (× poll_delay_sec) before
# handing the still-LIVE order to the next tick as a ``PendingClose`` (mirrors
# the Capital open leg's ``_CONFIRM_POLLS``). The prior single 0.5s poll missed
# late fills and the next tick re-fired a FULL sell → double sell (SELX).
ALPACA_CLOSE_CONFIRM_POLLS: int = 6

# Order states after which the order can never fill again. A ZERO-filled
# terminal order is safe to re-fire; a part-filled terminal order settles its
# final slice first (``_terminal_row_fill``).
_ALPACA_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {"canceled", "cancelled", "expired", "rejected", "done_for_day", "stopped"}
)
# Fully settled — every share sold, nothing remains live at the venue.
_ALPACA_SETTLED_STATE: Final[str] = "filled"
# BUG E-d: a filled SLICE whose remainder is still LIVE at the venue. NOT
# settled — booking it (and clearing the pending ref) re-fires a fresh sell
# next tick NEXT TO the live remainder = two live close orders (double sell).
_ALPACA_PARTIAL_STATE: Final[str] = "partially_filled"


async def _alpaca_market_is_open(adapter: Any) -> bool:
    """Best-effort session-state read. Any error treats the market as OPEN (do
    not block a real close on a clock-query failure — flow_not_block); the
    subsequent SELL would simply no-fill (→ transient None) if it were closed.
    """
    try:
        clock = await adapter.fetch_clock()
    except Exception as exc:  # noqa: BLE001 — clock query is best-effort
        logger.warning("[alpaca/close] clock query failed %r — assume open", exc)
        return True
    return bool(getattr(clock, "is_open", True))


async def _alpaca_available_shares(adapter: Any, symbol: str) -> float | None:
    """Available shares the wallet holds for ``symbol`` (over-count clamp).

    Returns ``None`` when the position list cannot be read (unknown → caller
    skips the clamp and sells the tracked qty, prior behaviour). A symbol absent
    from the list means the wallet holds 0 (a genuine over-count → 0.0).
    """
    try:
        positions = await adapter.fetch_positions()
    except Exception as exc:  # noqa: BLE001 — positions query is best-effort
        logger.warning(
            "[alpaca/close] positions query failed %r — skip over-count clamp", exc
        )
        return None
    target = symbol.upper()
    for pos in positions:
        if str(pos.get("symbol") or "").upper() == target:
            raw = pos.get("qty")
            try:
                return abs(float(raw)) if raw not in (None, "") else None
            except (TypeError, ValueError):
                return None
    return 0.0  # symbol not held — over-count (wallet holds nothing)


def _normalize_or_pending(
    row: dict[str, Any], *, symbol: str, order_id: str, strategy_id: str,
    last_price: float | None,
) -> Fill | PendingClose:
    """Normalize a FILLED-state order row; an un-parseable filled row keeps the
    ref pending (NEVER falls back to a re-fire — that is the double sell)."""
    try:
        return normalize_alpaca_fill(
            row, strategy_id=strategy_id, expected_price=last_price
        )
    except FillNormalizationError as exc:
        logger.warning("[alpaca/close] %s fill normalize failed %r", symbol, exc)
        return PendingClose(ref=order_id)


def _row_filled_qty(row: dict[str, Any]) -> float:
    try:
        return float(row.get("filled_qty") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _terminal_row_fill(
    row: dict[str, Any] | None, *, symbol: str, order_id: str, strategy_id: str,
    last_price: float | None,
) -> Fill | PendingClose | None:
    """Settle a TERMINAL-state (or vanished) order: no further fill is possible,
    so a partial ``filled_qty`` is FINAL → its slice ``Fill`` (the un-sold
    remainder re-fires fresh next tick with nothing live left at the venue);
    zero filled → ``None`` (dead order, safe to re-fire). Ignoring a
    part-filled terminal row was a silent double sell: the sold slice stayed
    tracked and was re-sold by the next tick's full re-fire."""
    if row is None or _row_filled_qty(row) <= 0.0:
        return None
    # ``normalize_alpaca_fill`` rejects terminal statuses; the partial fills
    # ARE final here, so present the row as its settled partial slice.
    return _normalize_or_pending(
        {**row, "status": _ALPACA_PARTIAL_STATE}, symbol=symbol,
        order_id=order_id, strategy_id=strategy_id, last_price=last_price,
    )


async def _cancel_then_settle(
    adapter: Any, *, symbol: str, order_id: str, strategy_id: str,
    last_price: float | None,
) -> Fill | PendingClose | None:
    """Cancel a LIVE order, then settle it from ONE re-query (mirror
    ``_okx_sell_child``): a ``Fill`` is returned only once the order is
    TERMINAL at the venue — never while a remainder can still fill.

    * re-query ``filled`` → the full ``Fill`` (race fill during the cancel).
    * re-query terminal / order gone → partial slice final → its ``Fill``;
      zero filled → ``None`` (dead, safe to re-fire).
    * re-query still LIVE (``pending_cancel`` / ``partially_filled``) or read
      failure → ``PendingClose`` (confirm next tick; never settle a row the
      venue can still fill against).
    """
    with contextlib.suppress(Exception):
        await adapter.cancel_order(order_id)
    try:
        row = await adapter.fetch_order(order_id=order_id)
    except Exception as exc:  # noqa: BLE001 — keep pending on uncertainty
        logger.warning(
            "[alpaca/close] %s order %s post-cancel fetch failed %r — keep "
            "pending", symbol, order_id, exc,
        )
        return PendingClose(ref=order_id)
    state = str((row or {}).get("status") or "").lower()
    if row and state == _ALPACA_SETTLED_STATE:
        return _normalize_or_pending(
            row, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
    if not row or state in _ALPACA_TERMINAL_STATES:
        return _terminal_row_fill(
            row, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
    return PendingClose(ref=order_id)


async def _resolve_pending_order(
    adapter: Any, *, symbol: str, order_id: str, strategy_id: str,
    last_price: float | None,
) -> Fill | PendingClose | None:
    """BUG E-b: settle a previously-fired close order BEFORE firing a new one.

    * fully FILLED → that order's normalized ``Fill`` (the SELX double-sell
      block: the shares are already sold — never place a second sell).
    * TERMINAL / unknown order → a part-filled terminal row settles its final
      slice (BUG E-d); zero filled → ``None`` — no fill remains possible, the
      caller may fall through to a fresh sell.
    * LIVE (incl. ``partially_filled`` — its remainder is still live) →
      ``cancel_order`` (so it cannot fill AFTER a re-fire) + one re-check;
      only a TERMINAL row settles (``_cancel_then_settle``).
    * Any read failure → ``PendingClose`` again (retry the confirm next tick;
      never blind-re-fire on uncertainty).
    """
    try:
        row = await adapter.fetch_order(order_id=order_id)
    except Exception as exc:  # noqa: BLE001 — venue read is best-effort
        logger.warning(
            "[alpaca/close] %s pending order %s fetch failed %r — keep pending",
            symbol, order_id, exc,
        )
        return PendingClose(ref=order_id)
    state = str((row or {}).get("status") or "").lower()
    if row and state == _ALPACA_SETTLED_STATE:
        logger.info(
            "[alpaca/close] %s pending order %s already %s — using its fill "
            "(no duplicate sell)", symbol, order_id, state,
        )
        return _normalize_or_pending(
            row, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
    if not row or state in _ALPACA_TERMINAL_STATES:
        return _terminal_row_fill(
            row, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
    # Still LIVE (part-filled or not) — cancel so it cannot fill AFTER a
    # re-fire, then settle from one re-check (race fill / final slice only).
    return await _cancel_then_settle(
        adapter, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
        last_price=last_price,
    )


async def real_alpaca_close_fill(
    adapter: Any,
    *,
    symbol: str,
    base_qty: float,
    strategy_id: str,
    last_price: float | None = None,
    poll_delay_sec: float = ALPACA_CLOSE_POLL_DELAY_SEC,
    pending_order_id: str | None = None,
) -> Fill | PendingClose | CloseOrphan | None:
    """Alpaca close leg: SELL the position's ``base_qty`` shares → poll → normalize.

    * ``pending_order_id`` (BUG E-b): a prior tick's unconfirmed close order —
      settled FIRST (filled → its ``Fill``; live/part-filled → cancel +
      re-check, only a TERMINAL slice settles; terminal → final slice or fall
      through). Never two live close orders at once.
    * ``base_qty <= 0`` → ``None`` (nothing to close).
    * MARKET CLOSED → ``None`` (TRANSIENT — retry at the next open; the rail's
      stale-overnight trigger re-arms the flatten in-session).
    * OVER-COUNT (wallet holds < ``base_qty``) → ``CloseOrphan(available)`` so the
      caller reconciles (state-drift recovery), mirroring OKX. Never oversell.
    * SELL fill → normalized close ``Fill``; venue reject / terminal no-fill →
      ``None``; still LIVE after the poll budget → ``PendingClose(order_id)``
      (BUG E-c — confirm-first next tick instead of a duplicate re-fire);
      part-filled after the budget → cancel remainder + book the terminal
      slice only (BUG E-d — ``partially_filled`` is never settled while its
      remainder is live).
    """
    if base_qty <= 0.0:
        return None

    if pending_order_id:
        resolved = await _resolve_pending_order(
            adapter, symbol=symbol, order_id=pending_order_id,
            strategy_id=strategy_id, last_price=last_price,
        )
        if resolved is not None:
            return resolved
        # Pending order is dead with no fill — safe to fire a fresh sell.

    # MARKET-CLOSED guard: the US equity venue cannot fill a sell while the
    # session is closed → transient None (retry at open), NOT a fabricated fill.
    if not await _alpaca_market_is_open(adapter):
        logger.info(
            "[alpaca/close] %s market closed — transient, retry at open (no fill)",
            symbol,
        )
        return None

    # OVER-COUNT guard (mirror OKX): the wallet must actually hold the tracked
    # shares. Fewer → state drift → reconcile (orphan), never oversell.
    available = await _alpaca_available_shares(adapter, symbol)
    sell_qty = base_qty
    if available is not None and available < base_qty:
        logger.warning(
            "[alpaca/close] %s base_qty=%.10f but wallet holds %.10f (over-count) "
            "— orphan; skip sell, mark reconciled (state-drift, not a throttle)",
            symbol, base_qty, available,
        )
        return CloseOrphan(available=available)
    # SYMMETRIC over-count (open-side orphans landed SURPLUS shares on this
    # symbol): the wallet holds MORE than the tracked base_qty. Sell the ACTUAL
    # venue-held qty so the surplus is flattened, not left orphaned on close
    # (flow_not_block — flatten what is really there). ``available == base_qty``
    # keeps sell_qty == base_qty (byte-identical to the prior behaviour).
    if available is not None and available > base_qty:
        logger.warning(
            "[alpaca/close] %s base_qty=%.10f but wallet holds %.10f (over-held) "
            "— sell the actual venue qty so the surplus is not left orphaned",
            symbol, base_qty, available,
        )
        sell_qty = available

    cl_ord_id = f"polAsell{uuid.uuid4().hex[:10]}"
    resp = await adapter.place_market_order(
        symbol=symbol, side="sell", qty=sell_qty, client_order_id=cl_ord_id,
    )
    if not resp.ok or not resp.venue_order_id:
        # Venue reject (EXTERNAL) — transient, preserve the position + retry.
        logger.warning(
            "[alpaca/close] %s sell rejected code=%s msg=%s — transient (no fault)",
            symbol, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
        )
        return None
    order_id = str(resp.venue_order_id)
    # BUG E-c: poll the confirm up to the budget (break on a settled state) —
    # a single early poll missed late fills and the next tick double-sold.
    row: dict[str, Any] | None = None
    state = ""
    for _poll in range(ALPACA_CLOSE_CONFIRM_POLLS):
        await asyncio.sleep(poll_delay_sec)
        try:
            row = await adapter.fetch_order(order_id=order_id)
        except Exception as exc:  # noqa: BLE001 — keep pending on uncertainty
            logger.warning(
                "[alpaca/close] %s order %s poll failed %r", symbol, order_id, exc,
            )
            row = None
            continue
        state = str((row or {}).get("status") or "").lower()
        if state == _ALPACA_SETTLED_STATE or state in _ALPACA_TERMINAL_STATES:
            break
        # BUG E-d: ``partially_filled`` does NOT break — its remainder is
        # still LIVE and may complete; settling the slice now (+ ref clear)
        # would put a next-tick fresh sell NEXT TO the live remainder.
    if row and state == _ALPACA_SETTLED_STATE:
        return _normalize_or_pending(
            row, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
    if row and state in _ALPACA_TERMINAL_STATES:
        # Order died — a part-filled slice (if any) is final and books now;
        # zero filled → EXTERNAL no-fill, safe to re-fire next tick.
        return _terminal_row_fill(
            row, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
    if row and state == _ALPACA_PARTIAL_STATE:
        # Poll budget exhausted on a part-filled LIVE order: cancel the
        # remainder + one re-query (mirror ``_okx_sell_child``) so the slice
        # we book is TERMINAL; the remainder re-fires fresh next tick.
        logger.info(
            "[alpaca/close] %s order %s part-filled after %d polls — cancel "
            "remainder + settle terminal slice",
            symbol, order_id, ALPACA_CLOSE_CONFIRM_POLLS,
        )
        return await _cancel_then_settle(
            adapter, symbol=symbol, order_id=order_id, strategy_id=strategy_id,
            last_price=last_price,
        )
    # Accepted but unconfirmed after the whole budget — the order is still
    # LIVE: hand the ref to the next tick (confirm-first, no duplicate sell).
    logger.info(
        "[alpaca/close] %s order %s unconfirmed after %d polls — pending "
        "(confirm-first next tick)", symbol, order_id, ALPACA_CLOSE_CONFIRM_POLLS,
    )
    return PendingClose(ref=order_id)
