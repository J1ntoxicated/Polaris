"""OKX venue-resting conditional stop arming — split out of ``_production_recalc_exit``.

Move-only extraction for the ≤500-LOC budget. ``arm_okx_venue_stop`` rests the
freshly-ratcheted software stop AT the OKX venue so a gap through it triggers
venue-side in the inter-tick gap (not on the next ~5s poll).
``_production_recalc_exit`` re-exports ``arm_okx_venue_stop`` so existing import
paths keep working.

PRECISE-EXIT loss-defence, NOT a throttle: it never changes size, never blocks
entry, and only mirrors the stop the FSM already computed for THIS position.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# A resting venue stop is only cancelled+replaced when the trailing stop tightens
# by at least this fraction of the trigger price — steady ticks where the stop is
# unchanged (or loosens, which the ratchet forbids) are a no-op (no churn). This
# bounds the per-tick venue I/O to symbols whose stop actually moved.
_VENUE_STOP_REPLACE_EPS_FRAC: float = 0.0005  # 5 bps

# When the OKX algo endpoint reports the conditional stop is unavailable for a
# symbol, that result is cached on the trade for this long so the arm does NOT
# re-attempt every ~5s recalc tick (XRP-USDT logged ~2 arm attempts/sec → algo
# rate-limit risk). After the window the arm retries ONCE (the endpoint may have
# recovered); the software-polled stop is the backstop the whole time (fail-open
# UNCHANGED — never blocks the trade, never changes size). The marker lives on
# the per-position trade object, so it clears automatically when the position
# closes / changes (a fresh symbol carries no cooldown and still arms).
_VENUE_STOP_UNAVAIL_COOLDOWN_SEC: float = 300.0


async def arm_okx_venue_stop(
    *,
    trade: Any,
    okx_adapter: Any,
    side: str,
    stop_price: float | None,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Place / replace a VENUE-RESTING conditional stop on OKX for ``trade``.

    The software stop fires only on the next ~5s recalc tick; an OKX SPOT alt can
    gap through it in that window and the polled market close then fills far below
    (the -34..-100R orphan). This rests the stop AT the venue so OKX triggers it
    the instant the trigger crosses — closing the inter-tick gap. PRECISE-EXIT
    loss-defence, NOT a throttle: it never changes size, never blocks entry, and
    only mirrors the stop the FSM already computed for THIS position.

    Fully fail-open (flow_not_block): a missing adapter / non-OKX venue / no stop
    yet / any venue error leaves the in-memory ref untouched and the software
    stop as the backstop. Cancel-then-replace fires only when the stop TIGHTENED
    by ``_VENUE_STOP_REPLACE_EPS_FRAC`` (steady ticks are a no-op).
    """
    if okx_adapter is None or stop_price is None or stop_price <= 0.0:
        return
    if getattr(trade, "venue", "") != "okx":
        return
    base_qty = float(getattr(trade, "base_qty", 0.0) or 0.0)
    if base_qty <= 0.0:
        return
    prev_px = getattr(trade, "okx_stop_px", None)
    prev_algo = getattr(trade, "okx_stop_algo_id", None)
    # Replace only on a material tighten (SPOT long stop ratchets UP) — a steady
    # tick where the stop is unchanged (or loosens, which the ratchet forbids) is
    # a no-op so the per-tick venue I/O is bounded to symbols whose stop moved.
    if (
        prev_algo is not None
        and prev_px is not None
        and stop_price <= float(prev_px) * (1.0 + _VENUE_STOP_REPLACE_EPS_FRAC)
    ):
        return
    # Rate-defence: if the algo endpoint was already reported unavailable for this
    # symbol, skip the re-attempt until the cooldown elapses (no per-tick arm spam
    # → no OKX algo-order rate-limit risk). Software stop stays the backstop.
    unavail_until = getattr(trade, "okx_stop_unavail_until", None)
    if unavail_until is not None and now_monotonic() < float(unavail_until):
        return
    # OKX SPOT positions are long-only → the protective stop is a SELL.
    close_side = "sell" if side == "long" else "buy"
    try:
        if prev_algo is not None:
            with contextlib.suppress(Exception):
                await okx_adapter.cancel_algo_order(
                    inst_id=trade.symbol, algo_id=prev_algo
                )
        resp = await okx_adapter.place_conditional_stop(
            inst_id=trade.symbol,
            side=close_side,
            base_qty=base_qty,
            trigger_px=stop_price,
            client_order_id=f"polstop{(trade.position_id or trade.symbol)}",
        )
    except Exception as exc:  # noqa: BLE001 — resting stop is best-effort
        logger.warning(
            "[L6/exit] venue-stop arm failed %s — software stop holds: %r",
            getattr(trade, "symbol", "?"), exc,
        )
        return
    if resp.ok and resp.algo_id:
        trade.okx_stop_algo_id = resp.algo_id
        trade.okx_stop_px = stop_price
        # Endpoint healthy → clear any prior unavailable cooldown.
        trade.okx_stop_unavail_until = None
        logger.info(
            "[L6/exit] venue-stop armed %s algoId=%s slTriggerPx=%.6g",
            trade.symbol, resp.algo_id, stop_price,
        )
        return
    if resp.code == "51068":
        # 51068 = a stop with THIS deterministic algoClOrdId already rests on OKX:
        # an orphan from before a restart (the in-mem algoId was lost, so prev_algo
        # was None and this re-POST collided). Recover the resting algoId via a GET
        # and ADOPT it so the NEXT tighten cancel-then-replaces it — otherwise the
        # arm loops 51068 forever with the venue stop frozen at the prior run's
        # stale trigger, defeating the ratchet. Fail-open: a recovery miss falls
        # through to the software-stop backstop below.
        existing = await okx_adapter.fetch_algo_order(
            inst_id=trade.symbol,
            algo_cl_ord_id=f"polstop{(trade.position_id or trade.symbol)}",
        )
        rows = existing.raw.get("data") or [{}]
        state = str(rows[0].get("state", "")).lower()
        if existing.ok and existing.algo_id and state == "live":
            adopted_px = stop_price
            with contextlib.suppress(TypeError, ValueError):
                px = float(rows[0].get("slTriggerPx") or 0.0)
                if px > 0.0:
                    adopted_px = px
            trade.okx_stop_algo_id = existing.algo_id
            trade.okx_stop_px = adopted_px
            trade.okx_stop_unavail_until = None
            logger.info(
                "[L6/exit] venue-stop ADOPTED orphan %s algoId=%s slTriggerPx=%.6g "
                "— next tighten will cancel-replace",
                trade.symbol, existing.algo_id, adopted_px,
            )
            return
    # Algo endpoint unavailable / orphan unrecoverable — cache the result so the
    # arm backs off instead of re-attempting every tick; keep the software stop.
    trade.okx_stop_unavail_until = (
        now_monotonic() + _VENUE_STOP_UNAVAIL_COOLDOWN_SEC
    )
    logger.info(
        "[L6/exit] venue-stop unavailable %s (%s) — software stop backstop, "
        "arm backed off %.0fs",
        trade.symbol, resp.code, _VENUE_STOP_UNAVAIL_COOLDOWN_SEC,
    )
