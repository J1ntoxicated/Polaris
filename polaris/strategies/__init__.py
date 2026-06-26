"""Polaris strategy registry — signal generators (ADR-008).

Each strategy = ``BaseStrategy`` subclass that emits ``RawSignal | None`` from
``generate_raw_signal(market_view)``. Lifecycle (entry / exit / swap) belongs
to the AI gate pipeline (Layer 2) and the live-recalc engine (Layer 6).

Track A — OKX SPOT:
  - ``rsi_bb_pullback``            (correlation_group=spot_mean_reversion)
  - ``bar_breakout_run``           (correlation_group=bar_momentum_breakout, 1D)
  - ``okx_donchian_55_breakout``   (correlation_group=okx_donchian_55_breakout, 1D)
  - ``tsmom_12_1_multiasset``      (correlation_group=tsmom_position_momentum, 1D)
  - ``macd_ema_trend_pullback``    (correlation_group=macd_ema_trend_continuation, 1D)
  - ``donchian_turtle_breakout``   (correlation_group=turtle_donchian_breakout, 1D)
  - ``weekend_thin_book_flush_maker`` (correlation_group=weekend_thin_book_mean_reversion,
    1H, WEEKEND-only maker — the single verified crypto maker BUILD #77)

Track B — Capital CFD:
  - ``fx_breakout_basket``     (correlation_group=cfd_fx_trend)
  - ``xau_indices_trend``      (correlation_group=cfd_index_commodity_trend)
  - ``session_breakout``       (correlation_group=cfd_session_event)
  - ``gold_trend_chandelier_1d``      (correlation_group=cfd_gold_trend_chandelier, 1D)
  - ``gold_riskoff_trend_amplify``    (correlation_group=cfd_gold_riskoff_trend, 1D)
  - ``gold_breakout_1h``              (correlation_group=cfd_gold_breakout_1h, 1H)
  - ``index_52w_high_momentum``       (correlation_group=cfd_index_52w_high_momentum, 1D)
  - ``index_dual_momentum_rotation``  (correlation_group=cfd_index_dual_momentum, 1D monthly)

Track C — Alpaca US equity (INERT until SIP key #42 routes equity bars):
  - ``equity_52wk_high_breakout``         (correlation_group=equity_52wk_high_breakout, 1D)
  - ``equity_vol_expansion_pocket_pivot`` (correlation_group=equity_vol_expansion_pocket_pivot, 1D)

(``tsmom``, ``equity_tsmom``, ``equity_rsi_bb_pullback``, ``equity_gap_go`` were
KILLed 2026-06-26 — gross-negative entry expectancy (negative BEFORE fees,
cross-validated over two windows). Their modules stay read-only for research;
they are no longer registered or dispatched.)

(``fx_range_fade`` was un-registered 2026-06-27 — KILLed in the strategy-wave1
restructure; its module + historical data are preserved read-only, behaviour
only is severed.)

(``volume_burst`` was un-registered 2026-06-27 — KILLed (#61, autonomous loop):
live churn measured net-negative inverted-asymmetry expectancy (big losses /
small wins, the only high-frequency churner). Its module + historical fills are
preserved read-only; behaviour only is severed — no longer registered or
dispatched.)

(``spot_donchian`` was un-registered 2026-06-27 — KILLed (#56 stop-bleeders):
OKX 1H Donchian is the fee-fatal intraday class the overnight research REJECTed
(slippage-fragile), and it kept losing live AFTER the exit fix (-$85.66 / 16
closes). Same dispatch-level KILL as the autopsy survivors — module + the
open-position close path are preserved read-only; only the signal-emit
behaviour is severed. The dead registry/learner rows are swept by the next
``learner_prune``.)

The four 1D OKX strategies above are the verified fee-beating survivors built in
the strategy-wave1 restructure (OOS + slippage + fee-hurdle). The crypto-major
legs deploy live now; the multi-asset strategies' equity-ETF legs (tsmom / macd /
turtle) are inert until the Alpaca SIP key (#42) routes equity bars
(degrade-never-crash: an un-routed symbol → no emit, never a crash).
"""

from __future__ import annotations

from polaris.strategies.bar_breakout_run import BarBreakoutRunStrategy
from polaris.strategies.base import (
    COLD_START_NEUTRAL_STRENGTH,
    AltDataView,
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
)
from polaris.strategies.cci_reversion import CCIReversionStrategy
from polaris.strategies.connors_rsi2 import ConnorsRSI2Strategy
from polaris.strategies.donchian_turtle_breakout import DonchianTurtleBreakoutStrategy
from polaris.strategies.ema_crossover import EMACrossoverStrategy
from polaris.strategies.equity_52wk_high_breakout import Equity52WkHighBreakoutStrategy
from polaris.strategies.equity_vol_expansion_pocket_pivot import (
    EquityVolExpansionPocketPivotStrategy,
)
from polaris.strategies.fx_breakout_basket import FXBreakoutBasketStrategy
from polaris.strategies.gold_breakout_1h import GoldBreakout1HStrategy
from polaris.strategies.gold_riskoff_trend_amplify import GoldRiskoffTrendAmplifyStrategy
from polaris.strategies.gold_trend_chandelier_1d import GoldTrendChandelier1DStrategy
from polaris.strategies.index_52w_high_momentum import Index52WHighMomentumStrategy
from polaris.strategies.index_dual_momentum_rotation import (
    IndexDualMomentumRotationStrategy,
)
from polaris.strategies.macd_ema_trend_pullback import MACDEMATrendPullbackStrategy
from polaris.strategies.okx_donchian_55_breakout import OKXDonchian55BreakoutStrategy
from polaris.strategies.rsi_bb_pullback import RSIBBPullbackStrategy
from polaris.strategies.session_breakout import SessionBreakoutStrategy
from polaris.strategies.supertrend import SupertrendStrategy
from polaris.strategies.tsmom_12_1_multiasset import TSMom12_1MultiAssetStrategy
from polaris.strategies.weekend_thin_book_flush_maker import (
    WeekendThinBookFlushMakerStrategy,
)
from polaris.strategies.xau_indices_trend import XAUIndicesTrendStrategy

