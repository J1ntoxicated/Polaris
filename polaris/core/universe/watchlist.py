"""Layer 0 — focus watchlist (dynamic 12-48) + eviction + listing watchdog.

Pure functions. Persistence helpers at the bottom (thin SQLite glue).

Spec source: vault/30_components/layer-0-universe-discovery.md (Q3 + Q4 + Q6).
"""

from __future__ import annotations

import logging
import math
import sqlite3
import statistics
import time
from collections.abc import Sequence
from typing import Final

from polaris.core.universe.schema import (
    FOCUS_TARGET_BASE,
    FOCUS_TARGET_MAX,
    FOCUS_TARGET_MIN,
    NEW_LISTING_WATCH_HOURS,
    RANK_WEIGHT_ATR_Z,
    RANK_WEIGHT_CELL_Z,
    RANK_WEIGHT_DEPTH_Z,
    RANK_WEIGHT_SIGNAL_DENSITY_Z,
    RANK_WEIGHT_VOL_Z,
    FocusBucket,
    FocusSelection,
    UniverseInstrument,
)

logger = logging.getLogger(__name__)

__all__ = [
    "compute_dynamic_focus",
    "compute_dynamic_target_size",
    "persist_focus",
    "score_focus_candidate",
    "score_focus_candidates",
    "select_focus_watchlist",
    "should_evict_from_focus",
]

# Top quartile cutoff for `core` bucket.
CORE_QUANTILE: Final[float] = 0.75
LISTING_WATCH_SECONDS: Final[int] = NEW_LISTING_WATCH_HOURS * 3600


# ---------------------------------------------------------------------------
# Pre-rank score (Q3, deterministic)
# ---------------------------------------------------------------------------


def _z_score(values: Sequence[float]) -> list[float]:
    """Population z-score; returns zeros when stdev is 0 or len<2."""
    if len(values) < 2:
        return [0.0] * len(values)
    mu = statistics.fmean(values)
    sigma = statistics.pstdev(values)
    if sigma <= 0.0 or not math.isfinite(sigma):
        return [0.0] * len(values)
    return [(v - mu) / sigma for v in values]


def score_focus_candidates(
    instruments: list[UniverseInstrument],
    *,
    cell_scores: dict[str, float] | None = None,
) -> list[float]:
    """Compute deterministic pre-rank score per instrument (parallel order).

    Score = w_vol·z(vol) + w_sig·z(sig_density) + w_atr·z(atr) + w_depth·z(depth)
            + w_cell·z(cell). Missing cell scores → 0 z.
    """
    if not instruments:
        return []

    vol_z = _z_score([ins.vol_24h_usd for ins in instruments])
    sig_z = _z_score([ins.signal_density_7d for ins in instruments])
    atr_z = _z_score([ins.atr_24h_pct for ins in instruments])
    depth_z = _z_score([ins.depth_10bps_usd for ins in instruments])

    cell_scores = cell_scores or {}
    cell_raw = [float(cell_scores.get(ins.instrument_id, 0.0)) for ins in instruments]
    cell_z = _z_score(cell_raw)

    return [
        RANK_WEIGHT_VOL_Z * vol_z[i]
        + RANK_WEIGHT_SIGNAL_DENSITY_Z * sig_z[i]
        + RANK_WEIGHT_ATR_Z * atr_z[i]
        + RANK_WEIGHT_DEPTH_Z * depth_z[i]
        + RANK_WEIGHT_CELL_Z * cell_z[i]
        for i in range(len(instruments))
    ]


# ---------------------------------------------------------------------------
# Dynamic target size (Q3)
# ---------------------------------------------------------------------------


def compute_dynamic_target_size(
    *,
    active_count: int,
    recent_signal_density_top_q: float = 0.0,
    top_score_concentration: float = 0.5,
    baseline: int = FOCUS_TARGET_BASE,
    min_target: int = FOCUS_TARGET_MIN,
    max_target: int = FOCUS_TARGET_MAX,
) -> int:
    """Resolve focus target_size in [12, 48] (Q3).

    - High recent signal density (top quartile) → +6 / +12.
    - Low top-score concentration (broad mass) → -6 / -12.
    """
    target = baseline
    if recent_signal_density_top_q >= 0.7:
        target += 12
    elif recent_signal_density_top_q >= 0.5:
        target += 6
    if top_score_concentration <= 0.2:
        target -= 12
    elif top_score_concentration <= 0.35:
        target -= 6
    target = max(min_target, min(max_target, target))
    return target


