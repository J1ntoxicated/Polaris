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
import logging
import uuid
from typing import Any

from polaris.core.data.fill_normalizer import (
    ALPACA_FILLED_STATES,
    Fill,
    FillNormalizationError,
    normalize_alpaca_fill,
)
from polaris.scripts._smoke_roundtrip_shared import CloseOrphan

logger = logging.getLogger(__name__)

__all__ = ["real_alpaca_close_fill"]

ALPACA_CLOSE_POLL_DELAY_SEC: float = 0.5


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


async def real_alpaca_close_fill(
    adapter: Any,
    *,
    symbol: str,
    base_qty: float,
    strategy_id: str,
    last_price: float | None = None,
    poll_delay_sec: float = ALPACA_CLOSE_POLL_DELAY_SEC,
) -> Fill | CloseOrphan | None:
    """Alpaca close leg: SELL the position's ``base_qty`` shares → poll → normalize.

    * ``base_qty <= 0`` → ``None`` (nothing to close).
    * MARKET CLOSED → ``None`` (TRANSIENT — retry at the next open; the rail's
      stale-overnight trigger re-arms the flatten in-session).
    * OVER-COUNT (wallet holds < ``base_qty``) → ``CloseOrphan(available)`` so the
      caller reconciles (state-drift recovery), mirroring OKX. Never oversell.
    * SELL fill → normalized close ``Fill``; venue reject / non-fill → ``None``.
    """
    if base_qty <= 0.0:
        return None

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
    if available is not None and available < base_qty:
        logger.warning(
            "[alpaca/close] %s base_qty=%.10f but wallet holds %.10f (over-count) "
            "— orphan; skip sell, mark reconciled (state-drift, not a throttle)",
            symbol, base_qty, available,
        )
        return CloseOrphan(available=available)

    cl_ord_id = f"polAsell{uuid.uuid4().hex[:10]}"
    resp = await adapter.place_market_order(
        symbol=symbol, side="sell", qty=base_qty, client_order_id=cl_ord_id,
    )
    if not resp.ok or not resp.venue_order_id:
        # Venue reject (EXTERNAL) — transient, preserve the position + retry.
        logger.warning(
            "[alpaca/close] %s sell rejected code=%s msg=%s — transient (no fault)",
            symbol, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
        )
        return None
    await asyncio.sleep(poll_delay_sec)
    row = await adapter.fetch_order(order_id=resp.venue_order_id)
    if not row:
        return None
    state = str(row.get("status") or "").lower()
    if state not in ALPACA_FILLED_STATES:
        # Accepted but not yet filled — EXTERNAL no-fill, retry next tick.
        return None
    try:
        return normalize_alpaca_fill(
            row, strategy_id=strategy_id, expected_price=last_price
        )
    except FillNormalizationError as exc:
        logger.warning("[alpaca/close] %s fill normalize failed %r", symbol, exc)
        return None
