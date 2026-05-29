"""Polaris strategy registry — 7 P0 signal generators (ADR-008).

Each strategy = ``BaseStrategy`` subclass that emits ``RawSignal | None`` from
``generate_raw_signal(market_view)``. Lifecycle (entry / exit / swap) belongs
to the AI gate pipeline (Layer 2) and the live-recalc engine (Layer 6).

Track A — OKX SPOT (4):
  - ``volume_burst``           (correlation_group=spot_intraday_event)
  - ``tsmom``                  (correlation_group=spot_cross_sectional_momo)
  - ``rsi_bb_pullback``        (correlation_group=spot_mean_reversion)
  - ``spot_donchian``          (correlation_group=spot_breakout)

Track B — Capital CFD (3):
  - ``fx_breakout_basket``     (correlation_group=cfd_fx_trend)
  - ``xau_indices_trend``      (correlation_group=cfd_index_commodity_trend)
  - ``session_breakout``       (correlation_group=cfd_session_event)

Track C — Alpaca US equity (3, additive — A/B unchanged):
  - ``equity_tsmom``           (correlation_group=equity_cross_sectional_momo)
  - ``equity_rsi_bb_pullback`` (correlation_group=equity_mean_reversion)
  - ``equity_gap_go``          (correlation_group=equity_gap)
"""

from __future__ import annotations

from polaris.strategies.base import (
    COLD_START_NEUTRAL_STRENGTH,
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
)
from polaris.strategies.equity_gap_go import EquityGapGoStrategy
from polaris.strategies.equity_rsi_bb_pullback import EquityRSIBBPullbackStrategy
from polaris.strategies.equity_tsmom import EquityTSMOMStrategy
from polaris.strategies.fx_breakout_basket import FXBreakoutBasketStrategy
from polaris.strategies.rsi_bb_pullback import RSIBBPullbackStrategy
from polaris.strategies.session_breakout import SessionBreakoutStrategy
from polaris.strategies.spot_donchian import SpotDonchianStrategy
from polaris.strategies.tsmom import TSMOMStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy
from polaris.strategies.xau_indices_trend import XAUIndicesTrendStrategy

# Registry: strategy_id → factory. ``factory()`` returns a fresh instance.
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    VolumeBurstStrategy.metadata.strategy_id: VolumeBurstStrategy,
    TSMOMStrategy.metadata.strategy_id: TSMOMStrategy,
    RSIBBPullbackStrategy.metadata.strategy_id: RSIBBPullbackStrategy,
    SpotDonchianStrategy.metadata.strategy_id: SpotDonchianStrategy,
    FXBreakoutBasketStrategy.metadata.strategy_id: FXBreakoutBasketStrategy,
    XAUIndicesTrendStrategy.metadata.strategy_id: XAUIndicesTrendStrategy,
    SessionBreakoutStrategy.metadata.strategy_id: SessionBreakoutStrategy,
    EquityTSMOMStrategy.metadata.strategy_id: EquityTSMOMStrategy,
    EquityRSIBBPullbackStrategy.metadata.strategy_id: EquityRSIBBPullbackStrategy,
    EquityGapGoStrategy.metadata.strategy_id: EquityGapGoStrategy,
}


def all_strategies() -> list[BaseStrategy]:
    """Instantiate one of each registered strategy (used by smoke + tests)."""
    return [cls() for cls in STRATEGY_REGISTRY.values()]


__all__ = [
    "BarView",
    "BaseStrategy",
    "COLD_START_NEUTRAL_STRENGTH",
    "EquityGapGoStrategy",
    "EquityRSIBBPullbackStrategy",
    "EquityTSMOMStrategy",
    "FXBreakoutBasketStrategy",
    "MarketView",
    "RSIBBPullbackStrategy",
    "RawSignal",
    "STRATEGY_REGISTRY",
    "SessionBreakoutStrategy",
    "SpotDonchianStrategy",
    "StrategyMetadata",
    "TSMOMStrategy",
    "VolumeBurstStrategy",
    "XAUIndicesTrendStrategy",
    "all_strategies",
]
