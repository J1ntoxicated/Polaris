"""FX ADX dead-zone overlap + session_breakout window aperture (W-widen wave).

W1 made GOLD/US100 + the FX majors REACH the bar strategies, but emits stayed 0
because the entry CALIBRATION was a dead seam:

  - fx_breakout_basket fired only ADX > 20 and fx_range_fade only ADX < 20, so a
    pair sitting in its BB band with ADX in ~[20,28] (live: EURUSD 27.3,
    GBPUSD 22.7) matched NEITHER strategy.
  - session_window_now was True only for the first 30 min of UTC 00/07/13
    (~90 min/day, 6.25% of the clock), so session_breakout almost never armed.

This wave WIDENS the eligible set (aggressive / flow_not_block):
  - breakout ADX_THRESHOLD 20 -> 15, fade ADX_RANGE_MAX 20 -> 25, so the mid-ADX
    [15,25] zone is now covered by BOTH strategies (they COMPETE there; the later
    multi-signal arbitration wave ranks them — for now both eligible is the
    aggressive default, bounded by the existing risk caps).
  - session_window_now widened to the real liquidity opens: London 07:00-08:30,
    NY 13:00-14:30 (90 min each), Asia 00:00-01:00 (60 min). ~4-6x the aperture.

Every assertion below is a STRICT WIDENING check: a signal that previously matched
nothing now matches at least one strategy / the window now opens where it was shut.
"""

from __future__ import annotations

from polaris.scripts._production_indicators import session_window_now
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.fx_breakout_basket import FXBreakoutBasketStrategy
from polaris.strategies.fx_range_fade import FXRangeFadeStrategy


def _bars(n: int, close: float) -> list[BarView]:
    base = 1_700_000_000
    return [
        BarView(
            ts=base + i * 3600, open=close, high=close + 0.001,
            low=close - 0.001, close=close, volume=1000.0,
        )
        for i in range(n)
    ]


def _fade_mv(close: float, *, adx: float) -> MarketView:
    # Price sits ABOVE the upper band (an up-extreme to fade) inside an otherwise
    # range-y tape — the fade should bite whenever ADX is below its range max.
    return MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=_bars(31, close), last_price=close, spread_bps=1.0, atr_pct=0.001,
        adx_14=adx, bb_upper=close - 0.0010, bb_lower=close - 0.0090,
        bb_middle=close - 0.0050,
    )


def _breakout_mv(close: float, *, adx: float) -> MarketView:
    # Clean break ABOVE the 40-bar Donchian high — breakout should bite whenever
    # ADX is above its threshold.
    bars = _bars(60, close)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=close, high=close + 0.05, low=close - 0.01,
        close=close, volume=1500.0,
    )
    return MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=close, spread_bps=1.0,
        donchian_high_40=close - 0.04, adx_14=adx,
    )


# ── FIX A: ADX dead-zone overlap ────────────────────────────────────────────


def test_adx_22_matches_at_least_one_fx_strategy() -> None:
    """Live dead-seam case (EURUSD 27.3 / GBPUSD 22.7 sat here): an FX pair at
    the BB extreme with ADX=22 used to match NEITHER strategy. Post-widen it must
    match at least one — here the fade (ADX 22 < new max 25)."""
    fade = FXRangeFadeStrategy().generate_raw_signal(_fade_mv(1.1050, adx=22.0))
    breakout = FXBreakoutBasketStrategy().generate_raw_signal(
        _breakout_mv(1.2400, adx=22.0)
    )
    assert (fade is not None) or (breakout is not None)


def test_adx_18_matches_both_strategies_overlap() -> None:
    """The overlap is real: at ADX=18 the breakout (>=15) AND the fade (<25) are
    BOTH eligible on their respective setups (they compete in the mid band)."""
    fade = FXRangeFadeStrategy().generate_raw_signal(_fade_mv(1.1050, adx=18.0))
    breakout = FXBreakoutBasketStrategy().generate_raw_signal(
        _breakout_mv(1.2400, adx=18.0)
    )
    assert fade is not None, "fade must be eligible at ADX=18 (< 25)"
    assert breakout is not None, "breakout must be eligible at ADX=18 (>= 15)"


def test_adx_30_still_breakout_only() -> None:
    """High ADX (strong trend) stays breakout-only — the fade yields above its
    range max so it never fights a runaway move."""
    fade = FXRangeFadeStrategy().generate_raw_signal(_fade_mv(1.1050, adx=30.0))
    breakout = FXBreakoutBasketStrategy().generate_raw_signal(
        _breakout_mv(1.2400, adx=30.0)
    )
    assert fade is None, "fade must yield at ADX=30 (>= 25)"
    assert breakout is not None, "breakout must still fire at ADX=30"


def test_adx_bands_now_overlap_not_disjoint() -> None:
    """The CALIBRATION fix: the bands deliberately OVERLAP now (fade max ABOVE
    breakout threshold) so the old [threshold, max] dead seam is covered."""
    from polaris.strategies.fx_breakout_basket import ADX_THRESHOLD
    from polaris.strategies.fx_range_fade import ADX_RANGE_MAX
    assert ADX_RANGE_MAX > ADX_THRESHOLD
    assert ADX_THRESHOLD == 15.0
    assert ADX_RANGE_MAX == 25.0


# ── FIX B: session_window_now aperture ──────────────────────────────────────


def _utc(hour: int, minute: int) -> int:
    # A UTC timestamp at the given hour:minute (any day; predicate is hour-of-day).
    base_midnight = 1_700_006_400  # 2023-11-15 00:00:00 UTC
    return base_midnight + hour * 3600 + minute * 60


def test_session_window_open_at_london_0715() -> None:
    """London open window must now include 07:15 (was shut: minute 15 < 30 only
    by luck of the old 30-min rule — but the widen extends London to 08:30)."""
    assert session_window_now(_utc(7, 15)) is True


def test_session_window_open_at_ny_1315() -> None:
    """NY open window must include 13:15."""
    assert session_window_now(_utc(13, 15)) is True


def test_session_window_open_at_london_0815_newly_admitted() -> None:
    """The widen specifically admits the back half of the London open
    (07:00-08:30) that the old <30-min rule shut out at 08:15."""
    assert session_window_now(_utc(8, 15)) is True


def test_session_window_open_at_ny_1415_newly_admitted() -> None:
    """And the back half of the NY open (13:00-14:30) at 14:15."""
    assert session_window_now(_utc(14, 15)) is True


def test_session_window_still_open_at_asia_0015() -> None:
    """The Asia/midnight window (00:00-01:00) is preserved at 00:15."""
    assert session_window_now(_utc(0, 15)) is True


def test_session_window_shut_at_1000() -> None:
    """A genuinely off-session hour (10:00 UTC) stays shut — the widen targets
    the real opens, it does not open the whole clock."""
    assert session_window_now(_utc(10, 0)) is False
