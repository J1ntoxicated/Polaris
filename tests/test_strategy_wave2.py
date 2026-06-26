"""strategy-wave2 — 7 verified fee-beating research survivors (TDD).

DEMO/PAPER paper-trading. Each strategy is a signal generator (ADR-008): it emits
the ENTRY trigger only; exit / sizing lifecycle is owned by the G5/G7 gates via
StrategyMetadata (TREND let-run — no reversion substring, hold_overnight=True,
profit_target_r=None → let-winners-run, the loss/profit-asymmetry mandate).
flow_not_block: a clean trigger ALWAYS emits, no defensive entry block / size dampen.

Covers per strategy: ENTRY trigger, in-module indicator recompute (Donchian-55 /
252-bar high / ROC_60/120 / SMA10/50 / monthly-rebalance / pocket-pivot volume /
chandelier basis are NOT pre-fed), ADR-008 metadata wiring, registry registration,
no look-ahead (window excludes the current bar), out-of-universe no-emit, and the
degrade-never-crash fallbacks (peer-rank absent / DXY absent / VIX neutral).
"""

from __future__ import annotations

from datetime import UTC, datetime

from polaris.core.live_recalc.exit_thesis import bucket_from_correlation_group
from polaris.core.live_recalc.exit_types import Bucket
from polaris.strategies import STRATEGY_REGISTRY, AltDataView, BarView, MarketView
from polaris.strategies.equity_52wk_high_breakout import (
    HIGH_LOOKBACK as EQ_HIGH_LOOKBACK,
)
from polaris.strategies.equity_52wk_high_breakout import (
    PROXIMITY as EQ_PROXIMITY,
)
from polaris.strategies.equity_52wk_high_breakout import (
    Equity52WkHighBreakoutStrategy,
)
from polaris.strategies.equity_vol_expansion_pocket_pivot import (
    DOWN_VOL_LOOKBACK,
    SMA_SLOW,
    VOL_MULT,
    EquityVolExpansionPocketPivotStrategy,
)
from polaris.strategies.gold_breakout_1h import (
    DONCHIAN_WINDOW as G1H_WINDOW,
)
from polaris.strategies.gold_breakout_1h import (
    GoldBreakout1HStrategy,
)
from polaris.strategies.gold_riskoff_trend_amplify import (
    DONCHIAN_WINDOW as GRA_WINDOW,
)
from polaris.strategies.gold_riskoff_trend_amplify import (
    RISKOFF_AMP_MAX,
    VIX_RISKOFF_LEVEL,
    GoldRiskoffTrendAmplifyStrategy,
)
from polaris.strategies.gold_trend_chandelier_1d import (
    DONCHIAN_WINDOW as GTC_WINDOW,
)
from polaris.strategies.gold_trend_chandelier_1d import (
    GoldTrendChandelier1DStrategy,
)
from polaris.strategies.index_52w_high_momentum import (
    HIGH_LOOKBACK as IDX_HIGH_LOOKBACK,
)
from polaris.strategies.index_52w_high_momentum import (
    PROXIMITY as IDX_PROXIMITY,
)
from polaris.strategies.index_52w_high_momentum import (
    ROC_LOOKBACK as IDX_ROC,
)
from polaris.strategies.index_52w_high_momentum import (
    Index52WHighMomentumStrategy,
)
from polaris.strategies.index_dual_momentum_rotation import (
    ROC_LOOKBACK as DM_ROC,
)
from polaris.strategies.index_dual_momentum_rotation import (
    TOP_N,
    IndexDualMomentumRotationStrategy,
)

_DAY = 86_400
_HOUR = 3_600


# ---------------------------------------------------------------------------
# Bar fixtures
# ---------------------------------------------------------------------------


def _bars(
    n: int,
    *,
    base_close: float,
    drift: float,
    step: int = _DAY,
    start_ts: int = 1_700_000_000,
    vol: float = 1000.0,
) -> list[BarView]:
    """``n`` BarViews with a linear ``drift`` per bar (newest last)."""
    out: list[BarView] = []
    for i in range(n):
        c = base_close + i * drift
        out.append(
            BarView(
                ts=start_ts + i * step,
                open=c - 0.2,
                high=c + 0.3,
                low=c - 0.4,
                close=c,
                volume=vol,
                notional_usd=vol * c,
            )
        )
    return out


