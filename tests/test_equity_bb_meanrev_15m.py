"""equity_bb_meanrev_15m — Alpaca 15m BB-lower mean-reversion, RTH-interior gated.

DEMO/PAPER virtual sleeve, rsi_bb_pullback SHAPE clone (no R-tuned value
ported). Covers the RSI/BB/SMA200 trigger, the RTH-interior gate, the §0d
liquidity gate, pre-fed-field None handling, warmup, and degrade-never-crash.
"""

from __future__ import annotations

import datetime as dt

from polaris.core.sessions.equity_session_gate import NY_TZ
from polaris.strategies import STRATEGY_REGISTRY, EquityBbMeanrev15mStrategy
from polaris.strategies._virtual_loosen import virtual_loosen, virtual_mode_enabled
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.connors_rsi2 import _sma
from polaris.strategies.equity_bb_meanrev_15m import (
    RSI_THRESHOLD,
    SMA_WINDOW,
    WARMUP_BARS,
)

_BAR_SEC = 900


def _ny_ts(y: int, m: int, d: int, hh: int, mm: int) -> int:
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=NY_TZ).timestamp())


# 2024-01-09 is a Tuesday (no US market holiday).
_INTERIOR_TS = _ny_ts(2024, 1, 9, 10, 15)  # 10:15 ET — inside [10:00, 15:30)
_RTH_EDGE_TS = _ny_ts(2024, 1, 9, 9, 35)  # 09:35 ET — RTH but NOT interior
_AFTER_HOURS_TS = _ny_ts(2024, 1, 9, 17, 0)  # 17:00 ET — closed


def _rising_bars(
    n: int, *, last_ts: int, start: float = 50.0, step: float = 0.05,
    volume: float = 25_000.0,
) -> list[BarView]:
    """A steadily rising close series ending at ``last_ts`` (900s spacing)."""
    out: list[BarView] = []
    for i in range(n):
        c = start + step * i
        ts = last_ts - (n - 1 - i) * _BAR_SEC
        out.append(
            BarView(ts=ts, open=c, high=c + 0.1, low=c - 0.1, close=c,
                    volume=volume)
        )
    return out


def _mv(
    bars: list[BarView], *, bb_lower: float | None, rsi_14: float | None,
) -> MarketView:
    return MarketView(
        symbol="AAPL", venue="alpaca", timeframe="15m",
        bars=bars, last_price=bars[-1].close if bars else 0.0, spread_bps=2.0,
        bb_lower=bb_lower, rsi_14=rsi_14,
    )


def _pullback_fixture(n: int = WARMUP_BARS, *, last_ts: int = _INTERIOR_TS) -> tuple[
    list[BarView], float, float
]:
    """Rising trend (close > SMA200) with bb_lower/rsi pre-fed to trigger."""
    bars = _rising_bars(n, last_ts=last_ts)
    sma200 = _sma(bars, SMA_WINDOW)
    assert sma200 is not None and bars[-1].close > sma200
    bb_lower = bars[-1].close + 0.5  # close <= bb_lower (touch)
    rsi_14 = 20.0  # < RSI_THRESHOLD(30)
    return bars, bb_lower, rsi_14


# ---------------------------------------------------------------------------
# A — entry / no-entry boundary
# ---------------------------------------------------------------------------


def test_fires_on_bb_lower_touch_with_oversold_rsi_in_uptrend() -> None:
    bars, bb_lower, rsi_14 = _pullback_fixture()
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=bb_lower, rsi_14=rsi_14)
    )
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_bb_meanrev_15m"
    assert 0.0 < sig.strength <= 1.0
    assert sig.tags["rsi_14"] == "20.0"


def test_no_signal_when_rsi_at_or_above_threshold() -> None:
    bars, bb_lower, _ = _pullback_fixture()
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=bb_lower, rsi_14=RSI_THRESHOLD)
    )
    assert sig is None


def test_no_signal_when_close_above_bb_lower() -> None:
    bars, _, rsi_14 = _pullback_fixture()
    too_low_bb = bars[-1].close - 0.5  # close > bb_lower now
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=too_low_bb, rsi_14=rsi_14)
    )
    assert sig is None


