"""Strategy ABC + RawSignal + StrategyMetadata + MarketView.

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md.

Each strategy in this package is a **signal generator only** — it returns
``RawSignal | None`` from ``generate_raw_signal(market_view)``. Lifecycle
decisions (entry / size / exit / swap) belong to the AI gate pipeline
(Layer 2) and the live-recalc engine (Layer 6).

Constructors:
  - ``Strategy()`` — caller passes an instance to the worker.
  - ``Strategy.metadata`` — class attribute (frozen dataclass).
  - ``generate_raw_signal(market_view)`` — pure function; no side effects.

P0 = frozen v1 params (per ADR-008). Learner network (Layer 5) will tune
these in P1+ via ``cell_routing_mult`` and behavioural overlay.
"""

from __future__ import annotations

import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Final, Literal

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    """Immutable strategy descriptor (ADR-008)."""

    strategy_id: str
    timeframe: str  # "1m" / "5m" / "15m" / "1H"
    warmup_bars: int
    max_positions: int
    gross_cap: float
    per_symbol_cap: float
    expected_holding_bars: int
    asset_class: str  # "spot" / "fx" / "index" / "commodity"
    venue: str  # "okx" / "capital"
    correlation_group_id: str


@dataclass(frozen=True, slots=True)
class RawSignal:
    """Pre-validation signal emitted by a strategy.

    Spec source: ADR-008 §RawSignal Schema.
    """

    signal_id: str
    strategy_id: str
    symbol: str
    side: Literal["long", "short"]
    strength: float
    sizing_hint: float
    ttl_bars: int
    thesis_tag: str
    correlation_group: str
    venue_constraints: dict[str, Any] = field(default_factory=dict)
    created_at_bar: int = 0
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BarView:
    """Lightweight bar projection used by strategies (subset of canonical Bar)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    notional_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class MarketView:
    """Bundle of indicators a strategy needs for ``generate_raw_signal``.

    Strategies pull from this read-only snapshot — no DB calls. The
    ``running-paper-loop`` orchestrator (Day 5) builds the view per tick.
    """

    symbol: str
    venue: str
    timeframe: str
    bars: list[BarView]  # most recent last
    last_price: float
    spread_bps: float
    atr_pct: float = 0.0  # 24h ATR % (for liquidity floor / signal sizing)
    volume_z: float = 0.0  # rolling vol z-score (volume_burst)
    rsi_14: float | None = None
    bb_lower: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    ma_200: float | None = None
    adx_14: float | None = None
    momentum_20bar: float | None = None  # tsmom
    donchian_high_40: float | None = None
    donchian_low_40: float | None = None
    donchian_high_30: float | None = None
    donchian_low_30: float | None = None
    session_open_price: float | None = None  # session_breakout
    session_open_ts: int | None = None
    session_atr: float | None = None
    is_session_open_window: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers (shared by strategies)
# ---------------------------------------------------------------------------


COLD_START_NEUTRAL_STRENGTH: Final[float] = 0.5


def make_signal_id() -> str:
    return uuid.uuid4().hex


def is_finite(value: float | None) -> bool:
    if value is None:
        return False
    return math.isfinite(value)


# ---------------------------------------------------------------------------
# BaseStrategy ABC
# ---------------------------------------------------------------------------


class BaseStrategy(ABC):
    """Signal generator interface (ADR-008)."""

    metadata: StrategyMetadata  # subclass class attribute

    @abstractmethod
    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        """Return a ``RawSignal`` or ``None`` if no trigger this bar."""

    # default convenience: subclasses can override for special venues.
    def warmup_ok(self, market_view: MarketView) -> bool:
        return len(market_view.bars) >= self.metadata.warmup_bars


__all__ = [
    "BarView",
    "BaseStrategy",
    "COLD_START_NEUTRAL_STRENGTH",
    "MarketView",
    "RawSignal",
    "StrategyMetadata",
    "is_finite",
    "make_signal_id",
]