def _breakout_last(bars: list[BarView], window: int, *, clear: float = 5.0) -> None:
    """Rewrite the last bar so it strictly breaks the prior-``window`` high."""
    prior_high = max(b.high for b in bars[-(window + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + clear,
        low=last.low, close=prior_high + clear - 1.0, volume=last.volume,
        notional_usd=last.notional_usd,
    )


def _mv(
    bars: list[BarView],
    *,
    symbol: str,
    venue: str,
    timeframe: str = "1D",
    ma_200: float | None = None,
    altdata: AltDataView | None = None,
    extra: dict | None = None,
) -> MarketView:
    return MarketView(
        symbol=symbol,
        venue=venue,
        timeframe=timeframe,
        bars=bars,
        last_price=bars[-1].close if bars else 0.0,
        spread_bps=4.0,
        ma_200=ma_200,
        altdata=altdata if altdata is not None else AltDataView(),
        extra=extra if extra is not None else {},
    )


def _assert_trend_let_run(sid: str) -> None:
    m = STRATEGY_REGISTRY[sid].metadata
    assert bucket_from_correlation_group(m.correlation_group_id) is Bucket.TREND
    assert m.hold_overnight is True
    assert m.profit_target_r is None


# ---------------------------------------------------------------------------
# 1. gold_trend_chandelier_1d (Capital GOLD 1D Donchian-55)
# ---------------------------------------------------------------------------


def test_gold_chandelier_emits_on_d55_break() -> None:
    s = GoldTrendChandelier1DStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    _breakout_last(bars, GTC_WINDOW)
    sig = s.generate_raw_signal(_mv(bars, symbol="GOLD", venue="capital"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "gold_trend_chandelier_1d"
    assert sig.venue_constraints.get("leverage_max") == 20.0


def test_gold_chandelier_no_emit_below_break() -> None:
    s = GoldTrendChandelier1DStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)  # rising, last does NOT break
    last = bars[-1]
    prior_high = max(b.high for b in bars[-(GTC_WINDOW + 1):-1])
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high - 1.0,
        low=last.low - 2.0, close=prior_high - 2.0, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="GOLD", venue="capital")) is None


def test_gold_chandelier_no_lookahead() -> None:
    # close == prior-high (no strict break) but the bar's OWN high is higher —
    # a look-ahead bug would treat this as a breakout.
    s = GoldTrendChandelier1DStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    prior_high = max(b.high for b in bars[-(GTC_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 10.0,
        low=last.low, close=prior_high, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="GOLD", venue="capital")) is None


def test_gold_chandelier_out_of_universe() -> None:
    s = GoldTrendChandelier1DStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    _breakout_last(bars, GTC_WINDOW)
    assert s.generate_raw_signal(_mv(bars, symbol="US500", venue="capital")) is None


def test_gold_chandelier_xauusd_alias() -> None:
    s = GoldTrendChandelier1DStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    _breakout_last(bars, GTC_WINDOW)
    assert s.generate_raw_signal(_mv(bars, symbol="XAUUSD", venue="capital")) is not None


def test_gold_chandelier_metadata_and_registry() -> None:
    _assert_trend_let_run("gold_trend_chandelier_1d")
    m = GoldTrendChandelier1DStrategy.metadata
    assert m.timeframe == "1D"
    assert m.venue == "capital"
    assert m.product_class == "cfd"
    assert GTC_WINDOW == 55
    assert STRATEGY_REGISTRY["gold_trend_chandelier_1d"] is GoldTrendChandelier1DStrategy


# ---------------------------------------------------------------------------
# 2. gold_riskoff_trend_amplify (Capital GOLD 1D D-55 + risk-off amplifier)
# ---------------------------------------------------------------------------


def test_gold_riskoff_emits_on_break_neutral_amp() -> None:
    # No DXY (extra absent) + no VIX → amplifier NEUTRAL (1.0): still emits on break.
    s = GoldRiskoffTrendAmplifyStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    _breakout_last(bars, GRA_WINDOW)
    sig = s.generate_raw_signal(_mv(bars, symbol="GOLD", venue="capital"))
    assert sig is not None
    assert sig.tags["riskoff_amp"] == "1.00"  # degrade-never-crash neutral


def test_gold_riskoff_amplifier_strictly_ge_one() -> None:
    # High VIX → amplifier > 1.0 (strictly above base, never a dampen). The
    # amplified strength is >= the neutral strength on the SAME breakout bar.
    s = GoldRiskoffTrendAmplifyStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    _breakout_last(bars, GRA_WINDOW)
    neutral = s.generate_raw_signal(_mv(bars, symbol="GOLD", venue="capital"))
    hot = s.generate_raw_signal(
        _mv(
            bars, symbol="GOLD", venue="capital",
            altdata=AltDataView(vix=VIX_RISKOFF_LEVEL + 10.0),
        )
    )
    assert neutral is not None and hot is not None
    assert float(hot.tags["riskoff_amp"]) > 1.0
    assert float(hot.tags["riskoff_amp"]) <= RISKOFF_AMP_MAX
    assert hot.strength >= neutral.strength  # amplify never dampens below base


def test_gold_riskoff_dxy_downleg_amplifies() -> None:
    # DXY wired into extra below its EMA → dollar down-leg amplifies.
    s = GoldRiskoffTrendAmplifyStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    _breakout_last(bars, GRA_WINDOW)
    sig = s.generate_raw_signal(
        _mv(bars, symbol="GOLD", venue="capital", extra={"dxy": 100.0, "dxy_ema": 103.0})
    )
    assert sig is not None
    assert float(sig.tags["riskoff_amp"]) > 1.0


def test_gold_riskoff_no_emit_below_break() -> None:
    s = GoldRiskoffTrendAmplifyStrategy()
    bars = _bars(70, base_close=2000.0, drift=1.0)
    last = bars[-1]
    prior_high = max(b.high for b in bars[-(GRA_WINDOW + 1):-1])
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high - 1.0,
        low=last.low - 2.0, close=prior_high - 2.0, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="GOLD", venue="capital")) is None


