"""Maker exec layer (#77) — post-only reprice/repost loop + no-fill mode.

DEMO/PAPER only — every test injects a fake OKX adapter, NO real venue network
call ever happens. Covers the two NEW behaviours added to the production maker
entry leg over the prior "once-then-market-fallback" path:

  1. BOUNDED reprice/repost loop — an unfilled post-only is re-posted at the
     fresh touch up to ``POST_ONLY_MAX_REPOSTS`` times (each repost cancels the
     prior resting order — no leak), then resolves the no-fill. The loop is
     bounded by BOTH the repost count AND the per-attempt TTL (no infinite loop).

  2. ``no_fill`` mode — ``"market"`` (default, byte-identical to the legacy
     path) returns ``None`` so the caller falls back to a taker market order;
     ``"cancel"`` returns the ``"maker_no_fill"`` sentinel so the caller SKIPS
     (the weekend thin-book strategy: a missed deep-bid fill = 0 realised cost,
     NOT a forced taker — flow_not_block does not require a market order).

flow_not_block: the cancel mode is a legitimate skip for a strategy whose edge
IS the passive fill; it never blocks a strategy that wants the taker fallback.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._okx_post_only import _okx_post_only_open
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt


def _resp(*, ok: bool = True, ord_id: str = "ord_1", code: str = "0") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=ok, venue_order_id=ord_id if ok else None,
        client_order_id="cl1", code=code, msg="", raw={},
    )


def _filled_row(*, price: float = 59_999.0) -> dict[str, Any]:
    return {
        "ordId": "ord_1", "clOrdId": "cl1", "instId": "BTC-USDT",
        "side": "buy", "tgtCcy": "quote_ccy", "accFillSz": "100.0",
        "avgPx": str(price), "fee": "-0.02", "feeCcy": "USDT",
        "state": "filled", "uTime": str(int(time.time() * 1000)),
    }


class _RepostFakeOKX:
    """Counts post-only places + cancels; never fills (forces the repost loop).

    Each ``place_market_order`` (post_only) returns a fresh ord_id; ``fetch_order``
    always reports an unfilled (live) order so every attempt times out and the
    loop must cancel + repost. A ``bid`` that ticks down each fetch lets a test
    assert the repost re-resolves the FRESH touch.
    """

    def __init__(self, *, bid_seq: list[str] | None = None) -> None:
        self._bid_seq = bid_seq or ["59999.0"]
        self._bid_idx = 0
        self.place_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []

    async def fetch_ticker(self, inst_id: str) -> dict[str, Any]:
        bid = self._bid_seq[min(self._bid_idx, len(self._bid_seq) - 1)]
        self._bid_idx += 1
        return {"bidPx": bid, "askPx": "60001.0"}

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.place_calls.append(kwargs)
        return _resp(ord_id=f"ord_{len(self.place_calls)}")

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        # Always unfilled while resting (state=live) → no normalized fill.
        return {"data": [{"ordId": ord_id, "state": "live", "accFillSz": "0"}]}

    async def cancel_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        self.cancel_calls.append(ord_id)
        return {"code": "0"}


class _FillOnRepostFakeOKX(_RepostFakeOKX):
    """Fills only on the Nth post-only attempt (tests the loop reaches a fill)."""

    def __init__(self, *, fill_on_attempt: int) -> None:
        super().__init__()
        self._fill_on = fill_on_attempt

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        # ord_id is f"ord_{n}"; fill once we reach the target attempt.
        attempt_n = int(ord_id.split("_")[1])
        if attempt_n >= self._fill_on:
            return {"data": [_filled_row()]}
        return {"data": [{"ordId": ord_id, "state": "live", "accFillSz": "0"}]}


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_LIMIT_FILL_WAIT_SEC", "0.02")
    monkeypatch.setenv("POLARIS_POST_ONLY_MAX_REPOSTS", "3")


# ---------------------------------------------------------------------------
# (1) bounded repost loop — N attempts then resolve, cancel each prior order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repost_loop_is_bounded_by_max_reposts() -> None:
    fake = _RepostFakeOKX(bid_seq=["100.0", "99.0", "98.0", "97.0", "96.0"])
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="market",
    )
    # 1 initial post + 3 reposts = 4 total places, never unbounded.
    assert len(fake.place_calls) == 4
    assert all(c["ord_type"] == "post_only" for c in fake.place_calls)
    # Each resting order cancelled before the next repost (no leak): 4 places →
    # the 1st..4th are all cancelled on no-fill (final too, before resolving).
    assert len(fake.cancel_calls) == 4
    # market no_fill mode → None so the CALLER falls back to a taker market order.
    assert attempt is None


@pytest.mark.asyncio
async def test_repost_reresolves_fresh_touch() -> None:
    fake = _RepostFakeOKX(bid_seq=["100.0", "99.0", "98.0", "97.0"])
    await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="market",
    )
    hints = [c["last_price_hint"] for c in fake.place_calls]
    # The repost posts at the FRESH (lower) bid each time, not the stale touch.
    assert hints == [100.0, 99.0, 98.0, 97.0]


@pytest.mark.asyncio
async def test_repost_loop_returns_fill_when_a_repost_fills() -> None:
    fake = _FillOnRepostFakeOKX(fill_on_attempt=2)
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    assert attempt is not None
    assert isinstance(attempt.fill, Fill)
    # Stopped reposting as soon as it filled (2 places, not the full 4).
    assert len(fake.place_calls) == 2


# ---------------------------------------------------------------------------
# (2) no_fill mode — cancel returns the skip sentinel (NO market fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_fill_cancel_returns_skip_sentinel() -> None:
    fake = _RepostFakeOKX()
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    # cancel mode → a DISTINCT skip sentinel (not None), so the caller does NOT
    # market-fallback (a missed deep-bid = 0 realised cost, not a forced taker).
    assert isinstance(attempt, OpenAttempt)
    assert attempt.fill is None
    assert attempt.reject_code == "maker_no_fill"
    # The last resting order is still cancelled (no leaked live maker order).
    assert len(fake.cancel_calls) == len(fake.place_calls)


@pytest.mark.asyncio
async def test_no_fill_market_default_returns_none_for_fallback() -> None:
    fake = _RepostFakeOKX()
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="rsi_bb_pullback", last_price=100.0,
        poll_delay_sec=0.0,  # no_fill defaults to "market"
    )
    # Default market mode → None so the existing market fallback fires
    # (byte-identical to the legacy once-then-market path for every old caller).
    assert attempt is None
