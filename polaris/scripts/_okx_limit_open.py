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

from polaris.core.data.fill_normalizer import OKX_FILLED_STATES, Fill, normalize_okx_fill
from polaris.scripts._limit_exec_constants import (
    LIMIT_POLL_DELAY_SEC,
    OKX_BALANCE_CLAMP_BUFFER_FRAC,
    limit_fill_wait_sec,
    strong_signal_strength,
)
from polaris.scripts._okx_market_chunk import (
    _aggregate_child_fills,
    _split_market_notional,
)
from polaris.scripts._smoke_roundtrip_shared import MIN_OKX_NOTIONAL_USD, OpenAttempt

logger = logging.getLogger(__name__)

__all__ = ["fetch_okx_available_usdt", "real_okx_open_fill"]


def _parse_available_usdt(balance_body: dict[str, Any]) -> float | None:
    """Pull USDT ``availBal`` out of an OKX ``/account/balance`` payload.

    Returns the available USDT cash, or ``None`` when the payload has no USDT
    detail row (balance unknown → caller skips the clamp, prior behaviour).
    """
    data = balance_body.get("data") or []
    if not data:
        return None
    details = data[0].get("details") or []
    for ccy_detail in details:
        if ccy_detail.get("ccy") == "USDT":
            raw = ccy_detail.get("availBal")
            try:
                return float(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                return None
    return None


async def fetch_okx_available_usdt(adapter: Any) -> float | None:
    """Best-effort live OKX available-USDT lookup for the entry balance clamp.

    Any error returns ``None`` so the entry is never blocked by a balance-query
    failure (fail-safe / flow_not_block): an unknown balance simply skips the
    FIX-2 clamp and uses the prior un-clamped path.
    """
    try:
        body = await adapter.fetch_balance("USDT")
        return _parse_available_usdt(body)
    except Exception as exc:  # noqa: BLE001 — balance query is best-effort
        logger.warning("[okx/limit] available-USDT lookup failed %r", exc)
        return None


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


async def _okx_market_child(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None,
    poll_delay_sec: float,
) -> OpenAttempt:
    """One market child order (<=cap): place buy → poll once → normalize."""
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

    FIX 1 (OKX SPOT 1000-USDT market cap, reject 51201): a >1000 USDT entry is
    split into sequential market child-orders each <= the cap, each polled +
    normalized, then aggregated into ONE ``OpenAttempt`` (sum base_qty/fee,
    size-weighted avg fill_price). This makes a large entry FILL instead of
    being rejected/faulted (flow_not_block) — the total notional is unchanged.

    Aggressive bias (flow_not_block): a market order with quote-ccy notional
    fills at best available; the normalizer records honest fill_price +
    slippage_bps so the edge-validation cost overlay measures the real cost.

    Reject / orphan semantics:
    * No child filled at all → return the FIRST child's reject (no silent OK).
    * Some children filled then a later child fails → return the aggregated fill
      of the FUNDED children (never orphan a real position) and stop splitting.
    """
    chunks = _split_market_notional(notional_usd)
    if len(chunks) == 1:
        return await _okx_market_child(
            adapter, inst_id=inst_id, notional_usd=chunks[0],
            strategy_id=strategy_id, last_price=last_price,
            poll_delay_sec=poll_delay_sec,
        )
    filled: list[Fill] = []
    first_reject: OpenAttempt | None = None
    for chunk in chunks:
        child = await _okx_market_child(
            adapter, inst_id=inst_id, notional_usd=chunk,
            strategy_id=strategy_id, last_price=last_price,
            poll_delay_sec=poll_delay_sec,
        )
        if child.fill is not None:
            filled.append(child.fill)
            continue
        # Child did not fill — stop (do not keep churning oversized re-tries).
        if first_reject is None:
            first_reject = child
        break
    if filled:
        # At least one child funded a real position → track the aggregate.
        return OpenAttempt(fill=_aggregate_child_fills(filled))
    # No child filled at all → surface the first reject (51201/balance/no_fill).
    return first_reject or OpenAttempt(fill=None, reject_code="no_fill")


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


def _clamp_to_available(
    notional_usd: float, available_usdt: float | None
) -> float | None:
    """Clamp the entry notional to what the OKX demo wallet can fund (FIX 2).

    OKX SPOT cash buys (reject 51008 "insufficient balance") require real USDT
    cash. When ``available_usdt`` is known, cap the order at
    ``available_usdt × (1 - buffer)`` so SOME order gets through instead of a
    100 %-rejected oversized order (flow-ENABLING, NOT a defensive size cut).

    Returns the (possibly reduced) notional, or ``None`` when the fundable
    amount is below the OKX min notional — the caller skips cleanly rather than
    re-submitting an unfundable order forever (the 51008 churn this fixes).
    ``available_usdt=None`` (balance unknown) preserves the prior behaviour.
    """
    if available_usdt is None:
        return notional_usd
    fundable = available_usdt * (1.0 - OKX_BALANCE_CLAMP_BUFFER_FRAC)
    if fundable < MIN_OKX_NOTIONAL_USD:
        return None
    return min(notional_usd, fundable)


async def real_okx_open_fill(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None = None,
    strength: float = 0.0,
    poll_delay_sec: float = LIMIT_POLL_DELAY_SEC,
    available_usdt: float | None = None,
    prefer_maker: bool = False,
) -> OpenAttempt:
    """OKX entry leg: post-only limit at touch → market fallback → normalize.

    See module docstring for the path-selection rules. Returns an
    ``OpenAttempt`` carrying the venue reject ``code``/``msg`` on a rejected
    order, the ``"no_fill"`` sentinel when accepted-but-unfilled, or the
    normalized ``Fill`` on success — so the caller can tell an EXTERNAL venue
    reject apart from an internal fault.

    ``available_usdt`` (FIX 2): when the live OKX available USDT balance is
    known, the entry notional is clamped to what the wallet can fund BEFORE
    submit (prevents the 51008 insufficient-balance churn). ``None`` preserves
    the prior behaviour. A fundable amount below the OKX min notional skips the
    entry cleanly with ``reject_code="insufficient_balance"`` (no re-submit).

    ``prefer_maker`` (flow_pressure retune w02ccvq0q): force the maker-first
    (post-only at touch → TAKER fallback) path even for a STRONG signal that
    would otherwise skip straight to market. OFI momentum is not latency-critical
    to the tick, so it pays the maker fee (8 bps) when it can rest at the touch —
    and the existing post-only path ALWAYS falls back to a taker market order on a
    would-cross/reject/timeout, so the entry is NEVER missed (flow_not_block:
    cheaper when possible, never a skipped trade). Default ``False`` keeps the
    strength-gated behaviour byte-identical for every other caller.
    """
    clamped = _clamp_to_available(notional_usd, available_usdt)
    if clamped is None:
        logger.info(
            "[okx/limit] %s available USDT below min notional — skip (no churn)",
            inst_id,
        )
        return OpenAttempt(fill=None, reject_code="insufficient_balance")
    notional_usd = clamped

    async def _market() -> OpenAttempt:
        return await _okx_market_open(
            adapter, inst_id=inst_id, notional_usd=notional_usd,
            strategy_id=strategy_id, last_price=last_price,
            poll_delay_sec=poll_delay_sec,
        )

    if strength >= strong_signal_strength() and not prefer_maker:
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