def test_gold_riskoff_metadata_and_registry() -> None:
    _assert_trend_let_run("gold_riskoff_trend_amplify")
    assert GoldRiskoffTrendAmplifyStrategy.metadata.timeframe == "1D"
    assert STRATEGY_REGISTRY["gold_riskoff_trend_amplify"] is GoldRiskoffTrendAmplifyStrategy


# ---------------------------------------------------------------------------
# 3. gold_breakout_1h (Capital GOLD 1H Donchian-55)
# ---------------------------------------------------------------------------


def test_gold_1h_emits_on_break() -> None:
    s = GoldBreakout1HStrategy()
    bars = _bars(70, base_close=2000.0, drift=0.5, step=_HOUR)
    _breakout_last(bars, G1H_WINDOW)
    sig = s.generate_raw_signal(
        _mv(bars, symbol="GOLD", venue="capital", timeframe="1H")
    )
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "gold_breakout_1h"


def test_gold_1h_no_lookahead() -> None:
    s = GoldBreakout1HStrategy()
    bars = _bars(70, base_close=2000.0, drift=0.5, step=_HOUR)
    prior_high = max(b.high for b in bars[-(G1H_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 10.0,
        low=last.low, close=prior_high, volume=last.volume,
    )
    assert s.generate_raw_signal(
        _mv(bars, symbol="GOLD", venue="capital", timeframe="1H")
    ) is None


def test_gold_1h_metadata_and_registry() -> None:
    _assert_trend_let_run("gold_breakout_1h")
    m = GoldBreakout1HStrategy.metadata
    assert m.timeframe == "1H"
    assert G1H_WINDOW == 55
    assert STRATEGY_REGISTRY["gold_breakout_1h"] is GoldBreakout1HStrategy


# ---------------------------------------------------------------------------
# 4. index_52w_high_momentum (Capital index 252-bar high + ROC_60)
# ---------------------------------------------------------------------------


def _fresh_252_high(bars: list[BarView], roc_lookback: int) -> None:
    """Rewrite last bar to make a FRESH 252-bar high with positive ROC."""
    prior_high = max(b.high for b in bars[-(IDX_HIGH_LOOKBACK + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )


def test_index_52w_emits_on_fresh_high_with_roc() -> None:
    s = Index52WHighMomentumStrategy()
    bars = _bars(260, base_close=4000.0, drift=2.0)
    _fresh_252_high(bars, IDX_ROC)
    sig = s.generate_raw_signal(_mv(bars, symbol="US500", venue="capital"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "index_52w_high_momentum"


def test_index_52w_no_emit_on_stale_high() -> None:
    # Rising then last bar well BELOW the 252-high (> 2% away) → not within prox.
    s = Index52WHighMomentumStrategy()
    bars = _bars(260, base_close=4000.0, drift=2.0)
    prior_high = max(b.high for b in bars[-(IDX_HIGH_LOOKBACK + 1):-1])
    last = bars[-1]
    stale = prior_high * (IDX_PROXIMITY - 0.05)  # 5% below prox floor
    bars[-1] = BarView(
        ts=last.ts, open=stale, high=stale + 0.2, low=stale - 0.2,
        close=stale, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="US500", venue="capital")) is None


def test_index_52w_no_emit_when_roc_negative() -> None:
    # Make a fresh 252-high but a NEGATIVE ROC_60 (price 60 bars ago was higher).
    s = Index52WHighMomentumStrategy()
    bars = _bars(260, base_close=4000.0, drift=2.0)
    # Spike the bar 61 back ABOVE the current close so ROC_60 < 0, while still
    # letting the current bar be a fresh 252-high vs the *prior* window highs.
    idx = len(bars) - (IDX_ROC + 1)
    b = bars[idx]
    bars[idx] = BarView(
        ts=b.ts, open=b.open, high=b.high, low=b.low,
        close=bars[-1].close + 100.0, volume=b.volume,
    )
    _fresh_252_high(bars, IDX_ROC)
    # ROC_60 now uses the inflated close[-61] → negative → no emit.
    assert s.generate_raw_signal(_mv(bars, symbol="US500", venue="capital")) is None


def test_index_52w_out_of_universe() -> None:
    s = Index52WHighMomentumStrategy()
    bars = _bars(260, base_close=4000.0, drift=2.0)
    _fresh_252_high(bars, IDX_ROC)
    assert s.generate_raw_signal(_mv(bars, symbol="BTC-USDT", venue="okx")) is None


def test_index_52w_au200au_alias() -> None:
    s = Index52WHighMomentumStrategy()
    bars = _bars(260, base_close=4000.0, drift=2.0)
    _fresh_252_high(bars, IDX_ROC)
    assert s.generate_raw_signal(_mv(bars, symbol="AU200AU", venue="capital")) is not None


def test_index_52w_metadata_and_registry() -> None:
    _assert_trend_let_run("index_52w_high_momentum")
    assert IDX_HIGH_LOOKBACK == 252
    assert STRATEGY_REGISTRY["index_52w_high_momentum"] is Index52WHighMomentumStrategy


# ---------------------------------------------------------------------------
# 5. index_dual_momentum_rotation (Capital index monthly rotation)
# ---------------------------------------------------------------------------


def _monthly_rebalance_bars(n: int, *, base_close: float, drift: float) -> list[BarView]:
    """Daily bars whose LAST bar crosses a UTC month boundary (rebalance bar)."""
    # Anchor so the penultimate bar is the last day of a month and the last bar
    # is the first day of the next month.
    boundary = int(datetime(2022, 2, 1, tzinfo=UTC).timestamp())
    start = boundary - (n - 1) * _DAY
    bars = _bars(n, base_close=base_close, drift=drift, start_ts=start)
    return bars


def test_dual_momentum_emits_on_rebalance_abs_only() -> None:
    # No peer feed → absolute-gate-only fallback (ROC_120>0 → long).
    s = IndexDualMomentumRotationStrategy()
    bars = _monthly_rebalance_bars(140, base_close=4000.0, drift=2.0)
    sig = s.generate_raw_signal(_mv(bars, symbol="US500", venue="capital"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.tags["rank"] == "abs_only"  # degrade-never-crash fallback


def test_dual_momentum_no_emit_off_rebalance() -> None:
    # Mid-month last bar (no month rollover) → no emit despite positive momentum.
    s = IndexDualMomentumRotationStrategy()
    bars = _bars(140, base_close=4000.0, drift=2.0,
                 start_ts=int(datetime(2022, 1, 2, tzinfo=UTC).timestamp()))
    assert s.generate_raw_signal(_mv(bars, symbol="US500", venue="capital")) is None


def test_dual_momentum_no_emit_when_roc120_negative() -> None:
    # Falling series → ROC_120 < 0 → absolute gate blocks (cash safe-harbor).
    s = IndexDualMomentumRotationStrategy()
    bars = _monthly_rebalance_bars(140, base_close=5000.0, drift=-2.0)
    assert s.generate_raw_signal(_mv(bars, symbol="US500", venue="capital")) is None


def test_dual_momentum_relative_gate_top2() -> None:
    # Peer feed wired into extra: US500 ranked outside top-2 → no emit.
    s = IndexDualMomentumRotationStrategy()
    bars = _monthly_rebalance_bars(140, base_close=4000.0, drift=2.0)
    own_base = bars[-(DM_ROC + 1)].close
    own_roc = bars[-1].close / own_base - 1.0
    # Three peers all stronger than US500 → US500 is rank 4 (> TOP_N=2).
    peers = {
        "US100": own_roc + 0.30,
        "J225": own_roc + 0.20,
        "HK50": own_roc + 0.10,
    }
    sig = s.generate_raw_signal(
        _mv(bars, symbol="US500", venue="capital", extra={"peer_roc_120": peers})
    )
    assert sig is None
    # Now US500 is the strongest → rank 1 → emits with full rank score.
    weak_peers = {"US100": own_roc - 0.30, "J225": own_roc - 0.20}
    sig2 = s.generate_raw_signal(
        _mv(bars, symbol="US500", venue="capital", extra={"peer_roc_120": weak_peers})
    )
    assert sig2 is not None
    assert sig2.tags["rank"] == "1"
    assert TOP_N == 2


def test_dual_momentum_metadata_and_registry() -> None:
    _assert_trend_let_run("index_dual_momentum_rotation")
    assert DM_ROC == 120
    assert STRATEGY_REGISTRY["index_dual_momentum_rotation"] is IndexDualMomentumRotationStrategy


# ---------------------------------------------------------------------------
# 6. equity_52wk_high_breakout (Alpaca 252-bar high + SMA200)
# ---------------------------------------------------------------------------


def test_equity_52wk_emits_on_high_above_sma200() -> None:
    s = Equity52WkHighBreakoutStrategy()
    bars = _bars(260, base_close=100.0, drift=0.5)
    # Within 1% of the 252-high; rising series keeps close > SMA200.
    prior_high = max(b.high for b in bars[-(EQ_HIGH_LOOKBACK + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 2.0,
        low=last.low, close=prior_high + 1.0, volume=last.volume,
    )
    sig = s.generate_raw_signal(_mv(bars, symbol="AAPL", venue="alpaca"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_52wk_high_breakout"


def test_equity_52wk_no_emit_below_sma200() -> None:
    # Fresh local high but close BELOW SMA200 (regime gate) → no emit. Build a
    # series that rises then dips so the last close < SMA200 yet near a prior high.
    s = Equity52WkHighBreakoutStrategy()
    bars = _bars(260, base_close=100.0, drift=0.5)
    last = bars[-1]
    prior_high = max(b.high for b in bars[-(EQ_HIGH_LOOKBACK + 1):-1])
    # Pass ma_200 explicitly ABOVE the proximity-qualifying close → regime blocks.
    near_high = prior_high * EQ_PROXIMITY + 0.5
    bars[-1] = BarView(
        ts=last.ts, open=near_high, high=prior_high + 1.0, low=near_high - 1.0,
        close=near_high, volume=last.volume,
    )
    assert s.generate_raw_signal(
        _mv(bars, symbol="AAPL", venue="alpaca", ma_200=near_high + 50.0)
    ) is None


def test_equity_52wk_no_lookahead() -> None:
    s = Equity52WkHighBreakoutStrategy()
    bars = _bars(260, base_close=100.0, drift=0.5)
    prior_high = max(b.high for b in bars[-(EQ_HIGH_LOOKBACK + 1):-1])
    last = bars[-1]
    # close BELOW proximity floor even though the bar's own high is huge.
    stale = prior_high * (EQ_PROXIMITY - 0.05)
    bars[-1] = BarView(
        ts=last.ts, open=stale, high=prior_high + 50.0, low=stale - 1.0,
        close=stale, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="AAPL", venue="alpaca")) is None


def test_equity_52wk_metadata_and_registry() -> None:
    _assert_trend_let_run("equity_52wk_high_breakout")
    m = Equity52WkHighBreakoutStrategy.metadata
    assert m.venue == "alpaca"
    assert m.product_class == "equity"
    assert EQ_HIGH_LOOKBACK == 252
    assert STRATEGY_REGISTRY["equity_52wk_high_breakout"] is Equity52WkHighBreakoutStrategy


# ---------------------------------------------------------------------------
# 7. equity_vol_expansion_pocket_pivot (Alpaca pocket-pivot)
# ---------------------------------------------------------------------------


def _pocket_pivot_bars() -> list[BarView]:
    """Rising series with small down-bar volumes, then a big up-volume pivot bar."""
    bars = _bars(SMA_SLOW + 10, base_close=100.0, drift=0.5, vol=1000.0)
    # Make the recent prior-10 window contain only modest DOWN-bar volumes.
    for i in range(len(bars) - DOWN_VOL_LOOKBACK - 1, len(bars) - 1):
        b = bars[i]
        # alternate small down bars (close < open) with low volume
        bars[i] = BarView(
            ts=b.ts, open=b.close + 0.3, high=b.high, low=b.low - 0.2,
            close=b.close, volume=800.0,
        )
    last = bars[-1]
    # Pivot bar: up day (close>open), upper-third close, big volume.
    rng_low = last.close - 1.0
    rng_high = last.close + 1.0
    bars[-1] = BarView(
        ts=last.ts, open=rng_low + 0.1,
        high=rng_high, low=rng_low,
        close=rng_high - 0.1,  # upper third
        volume=5000.0,  # >> 1.25 * 50-bar vol SMA (~1000) and > max down vol 800
    )
    return bars


def test_pocket_pivot_emits_on_volume_thrust() -> None:
    s = EquityVolExpansionPocketPivotStrategy()
    bars = _pocket_pivot_bars()
    sig = s.generate_raw_signal(_mv(bars, symbol="NVDA", venue="alpaca"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_vol_expansion_pocket_pivot"
    assert float(sig.tags["vol_ratio"]) > VOL_MULT


def test_pocket_pivot_no_emit_on_weak_volume() -> None:
    s = EquityVolExpansionPocketPivotStrategy()
    bars = _pocket_pivot_bars()
    last = bars[-1]
    # Same up-bar shape but volume at the 50-bar average → fails the 1.25x gate.
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=last.high, low=last.low,
        close=last.close, volume=950.0,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="NVDA", venue="alpaca")) is None


def test_pocket_pivot_no_emit_lower_third_close() -> None:
    s = EquityVolExpansionPocketPivotStrategy()
    bars = _pocket_pivot_bars()
    last = bars[-1]
    # Big volume up day but close in the LOWER third of range → no emit.
    rng_low = last.low
    rng_high = last.high
    bars[-1] = BarView(
        ts=last.ts, open=rng_low + 0.05, high=rng_high, low=rng_low,
        close=rng_low + 0.1, volume=5000.0,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="NVDA", venue="alpaca")) is None


def test_pocket_pivot_metadata_and_registry() -> None:
    _assert_trend_let_run("equity_vol_expansion_pocket_pivot")
    m = EquityVolExpansionPocketPivotStrategy.metadata
    assert m.venue == "alpaca"
    assert SMA_SLOW == 200
    assert (
        STRATEGY_REGISTRY["equity_vol_expansion_pocket_pivot"]
        is EquityVolExpansionPocketPivotStrategy
    )


# ---------------------------------------------------------------------------
# Cross-cutting: registry count + no-regression on the wave1 survivors
# ---------------------------------------------------------------------------


def test_registry_has_20_strategies() -> None:
    # was 21 (strategy-wave2); spot_donchian KILLed 2026-06-27 (#56 stop-bleeders);
    # +weekend_thin_book_flush_maker (#77, single verified crypto maker BUILD) → 21.
    assert len(STRATEGY_REGISTRY) == 21


def test_wave2_all_registered() -> None:
    for sid in (
        "gold_trend_chandelier_1d",
        "gold_riskoff_trend_amplify",
        "gold_breakout_1h",
        "index_52w_high_momentum",
        "index_dual_momentum_rotation",
        "equity_52wk_high_breakout",
        "equity_vol_expansion_pocket_pivot",
    ):
        assert sid in STRATEGY_REGISTRY


def test_wave1_survivors_still_registered() -> None:
    # No regression: the wave1 fee-beating survivors remain registered.
    for sid in (
        "bar_breakout_run",
        "okx_donchian_55_breakout",
        "tsmom_12_1_multiasset",
        "xau_indices_trend",
    ):
        assert sid in STRATEGY_REGISTRY
