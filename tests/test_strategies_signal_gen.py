"""7 P0 strategies — signal generator tests (TDD).

Spec coverage:
  - vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md
  - vault/30_components/layer-7-strategy-isolation.md (correlation group ids)
"""

from __future__ import annotations

from polaris.strategies import (
    STRATEGY_REGISTRY,
    BarView,
    BaseStrategy,
    FXBreakoutBasketStrategy,
    MarketView,
    RawSignal,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    SpotDonchianStrategy,
    StrategyMetadata,
    TSMOMStrategy,
    VolumeBurstStrategy,
    XAUIndicesTrendStrategy,
    all_strategies,
)

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


# ---------------------------------------------------------------------------
# TSMOM
# ---------------------------------------------------------------------------


def test_tsmom_emits_signal_on_positive_momentum() -> None:
    s = TSMOMStrategy()
    bars = _make_bars(30, base_close=100.0, drift=1.0)
    mv = MarketView(
        symbol="ETH-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=3.0,
        momentum_20bar=0.10,
    )
    sig = s.generate_raw_signal(mv)
    assert sig is not None
    assert sig.correlation_group == "spot_cross_sectional_momo"


def test_tsmom_returns_none_on_negative_momentum() -> None:
    s = TSMOMStrategy()
    bars = _make_bars(30, base_close=100.0, drift=-1.0)
    mv = MarketView(
        symbol="ETH-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=3.0,
        momentum_20bar=-0.05,
    )
    assert s.generate_raw_signal(mv) is None


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
        donchian_high_40=110.0, adx_14=15.0,
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
    when adx_14 <= 20. The adx gate (direction-agnostic) blocks both sides."""
    s = FXBreakoutBasketStrategy()
    bars = _make_bars(60, base_close=1.10, drift=0.001)
    bars[-1] = BarView(
        ts=bars[-1].ts, open=1.00, high=1.01, low=0.94, close=0.95,
        volume=1500.0,
    )
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=0.95, spread_bps=1.0,
        donchian_high_40=1.30, donchian_low_40=1.00, adx_14=18.0,  # <= 20
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
    assert len(seen) == 7, f"correlation groups not unique: {seen}"


def test_strategy_registry_size() -> None:
    assert len(STRATEGY_REGISTRY) == 7


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
