"""Benchmark baselines TDD — buy&hold, naive TSMOM, naive Bollinger.

Each baseline runs on the SAME bars, the SAME real_fee_usd schedule, and the
SAME clock as the replay engine (spec §3 Relative). The headline TDD #4: on a
monotone series the buy&hold net return == close/open − 1 − round-trip fee.
"""

from __future__ import annotations

import pytest

from polaris.core.benchmark.baselines import (
    bollinger_baseline,
    buy_and_hold_baseline,
    tsmom_baseline,
)
from polaris.core.data.schema import Bar
from polaris.core.economics.fees import real_fee_usd

BASE_TS = 1_700_000_000
HOUR = 3600


def _bars(closes: list[float], *, venue: str = "okx", symbol: str = "BTC-USDT") -> list[Bar]:
    bars: list[Bar] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        op = prev
        bars.append(
            Bar(
                instrument_id=f"{venue}:{symbol}", underlying_group_id="crypto:BTC",
                venue=venue, symbol=symbol, bar_interval="1H", ts=BASE_TS + i * HOUR,
                open=op, high=max(op, c), low=min(op, c), close=c, volume=100.0,
                notional_usd=op * 100.0, spread_bps_close=0.0, source="test",
            )
        )
        prev = c
    return bars


# --- TDD #4: buy&hold on monotone series ------------------------------------
def test_buy_and_hold_monotone_equals_return_minus_fee() -> None:
    closes = [100.0, 110.0, 121.0, 133.1]  # +10% per bar, monotone
    bars = _bars(closes)
    start_equity = 10_000.0
    curve = buy_and_hold_baseline(bars, starting_equity=start_equity)
    # Enter at first bar open, hold to last close, one round trip.
    entry = bars[0].open
    exit_px = bars[-1].close
    notional = start_equity
    qty = notional / entry
    gross = (exit_px - entry) * qty
    rt_fee = real_fee_usd("okx", notional) + real_fee_usd("okx", abs(exit_px * qty))
    expected_final = start_equity + gross - rt_fee
    # Net return == close/open − 1 − fee-fraction.
    net_return = (curve.equity_curve[-1][1] - start_equity) / start_equity
    raw_ret = exit_px / entry - 1.0
    fee_frac = rt_fee / start_equity
    assert net_return == pytest.approx(raw_ret - fee_frac, rel=1e-9)
    assert curve.equity_curve[-1][1] == pytest.approx(expected_final)


def test_buy_and_hold_shares_clock_anchor() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    curve = buy_and_hold_baseline(bars, starting_equity=5_000.0)
    assert curve.equity_curve[0] == (bars[0].ts, 5_000.0)
    assert curve.equity_curve[-1][0] == bars[-1].ts


def test_buy_and_hold_empty_bars() -> None:
    curve = buy_and_hold_baseline([], starting_equity=1_000.0)
    assert curve.equity_curve == ((0, 1_000.0),)
    assert curve.returns == ()


# --- naive TSMOM ------------------------------------------------------------
def test_tsmom_uptrend_is_long_and_profitable() -> None:
    closes = [100.0 * (1.02 ** i) for i in range(12)]
    bars = _bars(closes)
    curve = tsmom_baseline(bars, starting_equity=10_000.0, lookback=3)
    # Monotone uptrend -> momentum positive -> always long -> ends above start.
    assert curve.equity_curve[-1][1] > 10_000.0


def test_tsmom_flip_charges_two_legs() -> None:
    # Up then down: forces at least one flip (each flip = exit + re-entry fees).
    closes = [100, 102, 104, 106, 104, 102, 100, 98]
    bars = _bars([float(c) for c in closes])
    curve = tsmom_baseline(bars, starting_equity=10_000.0, lookback=2)
    assert curve.total_fee_usd > 0.0
    assert len(curve.returns) >= 1


# --- naive Bollinger --------------------------------------------------------
def test_bollinger_returns_a_curve() -> None:
    closes = [100, 98, 96, 102, 105, 99, 95, 101, 108, 96, 94, 100]
    bars = _bars([float(c) for c in closes])
    curve = bollinger_baseline(bars, starting_equity=10_000.0, window=4, k=1.5)
    assert curve.equity_curve[0][1] == 10_000.0
    assert len(curve.equity_curve) >= 1


def test_all_baselines_share_starting_anchor() -> None:
    bars = _bars([float(100 + i) for i in range(20)])
    for fn in (
        lambda b: buy_and_hold_baseline(b, starting_equity=7_000.0),
        lambda b: tsmom_baseline(b, starting_equity=7_000.0, lookback=3),
        lambda b: bollinger_baseline(b, starting_equity=7_000.0, window=5, k=2.0),
    ):
        curve = fn(bars)
        assert curve.equity_curve[0][1] == pytest.approx(7_000.0)
