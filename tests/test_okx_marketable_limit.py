"""Build B — marketable-limit OKX entry (momentum/breakout).

A momentum/breakout entry cannot rest passively (the price runs away → non-fill),
so it crosses the spread with a limit capped ``cap_bps`` past the touch: it fills
like a taker but never WORSE than ``ask × (1 + cap_bps/1e4)`` (adverse-fill cap).
A no-fill / reject / timeout STILL falls back to a plain market order — so the
entry is never missed (flow_not_block: a maker-miss is a worse taker, not a skip).

All tests inject a fake OKX adapter; no real venue network call ever happens.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._limit_exec_constants import marketable_limit_cap_bps
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
        "avgPx": str(price), "fee": "-0.06", "feeCcy": "USDT",
        "state": state, "uTime": str(int(time.time() * 1000)),
    }


class _FakeOKX:
    """State-aware fake: limit resting → cancel → market fallback REST sequence."""

    def __init__(
        self,
        *,
        place_resp: Any,
        waiting_row: dict[str, Any] | None = None,
        post_cancel_row: dict[str, Any] | None = None,
        market_row: dict[str, Any] | None = None,
        ask: str | None = "60001.0",
        bid: str | None = "59999.0",
    ) -> None:
        self._place_resp = place_resp
        self._waiting_row = waiting_row
        self._post_cancel_row = post_cancel_row
        self._market_row = market_row
        self._ask = ask
        self._bid = bid
        self._cancelled = False
        self._market_placed = False
        self.place_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []

    async def fetch_ticker(self, inst_id: str) -> dict[str, Any]:
        tk: dict[str, Any] = {}
        if self._bid is not None:
            tk["bidPx"] = self._bid
        if self._ask is not None:
            tk["askPx"] = self._ask
        return tk

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
    monkeypatch.setenv("POLARIS_LIMIT_FILL_WAIT_SEC", "0.05")


# ---------------------------------------------------------------------------
# cap_bps tiering
# ---------------------------------------------------------------------------


def test_cap_bps_tiers() -> None:
    assert marketable_limit_cap_bps("BTC-USDT") == 5.0
    assert marketable_limit_cap_bps("ETH-USDT") == 5.0
    assert marketable_limit_cap_bps("SOL-USDT") == 6.0
    assert marketable_limit_cap_bps("FOO-USDT") == 10.0  # mid default
    assert marketable_limit_cap_bps("FOO-USDT", thin=True) == 20.0


def test_cap_bps_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_MKTLIMIT_CAP_BPS_MAJOR", "8")
    assert marketable_limit_cap_bps("BTC-USDT") == 8.0
    # A non-positive override is ignored (always at least the touch).
    monkeypatch.setenv("POLARIS_MKTLIMIT_CAP_BPS_MAJOR", "0")
    assert marketable_limit_cap_bps("BTC-USDT") == 5.0


# ---------------------------------------------------------------------------
# (1) marketable-limit fills at the capped cross price (no market)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_immediate_fill_at_cap() -> None:
    adapter = _FakeOKX(place_resp=_resp(), waiting_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0, marketable_limit=True,
    )
    assert isinstance(attempt.fill, Fill)
    # ONE limit place — no market fallback (it filled).
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "limit"
    # px = ask × (1 + 5bps) = 60001 × 1.0005 = 60031.0005 (crosses the spread).
    assert adapter.place_calls[0]["last_price_hint"] == pytest.approx(60_031.0005, rel=1e-9)
    assert adapter.cancel_calls == []


# ---------------------------------------------------------------------------
# (2) marketable-limit unfilled → cancel → market fallback (flow_not_block)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_timeout_market_fallback() -> None:
    adapter = _FakeOKX(
        place_resp=_resp(ord_id="ord_1"),
        waiting_row=_row(state="live", acc="0"),
        post_cancel_row=_row(state="canceled", acc="0"),
        market_row=_row(state="filled"),
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0, marketable_limit=True,
    )
    assert isinstance(attempt.fill, Fill)
    ord_types = [c["ord_type"] for c in adapter.place_calls]
    assert ord_types == ["limit", "market"]
    assert len(adapter.cancel_calls) == 1


# ---------------------------------------------------------------------------
# (3) marketable-limit rejected → market fallback (never a missed entry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_reject_market_fallback() -> None:
    rejected = _resp(ok=False, code="51000")

    class _RejectThenMarket(_FakeOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            self.place_calls.append(kwargs)
            if kwargs["ord_type"] == "limit":
                return rejected
            self._market_placed = True
            return _resp(ord_id="mkt_1")

    adapter = _RejectThenMarket(place_resp=_resp(), market_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0, marketable_limit=True,
    )
    assert isinstance(attempt.fill, Fill)
    assert [c["ord_type"] for c in adapter.place_calls] == ["limit", "market"]


# ---------------------------------------------------------------------------
# (4) marketable-limit overrides the strong-signal market skip (still capped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_applies_even_on_strong_signal() -> None:
    adapter = _FakeOKX(place_resp=_resp(), waiting_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=1.6,  # strong
        poll_delay_sec=0.0, marketable_limit=True,
    )
    assert isinstance(attempt.fill, Fill)
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "limit"


# ---------------------------------------------------------------------------
# (5) no usable ask → market fallback (fail-safe)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_no_ask_market_fallback() -> None:
    adapter = _FakeOKX(
        place_resp=_resp(), market_row=_row(state="filled"), ask=None,
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0, marketable_limit=True,
    )
    assert isinstance(attempt.fill, Fill)
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "market"


# ---------------------------------------------------------------------------
# (6) marketable-limit raises → fail-safe market fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_exception_market_fallback() -> None:
    calls: dict[str, int] = {"limit": 0, "market": 0}

    class _Boom(_FakeOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            calls[kwargs["ord_type"]] += 1
            if kwargs["ord_type"] == "limit":
                raise RuntimeError("limit unsupported")
            self._market_placed = True
            return _resp()

    adapter = _Boom(place_resp=_resp(), market_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0, marketable_limit=True,
    )
    assert isinstance(attempt.fill, Fill)
    assert calls["limit"] == 1 and calls["market"] == 1


# ---------------------------------------------------------------------------
# (7) partial fill on cancel race → tracked (orphan guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_partial_fill_on_cancel_tracked() -> None:
    adapter = _FakeOKX(
        place_resp=_resp(ord_id="ord_1"),
        waiting_row=_row(state="live", acc="0"),
        post_cancel_row=_row(state="canceled", acc="40.0"),
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=0.8,
        poll_delay_sec=0.0, marketable_limit=True,
    )
    assert isinstance(attempt.fill, Fill)
    assert attempt.fill.base_qty > 0.0
    assert len(adapter.cancel_calls) == 1
    # NO market fallback — the partial fill is a real position.
    assert all(c["ord_type"] == "limit" for c in adapter.place_calls)


# ---------------------------------------------------------------------------
# (8) marketable_limit=False (default) keeps the prior strength-gated path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketable_limit_false_is_byte_identical_default() -> None:
    adapter = _FakeOKX(place_resp=_resp(), market_row=_row(state="filled"))
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=60_000.0, strength=1.6,
        poll_delay_sec=0.0,  # default marketable_limit=False
    )
    assert isinstance(attempt.fill, Fill)
    # Strong signal → straight market (unchanged).
    assert len(adapter.place_calls) == 1
    assert adapter.place_calls[0]["ord_type"] == "market"


# ---------------------------------------------------------------------------
# (9) cap_bps thin tier widens the cross
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# (10) Capital CFD stays MARKET-DEFAULT even with marketable_limit=True — the
# working-order fill-rate is unverified, so the design routes Capital to market +
# a measurement shadow first ([[ab_letrun_maker_2026-06-24]] B3). The OKX maker/
# marketable flags never reach the Capital leg (it has no such param).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capital_entry_ignores_marketable_limit_market_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polaris.scripts._production_pipeline as pipe
    from polaris.core.data.fill_normalizer import Fill

    capital_calls: list[dict[str, Any]] = []

    async def _fake_capital(adapter: Any, **kwargs: Any) -> Any:
        capital_calls.append(kwargs)
        from polaris.scripts._smoke_roundtrip_shared import OpenAttempt

        fake_fill = Fill(
            venue="capital", instrument_id="EURUSD",
            strategy_id="fx_breakout_basket", side="buy", size_usd=200.0,
            fill_price=1.1, fee_usd=0.0, slippage_bps=0.0, ts_ms=0,
            order_id="d1", base_qty=1.0,
        )
        return OpenAttempt(fill=fake_fill, deal_id="d1")

    async def _fail_okx(*a: Any, **k: Any) -> Any:  # must NOT be called
        raise AssertionError("Capital CFD must not route through the OKX leg")

    monkeypatch.setattr(pipe, "real_capital_open_fill", _fake_capital)
    monkeypatch.setattr(pipe, "real_okx_open_fill", _fail_okx)

    class _CapSession:
        pass

    attempt = await pipe._real_open_fill(
        venue="capital", symbol="EURUSD", side="long", notional_usd=200.0,
        last_price=1.1, strategy_id="fx_breakout_basket", asset_class="forex",
        capital_session=_CapSession(),
        marketable_limit=True, prefer_maker=True,  # both set — Capital ignores
    )
    assert isinstance(attempt.fill, Fill)
    # Capital path was taken exactly once; it received NO maker/marketable kwarg.
    assert len(capital_calls) == 1
    assert "marketable_limit" not in capital_calls[0]
    assert "prefer_maker" not in capital_calls[0]


@pytest.mark.asyncio
async def test_marketable_limit_thin_uses_wide_cap() -> None:
    adapter = _FakeOKX(
        place_resp=_resp(), waiting_row=_row(state="filled"),
        ask="100.0",
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="THIN-USDT", notional_usd=100.0,
        strategy_id="burst_rider", last_price=100.0, strength=0.8,
        poll_delay_sec=0.0, marketable_limit=True, thin_market=True,
    )
    assert isinstance(attempt.fill, Fill)
    # px = ask × (1 + 20bps) = 100 × 1.002 = 100.2
    assert adapter.place_calls[0]["last_price_hint"] == pytest.approx(100.2, rel=1e-9)
