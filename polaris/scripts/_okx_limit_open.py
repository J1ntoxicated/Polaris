"""#7 maker/limit OKX entry leg — post-only limit at touch + market fallback.

Split out of ``_smoke_real_roundtrip`` for the 500-LOC budget. The public entry
point ``real_okx_open_fill`` is re-exported there so existing import paths keep
working.

Entry path (``real_okx_open_fill``):

* **Strong signal** (``strength >= strong_signal_strength()``, default 1.5) →
  straight to market (do not risk missing the move waiting on a maker fill —
  AGGRESSIVE).
* **Otherwise** → post a ``post_only`` limit at the passive touch (best bid for
  a buy) so a fill pays the maker fee (0.02 %) + zero spread-cross. Poll up to
  ``limit_fill_wait_sec`` (default ~3 s). On (partial) fill → normalize +
  return; on timeout → **cancel + market fallback** (the fallback always closes
  the entry → flow preserved, NOT a throttle).
* **Fail-safe** — any error in the limit branch (ticker unavailable, post_only
  unsupported, adapter raise) falls back to the market path so the entry is
  never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from polaris.core.data.fill_normalizer import OKX_FILLED_STATES, normalize_okx_fill
from polaris.scripts._limit_exec_constants import (
    LIMIT_POLL_DELAY_SEC,
    limit_fill_wait_sec,
    strong_signal_strength,
)
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt

logger = logging.getLogger(__name__)

__all__ = ["real_okx_open_fill"]


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


async def _okx_market_open(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None,
    poll_delay_sec: float,
) -> OpenAttempt:
    """Market entry leg (taker): place buy → poll once → normalize.

    The unconditional fill path (strong signal or limit-fallback). Aggressive
    bias (flow_not_block): a market order with quote-ccy notional fills at best
    available; the normalizer records honest fill_price + slippage_bps so the
    edge-validation cost overlay measures the real cost.
    """
    buy_resp = await adapter.place_market_order(
        inst_id=inst_id,
        side="buy",
        notional_usd=notional_usd,
        client_order_id=f"polLbuy{uuid.uuid4().hex[:8]}",
        tgt_ccy="quote_ccy",
        ord_type="market",
    )
    if not buy_resp.ok or not buy_resp.venue_order_id:
        return OpenAttempt(
            fill=None, reject_code=buy_resp.code, reject_msg=buy_resp.msg,
        )
    await asyncio.sleep(poll_delay_sec)
    state = await adapter.fetch_order(inst_id=inst_id, ord_id=buy_resp.venue_order_id)
    rows = state.get("data", []) or []
    if not rows:
        return OpenAttempt(fill=None, reject_code="no_fill")
    # A non-filled venue state (e.g. 'canceled') is an EXTERNAL no-fill, not a
    # fault: route it to no_fill instead of letting normalize_okx_fill raise.
    attempt = _normalize_open_rows(rows, strategy_id=strategy_id, last_price=last_price)
    if attempt is None:
        order_state = str(rows[0].get("state") or "").lower()
        return OpenAttempt(
            fill=None, reject_code="no_fill", reject_msg=f"state={order_state}",
        )
    return attempt


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


async def real_okx_open_fill(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None = None,
    strength: float = 0.0,
    poll_delay_sec: float = LIMIT_POLL_DELAY_SEC,
) -> OpenAttempt:
    """OKX entry leg: post-only limit at touch → market fallback → normalize.

    See module docstring for the path-selection rules. Returns an
    ``OpenAttempt`` carrying the venue reject ``code``/``msg`` on a rejected
    order, the ``"no_fill"`` sentinel when accepted-but-unfilled, or the
    normalized ``Fill`` on success — so the caller can tell an EXTERNAL venue
    reject apart from an internal fault.
    """

    async def _market() -> OpenAttempt:
        return await _okx_market_open(
            adapter, inst_id=inst_id, notional_usd=notional_usd,
            strategy_id=strategy_id, last_price=last_price,
            poll_delay_sec=poll_delay_sec,
        )

    if strength >= strong_signal_strength():
        logger.info(
            "[okx/limit] %s strong signal strength=%.3f — market entry (no limit)",
            inst_id, strength,
        )
        return await _market()
    try:
        attempt = await _okx_post_only_open(
            adapter, inst_id=inst_id, notional_usd=notional_usd,
            strategy_id=strategy_id, last_price=last_price,
            poll_delay_sec=poll_delay_sec,
        )
    except Exception as exc:  # noqa: BLE001 — fail-safe: never block the entry
        logger.warning(
            "[okx/limit] %s limit path raised %r — market fallback", inst_id, exc,
        )
        return await _market()
    if attempt is not None:
        return attempt
    # Limit did not fill inside the wait window — market fallback (flow_not_block).
    logger.info("[okx/limit] %s limit unfilled in window — market fallback", inst_id)
    return await _market()
