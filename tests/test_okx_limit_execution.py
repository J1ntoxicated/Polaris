"""#7 maker/limit execution — post-only limit at touch + market fallback.

All tests inject a fake OKX adapter; **no real venue network call ever
happens**. Covers: limit immediate fill (no market), limit timeout → cancel +
market fallback, strong signal → market (no limit), limit-path exception →
market fallback (entry guaranteed), and the partial-fill-on-cancel orphan guard.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._smoke_real_roundtrip import real_okx_open_fill


def _resp(*, ok: bool = True, ord_id: str = "ord_1", code: str = "0") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=ok, venue_order_id=ord_id if ok else None,
        client_order_id="cl1", code=code, msg="", raw={},
    )


def _row(*, state: str = "filled", price: float = 60_000.0, acc: str = "100.0") -> dict[str, Any]:
    return {
        "ordId": "ord_1", "clOrdId": "cl1", "instId": "BTC-USDT",
        "side": "buy", "tgtCcy": "quote_ccy", "accFillSz": acc,
        "avgPx": str(price), "fee": "-0.02", "feeCcy": "USDT",
        "state": state, "uTime": str(int(time.time() * 1000)),
    }


class _FakeOKX:
    """State-aware fake adapter.

    ``waiting_row`` is returned by ``fetch_order`` while the post-only limit is
    resting (before timeout/cancel). After ``cancel_order`` fires, ``post_cancel_row``
    is returned. ``market_row`` is returned once a market order is placed (the
    fallback leg). This mirrors the real REST sequence without relying on poll
    iteration counts.
    """

    def __init__(
        self,
        *,
        place_resp: Any,
        waiting_row: dict[str, Any] | None = None,
        post_cancel_row: dict[str, Any] | None = None,
        market_row: dict[str, Any] | None = None,
        bid: str | None = "59999.0",
    ) -> None:
        self._place_resp = place_resp
        self._waiting_row = waiting_row
        self._post_cancel_row = post_cancel_row
        self._market_row = market_row
        self._bid = bid
        self._cancelled = False
        self._market_placed = False
        self.place_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []

    async def fetch_ticker(self, inst_id: str) -> dict[str, Any]:
        return {"bidPx": self._bid, "askPx": "60001.0"} if self._bid is not None else {}

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.place_calls.append(kwargs)
        if kwargs["ord_type"] == "market":
            self._market_placed = True
        return self._place_resp

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        if self._market_placed:
            return {"data": [self._market_row] if self._market_row else []}
        if self._cancelled:
            return {"data": [self._post_cancel_row] if self._post_cancel_row else []}
        return {"data": [self._waiting_row] if self._waiting_row else []}

    async def cancel_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        self._cancelled = True
        self.cancel_calls.append({"ord_id": ord_id})
        return {"code": "0"}


@pytest.fixture(autouse=True)
def _fast_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the limit fill-wait so timeout tests run fast."""
    monkeypatch.setenv("POLARIS_LIMIT_FILL_WAIT_SEC", "0.05")


