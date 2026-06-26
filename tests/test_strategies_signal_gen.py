"""Registered strategies — signal generator tests (TDD).

Spec coverage:
  - vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md
  - vault/30_components/layer-7-strategy-isolation.md (correlation group ids)

(tsmom / equity_tsmom / equity_rsi_bb_pullback / equity_gap_go were KILLed
2026-06-26 — gross-negative entry expectancy; their per-strategy tests were
removed and the registry-size invariants dropped from 15 to 11.)
"""

from __future__ import annotations

from polaris.strategies import (
    STRATEGY_REGISTRY,
    BarView,
    BaseStrategy,
    EMACrossoverStrategy,
    FXBreakoutBasketStrategy,
    MarketView,
    RawSignal,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    StrategyMetadata,
    XAUIndicesTrendStrategy,
    all_strategies,
)

# volume_burst un-registered 2026-06-27 (#61 live-churn KILL) + spot_donchian
# un-registered 2026-06-27 (#56 stop-bleeders KILL) — modules preserved
# read-only, so isolated signal-gen tests import the classes directly.
from polaris.strategies.spot_donchian import SpotDonchianStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bars(n: int, *, base_close: float, drift: float = 0.0,
               base_high: float = 0.0, vol: float = 1000.0) -> list[BarView]:
    out: list[BarView] = []
    for i in range(n):
        c = base_close + i * drift
        out.append(
            BarView(
                ts=1_700_000_000 + i * 60,
                open=c - 0.5,
                high=max(c, base_high) + 0.5,
                low=c - 1.0,
                close=c,
                volume=vol,
                notional_usd=vol * c,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Volume Burst
# ---------------------------------------------------------------------------


def test_volume_burst_emits_signal_on_break() -> None:
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    # last bar breaks prior high.
    last = BarView(ts=bars[-1].ts + 60, open=101.0, high=110.0, low=100.5,
                   close=109.0, volume=5000.0, notional_usd=545000.0)
    bars[-1] = last
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=109.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=3.5,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"
    assert sig.correlation_group == "spot_intraday_event"


def test_volume_burst_returns_none_on_low_z() -> None:
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=100.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=1.0,  # below threshold
    )
    assert s.generate_raw_signal(mv) is None


def test_volume_burst_returns_none_below_atr_floor() -> None:
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=100.0, spread_bps=2.0, atr_pct=0.0001,
        volume_z=3.5,
    )
    assert s.generate_raw_signal(mv) is None


def test_volume_burst_fades_spike_failure_at_resistance() -> None:
    """D4 fade-first: a volume spike whose HIGH pierces resistance (prior high)
    but whose CLOSE fails to hold above it = rejection at resistance -> SHORT.
    The strategy still FIRES (flow_not_block) — re-aimed, not silenced."""
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    # prior_high over the lookback window ≈ 101.9. Spike: high pierces to 103
    # (above resistance) but close falls back to 101.0 (<= prior_high) — the
    # burst was rejected at the level.
    bars[-1] = BarView(ts=bars[-1].ts + 60, open=101.5, high=103.0, low=100.8,
                       close=101.0, volume=5000.0, notional_usd=505000.0)
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=101.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=3.5,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "short"
    assert sig.correlation_group == "spot_intraday_event"


def test_volume_burst_continuation_long_on_clear_acceptance() -> None:
    """Continuation allowed ONLY on clear acceptance: the spike CLOSES decisively
    above resistance (body accepted above the level), not just a wick poke."""
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    # close=109 is far above prior_high≈101.9 -> accepted -> continuation long.
    bars[-1] = BarView(ts=bars[-1].ts + 60, open=101.0, high=110.0, low=100.5,
                       close=109.0, volume=5000.0, notional_usd=545000.0)
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=109.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=3.5,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"
    assert sig.correlation_group == "spot_intraday_event"


