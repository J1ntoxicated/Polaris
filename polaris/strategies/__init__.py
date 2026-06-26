"""Polaris strategy registry — signal generators (ADR-008).

Each strategy = ``BaseStrategy`` subclass that emits ``RawSignal | None`` from
``generate_raw_signal(market_view)``. Lifecycle (entry / exit / swap) belongs
to the AI gate pipeline (Layer 2) and the live-recalc engine (Layer 6).

Track A — OKX SPOT:
  - ``volume_burst``               (correlation_group=spot_intraday_event)
  - ``rsi_bb_pullback``            (correlation_group=spot_mean_reversion)
  - ``spot_donchian``              (correlation_group=spot_breakout, 1H)
  - ``bar_breakout_run``           (correlation_group=bar_momentum_breakout, 1D)
  - ``okx_donchian_55_breakout``   (correlation_group=okx_donchian_55_breakout, 1D)
  - ``tsmom_12_1_multiasset``      (correlation_group=tsmom_position_momentum, 1D)
  - ``macd_ema_trend_pullback``    (correlation_group=macd_ema_trend_continuation, 1D)
  - ``donchian_turtle_breakout``   (correlation_group=turtle_donchian_breakout, 1D)

Track B — Capital CFD:
  - ``fx_breakout_basket``     (correlation_group=cfd_fx_trend)
  - ``xau_indices_trend``      (correlation_group=cfd_index_commodity_trend)
  - ``session_breakout``       (correlation_group=cfd_session_event)

(``tsmom``, ``equity_tsmom``, ``equity_rsi_bb_pullback``, ``equity_gap_go`` were
KILLed 2026-06-26 — gross-negative entry expectancy (negative BEFORE fees,
cross-validated over two windows). Their modules stay read-only for research;
they are no longer registered or dispatched.)

(``fx_range_fade`` was un-registered 2026-06-27 — KILLed in the strategy-wave1
restructure; its module + historical data are preserved read-only, behaviour
only is severed.)

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
from polaris.strategies.fx_breakout_basket import FXBreakoutBasketStrategy
from polaris.strategies.macd_ema_trend_pullback import MACDEMATrendPullbackStrategy
from polaris.strategies.okx_donchian_55_breakout import OKXDonchian55BreakoutStrategy
from polaris.strategies.rsi_bb_pullback import RSIBBPullbackStrategy
from polaris.strategies.session_breakout import SessionBreakoutStrategy
from polaris.strategies.spot_donchian import SpotDonchianStrategy
from polaris.strategies.supertrend import SupertrendStrategy
from polaris.strategies.tsmom_12_1_multiasset import TSMom12_1MultiAssetStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy
from polaris.strategies.xau_indices_trend import XAUIndicesTrendStrategy

# Registry: strategy_id → factory. ``factory()`` returns a fresh instance.
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    VolumeBurstStrategy.metadata.strategy_id: VolumeBurstStrategy,
    RSIBBPullbackStrategy.metadata.strategy_id: RSIBBPullbackStrategy,
    SpotDonchianStrategy.metadata.strategy_id: SpotDonchianStrategy,
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
    "FXBreakoutBasketStrategy",
    "MACDEMATrendPullbackStrategy",
    "MarketView",
    "OKXDonchian55BreakoutStrategy",
    "RSIBBPullbackStrategy",
    "RawSignal",
    "STRATEGY_REGISTRY",
    "SessionBreakoutStrategy",
    "SpotDonchianStrategy",
    "StrategyMetadata",
    "SupertrendStrategy",
    "TSMom12_1MultiAssetStrategy",
    "VolumeBurstStrategy",
    "XAUIndicesTrendStrategy",
    "all_strategies",
]
