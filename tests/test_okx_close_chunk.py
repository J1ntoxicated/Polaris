"""OKX SPOT close-chunking fix (LIVE P0 incident).

The close path submitted the FULL position base_qty as ONE market sell, which
OKX rejects with 51201 ("The value of a market order can't exceed 1000USDT")
once the position notional > 1000 USDT — so 49/50 positions never closed and
bled. This mirrors the entry path's split: a >1000-USDT close is delivered as
SEQUENTIAL child market sells each <= the cap, then aggregated into ONE Fill.

All tests inject a fake OKX adapter; **no real venue network call ever
happens**. Also covers the balance clamp (51008 insufficient-balance fix).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._smoke_real_roundtrip import real_okx_close_fill


def _resp(*, ok: bool = True, ord_id: str = "ord_1", code: str = "0") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=ok, venue_order_id=ord_id if ok else None,
        client_order_id="cl1", code=code, msg="", raw={},
    )


class _CloseSplitOKX:
    """Fake adapter for the close market-split path.

    Each market sell POST gets its own ordId and a filled SELL row whose
    accFillSz is the child's submitted base qty (notional_usd carries base qty
    when tgt_ccy=base_ccy) at ``price`` per unit. ``avail`` (when set) is
    returned by ``fetch_balance`` as the base-ccy available balance.
    """

    def __init__(self, *, price: float = 10.0, avail: float | None = None) -> None:
        self._price = price
        self._avail = avail
        self.sell_base_qtys: list[float] = []
        self._rows: dict[str, dict[str, Any]] = {}

    async def place_market_order(self, **kwargs: Any) -> Any:
        assert kwargs["side"] == "sell"
        assert kwargs["ord_type"] == "market"
        assert kwargs["tgt_ccy"] == "base_ccy"
        base_qty = float(kwargs["notional_usd"])  # base qty under tgt_ccy=base_ccy
        self.sell_base_qtys.append(base_qty)
        ord_id = f"sell_{len(self.sell_base_qtys)}"
        self._rows[ord_id] = {
            "ordId": ord_id, "clOrdId": kwargs["client_order_id"],
            "instId": kwargs["inst_id"], "side": "sell", "tgtCcy": "base_ccy",
            "accFillSz": f"{base_qty:.10f}", "avgPx": str(self._price),
            "fee": "-0.02", "feeCcy": "USDT", "state": "filled",
            "uTime": str(int(time.time() * 1000)),
        }
        return _resp(ord_id=ord_id)

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        row = self._rows.get(ord_id)
        return {"data": [row] if row else []}

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        if self._avail is None:
            return {"data": []}
        return {
            "data": [
                {"details": [{"ccy": ccy or "BTC", "availBal": str(self._avail)}]}
            ]
        }


# ---------------------------------------------------------------------------
# CHUNKING (primary, fixes 51201)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_over_cap_splits_into_children() -> None:
    """A 1468-USDT close (146.8 base @ 10) splits off the BUFFERED cap (900) so
    each child quote stays <= cap×(1-buffer); the aggregate is the FULL base_qty.

    FIX A: the close sizes off cap×(1-0.10)=900 USDT, not the hard 1000, leaving
    upward-drift headroom → quote chunks [900, 568] → base [90.0, 56.8]."""
    adapter = _CloseSplitOKX(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="ALGO-USDT", base_qty=146.8,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    # Quote per child: 900 + 568 USDT → base per child: 90.0 + 56.8.
    assert adapter.sell_base_qtys == pytest.approx([90.0, 56.8], rel=1e-9)
    # No child quote notional ever > the buffered cap (900 USDT) → << hard 1000.
    assert all(q * 10.0 <= 900.0 + 1e-6 for q in adapter.sell_base_qtys)
    # Aggregated base_qty == original (total unchanged — NOT a size cut).
    assert fill.base_qty == pytest.approx(146.8, rel=1e-9)
    assert fill.side == "sell"
    # size-weighted avg price (uniform here) + summed REAL fee (10bps of $1468).
    assert fill.fill_price == pytest.approx(10.0, rel=1e-9)
    assert fill.fee_usd == pytest.approx(1.468, rel=1e-9)


@pytest.mark.asyncio
async def test_close_at_or_below_cap_single_order_behavior_zero() -> None:
    """A <=1000 USDT close is exactly ONE market sell, submitting base_qty
    unchanged (behavior-0: identical to the pre-fix single-order path)."""
    adapter = _CloseSplitOKX(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="BTC-USDT", base_qty=50.0,  # 500 USDT @ 10
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    assert adapter.sell_base_qtys == [50.0]
    assert fill.base_qty == pytest.approx(50.0, rel=1e-9)


@pytest.mark.asyncio
async def test_close_no_mark_price_single_order_behavior_zero() -> None:
    """No mark_price (None) → exactly ONE order with base_qty unchanged — the
    pre-fix behaviour (callers that don't supply a price never split)."""
    adapter = _CloseSplitOKX(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="BTC-USDT", base_qty=0.001666,
        strategy_id="volume_burst", poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    assert adapter.sell_base_qtys == [0.001666]


@pytest.mark.asyncio
async def test_close_3000_buffered_children() -> None:
    """3000 USDT (300 base @ 10) splits off the buffered 900 cap → 900×3 + 300
    tail → base [90,90,90,30] (the 300 tail >= min, no fold); sum == 300."""
    adapter = _CloseSplitOKX(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="ETH-USDT", base_qty=300.0,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    assert adapter.sell_base_qtys == pytest.approx([90.0, 90.0, 90.0, 30.0], rel=1e-9)
    assert all(q * 10.0 <= 900.0 + 1e-6 for q in adapter.sell_base_qtys)
    assert fill.base_qty == pytest.approx(300.0, rel=1e-9)


@pytest.mark.asyncio
async def test_close_mid_sequence_child_reject_returns_partial() -> None:
    """A later child rejecting after earlier children filled returns the
    aggregated partial Fill (filled portion) — never raises (flow_not_block).
    The remaining qty closes next tick."""

    class _FailSecond(_CloseSplitOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            if len(self.sell_base_qtys) >= 1:
                self.sell_base_qtys.append(float(kwargs["notional_usd"]))
                return _resp(ok=False, code="51201")
            return await super().place_market_order(**kwargs)

    adapter = _FailSecond(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="ALGO-USDT", base_qty=146.8,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    # First child (90 base / 900 USDT buffered) filled → partial aggregate.
    assert isinstance(fill, Fill)
    assert fill.base_qty == pytest.approx(90.0, rel=1e-9)


@pytest.mark.asyncio
async def test_close_first_child_reject_returns_none() -> None:
    """If the FIRST child rejects (nothing filled) the close returns None so the
    position is preserved + retried next tick (no raise, no orphan)."""

    class _RejectAll(_CloseSplitOKX):
        async def place_market_order(self, **kwargs: Any) -> Any:
            self.sell_base_qtys.append(float(kwargs["notional_usd"]))
            return _resp(ok=False, code="51201")

    adapter = _RejectAll(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="ALGO-USDT", base_qty=146.8,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert fill is None


# ---------------------------------------------------------------------------
# BALANCE CLAMP (secondary, fixes 51008 insufficient-balance)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_balance_clamp_caps_to_available() -> None:
    """base_qty over the available spot balance → close min(base_qty, avail)."""
    adapter = _CloseSplitOKX(price=10.0, avail=60.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="ETHW-USDT", base_qty=146.8,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    # Clamped to 60 base (600 USDT) → single child, never the over-count 146.8.
    assert sum(adapter.sell_base_qtys) == pytest.approx(60.0, rel=1e-9)
    assert fill.base_qty == pytest.approx(60.0, rel=1e-9)


@pytest.mark.asyncio
async def test_close_balance_zero_returns_orphan_sentinel_no_exception() -> None:
    """A true orphan (available ~0) → CloseOrphan sentinel + warning, no order, no
    exception. FIX 2: the sentinel (DISTINCT from a transient None reject) lets
    the caller mark the position 'reconciled' so it stops being retried."""
    from polaris.scripts._smoke_real_roundtrip import CloseOrphan

    adapter = _CloseSplitOKX(price=10.0, avail=0.0)
    out = await real_okx_close_fill(
        adapter, inst_id="FIL-USDT", base_qty=146.8,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(out, CloseOrphan)
    assert out.available == pytest.approx(0.0)
    assert adapter.sell_base_qtys == []  # nothing submitted


@pytest.mark.asyncio
async def test_close_balance_unknown_skips_clamp() -> None:
    """Balance lookup returns no USDT/base detail → clamp skipped (prior
    behaviour): the full base_qty is closed (split under the cap)."""
    adapter = _CloseSplitOKX(price=10.0, avail=None)
    fill = await real_okx_close_fill(
        adapter, inst_id="ALGO-USDT", base_qty=146.8,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    assert fill.base_qty == pytest.approx(146.8, rel=1e-9)


@pytest.mark.asyncio
async def test_close_dust_positive_below_min_notional_returns_orphan() -> None:
    """A dust-POSITIVE available whose ENTIRE closeable notional is below the OKX
    minimum sellable notional → CloseOrphan (NOT a dust clamp that drops every
    sub-min child → None → endless retry). e.g. base_qty=5622 but available is
    0.0000843 base @ mark 0.28 = 0.0000236 USD << MIN_OKX_NOTIONAL_USD."""
    from polaris.scripts._smoke_real_roundtrip import (
        MIN_OKX_NOTIONAL_USD,
        CloseOrphan,
    )

    dust = 0.0000843
    mark = 0.28
    assert dust * mark < MIN_OKX_NOTIONAL_USD  # premise: un-sellable dust
    adapter = _CloseSplitOKX(price=mark, avail=dust)
    out = await real_okx_close_fill(
        adapter, inst_id="ETHW-USDT", base_qty=5622.0,
        strategy_id="tsmom", mark_price=mark, poll_delay_sec=0.0,
    )
    assert isinstance(out, CloseOrphan)
    assert out.available == pytest.approx(dust)
    assert adapter.sell_base_qtys == []  # nothing submitted (was: dust clamp → None)


@pytest.mark.asyncio
async def test_close_dust_positive_unknown_mark_falls_back_to_clamp() -> None:
    """A dust-POSITIVE available with NO fresh mark (mark_price None) must NOT
    fabricate a mark to test the min-notional → falls back to the available<=0
    test only (not an orphan): the existing clamp-and-sell path runs (single
    child, no split since mark unknown)."""
    dust = 0.0000843
    adapter = _CloseSplitOKX(price=0.28, avail=dust)  # price irrelevant: no mark
    out = await real_okx_close_fill(
        adapter, inst_id="ETHW-USDT", base_qty=5622.0,
        strategy_id="tsmom", poll_delay_sec=0.0,  # mark_price omitted (None)
    )
    # Not an orphan: clamps to the dust available and submits a single child.
    assert isinstance(out, Fill)
    assert adapter.sell_base_qtys == pytest.approx([dust])


# ---------------------------------------------------------------------------
# FIX A — fresh mark + downward cap buffer (risen-price closes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_appreciated_children_stay_under_buffered_cap() -> None:
    """An APPRECIATED position (mark > entry) sizes its close children off the
    FRESH mark + buffered cap so each child quote-at-mark stays <= cap×(1-buffer)
    — AND survives +10% upward drift at venue-eval without breaching the hard
    1000-USDT cap (the 51201-never-converges bug)."""
    from polaris.scripts._limit_exec_constants import (
        OKX_CLOSE_CAP_BUFFER_FRAC,
        OKX_MAX_MARKET_NOTIONAL_USDT,
    )

    mark = 50.0  # entry was, say, 30 → position appreciated; we size off 50.
    base_qty = 73.0  # 73 × 50 = 3650 USDT > buffered cap → must split.
    adapter = _CloseSplitOKX(price=mark)
    fill = await real_okx_close_fill(
        adapter, inst_id="SOL-USDT", base_qty=base_qty,
        strategy_id="tsmom", mark_price=mark, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    buffered_cap = OKX_MAX_MARKET_NOTIONAL_USDT * (1.0 - OKX_CLOSE_CAP_BUFFER_FRAC)
    # Every child quote at the split mark is within the buffered cap...
    assert all(q * mark <= buffered_cap + 1e-6 for q in adapter.sell_base_qtys)
    # ...and a +10% price drift between split-time and venue-eval still keeps
    # the venue-evaluated quote under the HARD 1000-USDT cap (51201 headroom).
    assert all(
        q * mark * 1.10 <= OKX_MAX_MARKET_NOTIONAL_USDT + 1e-6
        for q in adapter.sell_base_qtys
    )
    # Total closed base_qty is unchanged (NOT a size cut).
    assert fill.base_qty == pytest.approx(base_qty, rel=1e-9)


@pytest.mark.asyncio
async def test_close_plus_ten_percent_drift_each_child_under_hard_cap() -> None:
    """A close sized so a full child is exactly the buffered cap survives a +10%
    venue-eval drift: 90 base @ mark 10 → quote 900 → at 1.10× = 990 <= 1000."""
    adapter = _CloseSplitOKX(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="ALGO-USDT", base_qty=180.0,  # 1800 USDT @ 10
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    # Buffered split: [900, 900] → base [90, 90]; each at +10% = 990 <= 1000.
    assert adapter.sell_base_qtys == pytest.approx([90.0, 90.0], rel=1e-9)
    assert all(q * 10.0 * 1.10 <= 1_000.0 + 1e-6 for q in adapter.sell_base_qtys)


# ---------------------------------------------------------------------------
# FIX C — min-order-size tail merge (shared splitter), entry behavior-0
# ---------------------------------------------------------------------------


def test_split_dust_tail_folds_into_previous() -> None:
    """1001 USDT (cap 1000) → the bare 1-USDT tail is < min → folded + halved
    into [500.5, 500.5] so both children clear the min and stay under the cap."""
    from polaris.scripts._okx_market_chunk import _split_market_notional

    chunks = _split_market_notional(1001.0)
    assert chunks == pytest.approx([500.5, 500.5], rel=1e-9)
    assert all(10.0 <= c <= 1_000.0 + 1e-9 for c in chunks)
    assert sum(chunks) == pytest.approx(1001.0, rel=1e-12)


def test_split_entry_behavior_zero_non_dust_unchanged() -> None:
    """Entry split (hard cap, no buffer) is behavior-0 for every non-dust case:
    1468 → [1000, 468]; 3000 → [1000,1000,1000]; 1500 → [1000,500]."""
    from polaris.scripts._okx_market_chunk import _split_market_notional

    assert _split_market_notional(1468.0) == pytest.approx([1000.0, 468.0])
    assert _split_market_notional(3000.0) == pytest.approx([1000.0, 1000.0, 1000.0])
    assert _split_market_notional(1500.0) == pytest.approx([1000.0, 500.0])
    assert _split_market_notional(500.0) == [500.0]  # <= cap → single chunk


def test_split_multi_cap_dust_tail_folds() -> None:
    """A dust tail after MULTIPLE full caps folds only the LAST pair: 2000.5 →
    [1000, 1000, 0.5] → [1000, 500.25, 500.25] (earlier full caps untouched)."""
    from polaris.scripts._okx_market_chunk import _split_market_notional

    chunks = _split_market_notional(2000.5)
    assert chunks == pytest.approx([1000.0, 500.25, 500.25], rel=1e-9)
    assert all(10.0 <= c <= 1_000.0 + 1e-9 for c in chunks)
    assert sum(chunks) == pytest.approx(2000.5, rel=1e-12)


@pytest.mark.asyncio
async def test_close_dust_tail_no_submin_child_submitted() -> None:
    """A close whose buffered split would leave a sub-min tail folds it so NO
    child quote is below the OKX min (10 USDT) — no 51020 dust child."""
    # 901 USDT @ mark 10 (90.1 base) → buffered cap 900 → [900, 1] → 1 < min →
    # fold + halve → [450.5, 450.5] quote → base [45.05, 45.05].
    adapter = _CloseSplitOKX(price=10.0)
    fill = await real_okx_close_fill(
        adapter, inst_id="ALGO-USDT", base_qty=90.1,
        strategy_id="tsmom", mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(fill, Fill)
    assert adapter.sell_base_qtys == pytest.approx([45.05, 45.05], rel=1e-9)
    assert all(q * 10.0 >= 10.0 for q in adapter.sell_base_qtys)
    assert fill.base_qty == pytest.approx(90.1, rel=1e-9)
