"""TDD — every trade-execution price defaults to the LIVE WS mid.

Jin's rule (2026-06-21): "지연된건 전략·테크니컬·판단 기준용, 모든 거래 기본값은 라이브."
Strategy signals / indicators / regime stay on the (delayed) 1m bars — that is
analysis. But the price the bot ACTS on (entry fill ref, exit/close sizing mark,
sim-exit mark, upnl/stop evaluation) defaults to the live WebSocket mid, falling
back to the most-recent bar close ONLY when no fresh tick exists for the symbol.

These tests pin the shared execution-default accessor ``live_or_bar_price`` and
its freshness/fallback contract. A signal can fire on a stale bar while the
entry/exit uses the live price; the live price crossing the stop mid-bar drives
the exit.
"""

from __future__ import annotations

import time

from polaris.core.data.quote_writer import (
    LIVE_PRICE_FRESH_SEC,
    QuoteTickWriter,
    live_or_bar_price,
)
from polaris.core.data.schema import QuoteTick

INST = "okx:BTC-USDT"
BAR_CLOSE = 100.0  # 1-min-old bar close (the DELAYED analysis price).
LIVE_MID = 105.0   # the live WS mid (the EXECUTION price).


def _qt(mid: float) -> QuoteTick:
    return QuoteTick(
        instrument_id=INST, venue="okx", symbol="BTC-USDT",
        ts=1_900_000_000, bid=mid - 0.5, ask=mid + 0.5, mid=mid,
        spread_bps=10.0, source="okx_ws",
    )


def test_fresh_tick_is_the_execution_default() -> None:
    """A fresh WS tick wins over the bar close — execution acts at the live mid."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(LIVE_MID))
    assert live_or_bar_price(w, INST, BAR_CLOSE) == LIVE_MID


def test_no_tick_falls_back_to_bar_close() -> None:
    """No WS history for the symbol → graceful degrade to the bar close."""
    w = QuoteTickWriter(":memory:")  # never received a tick for INST
    assert live_or_bar_price(w, INST, BAR_CLOSE) == BAR_CLOSE


def test_none_writer_falls_back_to_bar_close() -> None:
    """No quote_writer at all (e.g. a test/boot path) → bar close, never raises."""
    assert live_or_bar_price(None, INST, BAR_CLOSE) == BAR_CLOSE


def test_stale_tick_falls_back_to_bar_close() -> None:
    """A tick older than the freshness window degrades to the bar (no flap)."""
    w = QuoteTickWriter(":memory:")
    # Plant a stale tick directly: monotonic stamp older than the threshold.
    w._live_px[INST] = (LIVE_MID, time.monotonic() - (LIVE_PRICE_FRESH_SEC + 5.0))
    assert live_or_bar_price(w, INST, BAR_CLOSE) == BAR_CLOSE


def test_zero_or_negative_mid_falls_back() -> None:
    """A non-positive live mid is never trusted as an execution price."""
    w = QuoteTickWriter(":memory:")
    w._live_px[INST] = (0.0, time.monotonic())
    assert live_or_bar_price(w, INST, BAR_CLOSE) == BAR_CLOSE


def test_signal_on_stale_bar_entry_uses_live_price() -> None:
    """Entry semantics: the signal is decided on the (stale) bar, but the entry
    price the bot acts on is the live WS mid — bar drives WHAT/WHEN, live drives
    the AT."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(LIVE_MID))
    bar_close_signal_price = BAR_CLOSE  # what the indicator/strategy saw
    entry_exec_price = live_or_bar_price(w, INST, bar_close_signal_price)
    assert entry_exec_price == LIVE_MID
    assert entry_exec_price != bar_close_signal_price


def test_live_price_crosses_stop_mid_bar() -> None:
    """Exit semantics: with a long stop at 102 and the bar close (100) NOT below
    it, a fresh live tick at 101 (below the stop) must be the price the exit
    evaluates — the live cross triggers mid-bar, before a new bar prints."""
    stop = 102.0
    w = QuoteTickWriter(":memory:")
    # Bar close 100 is below the long stop too, so make the bar look safe (103)
    # while the live tick has dropped through the stop to 101.
    safe_bar = 103.0
    w.on_quote(_qt(101.0))
    exec_mark = live_or_bar_price(w, INST, safe_bar)
    assert exec_mark == 101.0
    # Long stop breach is evaluated on the LIVE mark, not the safe bar close.
    assert exec_mark < stop  # would trigger the exit
    assert safe_bar > stop   # the delayed bar alone would have missed it