def test_volume_burst_no_double_fire_fade_vs_continuation() -> None:
    """A clear-acceptance break (continuation long) must NOT also emit a fade:
    at most one side fires per bar (close cannot be both above and below level)."""
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    bars[-1] = BarView(ts=bars[-1].ts + 60, open=101.0, high=110.0, low=100.5,
                       close=109.0, volume=5000.0, notional_usd=545000.0)
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=109.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=3.5,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"


def test_volume_burst_no_pierce_no_fade_still_emits_nothing() -> None:
    """Burst that never reaches resistance (high stays below prior_high) is
    neither a failed retest nor an acceptance break -> no signal (no panic)."""
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    # prior_high ≈ 101.9; this spike's high (101.0) never reaches the level.
    bars[-1] = BarView(ts=bars[-1].ts + 60, open=100.2, high=101.0, low=99.8,
                       close=100.5, volume=5000.0, notional_usd=502500.0)
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=100.5, spread_bps=2.0, atr_pct=0.001,
        volume_z=3.5,
    )
    assert s.generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# TSMOM
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RSI-BB Pullback
# ---------------------------------------------------------------------------


def test_rsi_bb_pullback_emits_signal() -> None:
    s = RSIBBPullbackStrategy()
    bars = _make_bars(220, base_close=100.0, drift=0.10)
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=last.high,
        low=85.0, close=88.0, volume=last.volume,
    )
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="15m",
        bars=bars, last_price=88.0, spread_bps=3.0,
        rsi_14=22.0, bb_lower=90.0, ma_200=80.0,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.correlation_group == "spot_mean_reversion"


def test_rsi_bb_pullback_returns_none_when_above_ma_filter_fails() -> None:
    s = RSIBBPullbackStrategy()
    bars = _make_bars(220, base_close=100.0, drift=0.10)
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=last.high,
        low=85.0, close=70.0, volume=last.volume,  # below ma_200
    )
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="15m",
        bars=bars, last_price=70.0, spread_bps=3.0,
        rsi_14=22.0, bb_lower=90.0, ma_200=120.0,
    )
    assert s.generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# Spot Donchian
# ---------------------------------------------------------------------------


def test_spot_donchian_emits_signal_on_break_with_adx() -> None:
    s = SpotDonchianStrategy()
    bars = _make_bars(60, base_close=100.0, drift=0.10)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=110.0, high=115.0, low=109.0, close=114.0,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=114.0, spread_bps=3.0,
        donchian_high_40=110.0, adx_14=30.0,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.correlation_group == "spot_breakout"


def test_spot_donchian_blocks_low_adx() -> None:
    s = SpotDonchianStrategy()
    bars = _make_bars(60, base_close=100.0, drift=0.10)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=110.0, high=115.0, low=109.0, close=114.0,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=114.0, spread_bps=3.0,
        donchian_high_40=110.0, adx_14=12.0,  # below the relaxed 14.0 floor
    )
    assert s.generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# FX Breakout Basket
# ---------------------------------------------------------------------------


def test_fx_breakout_emits_for_supported_symbol() -> None:
    s = FXBreakoutBasketStrategy()
    bars = _make_bars(60, base_close=1.10, drift=0.001)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1.20, high=1.25, low=1.19, close=1.24,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=1.24, spread_bps=1.0,
        donchian_high_40=1.20, adx_14=28.0,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.correlation_group == "cfd_fx_trend"
    assert sig.venue_constraints.get("leverage_max") == 30.0


def test_fx_breakout_blocks_unsupported_symbol() -> None:
    s = FXBreakoutBasketStrategy()
    bars = _make_bars(60, base_close=1.10, drift=0.001)
    mv = MarketView(
        symbol="NZDUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=1.24, spread_bps=1.0,
        donchian_high_40=1.0, adx_14=28.0,
    )
    assert s.generate_raw_signal(mv) is None


