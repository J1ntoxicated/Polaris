"""Layer 3 — Sizing dataclasses + constants (T4 formula building blocks).

Spec source:
- vault/30_components/layer-3-sizing-risk.md (Q1 placement, Q2 CS-3, Q3 cluster, Q5 hard cap)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4 formula + caps)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Constants (P0)
# ---------------------------------------------------------------------------

KELLY_FRACTION_K: Final[float] = 0.50
"""Fractional Kelly multiplier (ADR-005 — k=0.5)."""

CS3_N_THRESHOLD: Final[int] = 20
"""Closed trades < this → Cold-Start mode CS-3 (Kelly off, single 6%/7%)."""

CS3_SINGLE_DEFAULT_PCT: Final[float] = 0.06
"""Cold-start single-trade cap (no amplifier)."""

CS3_SINGLE_AMPLIFIED_PCT: Final[float] = 0.07
"""Cold-start single-trade cap (amplifier on)."""

SINGLE_TRADE_DEFAULT_PCT: Final[float] = 0.08
"""ADR-005 max single-trade risk (default)."""

SINGLE_TRADE_AMPLIFIED_PCT: Final[float] = 0.09
"""ADR-005 max single-trade risk (amplifier on)."""

SINGLE_TRADE_ABSOLUTE_CEILING_PCT: Final[float] = 0.09
"""ADR-005 absolute single-trade ceiling (hard MAX)."""

PER_SYMBOL_SPOT_PCT: Final[float] = 0.50
"""ADR-005 — per-symbol cap, OKX SPOT."""

PER_SYMBOL_CFD_PCT: Final[float] = 0.35
"""ADR-005 — per-symbol cap, Capital CFD."""

TRACK_A_GROSS_PCT: Final[float] = 0.60
"""ADR-005 — Track A gross cap."""

TRACK_B_GROSS_PCT: Final[float] = 0.80
"""ADR-005 — Track B gross cap."""

TRACK_A_DAILY_VENUE_PCT: Final[float] = 0.08
"""ADR-005 — Track A daily venue risk."""

TRACK_B_DAILY_VENUE_PCT: Final[float] = 0.09
"""ADR-005 — Track B daily venue risk."""

TOTAL_DAILY_RISK_CEILING_PCT: Final[float] = 0.10
"""ADR-005 — total daily risk absolute ceiling."""

CLUSTER_BTC_ETH_PCT: Final[float] = 0.40
"""ADR-005 cluster cap — BTC/ETH spot."""

CLUSTER_XAU_INDICES_PCT: Final[float] = 0.50
"""ADR-005 cluster cap — XAU/indices CFD."""

CLUSTER_FX_MAJORS_PCT: Final[float] = 0.60
"""ADR-005 cluster cap — FX majors."""

# Tier amplifier triggers
TIER_3WIN_AMP: Final[float] = 1.5
TIER_5WIN_AMP: Final[float] = 2.0
TIER_8WIN_AMP: Final[float] = 3.0
TIER_RESET_AMP: Final[float] = 1.0

TIER_3WIN_MIN_N_LOW: Final[int] = 8
TIER_3WIN_MIN_N_HIGH: Final[int] = 10
TIER_3WIN_HIT_LOW: Final[float] = 0.75
TIER_3WIN_HIT_HIGH: Final[float] = 0.70
TIER_5WIN_MIN_N: Final[int] = 10
TIER_5WIN_HIT: Final[float] = 0.70
TIER_8WIN_MIN_N: Final[int] = 10
TIER_8WIN_HIT: Final[float] = 0.70

# Listing watchdog
LISTING_WATCHDOG_AGE_HOURS: Final[int] = 24
LISTING_WATCHDOG_MULT: Final[float] = 0.5

# Fill-rate cut
FILL_RATE_CUT_THRESHOLD: Final[float] = 0.70
FILL_RATE_RESUME_THRESHOLD: Final[float] = 0.60

# Continuous scalar bounds (ADR-005 T4)
CONT_SCALAR_MIN: Final[float] = 0.75
CONT_SCALAR_MAX: Final[float] = 1.50

# Default base risk (% of equity) when caller didn't specify.
DEFAULT_BASE_RISK_PCT: Final[float] = 0.02


Track = Literal["A", "B"]


@dataclass(frozen=True, slots=True)
class SizingProposal:
    """Pre-cap T4 proposal: ``base × continuous × tier × cell × listing × L5_learner_product``.

    Each multiplier captured for audit so post-trade reflector can attribute
    sizing back to the inputs that drove it. L5 learner mults
    (session/regime/triple_block) default to 1.0 (neutral) for backward compat.
    """

    base_risk_pct: float
    continuous_scalar: float
    tier_amplifier: float
    cell_routing_mult: float
    listing_watchdog_mult: float
    proposed_risk_pct: float
    # L5 learner wire (2026-05-26 p0_l5_l3_sizing_wire)
    session_mult: float = 1.0
    regime_mult: float = 1.0
    triple_block_mult: float = 1.0


@dataclass(frozen=True, slots=True)
class SizingFinal:
    """Post-cap T4 output (composer of headroom min() result)."""

    proposed: SizingProposal
    final_risk_pct: float
    final_notional_usd: float
    leverage: float
    binding_cap: str  # which cap clipped (audit string)


@dataclass(frozen=True, slots=True)
class StrategyRiskState:
    """Per-strategy rolling risk state (informs Kelly + amplifier)."""

    venue: str
    strategy: str
    closed_trades: int
    kelly_p: float
    kelly_q: float
    kelly_fraction: float
    win_streak: int
    hit_rate_10: float
    updated_ts: int


@dataclass(frozen=True, slots=True)
class PositionRiskState:
    """Open-position contribution to caps."""

    venue: str
    symbol: str
    instrument_id: str
    underlying_group_id: str
    cluster_id: str | None
    strategy: str
    track: Track
    signal_strength: float
    open_risk_pct: float
    notional_usd: float
    opened_ts: int


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Snapshot of live risk usage (composer input)."""

    equity_usd: float
    venue_daily_used_pct: float  # used so far today, this venue
    total_daily_used_pct: float  # used so far today, all venues
    track_used_pct: dict[Track, float] = field(default_factory=dict)
    open_positions: list[PositionRiskState] = field(default_factory=list)
    fill_rate_active_cut: bool = False  # post-hysteresis state
