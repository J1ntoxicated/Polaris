"""Maker exec layer (#77) — ``real_okx_open_fill`` no-fill routing.

DEMO/PAPER only — fake adapter, NO real network. Asserts that the ``maker_no_fill``
mode threads through the public entry leg:

  * ``maker_no_fill="cancel"`` (weekend thin-book) + a resting post-only that
    never fills → the entry SKIPS (no taker market order is ever placed). The
    missed deep-bid fill is 0 realised cost (flow_not_block does NOT mandate a
    market order); forcing a taker here would destroy the weekend edge.
  * ``maker_no_fill="market"`` (default, every other prefer-maker caller) →
    falls back to a taker market order (byte-identical to the legacy path).

This is the entry-leg counterpart to the exit-leg rail-taker guarantee
(``test_rail_close_is_taker``): maker passivity is opt-in per strategy and never
leaks onto a path that must guarantee a fill.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._okx_limit_open import real_okx_open_fill


def _resp(*, ord_id: str = "ord_1") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=True, venue_order_id=ord_id, client_order_id="cl1",
        code="0", msg="", raw={},
    )


def _market_row() -> dict[str, Any]:
    return {
        "ordId": "ord_mkt", "clOrdId": "clm", "instId": "BTC-USDT",
        "side": "buy", "tgtCcy": "quote_ccy", "accFillSz": "100.0",
        "avgPx": "60001.0", "fee": "-0.06", "feeCcy": "USDT",
        "state": "filled", "uTime": str(int(time.time() * 1000)),
    }


class _NeverFillsOKX:
    """post-only never fills; tracks whether a TAKER market order is placed."""

    def __init__(self) -> None:
        self.place_calls: list[dict[str, Any]] = []
        self._market_placed = False

    async def fetch_ticker(self, inst_id: str) -> dict[str, Any]:
        return {"bidPx": "59999.0", "askPx": "60001.0"}

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.place_calls.append(kwargs)
        if kwargs["ord_type"] == "market":
            self._market_placed = True
        return _resp(ord_id=f"ord_{len(self.place_calls)}")

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        if self._market_placed:
            return {"data": [_market_row()]}
        return {"data": [{"ordId": ord_id, "state": "live", "accFillSz": "0"}]}

    async def cancel_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        return {"code": "0"}


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_LIMIT_FILL_WAIT_SEC", "0.02")
    monkeypatch.setenv("POLARIS_POST_ONLY_MAX_REPOSTS", "1")


@pytest.mark.asyncio
async def test_cancel_mode_never_places_market_order() -> None:
    fake = _NeverFillsOKX()
    attempt = await real_okx_open_fill(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=60_000.0,
        strength=0.5, poll_delay_sec=0.0,
        prefer_maker=True, maker_no_fill="cancel",
    )
    # The weekend skip: NO taker market order ever placed (all posts are maker).
    assert all(c["ord_type"] == "post_only" for c in fake.place_calls)
    assert attempt.fill is None
    assert attempt.reject_code == "maker_no_fill"


@pytest.mark.asyncio
async def test_market_mode_falls_back_to_taker() -> None:
    fake = _NeverFillsOKX()
    attempt = await real_okx_open_fill(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="rsi_bb_pullback", last_price=60_000.0,
        strength=0.5, poll_delay_sec=0.0,
        prefer_maker=True,  # maker_no_fill defaults to "market"
    )
    # Default: a maker miss STILL fills via the taker market fallback.
    assert any(c["ord_type"] == "market" for c in fake.place_calls)
    assert isinstance(attempt.fill, Fill)