# ---------------------------------------------------------------------------
# (1) limit immediate fill → market is NOT used (single post_only place)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_immediate_fill_no_market() -> None:
    adapter = _FakeOKX(place_resp=_resp(), waiting_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="volume_burst", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "post_only"
    assert adapter.place_calls[0]["last_price_hint"] == 59_999.0  # best bid touch
    assert adapter.cancel_calls == []  # filled → no cancel


# ---------------------------------------------------------------------------
# (2) limit unfilled → timeout → cancel + market fallback fills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_timeout_cancel_then_market_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # post_only stays 'live' (unfilled) on every poll; after the bounded reprice
    # loop (1 post here) the resting order is cancelled, then market fallback
    # returns a filled row. One repost-attempt keeps the assertion deterministic.
    monkeypatch.setenv("POLARIS_POST_ONLY_MAX_REPOSTS", "1")
    adapter = _FakeOKX(
        place_resp=_resp(ord_id="ord_1"),
        waiting_row=_row(state="live", acc="0"),          # unfilled while resting
        post_cancel_row=_row(state="canceled", acc="0"),  # re-check after cancel
        market_row=_row(state="filled"),                  # market fallback fill
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="volume_burst", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    # The bounded reprice loop posts post_only (1 + reposts), cancelling each
    # resting order, then falls back to ONE market order (the final place).
    ord_types = [c["ord_type"] for c in adapter.place_calls]
    assert ord_types[0] == "post_only"
    assert ord_types[-1] == "market"
    assert ord_types.count("market") == 1
    # Every resting post_only was cancelled before the next repost / fallback.
    assert len(adapter.cancel_calls) == ord_types.count("post_only")


# ---------------------------------------------------------------------------
# (3) strong signal → straight to market (no limit place, no ticker fetch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strong_signal_skips_limit_goes_market() -> None:
    adapter = _FakeOKX(place_resp=_resp(), market_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="volume_burst", last_price=60_000.0, strength=1.6,
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "market"
    assert adapter.cancel_calls == []


# ---------------------------------------------------------------------------
# (3b) flow_pressure RE-AIM (lever 3): prefer_maker forces the maker-first
# (post-only at touch → TAKER fallback) path EVEN for a strong signal that would
# otherwise skip straight to market — OFI momentum pays 8 bps when it can rest,
# and NEVER misses the trade (post-only would-cross/reject → taker fallback).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefer_maker_posts_post_only_even_on_strong_signal() -> None:
    # Strong strength (1.6) would normally skip to market; prefer_maker routes it
    # through post_only first, and here the maker rests + fills (8 bps, no cross).
    adapter = _FakeOKX(place_resp=_resp(), waiting_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="flow_pressure", last_price=60_000.0, strength=1.6,
        poll_delay_sec=0.0, prefer_maker=True,
    )
    assert isinstance(attempt.fill, Fill)
    # Maker-first: the ONLY place is post_only (it filled at the touch).
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "post_only"
    assert adapter.place_calls[0]["last_price_hint"] == 59_999.0  # best bid touch
    assert adapter.cancel_calls == []


@pytest.mark.asyncio
async def test_prefer_maker_falls_back_to_taker_on_would_cross(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # post_only rejected (would cross the book) — the trade must STILL fill via the
    # taker market fallback (flow_not_block: cheaper when possible, never missed).
    monkeypatch.setenv("POLARIS_POST_ONLY_MAX_REPOSTS", "1")
    rejected = _resp(ok=False, code="51020")  # post_only would-cross reject

    class _RejectThenMarket(_FakeOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            self.place_calls.append(kwargs)
            if kwargs["ord_type"] == "post_only":
                return rejected
            self._market_placed = True
            return _resp(ord_id="mkt_1")

    adapter = _RejectThenMarket(
        place_resp=_resp(), market_row=_row(state="filled"),
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="flow_pressure", last_price=60_000.0, strength=1.6,
        poll_delay_sec=0.0, prefer_maker=True,
    )
    # NEVER a no-fill: the post_only would-cross (re-tried in the bounded loop)
    # fell back to a taker market fill — the first place is post_only, the last
    # is the single market fallback.
    assert isinstance(attempt.fill, Fill)
    ord_types = [c["ord_type"] for c in adapter.place_calls]
    assert ord_types[0] == "post_only"
    assert ord_types[-1] == "market"
    assert ord_types.count("market") == 1


@pytest.mark.asyncio
async def test_prefer_maker_false_keeps_strong_signal_market_path() -> None:
    # Default (prefer_maker=False) — a strong signal still skips to market
    # (byte-identical to the pre-retune behaviour for every other caller).
    adapter = _FakeOKX(place_resp=_resp(), market_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=1.6,
        poll_delay_sec=0.0, prefer_maker=False,
    )
    assert isinstance(attempt.fill, Fill)
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "market"


# ---------------------------------------------------------------------------
# (4) limit path raises → fail-safe market fallback still fills the entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_exception_falls_back_to_market() -> None:
    calls: dict[str, int] = {"post_only": 0, "market": 0}

    class _Boom(_FakeOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            calls[kwargs["ord_type"]] += 1
            if kwargs["ord_type"] == "post_only":
                raise RuntimeError("post_only unsupported on this pair")
            self._market_placed = True
            return _resp()

    adapter = _Boom(place_resp=_resp(), market_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="volume_burst", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0,
    )
    # Entry NOT blocked — market fallback filled it.
    assert isinstance(attempt.fill, Fill)
    assert calls["post_only"] == 1 and calls["market"] == 1


# ---------------------------------------------------------------------------
# (5) no usable bid (ticker has no bidPx) → market fallback (fail-safe)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_bid_falls_back_to_market() -> None:
    adapter = _FakeOKX(
        place_resp=_resp(), market_row=_row(state="filled"), bid=None,
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="volume_burst", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    # No post_only place (no bid) — straight market.
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "market"


# ---------------------------------------------------------------------------
# (6) partial fill on cancel race → tracked as a REAL position (orphan guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_fill_on_cancel_is_tracked() -> None:
    # Unfilled while waiting, then a partial fill (accFillSz>0) appears in the
    # post-cancel re-check → must be normalized + returned, never orphaned.
    adapter = _FakeOKX(
        place_resp=_resp(ord_id="ord_1"),
        waiting_row=_row(state="live", acc="0"),           # unfilled while resting
        post_cancel_row=_row(state="canceled", acc="40.0"),  # cancel race partial
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="volume_burst", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    assert attempt.fill.base_qty > 0.0
    assert len(adapter.cancel_calls) == 1
    # NO market fallback — the partial fill is a real position.
    assert all(c["ord_type"] == "post_only" for c in adapter.place_calls)


# ---------------------------------------------------------------------------
# FIX 1 — OKX SPOT 1000-USDT market-order cap: split a >1000 USDT entry into
# sequential market child-orders so it FILLS instead of being rejected (51201).
# ---------------------------------------------------------------------------


class _ChildSplitOKX:
    """Fake adapter for the market-split path.

    Each market POST gets its own ordId and a filled row whose accFillSz is the
    child's notional / price (so the aggregated base_qty == sum of children).
    No ticker / post_only path is exercised (strong-signal → straight market).
    """

    def __init__(self, *, price: float = 10.0) -> None:
        self._price = price
        self.market_notionals: list[float] = []
        self._rows: dict[str, dict[str, Any]] = {}

    async def fetch_ticker(self, inst_id: str) -> dict[str, Any]:
        return {"bidPx": str(self._price), "askPx": str(self._price)}

    async def place_market_order(self, **kwargs: Any) -> Any:
        assert kwargs["ord_type"] == "market"
        notional = float(kwargs["notional_usd"])
        self.market_notionals.append(notional)
        ord_id = f"ord_{len(self.market_notionals)}"
        base = notional / self._price
        self._rows[ord_id] = {
            "ordId": ord_id, "clOrdId": kwargs["client_order_id"],
            "instId": kwargs["inst_id"], "side": "buy", "tgtCcy": "quote_ccy",
            "accFillSz": f"{base:.8f}", "avgPx": str(self._price),
            "fee": "-0.02", "feeCcy": "USDT", "state": "filled",
            "uTime": str(int(time.time() * 1000)),
        }
        return _resp(ord_id=ord_id)

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        row = self._rows.get(ord_id)
        return {"data": [row] if row else []}


@pytest.mark.asyncio
async def test_market_entry_over_cap_splits_into_children() -> None:
    """A 1468-USDT entry must be POSTed as children each <= 1000 USDT, and the
    aggregated fill must reflect the full notional (no order ever >1000 USDT)."""
    adapter = _ChildSplitOKX(price=10.0)
    attempt = await real_okx_open_fill(
        adapter, inst_id="ALGO-USDT", notional_usd=1_468.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,  # strong → market
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    # Children: 1000 + 468 (both <= cap), never a single >1000 POST.
    assert adapter.market_notionals == [1_000.0, 468.0]
    assert all(n <= 1_000.0 for n in adapter.market_notionals)
    # Aggregated base_qty == sum of children (1468/10 = 146.8).
    assert attempt.fill.base_qty == pytest.approx(146.8, rel=1e-6)
    # size-weighted avg price (uniform here) + summed fee.
    assert attempt.fill.fill_price == pytest.approx(10.0, rel=1e-6)
    assert attempt.fill.fee_usd == pytest.approx(1.468, rel=1e-6)  # real 10bps of $1468


@pytest.mark.asyncio
async def test_market_entry_at_or_below_cap_single_order() -> None:
    """A <=1000 USDT entry is a single market POST (no needless splitting)."""
    adapter = _ChildSplitOKX(price=10.0)
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=950.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    assert adapter.market_notionals == [950.0]


@pytest.mark.asyncio
async def test_market_entry_exactly_3000_three_children() -> None:
    """3000 USDT → 1000 + 1000 + 1000 (N ceil split, each at the cap)."""
    adapter = _ChildSplitOKX(price=10.0)
    attempt = await real_okx_open_fill(
        adapter, inst_id="ETH-USDT", notional_usd=3_000.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,
        poll_delay_sec=0.0,
    )
    assert isinstance(attempt.fill, Fill)
    assert adapter.market_notionals == [1_000.0, 1_000.0, 1_000.0]
    assert attempt.fill.base_qty == pytest.approx(300.0, rel=1e-6)


@pytest.mark.asyncio
async def test_split_first_child_rejected_returns_reject() -> None:
    """If the FIRST child is rejected (no fill at all) the attempt carries the
    reject code (no silent success, no orphan)."""

    class _RejectFirst(_ChildSplitOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            self.market_notionals.append(float(kwargs["notional_usd"]))
            return _resp(ok=False, code="51201")

    adapter = _RejectFirst(price=10.0)
    attempt = await real_okx_open_fill(
        adapter, inst_id="ALGO-USDT", notional_usd=1_468.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,
        poll_delay_sec=0.0,
    )
    assert attempt.fill is None
    assert attempt.reject_code == "51201"


@pytest.mark.asyncio
async def test_split_partial_children_aggregates_filled_only() -> None:
    """If a later child fails after earlier children filled, the aggregated fill
    tracks the REAL filled position (never orphan the funded children)."""

    class _FailSecond(_ChildSplitOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            if len(self.market_notionals) >= 1:
                self.market_notionals.append(float(kwargs["notional_usd"]))
                return _resp(ok=False, code="51008")
            return await super().place_market_order(**kwargs)

    adapter = _FailSecond(price=10.0)
    attempt = await real_okx_open_fill(
        adapter, inst_id="ALGO-USDT", notional_usd=1_468.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,
        poll_delay_sec=0.0,
    )
    # First child (1000 USDT) filled → tracked; second (468) rejected → stop.
    assert isinstance(attempt.fill, Fill)
    assert attempt.fill.base_qty == pytest.approx(100.0, rel=1e-6)  # 1000/10


# ---------------------------------------------------------------------------
# FIX 2 — balance clamp: never emit an OKX order the demo account cannot fund.
# Clamp the notional to (available_usdt - buffer) BEFORE submit (51008 cause).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_clamp_caps_notional_to_available() -> None:
    """available_usdt far below the sized notional → the submitted notional is
    clamped to availBal-minus-buffer (and split under the 1000 cap)."""
    adapter = _ChildSplitOKX(price=10.0)
    attempt = await real_okx_open_fill(
        adapter, inst_id="INJ-USDT", notional_usd=1_468.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,
        poll_delay_sec=0.0, available_usdt=300.0,
    )
    assert isinstance(attempt.fill, Fill)
    # Clamped to 300 × (1 - 0.01) = 297 → single child (<=1000), never 1468.
    assert sum(adapter.market_notionals) == pytest.approx(297.0, rel=1e-6)
    assert all(n <= 1_000.0 for n in adapter.market_notionals)


@pytest.mark.asyncio
async def test_balance_clamp_below_min_notional_skips_cleanly() -> None:
    """available_usdt below the OKX min notional → skip with a clean no-fill
    sentinel (no churn / no oversized re-submit)."""
    adapter = _ChildSplitOKX(price=10.0)
    attempt = await real_okx_open_fill(
        adapter, inst_id="INJ-USDT", notional_usd=1_468.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,
        poll_delay_sec=0.0, available_usdt=2.0,  # below MIN_OKX_NOTIONAL_USD=10
    )
    assert attempt.fill is None
    assert attempt.reject_code == "insufficient_balance"
    # NOTHING was POSTed (no churn).
    assert adapter.market_notionals == []


@pytest.mark.asyncio
async def test_no_balance_clamp_when_available_none() -> None:
    """available_usdt=None (default) preserves prior behaviour — no clamp."""
    adapter = _ChildSplitOKX(price=10.0)
    await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=800.0,
        strategy_id="tsmom", last_price=10.0, strength=1.6,
        poll_delay_sec=0.0, available_usdt=None,
    )
    assert adapter.market_notionals == [800.0]