# Registry: strategy_id → factory. ``factory()`` returns a fresh instance.
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    RSIBBPullbackStrategy.metadata.strategy_id: RSIBBPullbackStrategy,
    BarBreakoutRunStrategy.metadata.strategy_id: BarBreakoutRunStrategy,
    OKXDonchian55BreakoutStrategy.metadata.strategy_id: OKXDonchian55BreakoutStrategy,
    TSMom12_1MultiAssetStrategy.metadata.strategy_id: TSMom12_1MultiAssetStrategy,
    MACDEMATrendPullbackStrategy.metadata.strategy_id: MACDEMATrendPullbackStrategy,
    DonchianTurtleBreakoutStrategy.metadata.strategy_id: DonchianTurtleBreakoutStrategy,
    FXBreakoutBasketStrategy.metadata.strategy_id: FXBreakoutBasketStrategy,
    XAUIndicesTrendStrategy.metadata.strategy_id: XAUIndicesTrendStrategy,
    SessionBreakoutStrategy.metadata.strategy_id: SessionBreakoutStrategy,
    EMACrossoverStrategy.metadata.strategy_id: EMACrossoverStrategy,
    ConnorsRSI2Strategy.metadata.strategy_id: ConnorsRSI2Strategy,
    SupertrendStrategy.metadata.strategy_id: SupertrendStrategy,
    CCIReversionStrategy.metadata.strategy_id: CCIReversionStrategy,
    # strategy-wave2 — 7 verified fee-beating research survivors (2026-06-27).
    # Capital CFD GOLD / index (5, deploy live) + Alpaca equity (2, inert until
    # SIP #42 routes equity bars — degrade-never-crash).
    GoldTrendChandelier1DStrategy.metadata.strategy_id: GoldTrendChandelier1DStrategy,
    GoldRiskoffTrendAmplifyStrategy.metadata.strategy_id: GoldRiskoffTrendAmplifyStrategy,
    GoldBreakout1HStrategy.metadata.strategy_id: GoldBreakout1HStrategy,
    Index52WHighMomentumStrategy.metadata.strategy_id: Index52WHighMomentumStrategy,
    IndexDualMomentumRotationStrategy.metadata.strategy_id: IndexDualMomentumRotationStrategy,
    Equity52WkHighBreakoutStrategy.metadata.strategy_id: Equity52WkHighBreakoutStrategy,
    EquityVolExpansionPocketPivotStrategy.metadata.strategy_id: (
        EquityVolExpansionPocketPivotStrategy
    ),
    # #77 weekend maker — the SINGLE verified crypto maker BUILD (research
    # w5xhhz2m9: 1 of 12 net-positive under the real maker fee, +73 bps). OKX
    # SPOT, weekend (Sat/Sun UTC) thin-book flush, post-only deep bid, no-fill =
    # cancel/skip (a missed deep bid = 0 cost), bounded +0.30R revert harvest /
    # -1.0R rail (engine-owned TAKER).
    WeekendThinBookFlushMakerStrategy.metadata.strategy_id: (
        WeekendThinBookFlushMakerStrategy
    ),
}


def all_strategies() -> list[BaseStrategy]:
    """Instantiate one of each registered strategy (used by smoke + tests)."""
    return [cls() for cls in STRATEGY_REGISTRY.values()]


__all__ = [
    "AltDataView",
    "BarBreakoutRunStrategy",
    "BarView",
    "BaseStrategy",
    "COLD_START_NEUTRAL_STRENGTH",
    "CCIReversionStrategy",
    "ConnorsRSI2Strategy",
    "DonchianTurtleBreakoutStrategy",
    "EMACrossoverStrategy",
    "Equity52WkHighBreakoutStrategy",
    "EquityVolExpansionPocketPivotStrategy",
    "FXBreakoutBasketStrategy",
    "GoldBreakout1HStrategy",
    "GoldRiskoffTrendAmplifyStrategy",
    "GoldTrendChandelier1DStrategy",
    "Index52WHighMomentumStrategy",
    "IndexDualMomentumRotationStrategy",
    "MACDEMATrendPullbackStrategy",
    "MarketView",
    "OKXDonchian55BreakoutStrategy",
    "RSIBBPullbackStrategy",
    "RawSignal",
    "STRATEGY_REGISTRY",
    "SessionBreakoutStrategy",
    "StrategyMetadata",
    "SupertrendStrategy",
    "TSMom12_1MultiAssetStrategy",
    "WeekendThinBookFlushMakerStrategy",
    "XAUIndicesTrendStrategy",
    "all_strategies",
]
