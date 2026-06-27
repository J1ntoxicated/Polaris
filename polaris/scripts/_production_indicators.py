"""Re-export shim — indicator + regime computation moved to core.

``build_real_market_view`` / ``compute_real_regime_signal`` (and the whole
indicator family) are pure functions over bars (deps: ``core.data.schema``,
``core.metrics.risk_unit``, ``strategies.base`` — all core-or-below). The REPLAY
engine (``polaris.core.replay.engine``) consumes them, so importing them UP from
``polaris.scripts`` was a layer inversion. The module now lives at
``polaris.core.indicators.production``. This shim re-exports the full public
surface so every existing
``from polaris.scripts._production_indicators import ...`` keeps working
byte-for-byte. Behaviour is unchanged — this is a move only.
"""

from __future__ import annotations

from polaris.core.indicators.production import (
    REGIME_BULL_THRESHOLD_PCT,
    REGIME_CHOP_EFFICIENCY,
    REGIME_CRISIS_DRAWDOWN_PCT,
    REGIME_CRISIS_M,
    REGIME_CRISIS_VOL_CAP_BY_CLASS,
    REGIME_TREND_K,
    REGIME_VOL_FLOOR_BY_CLASS,
    build_real_market_view,
    compute_adx_14,
    compute_atr_pct,
    compute_bollinger,
    compute_donchian,
    compute_efficiency_ratio,
    compute_ma,
    compute_momentum,
    compute_real_regime,
    compute_real_regime_signal,
    compute_rsi_14,
    compute_unrealized_pnl_r,
    compute_volume_z,
    filter_real_bars,
    is_synthetic_bar,
    map_altdata_to_market_fields,
    session_anchor_index,
    session_window_now,
)

__all__ = [
    "REGIME_BULL_THRESHOLD_PCT",
    "REGIME_CRISIS_DRAWDOWN_PCT",
    "REGIME_CHOP_EFFICIENCY",
    "REGIME_TREND_K",
    "REGIME_CRISIS_M",
    "REGIME_CRISIS_VOL_CAP_BY_CLASS",
    "REGIME_VOL_FLOOR_BY_CLASS",
    "is_synthetic_bar",
    "filter_real_bars",
    "build_real_market_view",
    "map_altdata_to_market_fields",
    "compute_adx_14",
    "compute_atr_pct",
    "compute_bollinger",
    "compute_donchian",
    "compute_efficiency_ratio",
    "compute_ma",
    "compute_momentum",
    "compute_real_regime",
    "compute_real_regime_signal",
    "compute_rsi_14",
    "compute_volume_z",
    "compute_unrealized_pnl_r",
    "session_anchor_index",
    "session_window_now",
]
