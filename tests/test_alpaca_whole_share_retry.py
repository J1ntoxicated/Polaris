"""Alpaca whole-share retry — a $-notional order on a non-fractionable asset is
rejected ('not fractionable'); the entry leg must retry as a floor(notional/price)
whole-share qty order so those entries are not silently lost (live: 609 of 612
venue rejects were exactly this, leaving the bot with almost no equity entries).
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.scripts._alpaca_open import real_alpaca_open_fill


class _Resp:
    def __init__(self, ok: bool, venue_order_id: str | None = None,
                 code: str = "", msg: str = "") -> None:
        self.ok = ok
        self.venue_order_id = venue_order_id
        self.client_order_id = None
        self.code = code
        self.msg = msg
        self.raw: dict[str, Any] = {}


class _Adapter:
    """Notional order → not-fractionable reject; qty order → accepted+filled."""

    def __init__(self, *, reject_code: str = "insufficient_buying_power",
                 reject_msg: str = 'asset "CXAI" is not fractionable') -> None:
        self.calls: list[dict[str, Any]] = []
        self._reject_code = reject_code
        self._reject_msg = reject_msg

    async def place_market_order(self, *, symbol: str, side: str,
                                 notional_usd: float = 0.0,
                                 qty: float | None = None,
                                 client_order_id: str = "") -> _Resp:
        self.calls.append({"notional_usd": notional_usd, "qty": qty})
        if qty is None:
            return _Resp(False, code=self._reject_code, msg=self._reject_msg)
        return _Resp(True, venue_order_id="ord1")

    async def fetch_order(self, *, order_id: str) -> dict[str, Any]:
        return {"status": "filled", "symbol": "CXAI", "side": "buy",
                "filled_qty": "40", "filled_avg_price": "5.00"}


@pytest.mark.asyncio
async def test_retries_whole_share_on_not_fractionable() -> None:
    adapter = _Adapter()
    res = await real_alpaca_open_fill(
        adapter, symbol="CXAI", notional_usd=200.0, strategy_id="s",
        side="buy", last_price=5.0,
    )
    assert res.fill is not None  # the whole-share retry filled
    assert len(adapter.calls) == 2
    assert adapter.calls[0]["qty"] is None  # 1st = $-notional
    assert adapter.calls[1]["qty"] == 40.0  # 2nd = floor(200/5) whole shares


@pytest.mark.asyncio
async def test_no_retry_on_other_reject() -> None:
    # A non-"not fractionable" reject must NOT trigger the whole-share retry.
    adapter = _Adapter(reject_code="some_other", reject_msg="rejected by venue")
    res = await real_alpaca_open_fill(
        adapter, symbol="AAPL", notional_usd=200.0, strategy_id="s",
        side="buy", last_price=150.0,
    )
    assert res.fill is None
    assert res.reject_code == "some_other"
    assert len(adapter.calls) == 1  # no retry


@pytest.mark.asyncio
async def test_no_retry_below_one_share() -> None:
    # notional < price → floor() is 0 shares → skip the retry (no sub-share order).
    adapter = _Adapter()
    res = await real_alpaca_open_fill(
        adapter, symbol="BRKA", notional_usd=200.0, strategy_id="s",
        side="buy", last_price=500000.0,
    )
    assert res.fill is None
    assert len(adapter.calls) == 1  # would round to 0 shares → no retry
