"""Rail-taker preservation (#77) — the OKX close/exit leg is ALWAYS a TAKER.

DEMO/PAPER only — fake adapter, NO real network. This is the load-bearing safety
test for the maker exec-layer build: profit exits MAY rest as makers, but the
-1.0R rail / hard-stop / loser_timeout MUST execute as a TAKER (a market order
that fills immediately). A maker (post-only) stop could rest unfilled while the
loss runs PAST the rail = rail무력화 = 절대금지.

There is exactly ONE OKX close path (``real_okx_close_fill``) and ALL exits —
including the rail — flow through it. This test pins that it places
``ord_type="market"`` (taker) and NEVER ``"post_only"``. The maker work in this
build is ENTRY-only; it does not (and must not) wire any maker order onto the
close path, so the rail stays taker structurally.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polaris.scripts._smoke_real_roundtrip import real_okx_close_fill


def _resp(*, ord_id: str = "sell_1") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=True, venue_order_id=ord_id, client_order_id="cl1",
        code="0", msg="", raw={},
    )


def _sell_filled_row() -> dict[str, Any]:
    return {
        "ordId": "sell_1", "clOrdId": "cl1", "instId": "BTC-USDT",
        "side": "sell", "tgtCcy": "base_ccy", "accFillSz": "0.001",
        "avgPx": "59000.0", "fee": "-0.04", "feeCcy": "USDT",
        "state": "filled", "uTime": str(int(time.time() * 1000)),
    }


class _CloseFakeOKX:
    def __init__(self) -> None:
        self.place_calls: list[dict[str, Any]] = []

    async def fetch_balance(self, ccy: str) -> dict[str, Any]:
        return {"data": [{"details": [{"ccy": "BTC", "availBal": "1.0"}]}]}

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.place_calls.append(kwargs)
        return _resp()

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        return {"data": [_sell_filled_row()]}


@pytest.mark.asyncio
async def test_okx_close_is_market_taker_never_post_only() -> None:
    fake = _CloseFakeOKX()
    await real_okx_close_fill(
        fake, inst_id="BTC-USDT", base_qty=0.001,
        strategy_id="weekend_thin_book_flush_maker", mark_price=59_000.0,
        poll_delay_sec=0.0,
    )
    assert fake.place_calls, "close must place a sell order"
    # THE rail-taker invariant: every close-leg order is a market (taker) sell,
    # NEVER a post-only maker. A maker stop could rest unfilled past the rail.
    assert all(c["ord_type"] == "market" for c in fake.place_calls)
    assert all(c["side"] == "sell" for c in fake.place_calls)
    assert not any(c["ord_type"] == "post_only" for c in fake.place_calls)
