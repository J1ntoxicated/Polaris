"""§0d — Equity liquidity helper (value-based, no ticker hardcode).

DEMO/PAPER virtual. Pure module (no I/O, no DB, no network) — mirrors the
positive-match-set ROLE of ``_okx_liquid_universe`` but expressed as a $
THRESHOLD on the bars themselves rather than a static symbol roster, so a T3
universe widen is a pure env change (``POLARIS_EQ_LIQ_MIN_DAILY_USD`` lower),
never a code change / ticker hardcode.
"""

from __future__ import annotations

import os

from polaris.strategies._equity_liquidity import (
    EQ_LIQ_LOOKBACK_DAYS,
    EQ_LIQ_MIN_DAILY_USD_DEFAULT,
    EQ_LIQ_MIN_DAILY_USD_ENV,
    equity_liquidity_ok,
    median_daily_dollar_volume,
)
from polaris.strategies.base import BarView


def _bar(*, close: float, volume: float, notional_usd: float = 0.0) -> BarView:
    return BarView(
        ts=0, open=close, high=close, low=close, close=close, volume=volume,
        notional_usd=notional_usd,
    )


# ---------------------------------------------------------------------------
# A — constants
# ---------------------------------------------------------------------------


def test_defaults() -> None:
    assert EQ_LIQ_MIN_DAILY_USD_ENV == "POLARIS_EQ_LIQ_MIN_DAILY_USD"
    assert EQ_LIQ_MIN_DAILY_USD_DEFAULT == 30_000_000.0
    assert EQ_LIQ_LOOKBACK_DAYS == 20


# ---------------------------------------------------------------------------
# B — median_daily_dollar_volume: 1D (bars_per_day=1) and 15m (bars_per_day=26)
# ---------------------------------------------------------------------------


def test_1d_median_uses_notional_usd_when_positive() -> None:
    bars = [_bar(close=100.0, volume=1_000_000.0, notional_usd=50_000_000.0)] * 25
    # bars_per_day=1 -> median of the trailing 20 per-bar $vol, scaled by 1.
    assert median_daily_dollar_volume(bars, 1) == 50_000_000.0


def test_1d_median_falls_back_to_close_times_volume_when_notional_absent() -> None:
    bars = [_bar(close=50.0, volume=2_000_000.0, notional_usd=0.0)] * 25
    assert median_daily_dollar_volume(bars, 1) == 100_000_000.0


def test_1d_median_uses_trailing_lookback_window_only() -> None:
    # 5 stale HIGH-volume bars outside the 20-day lookback, then 20 low-volume
    # bars — the median must reflect only the trailing 20.
    stale = [_bar(close=100.0, volume=10_000_000.0, notional_usd=1_000_000_000.0)] * 5
    recent = [_bar(close=10.0, volume=100_000.0, notional_usd=1_000_000.0)] * 20
    bars = stale + recent
    assert median_daily_dollar_volume(bars, 1) == 1_000_000.0


def test_15m_median_scales_by_bars_per_day() -> None:
    # 26 bars/day (RTH 6.5h / 15m), each $1M/bar -> daily estimate $26M.
    bars = [_bar(close=20.0, volume=50_000.0, notional_usd=1_000_000.0)] * (26 * 20)
    assert median_daily_dollar_volume(bars, 26) == 26_000_000.0


def test_median_none_when_fewer_than_one_session() -> None:
    bars = [_bar(close=10.0, volume=100.0, notional_usd=1_000.0)] * 25
    assert median_daily_dollar_volume(bars, 26) is None  # < 1 full 26-bar session


def test_median_none_when_bars_per_day_non_positive() -> None:
    bars = [_bar(close=10.0, volume=100.0, notional_usd=1_000.0)] * 10
    assert median_daily_dollar_volume(bars, 0) is None


def test_median_exactly_one_session_returns_value() -> None:
    bars = [_bar(close=10.0, volume=1_000.0, notional_usd=10_000.0)] * 26
    assert median_daily_dollar_volume(bars, 26) == 260_000.0


# ---------------------------------------------------------------------------
# C — equity_liquidity_ok gate (arg -> env -> default resolution)
# ---------------------------------------------------------------------------


def test_liquid_symbol_clears_default_floor() -> None:
    bars = [_bar(close=100.0, volume=1_000_000.0, notional_usd=100_000_000.0)] * 25
    assert equity_liquidity_ok(bars, 1) is True


def test_illiquid_symbol_fails_default_floor() -> None:
    bars = [_bar(close=1.0, volume=10_000.0, notional_usd=10_000.0)] * 25
    assert equity_liquidity_ok(bars, 1) is False


def test_insufficient_bars_fails_closed() -> None:
    bars = [_bar(close=1000.0, volume=1_000_000.0, notional_usd=1_000_000_000.0)] * 3
    assert equity_liquidity_ok(bars, 26) is False


def test_explicit_min_usd_arg_overrides_default() -> None:
    bars = [_bar(close=10.0, volume=100_000.0, notional_usd=1_000_000.0)] * 25
    assert equity_liquidity_ok(bars, 1, min_usd=500_000.0) is True
    assert equity_liquidity_ok(bars, 1, min_usd=2_000_000.0) is False


def test_env_override_lowers_floor_for_t3_expansion() -> None:
    bars = [_bar(close=5.0, volume=200_000.0, notional_usd=1_000_000.0)] * 25
    os.environ[EQ_LIQ_MIN_DAILY_USD_ENV] = "500000"
    try:
        assert equity_liquidity_ok(bars, 1) is True  # $1M >= lowered $500k floor
    finally:
        del os.environ[EQ_LIQ_MIN_DAILY_USD_ENV]
    assert equity_liquidity_ok(bars, 1) is False  # back to the $30M default


def test_env_override_bad_value_falls_back_to_default() -> None:
    bars = [_bar(close=100.0, volume=1_000_000.0, notional_usd=100_000_000.0)] * 25
    os.environ[EQ_LIQ_MIN_DAILY_USD_ENV] = "not-a-number"
    try:
        assert equity_liquidity_ok(bars, 1) is True  # falls back to $30M default; still clears
    finally:
        del os.environ[EQ_LIQ_MIN_DAILY_USD_ENV]


def test_no_ticker_hardcode_module_has_no_symbol_roster() -> None:
    """Source-level guard: this module must stay a pure VALUE-based gate — no
    static symbol frozenset/roster (unlike _okx_liquid_universe's SET match).
    A T3 widen must be achievable via env alone."""
    import polaris.strategies._equity_liquidity as mod

    assert not any(
        isinstance(getattr(mod, name), frozenset) for name in dir(mod)
        if not name.startswith("_")
    )
