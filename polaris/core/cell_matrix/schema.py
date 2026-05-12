"""Layer 4 — Cell Matrix dataclasses + constants.

Spec source: vault/30_components/layer-4-cell-matrix.md (Dataclass + Constants).
ADR-006 patches: EWMA half-life 7d, warmup shrinkage 5-19, dynamic quartile gate ≥20.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Constants (P0)
# ---------------------------------------------------------------------------

CELL_BASELINE_N: Final[float] = 70.0
"""T11 calibration constant: ``score = avg_pnl_r × √n_eff / 70``."""

CELL_DECAY_HALF_LIFE_SEC: Final[int] = 7 * 86400
"""EWMA decay half-life in seconds (7 days)."""

CELL_MIN_LIVE_N: Final[float] = 5.0
"""Below this n_eff the cell is treated as cold and routes ×1.0 neutral."""

CELL_MIN_POOL_SIZE: Final[int] = 20
"""Activation gate: dynamic quartile routing fires only when eligible cell pool ≥ 20."""

CELL_SHRINKAGE_N: Final[float] = 20.0
"""Warmup ceiling: 5 ≤ n_eff < 20 = blended toward parent3 (then parent2)."""

ROUTING_TOP_MULT: Final[float] = 1.5
"""Top quartile aggressive amplifier (ADR-005 patch — 1.3 → 1.5)."""

ROUTING_BOTTOM_MULT: Final[float] = 0.5
"""Bottom quartile suppression."""

ROUTING_MID_MULT: Final[float] = 1.0
"""Mid 50% / sparse / cold neutral routing."""

EIGHT_DIM_PROMOTION_THRESHOLD: Final[int] = 1000
"""Portfolio closed_trades threshold to graduate to 8-dim cell matrix."""

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CellKeyP0:
    """4-dim primary cell key (exchange × strategy × ticker × regime)."""

    exchange: str
    strategy: str
    ticker: str
    regime: str


@dataclass(frozen=True, slots=True)
class CellContext:
    """Shadow context for the 4-dim cell (group/session/direction/liquidity_tier).

    P0: collected only (no routing impact). P1 promotion to 8-dim primary uses
    these fields once portfolio closed_trades ≥ 1000.
    """

    group: str
    session: str
    direction: str
    liquidity_tier: str


@dataclass(frozen=True, slots=True)
class CellStat:
    """Materialised cell statistic row (in-memory representation of cell_matrix_p0)."""

    key: CellKeyP0
    n_eff: float
    wins_eff: float
    pnl_r_sum_eff: float
    avg_pnl_r: float
    score: float
    last_closed_ts: int


@dataclass(frozen=True, slots=True)
class TradeClose:
    """Single trade close event consumed by cell-matrix updates."""

    key: CellKeyP0
    context: CellContext
    pnl_r: float
    won: bool
    closed_ts: int