def test_fx_breakout_emits_for_real_capital_aud_epic() -> None:
    """Capital's real AUD/USD epic is ``AUDUSD_ZERO`` (live DB: no bare AUDUSD).

    The basket must recognize the venue epic as the AUDUSD major and emit, else
    fx_breakout_basket never trades AUD despite it reaching the active set/focus.
    """
    s = FXBreakoutBasketStrategy()
    bars = _make_bars(60, base_close=0.66, drift=0.001)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=0.70, high=0.73, low=0.69, close=0.72,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="AUDUSD_ZERO", venue="capital", timeframe="1H",
        bars=bars, last_price=0.72, spread_bps=1.0,
        donchian_high_40=0.70, adx_14=28.0,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.symbol == "AUDUSD_ZERO"  # original epic preserved for routing
    assert sig.correlation_group == "cfd_fx_trend"


def test_fx_breakout_emits_short_on_break_below_low() -> None:
    s = FXBreakoutBasketStrategy()
    bars = _make_bars(60, base_close=1.10, drift=0.001)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1.00, high=1.01, low=0.94, close=0.95,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=0.95, spread_bps=1.0,
        donchian_high_40=1.30, donchian_low_40=1.00, adx_14=28.0,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "short"
    assert sig.correlation_group == "cfd_fx_trend"
    assert sig.venue_constraints.get("leverage_max") == 30.0


def test_fx_breakout_no_double_fire_long_and_short() -> None:
    """close cannot be both > high and < low → at most one side fires."""
    s = FXBreakoutBasketStrategy()
    # Long-trigger setup must NOT also fire short.
    bars = _make_bars(60, base_close=1.10, drift=0.001)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1.20, high=1.25, low=1.19, close=1.24,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=1.24, spread_bps=1.0,
        donchian_high_40=1.20, donchian_low_40=1.00, adx_14=28.0,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"


def test_fx_breakout_low_break_blocked_by_low_adx_returns_none() -> None:
    """Negative path: a clean break BELOW the 40-bar low must NOT fire short
    when adx_14 <= threshold. The adx gate (direction-agnostic) blocks both sides.
    ADX 9.0 is below the relaxed 10.5 threshold (range, not trend → fade's turf).
    """
    s = FXBreakoutBasketStrategy()
    bars = _make_bars(60, base_close=1.10, drift=0.001)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1.00, high=1.01, low=0.94, close=0.95,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=0.95, spread_bps=1.0,
        donchian_high_40=1.30, donchian_low_40=1.00, adx_14=9.0,  # <= 10.5 thresh
    )
    assert s.generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# XAU/Indices Trend
# ---------------------------------------------------------------------------


def test_xau_indices_emits_for_supported_symbol() -> None:
    s = XAUIndicesTrendStrategy()
    bars = _make_bars(50, base_close=2000.0, drift=2.0)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=2100.0, high=2110.0, low=2099.0,
        close=2105.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="XAUUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=2105.0, spread_bps=2.0,
        donchian_high_30=2098.0, momentum_20bar=0.04,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.correlation_group == "cfd_index_commodity_trend"


def test_xau_indices_blocks_unsupported_symbol() -> None:
    s = XAUIndicesTrendStrategy()
    bars = _make_bars(50, base_close=2000.0, drift=2.0)
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=2105.0, spread_bps=2.0,
        donchian_high_30=2098.0, momentum_20bar=0.04,
    )
    assert s.generate_raw_signal(mv) is None


def test_xau_indices_emits_short_on_break_below_low() -> None:
    s = XAUIndicesTrendStrategy()
    bars = _make_bars(50, base_close=2000.0, drift=2.0)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1900.0, high=1905.0, low=1880.0,
        close=1885.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="XAUUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=1885.0, spread_bps=2.0,
        donchian_high_30=2200.0, donchian_low_30=1900.0, momentum_20bar=-0.04,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "short"
    assert sig.correlation_group == "cfd_index_commodity_trend"


