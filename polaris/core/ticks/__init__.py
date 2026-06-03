"""Tick-decision engine core — pure types + feature/signal primitives (P5).

Spec SSOT: ``.claude/plans/p5_tick_decision_engine_2026-06-03.md``.

Exports:
    Types:    TickSample (microstructure projection of a QuoteTick)
    Config:   TickEngineConfig (AGGRESSIVE thresholds/spans), env_shadow_enabled
    Features: TickFeatures, compute_tick_features (pure)
    Signals:  TickIntent, burst_rider / flow_pressure / micro_reversion (pure)
    Regime:   active_signals / direction_bias / normalize_regime (pure gate)
"""

from polaris.core.ticks.config import TickEngineConfig, env_shadow_enabled
from polaris.core.ticks.features import TickFeatures, compute_tick_features
from polaris.core.ticks.regime_gate import (
    active_signals,
    direction_bias,
    normalize_regime,
)
from polaris.core.ticks.signals import (
    TickIntent,
    burst_rider,
    flow_pressure,
    micro_reversion,
)
from polaris.core.ticks.types import TickSample

__all__ = [
    "TickEngineConfig",
    "TickFeatures",
    "TickIntent",
    "TickSample",
    "active_signals",
    "burst_rider",
    "compute_tick_features",
    "direction_bias",
    "env_shadow_enabled",
    "flow_pressure",
    "micro_reversion",
    "normalize_regime",
]