# ---------------------------------------------------------------------------
# Bucket assignment + listing watchdog (Q3 + Q6)
# ---------------------------------------------------------------------------


def _bucket_for(
    inst: UniverseInstrument,
    *,
    rank: int,
    target_size: int,
    now_ts: int,
    cell_score: float,
    cell_q75: float,
    sig_q75: float,
) -> FocusBucket:
    """Bucket = listing_watch (< 24h) | core (top-quartile cell AND active signal) | satellite.

    Spec L0 Q3: ``core`` = top quartile cell + active signal density.
    Implementation:
    - cell_q75 = 75th percentile of cell_score across the *active universe*.
      ``cell_score >= cell_q75`` ⇒ top quartile cell.
    - sig_q75 = 75th percentile of signal_density_7d. ``inst.signal_density_7d
      > 0 AND >= sig_q75`` ⇒ active signal.
    - Otherwise satellite (regardless of rank).
    Rank-only fallback only kicks in when neither percentile is meaningful
    (degenerate population) so the spec contract holds in cold-start too.
    """
    if inst.listing_ts is not None and (now_ts - int(inst.listing_ts)) < LISTING_WATCH_SECONDS:
        return "listing_watch"

    has_population = cell_q75 > 0.0 or sig_q75 > 0.0
    if has_population:
        cell_top = cell_score >= cell_q75 if cell_q75 > 0.0 else False
        sig_top = inst.signal_density_7d > 0.0 and inst.signal_density_7d >= sig_q75
        if cell_top and sig_top:
            return "core"
        return "satellite"

    # Cold start: cell_matrix and signal_density both zero across the universe.
    # Fall back to rank-based core cut so the focus list is still useful.
    core_cut = max(1, int(target_size * (1.0 - CORE_QUANTILE)))
    if rank <= core_cut:
        return "core"
    return "satellite"


def compute_dynamic_focus(
    active_universe: list[UniverseInstrument],
    *,
    cell_scores: dict[str, float] | None = None,
    cycle_ts: int | None = None,
    recent_signal_density_top_q: float = 0.0,
    top_score_concentration: float = 0.5,
    target_size: int | None = None,
) -> list[FocusSelection]:
    """Pure-function focus selection.

    Steps:
    1. Score every active instrument deterministically.
    2. Resolve dynamic target size (12-48).
    3. Sort desc by score, take top-N.
    4. Bucket = listing_watch (<24h) | core (top-quartile cell AND active signal) | satellite.
    """
    if not active_universe:
        return []

    ts = cycle_ts if cycle_ts is not None else int(time.time())
    scores = score_focus_candidates(active_universe, cell_scores=cell_scores)
    order = sorted(range(len(active_universe)), key=lambda i: scores[i], reverse=True)

    if target_size is None:
        target_size = compute_dynamic_target_size(
            active_count=len(active_universe),
            recent_signal_density_top_q=recent_signal_density_top_q,
            top_score_concentration=top_score_concentration,
        )

    # Top-quartile thresholds across the *active universe* (not the focus subset).
    cell_score_lookup = cell_scores or {}
    cell_population = [
        float(cell_score_lookup.get(ins.instrument_id, 0.0)) for ins in active_universe
    ]
    sig_population = [ins.signal_density_7d for ins in active_universe]
    cell_q75 = _quantile(cell_population, 0.75)
    sig_q75 = _quantile(sig_population, 0.75)

    out: list[FocusSelection] = []
    for rank_idx, src_idx in enumerate(order[:target_size], start=1):
        inst = active_universe[src_idx]
        bucket = _bucket_for(
            inst,
            rank=rank_idx,
            target_size=target_size,
            now_ts=ts,
            cell_score=cell_population[src_idx],
            cell_q75=cell_q75,
            sig_q75=sig_q75,
        )
        out.append(
            FocusSelection(
                cycle_ts=ts,
                venue=inst.venue,
                symbol=inst.symbol,
                focus_score=float(scores[src_idx]),
                rank=rank_idx,
                bucket=bucket,
            )
        )
    bucket_counts: dict[str, int] = {}
    for f in out:
        bucket_counts[f.bucket] = bucket_counts.get(f.bucket, 0) + 1
    logger.info(
        "[universe] dynamic_focus: active=%d → focus=%d target=%d buckets=%s",
        len(active_universe),
        len(out),
        target_size,
        bucket_counts,
    )
    return out


