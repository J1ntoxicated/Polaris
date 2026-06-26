"""Polaris strategy registry — signal generators (ADR-008).

Each strategy = ``BaseStrategy`` subclass that emits ``RawSignal | None`` from
``generate_raw_signal(market_view)``. Lifecycle (entry / exit / swap) belongs
to the AI gate pipeline (Layer 2) and the live-recalc engine (Layer 6).

Track A — OKX SPOT:
  - ``volume_burst``           (correlation_group=spot_intraday_event)
  - ``rsi_bb_pullback``        (correlation_group=spot_mean_reversion)
  - ``spot_donchian``          (correlation_group=spot_breakout)

Track B — Capital CFD:
  - ``fx_breakout_basket``     (correlation_group=cfd_fx_trend)
  - ``xau_indices_trend``      (correlation_group=cfd_index_commodity_trend)
  - ``session_breakout``       (correlation_group=cfd_session_event)

(``tsmom``, ``equity_tsmom``, ``equity_rsi_bb_pullback``, ``equity_gap_go`` were
KILLed 2026-06-26 — gross-negative entry expectancy (negative BEFORE fees,
cross-validated over two windows). Their modules stay read-only for research;
they are no longer registered or dispatched.)
"""

from __future__ import annotations

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
from polaris.strategies.ema_crossover import EMACrossoverStrategy
from polaris.strategies.fx_breakout_basket import FXBreakoutBasketStrategy
from polaris.strategies.fx_range_fade import FXRangeFadeStrategy
from polaris.strategies.rsi_bb_pullback import RSIBBPullbackStrategy
from polaris.strategies.session_breakout import SessionBreakoutStrategy
from polaris.strategies.spot_donchian import SpotDonchianStrategy
from polaris.strategies.supertrend import SupertrendStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy
from polaris.strategies.xau_indices_trend import XAUIndicesTrendStrategy

# Registry: strategy_id → factory. ``factory()`` returns a fresh instance.
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    VolumeBurstStrategy.metadata.strategy_id: VolumeBurstStrategy,
    RSIBBPullbackStrategy.metadata.strategy_id: RSIBBPullbackStrategy,
    SpotDonchianStrategy.metadata.strategy_id: SpotDonchianStrategy,
    FXBreakoutBasketStrategy.metadata.strategy_id: FXBreakoutBasketStrategy,
    FXRangeFadeStrategy.metadata.strategy_id: FXRangeFadeStrategy,
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
    "BarView",
    "BaseStrategy",
    "COLD_START_NEUTRAL_STRENGTH",
    "CCIReversionStrategy",
    "ConnorsRSI2Strategy",
    "EMACrossoverStrategy",
    "FXBreakoutBasketStrategy",
    "FXRangeFadeStrategy",
    "MarketView",
    "RSIBBPullbackStrategy",
    "RawSignal",
    "STRATEGY_REGISTRY",
    "SessionBreakoutStrategy",
    "SpotDonchianStrategy",
    "StrategyMetadata",
    "SupertrendStrategy",
    "VolumeBurstStrategy",
    "XAUIndicesTrendStrategy",
    "all_strategies",
]
