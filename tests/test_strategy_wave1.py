"""strategy-wave1 — the 4 verified fee-beating strategies + the fx_range_fade kill.

Spec source: research selection ``build_specs`` (okx_donchian_55_breakout /
tsmom_12_1_multiasset / macd_ema_trend_pullback / donchian_turtle_breakout).
DEMO/PAPER 가상자금 — aggressive bias preserved, flow_not_block (a clean trigger
ALWAYS emits; no defensive block / size dampen). ADR-008: each strategy emits the
ENTRY trigger ONLY; exit/sizing is owned by G5/G7 via StrategyMetadata.

Asserts per strategy:
  * ENTRY trigger fires on a clean breakout/momentum setup (emit).
  * No-trigger setups produce NO emit (the discriminating gate holds).
  * In-module indicator recompute is correct (the build-real-market-view gap is
    filled in-module with the is_finite() fallback the template uses).
  * ADR-008 contract: side=="long" (ENTRY-only, no short mirror built), TREND
    let-run bucket (correlation_group has no reversion substring →
    bucket_from_correlation_group is TREND), profit_target_r is None,
    hold_overnight is True, registered in STRATEGY_REGISTRY.
  * flow_not_block: an out-of-set symbol is a no-op (positive symbol-SET match,
    never a universe block), and a clean in-set trigger always emits.

And for the kill:
  * fx_range_fade is un-registered (not in STRATEGY_REGISTRY, not dispatched by
    _all_strategies) — its module + data are preserved, only behaviour is severed.
  * the other live strategies do NOT regress (still registered + dispatched).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polaris.core.live_recalc.exit_thesis import (
    Bucket,
    bucket_from_correlation_group,
)
from polaris.strategies import STRATEGY_REGISTRY, BarView, MarketView
from polaris.strategies.donchian_turtle_breakout import (
    DON_FAST_ENTRY,
    DON_SLOW_ENTRY,
    DonchianTurtleBreakoutStrategy,
)
from polaris.strategies.macd_ema_trend_pullback import (
    EMA_FILTER,
    MACDEMATrendPullbackStrategy,
)
from polaris.strategies.macd_ema_trend_pullback import (
    VOL_LOOKBACK as MACD_VOL_LOOKBACK,
)
from polaris.strategies.okx_donchian_55_breakout import (
    DONCHIAN_WINDOW,
    ROC_LOOKBACK,
    OKXDonchian55BreakoutStrategy,
)
from polaris.strategies.tsmom_12_1_multiasset import (
    MOM_LOOKBACK,
    MOM_SKIP,
    TSMom12_1MultiAssetStrategy,
)

_DAY = 86_400


def _bars(
    closes: list[float], *, start_ts: int = 1_700_000_000, vol: float = 1000.0
) -> list[BarView]:
    """BarViews from a close series; high/low straddle close (newest last)."""
    out: list[BarView] = []
    for i, c in enumerate(closes):
        out.append(
            BarView(
                ts=start_ts + i * _DAY,
                open=c,
                high=c + 0.3,
                low=c - 0.4,
                close=c,
                volume=vol,
                notional_usd=vol * c,
            )
        )
    return out


def _mv(
    bars: list[BarView],
    *,
    symbol: str = "BTC-USDT",
    venue: str = "okx",
    momentum_20bar: float | None = None,
    ma_200: float | None = None,
) -> MarketView:
    return MarketView(
        symbol=symbol,
        venue=venue,
        timeframe="1D",
        bars=bars,
        last_price=bars[-1].close,
        spread_bps=4.0,
        momentum_20bar=momentum_20bar,
        ma_200=ma_200,
    )


# ===========================================================================
# okx_donchian_55_breakout
# ===========================================================================


def test_d55_emits_on_breakout_with_positive_roc20() -> None:
    s = OKXDonchian55BreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(80)]  # rising > warmup 76
    bars = _bars(closes)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )
    sig = s.generate_raw_signal(_mv(bars, momentum_20bar=None))  # recompute path
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "okx_donchian_55_breakout"
    assert sig.correlation_group == "okx_donchian_55_breakout"


def test_d55_no_emit_when_no_breakout() -> None:
    s = OKXDonchian55BreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(80)]
    bars = _bars(closes)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high - 1.0,
        low=last.low, close=prior_high - 2.0, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars)) is None


def test_d55_no_emit_when_roc20_not_positive() -> None:
    # Both gates are required. Force the breakout (lift the last close above the
    # prior-55 high) but feed a finite NEGATIVE pre-fed momentum_20bar so the
    # ROC-20 gate is the sole discriminator → still no emit (deterministic).
    s = OKXDonchian55BreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(80)]
    bars = _bars(closes)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, momentum_20bar=-0.05)) is None


def test_d55_reuses_prefed_momentum_when_finite() -> None:
    # When momentum_20bar is finite it is reused verbatim (no recompute). Feed a
    # POSITIVE pre-fed momentum and a breakout → emit; the tag echoes the value.
    s = OKXDonchian55BreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(80)]
    bars = _bars(closes)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )
    sig = s.generate_raw_signal(_mv(bars, momentum_20bar=0.123))
    assert sig is not None
    assert sig.tags["roc_20"] == "0.1230"


def test_d55_symbol_gate_is_flow_safe_noop_offset() -> None:
    # An out-of-set symbol is a no-op (positive symbol-SET match — NOT a block of
    # the universe; other strategies still emit on DOGE-USDT).
    s = OKXDonchian55BreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(80)]
    bars = _bars(closes)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="DOGE-USDT")) is None


def test_d55_metadata_adr008_contract() -> None:
    meta = OKXDonchian55BreakoutStrategy.metadata
    assert meta.timeframe == "1D"
    assert meta.venue == "okx"
    assert meta.hold_overnight is True
    assert meta.profit_target_r is None
    assert meta.warmup_bars == DONCHIAN_WINDOW + ROC_LOOKBACK + 1 == 76
    assert bucket_from_correlation_group(meta.correlation_group_id) is Bucket.TREND


# ===========================================================================
# donchian_turtle_breakout (dual-channel)
# ===========================================================================


def test_turtle_system1_fast_entry_emits() -> None:
    # close breaks the fast-20 high but NOT the slow-55 high → System-1.
    s = DonchianTurtleBreakoutStrategy()
    closes = [100.0 - i * 0.1 for i in range(60)]  # gently FALLING so slow-55 high is high
    bars = _bars(closes)
    fast_high = max(b.high for b in bars[-(DON_FAST_ENTRY + 1):-1])
    slow_high = max(b.high for b in bars[-(DON_SLOW_ENTRY + 1):-1])
    assert fast_high < slow_high  # fixture: slow high is above fast high
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=fast_high + 1.0,
        low=last.low, close=fast_high + 0.5, volume=last.volume,
    )
    sig = s.generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.side == "long"
    assert sig.tags["system"] == "1"
    assert sig.tags["entry_channel"] == str(DON_FAST_ENTRY)


def test_turtle_system2_slow_entry_higher_strength() -> None:
    # close breaks the slow-55 high → System-2 (higher conviction, higher strength).
    s = DonchianTurtleBreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(60)]  # rising
    bars = _bars(closes)
    slow_high = max(b.high for b in bars[-(DON_SLOW_ENTRY + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=slow_high + 5.0,
        low=last.low, close=slow_high + 4.0, volume=last.volume,
    )
    sig = s.generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.tags["system"] == "2"
    s1 = DonchianTurtleBreakoutStrategy()
    # a pure System-1 emit has strictly lower strength than this System-2 emit
    closes1 = [100.0 - i * 0.1 for i in range(60)]
    bars1 = _bars(closes1)
    fast_high = max(b.high for b in bars1[-(DON_FAST_ENTRY + 1):-1])
    last1 = bars1[-1]
    bars1[-1] = BarView(
        ts=last1.ts, open=last1.open, high=fast_high + 1.0,
        low=last1.low, close=fast_high + 0.5, volume=last1.volume,
    )
    sig1 = s1.generate_raw_signal(_mv(bars1))
    assert sig1 is not None
    assert sig.strength > sig1.strength


def test_turtle_no_emit_when_no_channel_break() -> None:
    s = DonchianTurtleBreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(60)]
    bars = _bars(closes)
    fast_high = max(b.high for b in bars[-(DON_FAST_ENTRY + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=fast_high - 1.0,
        low=last.low, close=fast_high - 2.0, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars)) is None


def test_turtle_no_lookahead_excludes_current_bar() -> None:
    # close EQUALS the fast-20 prior-high (no strict break) but the bar's OWN
    # high is higher — a look-ahead bug would treat it as a break.
    s = DonchianTurtleBreakoutStrategy()
    closes = [100.0 + i * 0.5 for i in range(60)]
    bars = _bars(closes)
    fast_high = max(b.high for b in bars[-(DON_FAST_ENTRY + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=fast_high + 10.0,
        low=last.low, close=fast_high, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars)) is None


def test_turtle_metadata_adr008_contract() -> None:
    meta = DonchianTurtleBreakoutStrategy.metadata
    assert meta.timeframe == "1D"
    assert meta.hold_overnight is True
    assert meta.profit_target_r is None
    assert bucket_from_correlation_group(meta.correlation_group_id) is Bucket.TREND


# ===========================================================================
# macd_ema_trend_pullback
# ===========================================================================


def _macd_uptrend_with_pullback_cross() -> list[BarView]:
    """A long uptrend (close > 200-EMA) that dips into a shallow pullback then
    re-accelerates so the MACD line crosses up THROUGH/below zero on the last
    bar, with a volume spike on the cross bar."""
    closes: list[float] = []
    # Phase 1: long, strong uptrend to lift the 200-EMA well below price.
    for i in range(240):
        closes.append(50.0 + i * 1.0)
    # Phase 2: a pullback (price dips but stays > 200-EMA) that pushes the MACD
    # line down BELOW zero (the re-acceleration-after-pullback condition).
    top = closes[-1]
    for i in range(1, 21):
        closes.append(top - i * 1.5)
    # Phase 3: re-acceleration up — strong enough that the MACD line crosses
    # ABOVE its signal on the last bar while STILL at/below zero (a re-accel from
    # a shallow pullback, NOT an exhaustion top).
    dip = closes[-1]
    for i in range(1, 6):
        closes.append(dip + i * 2.5)
    return _bars(closes)


def test_macd_emits_on_pullback_reaccel_cross() -> None:
    s = MACDEMATrendPullbackStrategy()
    bars = _macd_uptrend_with_pullback_cross()
    # Volume spike on the cross (last) bar so the volume-confirm gate passes.
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=last.high, low=last.low,
        close=last.close, volume=last.volume * 5.0,
    )
    sig = s.generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "macd_ema_trend_pullback"
    assert float(sig.tags["macd_line"]) <= 0.0  # re-accel below/at zero
    assert float(sig.tags["ema_200"]) < last.close  # regime: close > 200-EMA


def test_macd_no_emit_below_200ema_regime() -> None:
    # A downtrend (close < 200-EMA) → the regime filter blocks regardless of MACD.
    s = MACDEMATrendPullbackStrategy()
    closes = [300.0 - i * 1.0 for i in range(EMA_FILTER + 40)]  # falling
    bars = _bars(closes)
    assert s.generate_raw_signal(_mv(bars)) is None


def test_macd_no_emit_without_volume_confirm() -> None:
    # Same pullback-cross setup but WITHOUT the volume spike → volume gate blocks.
    s = MACDEMATrendPullbackStrategy()
    bars = _macd_uptrend_with_pullback_cross()
    last = bars[-1]
    # last-bar volume BELOW the 20-bar average → no confirm.
    avg = sum(b.volume for b in bars[-(MACD_VOL_LOOKBACK + 1):-1]) / MACD_VOL_LOOKBACK
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=last.high, low=last.low,
        close=last.close, volume=avg * 0.5,
    )
    assert s.generate_raw_signal(_mv(bars)) is None


def test_macd_symbol_gate_flow_safe() -> None:
    s = MACDEMATrendPullbackStrategy()
    bars = _macd_uptrend_with_pullback_cross()
    assert s.generate_raw_signal(_mv(bars, symbol="XRP-USDT")) is None  # XRP excluded


def test_macd_metadata_adr008_contract() -> None:
    meta = MACDEMATrendPullbackStrategy.metadata
    assert meta.timeframe == "1D"
    assert meta.hold_overnight is True
    assert meta.profit_target_r is None
    assert meta.warmup_bars == EMA_FILTER + 26 + 9 == 235
    assert bucket_from_correlation_group(meta.correlation_group_id) is Bucket.TREND


# ===========================================================================
# tsmom_12_1_multiasset (monthly rebalance)
# ===========================================================================


def _ts_at_month_start(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp())


def _tsmom_bars_rebalance(*, positive_mom: bool) -> list[BarView]:
    """Daily bars whose NEWEST bar is the first bar of a new month (a rebalance
    boundary). ``positive_mom`` controls the 12-1 sign."""
    n = MOM_LOOKBACK + 5
    # End the series exactly on a month-start so the last two bars straddle the
    # month boundary (prev = last day of prior month, last = 1st of new month).
    end_ts = _ts_at_month_start(2024, 7)  # 2024-07-01 UTC
    start_ts = end_ts - (n - 1) * _DAY
    if positive_mom:
        closes = [100.0 + i * 0.4 for i in range(n)]  # rising → 12-1 > 0
    else:
        closes = [200.0 - i * 0.4 for i in range(n)]  # falling → 12-1 < 0
    return _bars(closes, start_ts=start_ts)


def test_tsmom_emits_long_on_rebalance_with_positive_momentum() -> None:
    s = TSMom12_1MultiAssetStrategy()
    bars = _tsmom_bars_rebalance(positive_mom=True)
    # sanity: the newest bar is a month-start (rebalance boundary).
    last_m = datetime.fromtimestamp(bars[-1].ts, tz=UTC)
    prev_m = datetime.fromtimestamp(bars[-2].ts, tz=UTC)
    assert (last_m.year, last_m.month) != (prev_m.year, prev_m.month)
    sig = s.generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "tsmom_12_1_multiasset"
    assert sig.tags["rebalance"] == "monthly"


def test_tsmom_no_emit_off_rebalance_bar() -> None:
    # Shift the whole series back a few days so the newest bar is mid-month (NOT
    # a month boundary) → no emit even with positive momentum (cadence gate).
    s = TSMom12_1MultiAssetStrategy()
    bars = _tsmom_bars_rebalance(positive_mom=True)
    shifted = [
        BarView(ts=b.ts - 10 * _DAY, open=b.open, high=b.high, low=b.low,
                close=b.close, volume=b.volume)
        for b in bars
    ]
    last_m = datetime.fromtimestamp(shifted[-1].ts, tz=UTC)
    prev_m = datetime.fromtimestamp(shifted[-2].ts, tz=UTC)
    assert (last_m.year, last_m.month) == (prev_m.year, prev_m.month)  # mid-month
    assert s.generate_raw_signal(_mv(shifted)) is None


def test_tsmom_no_emit_when_momentum_negative() -> None:
    s = TSMom12_1MultiAssetStrategy()
    bars = _tsmom_bars_rebalance(positive_mom=False)  # 12-1 < 0 on a rebalance bar
    assert s.generate_raw_signal(_mv(bars)) is None


def test_tsmom_12_1_skip_math_is_correct() -> None:
    # The 12-1 momentum uses close[-(SKIP+1)] / close[-(LOOKBACK+1)] - 1 (skips
    # the most recent ~21 bars). Verify the emitted tag matches that exact math.
    s = TSMom12_1MultiAssetStrategy()
    bars = _tsmom_bars_rebalance(positive_mom=True)
    closes = [b.close for b in bars]
    expected = closes[-(MOM_SKIP + 1)] / closes[-(MOM_LOOKBACK + 1)] - 1.0
    sig = s.generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.tags["mom_12_1"] == f"{expected:.4f}"


def test_tsmom_symbol_gate_flow_safe() -> None:
    s = TSMom12_1MultiAssetStrategy()
    bars = _tsmom_bars_rebalance(positive_mom=True)
    assert s.generate_raw_signal(_mv(bars, symbol="DOGE-USDT")) is None


def test_tsmom_metadata_adr008_contract() -> None:
    meta = TSMom12_1MultiAssetStrategy.metadata
    assert meta.timeframe == "1D"
    assert meta.hold_overnight is True
    assert meta.profit_target_r is None
    assert meta.warmup_bars == MOM_LOOKBACK + 1 == 253
    assert bucket_from_correlation_group(meta.correlation_group_id) is Bucket.TREND


# ===========================================================================
# Registration — all 4 registered + dispatched; fx_range_fade un-registered
# ===========================================================================

_NEW_IDS = (
    "okx_donchian_55_breakout",
    "tsmom_12_1_multiasset",
    "macd_ema_trend_pullback",
    "donchian_turtle_breakout",
)


@pytest.mark.parametrize("sid", _NEW_IDS)
def test_new_strategy_registered(sid: str) -> None:
    assert sid in STRATEGY_REGISTRY


def test_new_strategies_dispatched() -> None:
    from polaris.scripts._production_tick import _all_strategies

    ids = {s.metadata.strategy_id for s in _all_strategies()}
    for sid in _NEW_IDS:
        assert sid in ids


def test_fx_range_fade_unregistered() -> None:
    # KILLed in the wave1 restructure — not in the live registry, not dispatched.
    from polaris.scripts._production_tick import _all_strategies

    assert "fx_range_fade" not in STRATEGY_REGISTRY
    assert "fx_range_fade" not in {s.metadata.strategy_id for s in _all_strategies()}


def test_fx_range_fade_module_preserved() -> None:
    # Behaviour is severed but the module + class are preserved (data/research).
    from polaris.strategies.fx_range_fade import FXRangeFadeStrategy

    assert FXRangeFadeStrategy.metadata.strategy_id == "fx_range_fade"


def test_other_strategies_not_regressed() -> None:
    # The kill + the 4 new builds must NOT drop the untouched live strategies.
    # (volume_burst was itself un-registered 2026-06-27 in the #61 live-churn
    # KILL — no longer a regression sentinel here.)
    for sid in (
        "rsi_bb_pullback",
        "spot_donchian",
        "bar_breakout_run",
        "session_breakout",
        "fx_breakout_basket",
        "xau_indices_trend",
    ):
        assert sid in STRATEGY_REGISTRY
