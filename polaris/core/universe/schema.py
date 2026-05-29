"""Layer 0 dataclasses + constants — UniverseInstrument + FocusSelection.

Spec source: vault/30_components/layer-0-universe-discovery.md (Dataclass + Constants).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Refresh / focus / listing constants (Q1, Q3, Q6)
# ---------------------------------------------------------------------------

OKX_UNIVERSE_REFRESH_SEC: Final[int] = 300
CAPITAL_UNIVERSE_REFRESH_SEC: Final[int] = 600

FOCUS_TARGET_BASE: Final[int] = 30
FOCUS_TARGET_MIN: Final[int] = 12
FOCUS_TARGET_MAX: Final[int] = 48

NEW_LISTING_WATCH_HOURS: Final[int] = 24
NEW_LISTING_SIZE_MULT: Final[float] = 0.5
NEW_LISTING_MAX_CONCURRENT: Final[int] = 1

UNDERLYING_BTC_GROSS_CAP: Final[float] = 0.60

# ---------------------------------------------------------------------------
# 4-axis hard filter defaults (Q2)
# ---------------------------------------------------------------------------

DEFAULT_MIN_VOL_24H_USD: Final[float] = 30_000_000.0
DEFAULT_MAX_SPREAD_BPS: Final[float] = 10.0
DEFAULT_MIN_ATR_24H_PCT: Final[float] = 2.0
DEFAULT_MIN_DEPTH_10BPS_USD: Final[float] = 25_000.0

# Asset-class-specific ATR floors (Day 2 patch).
# Crypto majors run ~3-6% daily; FX majors ~0.4-1%; indices ~0.5-1.5%; commodities ~1-3%.
# A single 2% gate would lock out every CFD venue, so the filter falls back to
# per-class floors when ``asset_class`` is set on the instrument.
ATR_FLOOR_BY_CLASS: Final[dict[str, float]] = {
    "crypto": 2.0,
    "forex": 0.3,
    "indices": 0.4,
    "commodity": 0.5,
    "equity": 1.0,
    "other": 0.5,
}

ALLOWED_QUOTE_CCY_OKX: Final[frozenset[str]] = frozenset({"USDT"})

# ---------------------------------------------------------------------------
# Continuous active-set ranking (flow_not_block — replaces hard 4-axis cut)
# ---------------------------------------------------------------------------
# The hard 4-axis gate over-cut the candidate set (189 → 6), starving cold-start
# samples. Liquidity / spread / depth / ATR are no longer hard blocks: they
# become a continuous composite score and the top-N rows become the active set.
# Hard keep is validity only (state=live; OKX USDT-quote already enforced at
# parse time). Weak candidates still flow — the downstream cell-matrix
# down-routes them. Aggressive bias preserved (flow_not_block).
UNIVERSE_RANK_TOP_N_DEFAULT: Final[int] = 40
UNIVERSE_RANK_TOP_N_ENV: Final[str] = "POLARIS_UNIVERSE_RANK_TOP_N"

# Composite reward weights (vol + realized-vol proxy), z-normalized population.
RANK_SCORE_W_VOL: Final[float] = 0.55
RANK_SCORE_W_ATR: Final[float] = 0.45
# Soft penalties (subtracted from the reward, also z-normalized population).
# Thin depth / wide spread lower the rank but never hard-reject — anomaly edge
# on thinner names is preserved (low-liquidity → anomaly profit thesis).
RANK_PENALTY_W_SPREAD: Final[float] = 0.30
RANK_PENALTY_W_DEPTH: Final[float] = 0.15

# ---------------------------------------------------------------------------
# Pre-rank score weights (Q3)
# ---------------------------------------------------------------------------

RANK_WEIGHT_VOL_Z: Final[float] = 0.35
RANK_WEIGHT_SIGNAL_DENSITY_Z: Final[float] = 0.25
RANK_WEIGHT_ATR_Z: Final[float] = 0.20
RANK_WEIGHT_DEPTH_Z: Final[float] = 0.10
RANK_WEIGHT_CELL_Z: Final[float] = 0.10

FocusBucket = Literal["core", "satellite", "listing_watch"]


@dataclass(frozen=True, slots=True)
class FilterThresholds:
    """4-axis hard-filter knobs (learner-tunable in P1)."""

    min_vol_24h_usd: float = DEFAULT_MIN_VOL_24H_USD
    max_spread_bps: float = DEFAULT_MAX_SPREAD_BPS
    min_atr_24h_pct: float = DEFAULT_MIN_ATR_24H_PCT
    min_depth_10bps_usd: float = DEFAULT_MIN_DEPTH_10BPS_USD


def default_thresholds() -> FilterThresholds:
    """Return spec-defaults thresholds (used by smoke + tests)."""
    return FilterThresholds()


@dataclass(frozen=True, slots=True)
class UniverseInstrument:
    """Snapshot row of an instrument considered for the active universe."""

    venue: str
    symbol: str
    instrument_id: str
    underlying_group_id: str
    asset_class: str
    quote_ccy: str
    state: str
    vol_24h_usd: float
    spread_bps: float
    atr_24h_pct: float
    depth_10bps_usd: float
    signal_density_7d: float = 0.0
    listing_ts: int | None = None
    last_seen_ts: int = 0


@dataclass(frozen=True, slots=True)
class FocusSelection:
    """One row of the focus watchlist for a given cycle."""

    cycle_ts: int
    venue: str
    symbol: str
    focus_score: float
    rank: int
    bucket: FocusBucket
