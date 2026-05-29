"""Layer 3 — Sizing engine (ADR-005 T4 formula full P0).

Exports:
    Pure compose: continuous_scalar / listing_watchdog_mult / compute_proposed
    Caps:        venue_per_symbol_cap / track_gross_cap / headroom_min
    Top-level:   compute_size (T4 main entry)
    Cell-mult:   apply_cell_routing_mult / resolve_cell_routing_mult / SizingInput
    Kelly:       kelly_fraction / kelly_or_cold_start / KellyDecision
    Amplifier:   resolve_tier_amplifier
    Cluster:     resolve_cluster_id / cluster_remaining_pct
    Fill-rate:   compute_fill_rate / resolve_cut_state / rank_cut_candidates

Spec source:
- vault/30_components/layer-3-sizing-risk.md
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md
"""

from polaris.core.sizing.amplifier import resolve_tier_amplifier
from polaris.core.sizing.cell_mult_application import (
    SizingInput,
    apply_cell_routing_mult,
    resolve_cell_routing_mult,
)
from polaris.core.sizing.cluster_cap import (
    cluster_remaining_pct,
    cluster_used_pct,
    resolve_cluster_id,
)
from polaris.core.sizing.engine import (
    SignalIntent,
    compute_proposed,
    compute_size,
    continuous_scalar,
    ewma_realized_vol,
    headroom_min,
    listing_watchdog_mult,
    per_symbol_remaining_pct,
    track_daily_cap,
    track_gross_cap,
    underlying_remaining_pct,
    venue_per_symbol_cap,
    vol_targeted_scalar,
)
from polaris.core.sizing.fill_rate_cut import (
    CutCandidate,
    compute_fill_rate,
    rank_cut_candidates,
    resolve_cut_state,
)
from polaris.core.sizing.kelly import (
    KellyDecision,
    is_cold_start,
    kelly_fraction,
    kelly_or_cold_start,
    single_trade_cap_pct,
)
from polaris.core.sizing.schema import (
    CONT_SCALAR_MAX,
    CONT_SCALAR_MIN,
    CS3_N_THRESHOLD,
    CS3_SINGLE_AMPLIFIED_PCT,
    CS3_SINGLE_DEFAULT_PCT,
    DEFAULT_BASE_RISK_PCT,
    DEFAULT_TARGET_VOL,
    EWMA_VOL_LAMBDA,
    KELLY_FRACTION_K,
    SINGLE_TRADE_ABSOLUTE_CEILING_PCT,
    SINGLE_TRADE_AMPLIFIED_PCT,
    SINGLE_TRADE_DEFAULT_PCT,
    PortfolioState,
    PositionRiskState,
    SizingFinal,
    SizingProposal,
    StrategyRiskState,
    Track,
)

__all__ = [
    "CONT_SCALAR_MAX",
    "CONT_SCALAR_MIN",
    "CS3_N_THRESHOLD",
    "CS3_SINGLE_AMPLIFIED_PCT",
    "CS3_SINGLE_DEFAULT_PCT",
    "CutCandidate",
    "DEFAULT_BASE_RISK_PCT",
    "DEFAULT_TARGET_VOL",
    "EWMA_VOL_LAMBDA",
    "KELLY_FRACTION_K",
    "KellyDecision",
    "PortfolioState",
    "PositionRiskState",
    "SINGLE_TRADE_ABSOLUTE_CEILING_PCT",
    "SINGLE_TRADE_AMPLIFIED_PCT",
    "SINGLE_TRADE_DEFAULT_PCT",
    "SignalIntent",
    "SizingFinal",
    "SizingInput",
    "SizingProposal",
    "StrategyRiskState",
    "Track",
    "apply_cell_routing_mult",
    "cluster_remaining_pct",
    "cluster_used_pct",
    "compute_fill_rate",
    "compute_proposed",
    "compute_size",
    "continuous_scalar",
    "ewma_realized_vol",
    "headroom_min",
    "is_cold_start",
    "kelly_fraction",
    "kelly_or_cold_start",
    "listing_watchdog_mult",
    "per_symbol_remaining_pct",
    "rank_cut_candidates",
    "resolve_cell_routing_mult",
    "resolve_cluster_id",
    "resolve_cut_state",
    "resolve_tier_amplifier",
    "single_trade_cap_pct",
    "track_daily_cap",
    "track_gross_cap",
    "underlying_remaining_pct",
    "venue_per_symbol_cap",
    "vol_targeted_scalar",
]