def test_xau_indices_no_double_fire_long_and_short() -> None:
    s = XAUIndicesTrendStrategy()
    bars = _make_bars(50, base_close=2000.0, drift=2.0)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=2100.0, high=2110.0, low=2099.0,
        close=2105.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="XAUUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=2105.0, spread_bps=2.0,
        donchian_high_30=2098.0, donchian_low_30=1900.0, momentum_20bar=0.04,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"


def test_xau_indices_low_break_non_negative_momentum_returns_none() -> None:
    """Subtle fall-through: a clean break BELOW the 30-bar low must NOT fire
    short while momentum_20bar >= 0 (the short branch requires momentum < 0).
    Long is also impossible (close < high), so the result is None."""
    s = XAUIndicesTrendStrategy()
    bars = _make_bars(50, base_close=2000.0, drift=2.0)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1900.0, high=1905.0, low=1880.0,
        close=1885.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="XAUUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=1885.0, spread_bps=2.0,
        donchian_high_30=2200.0, donchian_low_30=1900.0,
        momentum_20bar=0.0,  # >= 0 -> short branch must not fire
    )
    assert s.generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# Session Breakout
# ---------------------------------------------------------------------------


def test_session_breakout_emits_in_window() -> None:
    s = SessionBreakoutStrategy()
    bars = _make_bars(30, base_close=4500.0, drift=0.5)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=4520.0, high=4540.0, low=4515.0,
        close=4535.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="US500", venue="capital", timeframe="5m",
        bars=bars, last_price=4535.0, spread_bps=1.0,
        session_open_price=4500.0, session_atr=10.0,
        is_session_open_window=True,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.correlation_group == "cfd_session_event"


def test_session_breakout_returns_none_outside_window() -> None:
    s = SessionBreakoutStrategy()
    bars = _make_bars(30, base_close=4500.0, drift=0.5)
    mv = MarketView(
        symbol="US500", venue="capital", timeframe="5m",
        bars=bars, last_price=4535.0, spread_bps=1.0,
        session_open_price=4500.0, session_atr=10.0,
        is_session_open_window=False,
    )
    assert s.generate_raw_signal(mv) is None


