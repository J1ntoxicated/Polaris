"""Unit tests for QuoteTickWriter.feature_window (P5 gap-a, tick-decision engine).

Covers:
- feature_window exposes the full microstructure projection (size + trade fields),
  not just the 4 dict keys G4 reads.
- depth/order: oldest -> newest, capped at RING_BUFFER_DEPTH (600).
- spread_bps falls back to (ask-bid)/mid*1e4 when the stored QuoteTick has it as 0.
- recent_ticks() stays the exact 4-field dicts (G4 regression = 0).
"""

from __future__ import annotations

from polaris.core.data.quote_writer import RING_BUFFER_DEPTH, QuoteTickWriter
from polaris.core.data.schema import QuoteTick
from polaris.core.ticks.types import TickSample

NOW = 1_900_000_000


def _qt(
    inst: str = "okx:BTC-USDT",
    *,
    ts: int = NOW,
    mid: float = 100.0,
    spread_bps: float = 10.0,
    bid_size: float = 3.0,
    ask_size: float = 4.0,
    last_trade_price: float = 100.0,
    last_trade_size: float = 1.5,
) -> QuoteTick:
    return QuoteTick(
        instrument_id=inst,
        venue="okx",
        symbol=inst.split(":", 1)[-1],
        ts=ts,
        bid=mid - 0.5,
        ask=mid + 0.5,
        mid=mid,
        spread_bps=spread_bps,
        bid_size=bid_size,
        ask_size=ask_size,
        last_trade_price=last_trade_price,
        last_trade_size=last_trade_size,
        source="okx_ws",
    )


def test_ring_buffer_depth_is_deep() -> None:
    assert RING_BUFFER_DEPTH == 600


def test_feature_window_empty_when_no_history() -> None:
    w = QuoteTickWriter(":memory:")
    assert w.feature_window("okx:BTC-USDT") == []


def test_feature_window_exposes_size_and_trade_fields() -> None:
    w = QuoteTickWriter(":memory:")
    w.on_quote(
        _qt(
            ts=NOW,
            mid=100.0,
            spread_bps=12.5,
            bid_size=7.0,
            ask_size=9.0,
            last_trade_price=100.25,
            last_trade_size=2.5,
        )
    )
    win = w.feature_window("okx:BTC-USDT")
    assert len(win) == 1
    s = win[0]
    assert isinstance(s, TickSample)
    assert s.ts == NOW
    assert s.bid == 99.5
    assert s.ask == 100.5
    assert s.mid == 100.0
    assert s.spread_bps == 12.5
    assert s.bid_size == 7.0
    assert s.ask_size == 9.0
    assert s.last_trade_price == 100.25
    assert s.last_trade_size == 2.5


def test_feature_window_order_oldest_to_newest() -> None:
    w = QuoteTickWriter(":memory:")
    for i in range(5):
        w.on_quote(_qt(ts=NOW + i, mid=100.0 + i))
    win = w.feature_window("okx:BTC-USDT")
    assert [s.ts for s in win] == [NOW, NOW + 1, NOW + 2, NOW + 3, NOW + 4]
    assert [s.mid for s in win] == [100.0, 101.0, 102.0, 103.0, 104.0]


def test_feature_window_caps_at_ring_depth() -> None:
    w = QuoteTickWriter(":memory:")
    n = RING_BUFFER_DEPTH + 50
    for i in range(n):
        w.on_quote(_qt(ts=NOW + i, mid=100.0 + i))
    win = w.feature_window("okx:BTC-USDT")
    assert len(win) == RING_BUFFER_DEPTH
    # oldest 50 evicted: window starts at the (n - RING_BUFFER_DEPTH)-th tick.
    assert win[0].ts == NOW + (n - RING_BUFFER_DEPTH)
    assert win[-1].ts == NOW + n - 1


def test_feature_window_spread_bps_fallback_when_missing() -> None:
    w = QuoteTickWriter(":memory:")
    # stored spread_bps == 0.0 (missing) → recompute (ask-bid)/mid*1e4.
    # bid=99.5, ask=100.5, mid=100.0 → 1.0/100.0*1e4 = 100.0 bps.
    w.on_quote(_qt(spread_bps=0.0))
    win = w.feature_window("okx:BTC-USDT")
    assert len(win) == 1
    assert win[0].spread_bps == 100.0


def test_recent_ticks_still_four_field_dicts() -> None:
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(ts=NOW, mid=100.0))
    w.on_quote(_qt(ts=NOW + 1, mid=101.0))
    ticks = w.recent_ticks("okx:BTC-USDT")
    assert ticks == [
        {"ts": NOW, "bid": 99.5, "ask": 100.5, "mid": 100.0},
        {"ts": NOW + 1, "bid": 100.5, "ask": 101.5, "mid": 101.0},
    ]
    # exactly the 4 keys G4 reads — no leakage of size/trade fields.
    assert set(ticks[0].keys()) == {"ts", "bid", "ask", "mid"}


def test_recent_ticks_empty_when_no_history() -> None:
    w = QuoteTickWriter(":memory:")
    assert w.recent_ticks("okx:BTC-USDT") == []
