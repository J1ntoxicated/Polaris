"""OKX post-only maker entry leg — bounded reprice/repost loop + no-fill mode.

``_okx_post_only_open`` posts a ``post_only`` limit at the passive touch (best
bid for a buy) so a fill pays the maker fee + zero spread-cross. The #77 maker
exec-layer rebuild replaces the prior "post once → cancel → market fallback"
with a BOUNDED reprice/repost loop:

  * up to ``post_only_max_reposts()`` re-posts at the FRESH touch (each repost
    cancels the stale resting order — no leak), every attempt capped by
    ``limit_fill_wait_sec`` so the loop is bounded by BOTH count AND time (never
    infinite);
  * a ``no_fill`` mode — ``"market"`` (default, byte-identical to the legacy
    path) returns ``None`` so the caller falls back to a taker market order;
    ``"cancel"`` returns the ``"maker_no_fill"`` sentinel so the caller SKIPS
    (the weekend thin-book edge IS the passive fill — a miss is 0 realised cost,
    NOT a forced taker; flow_not_block never mandates a market order).

#91 maker fill-rate levers (all env-gated, DEFAULT-OFF → byte-identical):

  * TOUCH-WARD repost (``POLARIS_POST_ONLY_REPOST_STEP_BPS``) — each repost
    advances the post-only price from the deep touch toward the ask (clamped
    strictly below it → stays a maker), so a thin book that never trades back to
    the deep bid still fills at a price that crept toward where it trades;
  * WEEKEND taker fallback (``POLARIS_WEEKEND_MAKER_TAKER_FALLBACK`` /
    ``POLARIS_WEEKEND_MAKER_TAKER_AFTER_N``) — a ``"cancel"`` strategy can fall
    back to a TAKER market order (returns ``None``) after the loop is exhausted
    instead of skipping, a deliberate fill-guarantee-vs-edge knob (the maker_fill
    shadow keeps measuring the edge so the trade-off stays visible).

``_okx_limit_open`` calls it and, on ``None``, falls back to a market order; on
the ``"maker_no_fill"`` sentinel it returns the sentinel unchanged (skip).

The close/exit leg is a SEPARATE path (``real_okx_close_fill``) and is ALWAYS a
market order (taker) — the -1.0R rail / hard-stop is never a maker (a resting
stop could go unfilled past the rail). This module is ENTRY-only.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from polaris.scripts._limit_exec_constants import (
    LIMIT_POLL_DELAY_SEC,
    limit_fill_wait_sec,
    post_only_max_reposts,
    post_only_repost_step_bps,
    weekend_maker_taker_after_n,
)
from polaris.scripts._okx_open_shared import _normalize_open_rows
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt

logger = logging.getLogger(__name__)

# Sentinel reject_code on a no-fill in CANCEL mode: a deliberate skip (missed
# deep bid = 0 realised cost), distinct from a venue reject. The caller must NOT
# market-fallback on this (it is the whole point of the weekend maker thesis).
MAKER_NO_FILL_SENTINEL = "maker_no_fill"


async def _okx_maker_px(
    adapter: Any, *, inst_id: str, attempt_idx: int = 0
) -> float | None:
    """Resolve the post-only BUY price: best bid, advanced toward the ask by the
    TOUCH-WARD step (#91 lever a) on each repost.

    ``attempt_idx`` is the 0-based repost index. With the step OFF (default 0 bps)
    every attempt posts at the bare best bid (post-only, no cross — #77). With the
    step ON, attempt ``k`` posts at ``bid × (1 + step_bps × k / 1e4)``, CLAMPED
    strictly below the best ask so the order stays a maker (a price at/over the
    ask would be a would-cross post_only reject). A thin book that never trades
    back to the deep bid then fills at a price that crept toward where it trades.

    Returns ``None`` when the ticker has no usable bid so the caller falls back to
    a plain market order (fail-safe — never blocks the entry).
    """
    tk = await adapter.fetch_ticker(inst_id)
    bid = tk.get("bidPx")
    try:
        px = float(bid) if bid not in (None, "") else 0.0
    except (TypeError, ValueError):
        return None
    if px <= 0.0:
        return None
    step_bps = post_only_repost_step_bps()
    if step_bps <= 0.0 or attempt_idx <= 0:
        return px  # OFF, or the first post — bare touch (byte-identical to #77).
    advanced = px * (1.0 + step_bps * float(attempt_idx) / 10_000.0)
    # Clamp STRICTLY below the ask so the post_only never crosses (stays maker).
    ask_raw = tk.get("askPx")
    try:
        ask = float(ask_raw) if ask_raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        ask = 0.0
    if ask > 0.0:
        max_px = ask * (1.0 - 1e-4)  # one tick-fraction below the ask
        if max_px <= px:
            return px  # degenerate/crossed book → never go below the touch
        advanced = min(advanced, max_px)
    return advanced


async def _post_and_poll_once(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None,
    poll_delay_sec: float,
    maker_px: float,
) -> tuple[OpenAttempt | None, str | None]:
    """One post-only attempt: place at ``maker_px``, poll to the per-attempt TTL.

    Returns ``(attempt, resting_ord_id)``:
      * ``(attempt, None)`` — filled (the order is terminal; nothing to cancel);
      * ``(None, ord_id)`` — accepted but unfilled at TTL (the caller cancels +
        reposts / resolves);
      * ``(None, None)`` — the post_only was rejected at submit (would-cross /
        venue reject) → the caller resolves (market or cancel mode).
    """
    resp = await adapter.place_market_order(
        inst_id=inst_id,
        side="buy",
        notional_usd=notional_usd,
        client_order_id=f"polLpo{uuid.uuid4().hex[:8]}",
        ord_type="post_only",
        last_price_hint=maker_px,
    )
    if not resp.ok or not resp.venue_order_id:
        logger.info(
            "[okx/limit] %s post_only rejected code=%s — resolve no-fill",
            inst_id, resp.code,
        )
        return (None, None)
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
            return (attempt, None)
        if time.monotonic() >= deadline:
            return (None, ord_id)


async def _cancel_and_recheck(
    adapter: Any,
    *,
    inst_id: str,
    ord_id: str,
    strategy_id: str,
    last_price: float | None,
) -> OpenAttempt | None:
    """Cancel a resting order, then re-poll once for a cancel-race partial fill.

    Returns the (partial) ``OpenAttempt`` if the order filled during the cancel
    race (orphan guard — a partial leaves a REAL position that must be tracked),
    else ``None`` (nothing rests at the venue). Best-effort cancel: a raise is
    logged, never propagated (degrade-never-crash).
    """
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


async def _okx_post_only_open(
    adapter: Any,
    *,
    inst_id: str,
    notional_usd: float,
    strategy_id: str,
    last_price: float | None,
    poll_delay_sec: float = LIMIT_POLL_DELAY_SEC,
    no_fill: str = "market",
) -> OpenAttempt | None:
    """Post-only limit buy at the touch with a BOUNDED reprice/repost loop.

    Posts at the fresh best bid, polls to the per-attempt TTL, and on a no-fill
    re-resolves the touch + re-posts up to ``post_only_max_reposts()`` times
    (each repost cancels the prior resting order — no leaked maker order). The
    loop is bounded by BOTH the repost count AND the per-attempt TTL.

    Returns:
      * the filled ``OpenAttempt`` (incl. a cancel-race partial) on success;
      * ``None`` when ``no_fill="market"`` (default) and no attempt filled — the
        caller falls back to a market order (byte-identical to the legacy path);
      * ``OpenAttempt(reject_code="maker_no_fill")`` when ``no_fill="cancel"``
        and no attempt filled — a deliberate SKIP (the weekend thin-book thesis:
        a missed deep bid is 0 realised cost, never a forced taker) — UNLESS the
        #91 weekend taker-fallback knob is met, in which case it returns ``None``
        so the caller market-falls-back (a guaranteed taker fill).

    The repost price progression honours the #91 touch-ward step (each repost
    creeps from the deep touch toward the ask, clamped below it → stays a maker).

    Raises only on an unexpected adapter error — the caller treats a raise as a
    fail-safe market fallback.
    """
    max_reposts = post_only_max_reposts()
    resting_ord_id: str | None = None
    # 1 initial post + up to max_reposts re-posts (bounded — never infinite).
    for attempt_idx in range(max_reposts + 1):
        # TOUCH-WARD (#91 lever a): attempt_idx advances the post-only price from
        # the deep touch toward the ask each repost (OFF by default → bare touch).
        maker_px = await _okx_maker_px(
            adapter, inst_id=inst_id, attempt_idx=attempt_idx
        )
        if maker_px is None:
            break  # no usable bid → resolve no-fill (market / cancel)
        attempt, resting_ord_id = await _post_and_poll_once(
            adapter, inst_id=inst_id, notional_usd=notional_usd,
            strategy_id=strategy_id, last_price=last_price,
            poll_delay_sec=poll_delay_sec, maker_px=maker_px,
        )
        if attempt is not None:
            _log_maker_fill_basis(
                strategy_id=strategy_id, inst_id=inst_id, touch_px=maker_px,
                attempt=attempt, reposts=attempt_idx,
            )
            return attempt
        # Unfilled (or submit-rejected) — cancel any resting order before the
        # next repost / before resolving the no-fill (no leaked maker order).
        if resting_ord_id is not None:
            partial = await _cancel_and_recheck(
                adapter, inst_id=inst_id, ord_id=resting_ord_id,
                strategy_id=strategy_id, last_price=last_price,
            )
            resting_ord_id = None
            if partial is not None:
                _log_maker_fill_basis(
                    strategy_id=strategy_id, inst_id=inst_id, touch_px=maker_px,
                    attempt=partial, reposts=attempt_idx,
                )
                return partial
    # Exhausted the bounded repost loop with no fill — resolve per no-fill mode.
    attempts = max_reposts + 1
    if no_fill == "cancel":
        # WEEKEND TAKER FALLBACK (#91 lever b): a cancel-mode strategy normally
        # SKIPS (a missed deep bid = 0 realised cost — the weekend maker thesis).
        # With the opt-in knob, once the loop has taken >= N attempts it instead
        # falls back to a TAKER market order (returns None → the caller markets).
        # The maker_fill shadow keeps measuring the edge, so the fill-guarantee
        # vs maker-edge trade-off stays visible. Default (knob OFF) = skip.
        taker_after_n = weekend_maker_taker_after_n()
        if taker_after_n is not None and attempts >= taker_after_n:
            logger.info(
                "[okx/limit] %s post_only no-fill after %d attempt(s) — WEEKEND "
                "taker fallback (knob N=%d met)", inst_id, attempts, taker_after_n,
            )
            return None
        logger.info(
            "[okx/limit] %s post_only no-fill after %d attempt(s) — CANCEL/skip "
            "(maker thesis: missed deep bid = 0 cost)", inst_id, attempts,
        )
        return OpenAttempt(fill=None, reject_code=MAKER_NO_FILL_SENTINEL)
    logger.info(
        "[okx/limit] %s post_only no-fill after %d attempt(s) — market fallback",
        inst_id, attempts,
    )
    return None


def _log_maker_fill_basis(
    *,
    strategy_id: str,
    inst_id: str,
    touch_px: float,
    attempt: OpenAttempt,
    reposts: int,
) -> None:
    """Best-effort real-fee maker-fill shadow (entry-BASIS + real-maker net).

    Component C (#77): OKX demo's flat 70 bps hides the maker edge, so we record
    fill-vs-touch basis + a clean_fill/pick_off label per maker fill (the net is
    computed off real_fee_bps(is_maker=True) inside the shadow module). The DB
    conn is loop-owned and not threaded here, so this logs at INFO for the live
    shadow tail; the persisted row is written from the production close/fill path
    that DOES hold the conn. Never raises (degrade-never-crash).
    """
    fill = attempt.fill
    if fill is None:
        return
    try:
        from polaris.core.economics.maker_fill_shadow import (
            compute_entry_basis_bps,
        )

        basis = compute_entry_basis_bps(
            side="buy", touch_px=touch_px, fill_px=fill.fill_price,
        )
        outcome = "clean_fill" if basis >= 0.0 else "pick_off"
        logger.info(
            "[maker-fill-shadow] %s %s basis=%.2fbps fill=%.6f touch=%.6f "
            "reposts=%d outcome=%s",
            strategy_id, inst_id, basis, fill.fill_price, touch_px, reposts,
            outcome,
        )
    except Exception as exc:  # noqa: BLE001 — shadow is best-effort, never blocks
        logger.debug("[maker-fill-shadow] basis log skipped: %r", exc)