def test_session_breakout_emits_short_below_open_minus_atr() -> None:
    s = SessionBreakoutStrategy()
    bars = _make_bars(30, base_close=4500.0, drift=0.5)
    # session_open=4500, ATR=10, threshold_short = 4500 - 1.5*10 = 4485.
    bars[-1] = BarView(
        ts=bars[-1].ts, open=4480.0, high=4485.0, low=4460.0,
        close=4465.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="US500", venue="capital", timeframe="5m",
        bars=bars, last_price=4465.0, spread_bps=1.0,
        session_open_price=4500.0, session_atr=10.0,
        is_session_open_window=True,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "short"
    assert sig.correlation_group == "cfd_session_event"


def test_session_breakout_no_double_fire_long_and_short() -> None:
    s = SessionBreakoutStrategy()
    bars = _make_bars(30, base_close=4500.0, drift=0.5)
    # Long-trigger: close above open + 1.5*ATR; must NOT also fire short.
    bars[-1] = BarView(
        ts=bars[-1].ts, open=4520.0, high=4540.0, low=4515.0,
        close=4535.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="US500", venue="capital", timeframe="5m",
        bars=bars, last_price=4535.0, spread_bps=1.0,
        session_open_price=4500.0, session_atr=10.0,
        is_session_open_window=True,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"


def test_session_breakout_inside_band_returns_none() -> None:
    """Negative path: in-window but the close sits INSIDE the band — neither
    above open+1.5*ATR (4515) nor below open-1.5*ATR (4485) — fires nothing."""
    s = SessionBreakoutStrategy()
    bars = _make_bars(30, base_close=4500.0, drift=0.5)
    # session_open=4500, ATR=10 -> long >4515, short <4485. close=4500 is inside.
    bars[-1] = BarView(
        ts=bars[-1].ts, open=4498.0, high=4505.0, low=4495.0,
        close=4500.0, volume=2000.0,
    )
    mv = MarketView(
        symbol="US500", venue="capital", timeframe="5m",
        bars=bars, last_price=4500.0, spread_bps=1.0,
        session_open_price=4500.0, session_atr=10.0,
        is_session_open_window=True,
    )
    assert s.generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# Cross-strategy invariants
# ---------------------------------------------------------------------------


def test_each_strategy_returns_none_on_warmup_short() -> None:
    """Empty bars → None across the board (no panic)."""
    mv = MarketView(symbol="BTC-USDT", venue="okx", timeframe="1m",
                    bars=[], last_price=0.0, spread_bps=0.0)
    for s in all_strategies():
        assert s.generate_raw_signal(mv) is None


def test_strategy_metadata_complete() -> None:
    required = (
        "strategy_id", "timeframe", "warmup_bars", "max_positions", "gross_cap",
        "per_symbol_cap", "expected_holding_bars", "asset_class", "venue",
        "correlation_group_id",
    )
    for s in all_strategies():
        meta = s.metadata
        assert isinstance(meta, StrategyMetadata)
        for f in required:
            v = getattr(meta, f)
            assert v not in (None, ""), f"{type(s).__name__}.metadata.{f} missing"


def test_correlation_group_id_unique_per_strategy() -> None:
    seen = {s.metadata.correlation_group_id for s in all_strategies()}
    assert len(seen) == len(all_strategies()), (
        f"correlation groups not unique: {seen}"
    )
    # strategy-wave1 (2026-06-27): fx_range_fade un-registered (−1) and the 4
    # verified 1D survivors added (+4) on top of the prior 12 → 15, each with a
    # distinct correlation_group_id. volume_burst un-registered 2026-06-27
    # (#61 — live-churn KILL, net-negative scalp expectancy): 15 → 14.
    # strategy-wave2 (2026-06-27): +7 verified fee-beating research survivors
    # (Capital GOLD/index 5 + Alpaca equity 2), each a distinct group: 14 → 21.
    # spot_donchian un-registered 2026-06-27 (#56 — stop-bleeders KILL): 21 → 20.
    # +weekend_thin_book_flush_maker (#77 — single verified crypto maker BUILD,
    # distinct group weekend_thin_book_mean_reversion): 20 → 21.
    assert len(seen) == 21, f"correlation groups not unique: {seen}"


def test_strategy_registry_size() -> None:
    # strategy-wave1 (2026-06-27): was 12; −fx_range_fade +4 verified survivors = 15.
    # volume_burst un-registered 2026-06-27 (#61 — live-churn KILL): 15 → 14.
    # strategy-wave2 (2026-06-27): +7 verified research survivors → 21.
    # spot_donchian un-registered 2026-06-27 (#56 — stop-bleeders KILL): 21 → 20.
    # +weekend_thin_book_flush_maker (#77 — single verified crypto maker BUILD): 21.
    assert len(STRATEGY_REGISTRY) == 21


def test_each_strategy_emits_raw_signal_class() -> None:
    """Smoke: at least one strategy can emit RawSignal under happy path."""
    s = VolumeBurstStrategy()
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    last = BarView(ts=bars[-1].ts + 60, open=101.0, high=110.0, low=100.5,
                   close=109.0, volume=5000.0, notional_usd=545000.0)
    bars[-1] = last
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=109.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=3.5,
    )
    sig = s.generate_raw_signal(mv)
    assert isinstance(sig, RawSignal)


def test_strategy_inheritance_baseclass() -> None:
    for cls in STRATEGY_REGISTRY.values():
        assert issubclass(cls, BaseStrategy)


# ---------------------------------------------------------------------------
# EMA Crossover (OKX SPOT, long-only trend)
# ---------------------------------------------------------------------------


def _ema_cross_bars(*, regime_below: bool = False) -> list[BarView]:
    """A canonical bull-cross setup: a long flat base (fast EMA pinned ON the
    slow EMA) followed by ONE decisive up-bar that pulls the fast EMA UP through
    the slow EMA on the LAST bar — the trend inflection. ``regime_below`` shifts
    the whole series so the last close sits BELOW a 100-level EMA200 regime.
    """
    floor = 100.0
    closes: list[float] = [floor] * 60  # flat base → fast == slow
    closes.append(floor + 8.0)  # one up-bar → fast crosses UP through slow
    out: list[BarView] = []
    for i, c in enumerate(closes):
        out.append(
            BarView(
                ts=1_700_000_000 + i * 3600,
                open=c - 0.3, high=c + 0.5, low=c - 0.6, close=c,
                volume=1500.0, notional_usd=1500.0 * c,
            )
        )
    return out


def test_ema_crossover_emits_on_confirmed_cross() -> None:
    s = EMACrossoverStrategy()
    bars = _ema_cross_bars()
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=3.0,
        ma_200=100.0, adx_14=30.0,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"
    assert sig.correlation_group == "spot_ema_trend"
    assert sig.strength >= 0.5


def test_ema_crossover_no_signal_on_flat_noise() -> None:
    """Pure flat chop: no cross event -> no emit (attacks the dead-entry rate)."""
    s = EMACrossoverStrategy()
    bars = _make_bars(80, base_close=100.0, drift=0.0)
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=100.0, spread_bps=3.0,
        ma_200=100.0, adx_14=30.0,
    )
    assert s.generate_raw_signal(mv) is None