def _quantile(values: list[float], pct: float) -> float:
    """Sorted-population percentile; returns 0.0 on empty."""
    if not values:
        return 0.0
    arr = sorted(values)
    if pct <= 0.0:
        return arr[0]
    if pct >= 1.0:
        return arr[-1]
    pos = pct * (len(arr) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(arr) - 1)
    frac = pos - lo
    return arr[lo] * (1.0 - frac) + arr[hi] * frac


# ---------------------------------------------------------------------------
# Spec API aliases (vault/30_components/layer-0-universe-discovery.md)
# ---------------------------------------------------------------------------


def score_focus_candidate(
    inst: UniverseInstrument,
    *,
    cell_score: float,
    volume_z: float,
    atr_z: float,
    signal_density_z: float,
    depth_z: float,
    recent_activity_z: float = 0.0,
) -> float:
    """Score a single candidate using pre-computed z-scores (Q3 spec signature).

    Mirrors `score_focus_candidates` but operates on one row with externally
    supplied z-scores so callers can inject their own population.
    `recent_activity_z` is reserved for the P1 learner-tuned axis; weight 0 at P0.
    """
    _ = inst, recent_activity_z  # parameters reserved for future use / introspection
    return (
        RANK_WEIGHT_VOL_Z * volume_z
        + RANK_WEIGHT_SIGNAL_DENSITY_Z * signal_density_z
        + RANK_WEIGHT_ATR_Z * atr_z
        + RANK_WEIGHT_DEPTH_Z * depth_z
        + RANK_WEIGHT_CELL_Z * cell_score
    )


def select_focus_watchlist(
    active_universe: list[UniverseInstrument],
    *,
    target_size: int,
    cycle_ts: int,
    cell_scores: dict[str, float] | None = None,
) -> list[FocusSelection]:
    """Spec-named alias for `compute_dynamic_focus` with explicit target size."""
    return compute_dynamic_focus(
        active_universe,
        cell_scores=cell_scores,
        cycle_ts=cycle_ts,
        target_size=target_size,
    )


# ---------------------------------------------------------------------------
# Eviction (Q4)
# ---------------------------------------------------------------------------


def should_evict_from_focus(
    *,
    cell_quartile: float,
    trades_28d: int,
    signal_hits_7d: int,
) -> bool:
    """Bottom-quartile cell + 28d zero trades + zero recent signal → evict this cycle only.

    - ``cell_quartile`` in [0, 1] (0 = bottom). Returns True only if all conditions hold.
    """
    return cell_quartile <= 0.25 and trades_28d == 0 and signal_hits_7d == 0


# ---------------------------------------------------------------------------
# Persistence (thin SQLite glue)
# ---------------------------------------------------------------------------


def persist_focus(conn: sqlite3.Connection, focus: list[FocusSelection]) -> None:
    """Upsert focus rows for the given cycle into `watchlist_focus`."""
    rows = [(f.cycle_ts, f.venue, f.symbol, f.focus_score, f.rank, f.bucket, None) for f in focus]
    conn.executemany(
        """
        INSERT INTO watchlist_focus
            (cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket, evict_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cycle_ts, venue, symbol) DO UPDATE SET
            focus_score=excluded.focus_score,
            focus_rank=excluded.focus_rank,
            target_bucket=excluded.target_bucket,
            evict_reason=excluded.evict_reason
        """,
        rows,
    )
