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
    NEW_LISTING_WATCH_HOURS,
    RANK_WEIGHT_ATR_Z,
    RANK_WEIGHT_CELL_Z,
    RANK_WEIGHT_DEPTH_Z,
    RANK_WEIGHT_OPPORTUNITY_Z,
    RANK_WEIGHT_SIGNAL_DENSITY_Z,
    RANK_WEIGHT_VOL_Z,
    FocusBucket,
    FocusSelection,
    Tier,
    UniverseInstrument,
    crowding_lambda,
    tier_band_boundaries,
)

logger = logging.getLogger(__name__)

__all__ = [
    "apply_crowding_penalty",
    "assign_tiers",
    "cadence_fires",
    "compute_dynamic_focus",
    "persist_focus",
    "score_focus_candidates",
    "should_evict_from_focus",
    "tier_for_rank",
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


def _grouped_z_score(values: Sequence[float], groups: Sequence[str]) -> list[float]:
    """Per-group population z-score, returned in the original parallel order.

    Each ``group`` (here ``venue/asset_class``) is z-normalized WITHIN ITSELF, so
    a $1B equity is ranked against equities and a $251B crypto against crypto —
    never one mixed population (which let BTC's absolute vol flatten every equity
    vol-z negative so penny names out-ranked megacaps). A singleton or zero-stdev
    group collapses to 0 (same guard as :func:`_z_score`). A single-group
    population is byte-identical to the global :func:`_z_score` (no-op grouping).
    This is a RANKING-ORDER correction (flow_not_block): it re-orders survivors,
    it never blocks, size-cuts, or vetoes anything.
    """
    by_group: dict[str, list[int]] = {}
    for i, g in enumerate(groups):
        by_group.setdefault(g, []).append(i)
    out = [0.0] * len(values)
    for idxs in by_group.values():
        zs = _z_score([values[i] for i in idxs])
        for pos, i in enumerate(idxs):
            out[i] = zs[pos]
    return out


def score_focus_candidates(
    instruments: list[UniverseInstrument],
    *,
    cell_scores: dict[str, float] | None = None,
    opportunity_scores: dict[str, float] | None = None,
) -> list[float]:
    """Compute deterministic pre-rank score per instrument (parallel order).

    Score = w_vol·z(vol) + w_sig·z(sig_density) + w_atr·z(atr) + w_depth·z(depth)
            + w_cell·z(cell) + w_opp·z(opportunity). Missing cell / opportunity
    scores → 0 z (the opportunity term is fully no-op when no scores are passed,
    so the legacy focus rank is byte-identical).

    z-scores are computed PER (venue, asset_class) group, not over the whole mixed
    cross-venue pool — so equities are ranked equity-vs-equity and crypto
    crypto-vs-crypto (flow_not_block ranking-order fix; single-group → no-op).

    Increment 1: ``opportunity_scores`` (the EntranceJudge per-candidate [0,1]
    multi-lens judgment) adds a grouped-z term so focus rank reflects the entrance
    JUDGMENT, not just liquidity-z — the audit's "judgment layer unbuilt" fix.
    RANKING only (flow_not_block): a higher judgment lifts rank, never blocks/sizes.
    """
    if not instruments:
        return []

    groups = [f"{ins.venue}/{ins.asset_class}" for ins in instruments]
    vol_z = _grouped_z_score([ins.vol_24h_usd for ins in instruments], groups)
    sig_z = _grouped_z_score([ins.signal_density_7d for ins in instruments], groups)
    atr_z = _grouped_z_score([ins.atr_24h_pct for ins in instruments], groups)
    depth_z = _grouped_z_score([ins.depth_10bps_usd for ins in instruments], groups)

    cell_scores = cell_scores or {}
    cell_raw = [float(cell_scores.get(ins.instrument_id, 0.0)) for ins in instruments]
    cell_z = _grouped_z_score(cell_raw, groups)

    opportunity_scores = opportunity_scores or {}
    opp_raw = [
        float(opportunity_scores.get(ins.instrument_id, 0.0)) for ins in instruments
    ]
    opp_z = _grouped_z_score(opp_raw, groups)

    return [
        RANK_WEIGHT_VOL_Z * vol_z[i]
        + RANK_WEIGHT_SIGNAL_DENSITY_Z * sig_z[i]
        + RANK_WEIGHT_ATR_Z * atr_z[i]
        + RANK_WEIGHT_DEPTH_Z * depth_z[i]
        + RANK_WEIGHT_CELL_Z * cell_z[i]
        + RANK_WEIGHT_OPPORTUNITY_Z * opp_z[i]
        for i in range(len(instruments))
    ]


# ---------------------------------------------------------------------------
# Rank-attention gradient — tier band + tier-driven cadence (STAGE 1)
# ---------------------------------------------------------------------------


def tier_for_rank(
    rank: int,
    total: int,
    boundaries: tuple[float, float, float] | None = None,
) -> Tier:
    """Map a 1-based merit rank to a cadence tier {S,A,B,T} via rank-percentile.

    ``rank`` 1 = best merit. ``f = rank/total`` is the cumulative rank-fraction;
    ``f <= s_cut`` → S, ``<= a_cut`` → A, ``<= b_cut`` → B, else T (boundaries from
    :func:`tier_band_boundaries`). A lone row (``total<=1``) is S (it IS the best).
    Pure cadence band (flow_not_block): a worse rank only observes LESS OFTEN, it
    is never dropped.
    """
    if total <= 1:
        return "S"
    s_cut, a_cut, b_cut = boundaries if boundaries is not None else tier_band_boundaries()
    f = rank / total
    if f <= s_cut:
        return "S"
    if f <= a_cut:
        return "A"
    if f <= b_cut:
        return "B"
    return "T"


def assign_tiers(
    total: int,
    boundaries: tuple[float, float, float] | None = None,
) -> list[Tier]:
    """Tier per rank for a merit-ranked list of length ``total`` (rank 1..total)."""
    b = boundaries if boundaries is not None else tier_band_boundaries()
    return [tier_for_rank(r, total, b) for r in range(1, total + 1)]


def cadence_fires(tier: Tier, cycle_index: int, k: int, m: int) -> bool:
    """True iff a row of ``tier`` is polled/eval'd on cycle ``cycle_index``.

    S/A = every cycle (hot/warm); B = every ``k`` cycles; T = every ``m`` cycles
    (``m`` > ``k`` so the tail is rarer). The tail floor is positive (T fires on
    ``cycle_index % m == 0``) so even the tail is heartbeat-polled — never starved
    to zero (flow_not_block).
    """
    if tier in ("S", "A"):
        return True
    period = k if tier == "B" else m
    period = max(1, period)
    return cycle_index % period == 0


def apply_crowding_penalty(
    scores: Sequence[float],
    cluster_keys: Sequence[str],
    lam: float,
) -> list[float]:
    """Soft-crowding diversity seam — nudge crowded clusters DOWN the rank.

    Each row's score is reduced by ``lam × (cluster_rank − 1)`` where ``cluster_rank``
    is the row's position WITHIN its cluster by descending score (the cluster's
    best row keeps its score; its 2nd, 3rd, ... are nudged progressively down). A
    lone-in-cluster row is never penalized. ``lam <= 0`` ⇒ returned scores are the
    input unchanged (DEFAULT OFF — pure merit, byte-identical).

    A RANK nudge ONLY (flow_not_block): it never drops a row, blocks an entry, or
    cuts a size — it only re-orders WHICH crowded names sit where. It is NOT a slot
    floor (the removed quota was); diversity here is a soft penalty, never a hard
    reservation.
    """
    if lam <= 0.0 or not scores:
        return list(scores)
    # Position of each row within its cluster, ranked by descending score.
    by_cluster: dict[str, list[int]] = {}
    for i, key in enumerate(cluster_keys):
        by_cluster.setdefault(key, []).append(i)
    cluster_pos = [0] * len(scores)
    for idxs in by_cluster.values():
        for pos, i in enumerate(sorted(idxs, key=lambda j: scores[j], reverse=True)):
            cluster_pos[i] = pos
    return [scores[i] - lam * cluster_pos[i] for i in range(len(scores))]


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
    target_size: int | None = None,
    opportunity_scores: dict[str, float] | None = None,
    trade_eligible: dict[str, bool] | None = None,
) -> list[FocusSelection]:
    """Pure-function focus selection — rank-attention gradient (STAGE 1).

    Steps:
    1. Score every active instrument by the SINGLE grouped-z merit composite
       (``score_focus_candidates``, incl. ``RANK_WEIGHT_OPPORTUNITY_Z``).
    2. Optional soft-crowding nudge (``apply_crowding_penalty``) — DEFAULT OFF
       (``crowding_lambda()`` == 0 ⇒ pure merit, byte-identical order).
    3. Sort desc by (penalized) score → ALL active rows become focus rows (no
       count cap, no asset-class quota: those are GONE — Jin "갯수 제한 X"). The
       row's rank-percentile assigns its cadence ``tier`` {S,A,B,T}.
    4. Bucket = listing_watch (<24h) | core (top-quartile cell AND active signal)
       | satellite (unchanged — orthogonal to the tier band).

    ``target_size`` is OPTIONAL and defaults to None = ALL rows watched (the [12,48]
    window + ``compute_dynamic_target_size`` are removed). An explicit ``target_size``
    only caps the EXPLICIT-callers (spec alias / focused tests); production passes
    None so all 120 active rows are watched and tier-graded. flow_not_block: tier is
    a cadence band, NEVER a membership cut or sizing input (9-stack untouched);
    ``trade_eligible`` (Increment 1) still decouples the trade subset downstream.
    """
    if not active_universe:
        return []

    opportunity_scores = opportunity_scores or {}
    trade_eligible = trade_eligible or {}
    ts = cycle_ts if cycle_ts is not None else int(time.time())
    scores = score_focus_candidates(
        active_universe,
        cell_scores=cell_scores,
        opportunity_scores=opportunity_scores or None,
    )
    # Soft-crowding diversity seam (default OFF → pure merit). Cluster =
    # underlying_group_id (BTC vs ETH vs ...). A RANK nudge only, never a slot floor.
    rank_scores = apply_crowding_penalty(
        scores,
        [ins.underlying_group_id for ins in active_universe],
        crowding_lambda(),
    )
    order = sorted(
        range(len(active_universe)), key=lambda i: rank_scores[i], reverse=True
    )
    if target_size is not None:
        order = order[:target_size]

    # Tier band per rank-percentile across the watched set (single merit rank).
    boundaries = tier_band_boundaries()
    tiers = assign_tiers(len(order), boundaries)

    # Top-quartile thresholds across the *active universe* (not the focus subset).
    cell_score_lookup = cell_scores or {}
    cell_population = [
        float(cell_score_lookup.get(ins.instrument_id, 0.0)) for ins in active_universe
    ]
    sig_population = [ins.signal_density_7d for ins in active_universe]
    cell_q75 = _quantile(cell_population, 0.75)
    sig_q75 = _quantile(sig_population, 0.75)
    total = len(order)

    out: list[FocusSelection] = []
    for rank_idx, src_idx in enumerate(order, start=1):
        inst = active_universe[src_idx]
        bucket = _bucket_for(
            inst,
            rank=rank_idx,
            target_size=total,
            now_ts=ts,
            cell_score=cell_population[src_idx],
            cell_q75=cell_q75,
            sig_q75=sig_q75,
        )
        iid = inst.instrument_id
        opp = opportunity_scores.get(iid)
        out.append(
            FocusSelection(
                cycle_ts=ts,
                venue=inst.venue,
                symbol=inst.symbol,
                focus_score=float(scores[src_idx]),
                rank=rank_idx,
                bucket=bucket,
                opportunity_score=None if opp is None else float(opp),
                # Flow-preserving default: a row with no judgment stays eligible.
                trade_eligible=bool(trade_eligible.get(iid, True)),
                tier=tiers[rank_idx - 1],
            )
        )
    tier_counts: dict[str, int] = {}
    for f in out:
        tier_counts[f.tier] = tier_counts.get(f.tier, 0) + 1
    logger.info(
        "[universe] dynamic_focus: active=%d → watched=%d tiers=%s",
        len(active_universe),
        len(out),
        tier_counts,
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
    """Upsert focus rows for the given cycle into `watchlist_focus`.

    Increment 1: ``opportunity_score`` + ``trade_eligible`` (the EntranceJudge
    persistence) are upserted alongside. ``trade_eligible`` is stored as INT
    (True → 1) and defaults to 1 (flow-preserving) for a row with no judgment.

    STAGE 1: ``tier`` (the {S,A,B,T} cadence band) is persisted so the consume
    seam (``get_focus_targets``) can drive tier-cadence polling per cycle.
    """
    rows = [
        (
            f.cycle_ts, f.venue, f.symbol, f.focus_score, f.rank, f.bucket, None,
            f.opportunity_score, 1 if f.trade_eligible else 0, f.tier,
        )
        for f in focus
    ]
    conn.executemany(
        """
        INSERT INTO watchlist_focus
            (cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket,
             evict_reason, opportunity_score, trade_eligible, tier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cycle_ts, venue, symbol) DO UPDATE SET
            focus_score=excluded.focus_score,
            focus_rank=excluded.focus_rank,
            target_bucket=excluded.target_bucket,
            evict_reason=excluded.evict_reason,
            opportunity_score=excluded.opportunity_score,
            trade_eligible=excluded.trade_eligible,
            tier=excluded.tier
        """,
        rows,
    )
