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

import pytest

from polaris.core.data.quote_writer import (
    LIVE_PRICE_FRESH_SEC,
    QUOTE_SANITY_PCT_DEFAULT,
    QuoteTickWriter,
    live_or_bar_price,
    quote_sanity_pct,
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


# ---------------------------------------------------------------------------
# P2a quote sanity guard — STETH phantom forensic (2026-07-16): a degraded
# OKX demo book let a fresh WS mid drift ~9% off the true market, booking a
# phantom -15R close. ``bar_ref`` (only ever a genuine bar close) lets the
# caller pin a sanity ceiling on how far a fresh mid may diverge.
# ---------------------------------------------------------------------------


def test_bar_ref_none_preserves_legacy_behavior() -> None:
    """No bar_ref supplied → byte-identical to the pre-guard contract, no
    matter how far the mid diverges."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(LIVE_MID))
    assert live_or_bar_price(w, INST, BAR_CLOSE) == LIVE_MID


def test_mid_within_sanity_band_wins() -> None:
    bar_ref = 1922.0
    mid = 1900.0  # ~1.1% off — inside the default 3% band.
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(mid))
    assert live_or_bar_price(w, INST, bar_ref, bar_ref=bar_ref) == mid
    assert w.quote_sanity_rejects == 0


def test_mid_beyond_sanity_band_is_distrusted() -> None:
    """The STETH shape: market ~1922, phantom WS mid ~1747 (~9% off)."""
    bar_ref = 1922.0
    phantom_mid = 1747.0
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(phantom_mid))
    result = live_or_bar_price(w, INST, bar_ref, bar_ref=bar_ref)
    assert result == bar_ref  # bar_ref wins, NOT the phantom mid
    assert w.quote_sanity_rejects == 1


def test_sanity_guard_never_blocks_only_reroutes() -> None:
    """The guard is a hygiene reroute, not a halt — a rejected mid still
    returns a usable price (bar_ref), never raises / never None."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(1747.0))
    result = live_or_bar_price(w, INST, 0.0, bar_ref=1922.0)
    assert result == 1922.0


def test_custom_sanity_pct_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_QUOTE_SANITY_PCT", "0.10")  # widen to 10%
    assert quote_sanity_pct() == 0.10
    bar_ref = 1922.0
    mid = 1747.0  # ~9.1% off — now inside the widened 10% band.
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(mid))
    assert live_or_bar_price(w, INST, bar_ref, bar_ref=bar_ref) == mid


def test_sanity_pct_default_and_bad_env_fallback() -> None:
    assert quote_sanity_pct("") == QUOTE_SANITY_PCT_DEFAULT
    assert quote_sanity_pct("not-a-number") == QUOTE_SANITY_PCT_DEFAULT
    assert quote_sanity_pct("-0.05") == QUOTE_SANITY_PCT_DEFAULT
    assert quote_sanity_pct("0.10") == 0.10


def test_bar_ref_nonpositive_skips_guard() -> None:
    """A degenerate bar_ref (0.0/negative — not a genuine bar) never divides
    by zero and never overrides the mid."""
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt(1747.0))
    assert live_or_bar_price(w, INST, 100.0, bar_ref=0.0) == 1747.0


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