def test_ema_crossover_blocks_below_ema200_regime() -> None:
    """Cross is real but price is below the EMA200 trend filter -> no emit."""
    s = EMACrossoverStrategy()
    bars = _ema_cross_bars()
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=3.0,
        ma_200=bars[-1].close + 50.0, adx_14=30.0,  # price under regime mean
    )
    assert s.generate_raw_signal(mv) is None


def test_ema_crossover_blocks_low_adx_chop() -> None:
    """Cross + regime OK but ADX below the floor (chop) -> no emit (whipsaw guard)."""
    s = EMACrossoverStrategy()
    bars = _ema_cross_bars()
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=3.0,
        ma_200=100.0, adx_14=12.0,
    )
    assert s.generate_raw_signal(mv) is None


def test_ema_crossover_no_signal_when_already_above() -> None:
    """A sustained uptrend where fast is ALREADY above slow (no fresh cross this
    bar) does not re-fire -- only the inflection bar emits, not every bar after."""
    s = EMACrossoverStrategy()
    bars = _ema_cross_bars()
    last_close = bars[-1].close
    # Extend the up-leg: the cross is now in the PAST, the last bar has fast>slow
    # on both this AND the prior bar -> not a fresh cross.
    extra = [
        BarView(
            ts=bars[-1].ts + (i + 1) * 3600,
            open=last_close + 2.0 * i + 1.0, high=last_close + 2.0 * (i + 1) + 0.5,
            low=last_close + 2.0 * i, close=last_close + 2.0 * (i + 1),
            volume=1500.0, notional_usd=1500.0,
        )
        for i in range(4)
    ]
    bars = bars + extra
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=3.0,
        ma_200=100.0, adx_14=30.0,
    )
    assert s.generate_raw_signal(mv) is None


def test_ema_crossover_metadata_harvest_present() -> None:
    """The harvest target is BAKED IN (profit_target_r set) so the precise-exit
    engine banks a trend winner before the give-back — and it is long-only OKX."""
    m = EMACrossoverStrategy.metadata
    assert m.profit_target_r is not None
    assert m.profit_target_r > 0.0
    assert m.asset_class == "spot"
    assert m.venue == "okx"
    assert m.timeframe == "1H"
