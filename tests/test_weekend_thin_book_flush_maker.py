"""weekend_thin_book_flush_maker (#77) — the single verified maker BUILD.

DEMO/PAPER only. The ONLY strategy the crypto maker research validated as
net-positive (+73 bps real-fee): on a weekend (Sat/Sun UTC) thin-book FLUSH (a
deep over-sold over-correction) it rests a deep post-only bid to harvest the
revert. OKX SPOT, long-only. Verified behaviour:

  * fires ONLY on a Sat/Sun UTC bar (weekday bars never fire — the edge is the
    weekend thin book, not a general mean-reversion);
  * fires on a deep-oversold flush (RSI deep + a wick through the lower band),
    long side only;
  * metadata declares maker_no_fill_cancel=True (no taker fallback — a missed
    deep bid = 0 realised cost), a REVERSION exit bucket (post-only passive
    entry mode), and a bounded profit_target_r (clean revert harvest; the -1.0R
    rail is the floor, owned by the engine as a TAKER).
"""

from __future__ import annotations

from datetime import UTC, datetime

from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.weekend_thin_book_flush_maker import (
    WeekendThinBookFlushMakerStrategy,
)

STRATEGY_ID = "weekend_thin_book_flush_maker"


def _ts_on(weekday: int) -> int:
    """An epoch-seconds ts whose UTC weekday == ``weekday`` (Mon=0 .. Sun=6)."""
    # 2026-06-27 is a Saturday (weekday 5). Walk to the target weekday.
    sat = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
    delta_days = (weekday - 5) % 7
    return int(sat.timestamp()) + delta_days * 86400


def _flush_bars(n: int, *, weekday: int, flush: bool) -> list[BarView]:
    """A flat series then (optionally) a sharp flush wick on the last bar."""
    ts0 = _ts_on(weekday) - (n - 1) * 3600
    out: list[BarView] = []
    for i in range(n):
        c = 100.0
        out.append(BarView(ts=ts0 + i * 3600, open=c, high=c + 0.2, low=c - 0.2,
                           close=c, volume=1000.0))
    if flush:
        last_ts = out[-1].ts
        # A capitulation candle: low pierces well below, close near the low.
        out[-1] = BarView(ts=last_ts, open=99.0, high=99.2, low=95.0,
                          close=95.5, volume=4000.0)
    return out


def _mv(bars: list[BarView], *, rsi: float, bb_lower: float) -> MarketView:
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=4.0, atr_pct=0.02,
        rsi_14=rsi, bb_lower=bb_lower, bb_middle=100.0, bb_upper=104.0,
    )


def test_registered_three_touchpoints() -> None:
    # Registry wiring (one of the 3 touchpoints): id → class.
    assert STRATEGY_ID in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY[STRATEGY_ID] is WeekendThinBookFlushMakerStrategy


def test_metadata_maker_and_reversion_contract() -> None:
    md = WeekendThinBookFlushMakerStrategy.metadata
    assert md.venue == "okx"
    assert md.asset_class == "spot"
    # no-fill = CANCEL (the missed deep bid is a 0-cost skip, never a taker).
    assert md.maker_no_fill_cancel is True
    # REVERSION correlation group → post-only passive entry mode + bounded exit.
    assert "mean_reversion" in md.correlation_group_id
    # bounded clean-revert harvest target (the +0.30R clean-fill leg).
    assert md.profit_target_r is not None and md.profit_target_r > 0.0


def test_fires_on_weekend_flush_long_only() -> None:
    bars = _flush_bars(30, weekday=5, flush=True)  # Saturday
    # Deep oversold + a wick through the lower band = a thin-book flush.
    mv = _mv(bars, rsi=18.0, bb_lower=96.0)
    sig = WeekendThinBookFlushMakerStrategy().generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == STRATEGY_ID
    assert sig.symbol == "BTC-USDT"
    assert 0.0 < sig.strength <= 1.0


def test_fires_on_sunday_too() -> None:
    bars = _flush_bars(30, weekday=6, flush=True)  # Sunday
    mv = _mv(bars, rsi=18.0, bb_lower=96.0)
    assert WeekendThinBookFlushMakerStrategy().generate_raw_signal(mv) is not None


def test_no_fire_on_weekday_even_with_flush() -> None:
    for wd in range(0, 5):  # Mon..Fri
        bars = _flush_bars(30, weekday=wd, flush=True)
        mv = _mv(bars, rsi=18.0, bb_lower=96.0)
        assert WeekendThinBookFlushMakerStrategy().generate_raw_signal(mv) is None, (
            f"weekday {wd} must not fire — edge is weekend-only"
        )


def test_no_fire_on_weekend_without_flush() -> None:
    # Saturday but no flush (rsi not oversold, no wick) → no signal.
    bars = _flush_bars(30, weekday=5, flush=False)
    mv = _mv(bars, rsi=55.0, bb_lower=96.0)
    assert WeekendThinBookFlushMakerStrategy().generate_raw_signal(mv) is None


def test_no_fire_when_warmup_insufficient() -> None:
    bars = _flush_bars(3, weekday=5, flush=True)
    mv = _mv(bars, rsi=18.0, bb_lower=96.0)
    assert WeekendThinBookFlushMakerStrategy().generate_raw_signal(mv) is None
