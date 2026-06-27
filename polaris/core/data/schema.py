"""Layer 1 dataclasses — canonical bar / quote_tick / signal / baseline records.

Spec source: vault/30_components/layer-1-canonical-baseline.md (Dataclass section).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# Allowed metric tokens for normalize() / baseline.
BaselineMetric = Literal["atr", "size", "signal", "volume", "pnl_std"]

ALLOWED_METRICS: Final[frozenset[str]] = frozenset(
    {"atr", "size", "signal", "volume", "pnl_std"}
)

# Allowed bar intervals for the `bars` table. ``1D`` carries the equity
# (Alpaca) daily canvas — the three equity strategies are all ``timeframe=1D``.
# ``4H`` (added 2026-06-27) is the OKX-native swing canvas: yfinance has no 4h
# token, so 4H rides the exchange-fallback path (OKX ``bar=4H``, UTC+8 boundary)
# and never collides with the Yahoo-PRIMARY intraday/daily routing. ADDITIVE: a
# frozenset membership add leaves every existing interval's convert/persist/read
# path byte-identical (no key removed; strategy regression 0).
BAR_INTERVALS: Final[frozenset[str]] = frozenset(
    {"1m", "5m", "15m", "1H", "4H", "1D"}
)

# Cold-start neutral ratio (Q4 of L1 spec).
COLD_START_NEUTRAL: Final[float] = 1.0

# Rolling baseline windows in seconds.
LOOKBACK_FAST_SEC: Final[int] = 7 * 24 * 3600  # 7d for atr/size/signal/volume
LOOKBACK_SLOW_SEC: Final[int] = 30 * 24 * 3600  # 30d for pnl_std


@dataclass(frozen=True, slots=True)
class Bar:
    """Canonical OHLCV bar (cross-venue aligned)."""

    instrument_id: str
    underlying_group_id: str
    venue: str
    symbol: str
    bar_interval: str
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    notional_usd: float = 0.0
    trade_count: int = 0
    vwap: float = 0.0
    bid_close: float = 0.0
    ask_close: float = 0.0
    spread_bps_close: float = 0.0
    source: str = "rest"


@dataclass(frozen=True, slots=True)
class QuoteTick:
    """Best bid/ask snapshot (1Hz throttle)."""

    instrument_id: str
    venue: str
    symbol: str
    ts: int
    bid: float
    ask: float
    mid: float
    spread_bps: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    last_trade_price: float = 0.0
    last_trade_size: float = 0.0
    source: str = "rest"


@dataclass(frozen=True, slots=True)
class BaselineValue:
    """In-memory representation of a ticker baseline row."""

    metric: str
    p50: float
    p75: float
    sample_count: int
    lookback_sec: int
    updated_ts: int

    @property
    def p25(self) -> float:
        """Approximate p25 via reflection: p25 ≈ p50 - (p75 - p50).

        We do not store p25 (Q5 of L1 spec keeps state lean). Used by tests to
        verify p25 < p50 < p75 ordering invariant when a sample window has been
        rolled.
        """
        return max(0.0, 2.0 * self.p50 - self.p75)