def test_no_signal_when_below_sma200() -> None:
    # Falling series: last close ends BELOW its own SMA200 → trend filter blocks.
    n = WARMUP_BARS
    bars: list[BarView] = []
    for i in range(n):
        c = 200.0 - 0.3 * i
        ts = _INTERIOR_TS - (n - 1 - i) * _BAR_SEC
        bars.append(BarView(ts=ts, open=c, high=c + 0.1, low=c - 0.1, close=c,
                            volume=15_000.0))
    sma200 = _sma(bars, SMA_WINDOW)
    assert sma200 is not None and bars[-1].close <= sma200
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=bars[-1].close + 1.0, rsi_14=15.0)
    )
    assert sig is None


# ---------------------------------------------------------------------------
# B — §0d liquidity gate
# ---------------------------------------------------------------------------


def test_no_signal_when_illiquid() -> None:
    bars, bb_lower, rsi_14 = _pullback_fixture()
    illiquid = [
        BarView(ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close,
                volume=1.0)
        for b in bars
    ]
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(illiquid, bb_lower=bb_lower, rsi_14=rsi_14)
    )
    assert sig is None


# ---------------------------------------------------------------------------
# C — RTH-interior gate (§0b)
# ---------------------------------------------------------------------------


def test_no_signal_at_rth_edge_not_interior() -> None:
    bars, bb_lower, rsi_14 = _pullback_fixture(last_ts=_RTH_EDGE_TS)
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=bb_lower, rsi_14=rsi_14)
    )
    assert sig is None


def test_no_signal_after_hours() -> None:
    bars, bb_lower, rsi_14 = _pullback_fixture(last_ts=_AFTER_HOURS_TS)
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=bb_lower, rsi_14=rsi_14)
    )
    assert sig is None


# ---------------------------------------------------------------------------
# D — warmup
# ---------------------------------------------------------------------------


def test_no_signal_before_warmup() -> None:
    bars, bb_lower, rsi_14 = _pullback_fixture(n=WARMUP_BARS - 1)
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=bb_lower, rsi_14=rsi_14)
    )
    assert sig is None


# ---------------------------------------------------------------------------
# E — pre-fed field None → no-emit (degrade-never-crash)
# ---------------------------------------------------------------------------


def test_no_signal_when_bb_lower_none() -> None:
    bars, _, rsi_14 = _pullback_fixture()
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=None, rsi_14=rsi_14)
    )
    assert sig is None


def test_no_signal_when_rsi_none() -> None:
    bars, bb_lower, _ = _pullback_fixture()
    sig = EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=bb_lower, rsi_14=None)
    )
    assert sig is None


# ---------------------------------------------------------------------------
# F — degrade-never-crash
# ---------------------------------------------------------------------------


def test_degrade_never_crash_on_empty_bars() -> None:
    assert EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv([], bb_lower=1.0, rsi_14=10.0)
    ) is None


def test_degrade_never_crash_on_short_bars() -> None:
    bars = _rising_bars(5, last_ts=_INTERIOR_TS)
    assert EquityBbMeanrev15mStrategy().generate_raw_signal(
        _mv(bars, bb_lower=1000.0, rsi_14=10.0)
    ) is None


# ---------------------------------------------------------------------------
# G — metadata (spec §1 table row #4, verbatim)
# ---------------------------------------------------------------------------


def test_metadata_matches_spec_table() -> None:
    md = EquityBbMeanrev15mStrategy.metadata
    assert md.strategy_id == "equity_bb_meanrev_15m"
    assert md.timeframe == "15m"
    assert md.warmup_bars == 205
    assert md.max_positions == 4
    assert md.gross_cap == 0.12
    assert md.per_symbol_cap == 0.04
    assert md.expected_holding_bars == 4
    assert md.asset_class == "equity"
    assert md.venue == "alpaca"
    assert md.product_class == "equity"
    assert md.correlation_group_id == "equity_bb_meanrev_15m"
    assert md.hold_overnight is False
    assert md.profit_target_r == 1.0
    assert md.loss_cooldown_bars == 8


def test_dispatch_eligible_is_virtual_only() -> None:
    md = EquityBbMeanrev15mStrategy.metadata
    assert md.dispatch_eligible == virtual_loosen(True, False)
    assert md.dispatch_eligible == virtual_mode_enabled()


def test_registered_in_registry() -> None:
    assert (
        STRATEGY_REGISTRY["equity_bb_meanrev_15m"] is EquityBbMeanrev15mStrategy
    )
