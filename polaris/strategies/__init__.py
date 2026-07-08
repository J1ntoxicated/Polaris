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
  - ``weekend_funding_capitulation_maker`` (correlation_group=
    weekend_funding_positioning_mean_reversion, 1H, WEEKEND-only maker — the 2nd
    weekend edge, positioning-axis, shadow-first #80)

Track B — Capital CFD:
  - ``fx_breakout_basket``     (correlation_group=cfd_fx_trend)
  - ``xau_indices_trend``      (correlation_group=cfd_index_commodity_trend)
  - ``gold_trend_chandelier_1d``      (correlation_group=cfd_gold_trend_chandelier, 1D)
  - ``gold_riskoff_trend_amplify``    (correlation_group=cfd_gold_riskoff_trend, 1D)
  - ``gold_breakout_1h``              (correlation_group=cfd_gold_breakout_1h, 1H)
  - ``index_52w_high_momentum``       (correlation_group=cfd_index_52w_high_momentum, 1D)
  - ``index_dual_momentum_rotation``  (correlation_group=cfd_index_dual_momentum, 1D monthly)
  - ``silver_breakout_1h``            (correlation_group=cfd_silver_breakout_1h, 1H —
    per-symbol clone of gold_breakout_1h on SILVER)
  - ``us100_breakout_1h``             (correlation_group=cfd_us100_breakout_1h, 1H —
    per-symbol clone of gold_breakout_1h on US100, session-gated)
  - ``uk100_breakout_1h``             (correlation_group=cfd_uk100_breakout_1h, 1H —
    per-symbol clone of gold_breakout_1h on UK100, session-gated, probe cap)

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

(``session_breakout`` / ``donchian_turtle_breakout`` / ``equity_52wk_high_breakout``
/ ``equity_vol_expansion_pocket_pivot`` were un-registered 2026-07-06 — KILLed
(B1 prune, live-ledger forensic): these 4 ids are 100.9% of the -$2,024 book
loss — ``session_breakout`` -$933.65 NET / 88 trades / fees 9.7x gross (pure
fee-bleed churn), ``donchian_turtle_breakout`` -$540.58 (real directional
loss), ``equity_52wk_high_breakout`` -$137.23, ``equity_vol_expansion_pocket_pivot``
-$431.05 (0% win). /debate GO — this is CAPITAL CONCENTRATION on the validated
slow-trend edge (the surviving OKX 1D + Capital gold/index strategies), NOT a
defensive throttle. Evidence + debate transcript:
``vault/50_research/debates/strategy_prune_b1_b2_2026-07-06.md``. Same
dispatch-level KILL as the autopsy survivors above — modules preserved
read-only (2 ``donchian_turtle_breakout`` positions were live at prune time;
their exit path resolves via ``STRATEGY_REGISTRY.get()`` → ``None`` → default
exit, degrade-never-crash); only the signal-emit behaviour + registry
membership are severed.

VIRTUAL-mode exception (Jin 2026-07-08): the KILL rationale above is real
maker/taker FEE-BLEED — void when ``POLARIS_VIRTUAL_ACCOUNT=1`` (no real fees).
``session_breakout`` / ``donchian_turtle_breakout`` / ``spot_donchian`` /
``volume_burst`` are re-admitted into ``STRATEGY_REGISTRY`` for VIRTUAL only,
below. The two equity ids were withheld from that re-admit pending Alpaca 1D
bar supply; Jin 2026-07-09 — now confirmed live (``connors_rsi2`` fires on the
same 1D/alpaca feed) — so ``equity_52wk_high_breakout`` /
``equity_vol_expansion_pocket_pivot`` join the same VIRTUAL-only re-admit
below. REAL byte-identical: the re-registration block never runs when the env
is unset, so the REAL registry keeps exactly the members listed above.)

The four 1D OKX strategies above are the verified fee-beating survivors built in
the strategy-wave1 restructure (OOS + slippage + fee-hurdle). The crypto-major
legs deploy live now; the multi-asset strategies' equity-ETF legs (tsmom / macd /
turtle) are inert until the Alpaca SIP key (#42) routes equity bars
(degrade-never-crash: an un-routed symbol → no emit, never a crash).
"""

from __future__ import annotations

from polaris.strategies._virtual_loosen import virtual_mode_enabled
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
from polaris.strategies.silver_breakout_1h import SilverBreakout1HStrategy
from polaris.strategies.spot_donchian import SpotDonchianStrategy
from polaris.strategies.supertrend import SupertrendStrategy
from polaris.strategies.tsmom_12_1_multiasset import TSMom12_1MultiAssetStrategy
from polaris.strategies.uk100_breakout_1h import UK100Breakout1HStrategy
from polaris.strategies.us100_breakout_1h import US100Breakout1HStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy
from polaris.strategies.weekend_funding_capitulation_maker import (
    WeekendFundingCapitulationMakerStrategy,
)
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
    FXBreakoutBasketStrategy.metadata.strategy_id: FXBreakoutBasketStrategy,
    XAUIndicesTrendStrategy.metadata.strategy_id: XAUIndicesTrendStrategy,
    EMACrossoverStrategy.metadata.strategy_id: EMACrossoverStrategy,
    ConnorsRSI2Strategy.metadata.strategy_id: ConnorsRSI2Strategy,
    SupertrendStrategy.metadata.strategy_id: SupertrendStrategy,
    CCIReversionStrategy.metadata.strategy_id: CCIReversionStrategy,
    # strategy-wave2 — 7 verified fee-beating research survivors (2026-06-27).
    # Capital CFD GOLD / index (5, deploy live) + Alpaca equity (2, inert until
    # SIP #42 routes equity bars — degrade-never-crash). (equity_52wk_high_breakout
    # / equity_vol_expansion_pocket_pivot were un-registered 2026-07-06 — B1 prune,
    # see the KILL block in the module docstring above; the 5 Capital CFD survivors
    # below are unaffected.)
    GoldTrendChandelier1DStrategy.metadata.strategy_id: GoldTrendChandelier1DStrategy,
    GoldRiskoffTrendAmplifyStrategy.metadata.strategy_id: GoldRiskoffTrendAmplifyStrategy,
    GoldBreakout1HStrategy.metadata.strategy_id: GoldBreakout1HStrategy,
    Index52WHighMomentumStrategy.metadata.strategy_id: Index52WHighMomentumStrategy,
    IndexDualMomentumRotationStrategy.metadata.strategy_id: IndexDualMomentumRotationStrategy,
    # #77 weekend maker — the SINGLE verified crypto maker BUILD (research
    # w5xhhz2m9: 1 of 12 net-positive under the real maker fee, +73 bps). OKX
    # SPOT, weekend (Sat/Sun UTC) thin-book flush, post-only deep bid, no-fill =
    # cancel/skip (a missed deep bid = 0 cost), bounded +0.30R revert harvest /
    # -1.0R rail (engine-owned TAKER).
    WeekendThinBookFlushMakerStrategy.metadata.strategy_id: (
        WeekendThinBookFlushMakerStrategy
    ),
    # #80 weekend funding — the 2nd weekend OKX edge (positioning-axis,
    # ORTHOGONAL to the flush). OKX SPOT, weekend (Sat/Sun UTC), extreme-negative
    # per-symbol perp funding (<= p10 = crowded-short capitulation), post-only
    # SPOT long, no-fill = cancel/skip, bounded +1R squeeze-unwind harvest /
    # -1.0R rail (engine-owned TAKER). Shadow-first (95d single-period sample).
    WeekendFundingCapitulationMakerStrategy.metadata.strategy_id: (
        WeekendFundingCapitulationMakerStrategy
    ),
    # Opus CONFIRMED backtest spec — 3 per-symbol clones of gold_breakout_1h
    # (per_ticker_tailored: each its OWN correlation_group_id, no shared logic).
    SilverBreakout1HStrategy.metadata.strategy_id: SilverBreakout1HStrategy,
    US100Breakout1HStrategy.metadata.strategy_id: US100Breakout1HStrategy,
    UK100Breakout1HStrategy.metadata.strategy_id: UK100Breakout1HStrategy,
}

# VIRTUAL-mode-only re-registration (Jin 2026-07-08, extended 2026-07-09): the
# first 4 ids were un-registered 2026-07-06 (B1 prune) for FEE-BLEED — real
# maker/taker fees ate the edge live (session_breakout -$933.65/88 trades fees
# 9.7x gross, donchian_turtle_breakout -$540.58, spot_donchian fee-fatal
# intraday class, volume_burst inverted-asymmetry churn). VIRTUAL has NO real
# fees, so the fee-bleed KILL rationale is VOID there — re-admit for virtual
# observation only. The 2 equity ids were separately un-registered in the same
# B1 prune and withheld from the first re-admit pending Alpaca 1D bar supply;
# that is now confirmed live (``connors_rsi2`` fires on the same 1D/alpaca
# feed), so they join this VIRTUAL-only re-admit too. REAL registry above
# stays byte-identical (env unset -> this block never runs, so REAL never sees
# these ids).
if virtual_mode_enabled():  # pragma: no branch — deterministic per-process env read
    for _virtual_only_cls in (
        SessionBreakoutStrategy,
        DonchianTurtleBreakoutStrategy,
        SpotDonchianStrategy,
        VolumeBurstStrategy,
        Equity52WkHighBreakoutStrategy,
        EquityVolExpansionPocketPivotStrategy,
    ):
        STRATEGY_REGISTRY[_virtual_only_cls.metadata.strategy_id] = _virtual_only_cls


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
    "SilverBreakout1HStrategy",
    "SpotDonchianStrategy",
    "StrategyMetadata",
    "SupertrendStrategy",
    "TSMom12_1MultiAssetStrategy",
    "UK100Breakout1HStrategy",
    "US100Breakout1HStrategy",
    "VolumeBurstStrategy",
    "WeekendFundingCapitulationMakerStrategy",
    "WeekendThinBookFlushMakerStrategy",
    "XAUIndicesTrendStrategy",
    "all_strategies",
]
