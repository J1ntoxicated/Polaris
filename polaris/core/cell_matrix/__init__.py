"""Layer 4 — Cell Matrix package (4-dim P0 + parent3/parent2 + shadow context).

Spec source: vault/30_components/layer-4-cell-matrix.md.
"""

from polaris.core.cell_matrix.routing import (
    Quartile,
    active_eligible_cells,
    classify_quartile,
    compute_routing_mult,
    fetch_cell_stat,
    fetch_parent2_score,
    fetch_parent3_score,
    load_eligible_scores,
    load_eligible_scores_decayed,
    resolve_routing_for_cell,
    update_on_trade_close,
)
from polaris.core.cell_matrix.schema import (
    CELL_BASELINE_N,
    CELL_DECAY_HALF_LIFE_SEC,
    CELL_MIN_LIVE_N,
    CELL_MIN_POOL_SIZE,
    CELL_SHRINKAGE_N,
    EIGHT_DIM_PROMOTION_THRESHOLD,
    ROUTING_BOTTOM_MULT,
    ROUTING_MID_MULT,
    ROUTING_TOP_MULT,
    CellContext,
    CellKeyP0,
    CellStat,
    TradeClose,
)
from polaris.core.cell_matrix.score import (
    apply_exponential_decay,
    compute_avg_pnl_r,
    compute_cell_score,
    decay_factor,
    resolve_effective_score,
)

__all__ = [
    "CELL_BASELINE_N",
    "CELL_DECAY_HALF_LIFE_SEC",
    "CELL_MIN_LIVE_N",
    "CELL_MIN_POOL_SIZE",
    "CELL_SHRINKAGE_N",
    "EIGHT_DIM_PROMOTION_THRESHOLD",
    "ROUTING_BOTTOM_MULT",
    "ROUTING_MID_MULT",
    "ROUTING_TOP_MULT",
    "CellContext",
    "CellKeyP0",
    "CellStat",
    "Quartile",
    "TradeClose",
    "active_eligible_cells",
    "apply_exponential_decay",
    "classify_quartile",
    "compute_avg_pnl_r",
    "compute_cell_score",
    "compute_routing_mult",
    "decay_factor",
    "fetch_cell_stat",
    "fetch_parent2_score",
    "fetch_parent3_score",
    "load_eligible_scores",
    "load_eligible_scores_decayed",
    "resolve_effective_score",
    "resolve_routing_for_cell",
    "update_on_trade_close",
]
