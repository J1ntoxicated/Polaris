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
    RANK_WEIGHT_OPPORTUNITY_Z,
    RANK_WEIGHT_SIGNAL_DENSITY_Z,
    RANK_WEIGHT_VOL_Z,
    FocusBucket,
    FocusSelection,
    UniverseInstrument,
    focus_min_quota_by_class,
    is_capital_fx_major,
    is_liquid_equity,
)

logger = logging.getLogger(__name__)

__all__ = [
    "compute_dynamic_focus",
    "compute_dynamic_target_size",
    "compute_recent_signal_density_top_q",
    "compute_top_score_concentration",
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


def compute_recent_signal_density_top_q(
    active_universe: Sequence[UniverseInstrument],
) -> float:
    """Breadth of recent signal firing across the active universe, in [0, 1].

    = fraction of active instruments whose ``signal_density_7d > 0`` (i.e. carried
    at least one recent raw signal in the 7d window). High value ⇒ many names are
    firing ⇒ a dense, opportunity-rich tape ⇒ ``compute_dynamic_target_size`` widens
    the focus window (+6 at ≥0.5, +12 at ≥0.7). A flat/quiet tape (few names firing)
    leaves it at baseline. Empty / all-zero universe ⇒ 0.0 (baseline, no widen).

    Pure ranking/sizing-WIDTH input (flow_not_block): it can only GROW the focus
    list, never block an entry or cut a size; the 9-stack is untouched.
    """
    if not active_universe:
        return 0.0
    firing = sum(1 for ins in active_universe if ins.signal_density_7d > 0.0)
    return firing / len(active_universe)


def compute_top_score_concentration(scores: Sequence[float]) -> float:
    """Top-quartile focus-score mass share over total positive mass, in [0, 1].

    = sum(top-25% positive scores) / sum(all positive scores). High ⇒ a few names
    dominate the score mass (concentrated leaders) ⇒ no shrink. Low ⇒ score mass is
    spread thin/flat across many comparable names (broad mass) ⇒
    ``compute_dynamic_target_size`` shrinks the window (-6 at ≤0.35, -12 at ≤0.2) so
    focus tightens onto the few real leaders instead of diluting across a flat field.

    Only positive scores count (a focus score can be negative; negative mass is not
    "concentration"). Degenerate cases — empty, no positive mass — return 0.5 (the
    neutral baseline, no shrink) so a cold/flat universe stays at the baseline 30.

    Pure sizing-WIDTH input (flow_not_block): width adaptation only, no entry block.
    """
    positive = [s for s in scores if s > 0.0]
    total = sum(positive)
    if total <= 0.0:
        return 0.5
    k = max(1, round(len(positive) * (1.0 - CORE_QUANTILE)))
    top_k = sorted(positive, reverse=True)[:k]
    return sum(top_k) / total


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


def _apply_asset_class_quota(
    order: list[int],
    active_universe: list[UniverseInstrument],
    *,
    target_size: int,
) -> list[int]:
    """Guarantee each present asset class its minimum focus-slot quota.

    ``order`` is the global score order (desc). The plain top-``target_size`` cut
    lets the dominant class (24/7 crypto, huge vol) monopolize the window and
    starves Capital FX/indices/gold (``vol_24h_usd=0.0`` → lowest score) and
    Alpaca equity. This promotes the highest-scored *unselected* rows of any
    under-quota class into the focus, DISPLACING the lowest-scored rows of
    *over-quota* classes — a FLOW INCREASE (more classes trade), never a
    throttle: the dominant class keeps the bulk of the window, size is unchanged,
    no entry is blocked. A single-asset-class universe satisfies every quota
    trivially → this returns the plain top-``target_size`` cut unchanged (no-op).

    Returns source indices (subset of ``order``) of length
    ``min(target_size, len(order))``.
    """
    base = order[:target_size]
    quota = focus_min_quota_by_class()
    # No-op fast paths: nothing floored, or the whole universe is one asset class
    # (OKX-only crypto / Alpaca-only equity → every quota trivially satisfied).
    if not any(quota.values()):
        return base
    universe_classes = {ins.asset_class for ins in active_universe}
    if len(universe_classes) <= 1:
        return base
    selected = set(base)

    # Per-class shortfall = guaranteed min − already selected (capped by what
    # exists). Promote the highest-scored unselected rows of short classes.
    in_count: dict[str, int] = {}
    for i in base:
        cls = active_universe[i].asset_class
        in_count[cls] = in_count.get(cls, 0) + 1

    # Promotion candidate order = score-desc, but with curated per-venue PRIORITY
    # names pulled ahead of their class peers (flow_not_block, per-venue):
    #  - Capital FX rows all carry vol=0.0, so the quiet MAJORS (EURUSD/USDJPY/...)
    #    score below the high-ATR EXOTIC crosses (USDZAR/NOKSEK) — the forex quota
    #    would fill purely with exotics and the majors never reach focus, starving
    #    fx_breakout_basket / session_breakout.
    #  - Alpaca equities carry REAL vol+ATR, but megacaps/top-ETFs have LOW ATR%
    #    while penny names have HUGE ATR% — the equity quota would fill with penny
    #    names and the LIQUID megacaps never reach focus, starving equity_rsi_bb /
    #    equity_tsmom / equity_gap_go at the RTH open.
    # Both are a stable secondary priority, NOT a global weight change: for any
    # non-priority row the key is unchanged, so OKX, all-exotic forex, and
    # all-penny equity universes are byte-identical (no-op). The quota loop below
    # still gates promotions by each class's own shortfall, so a major/megacap is
    # promoted ONLY into its own class's quota slots, ALONGSIDE (never replacing)
    # the exotic/penny rows.
    order_pos = {idx: pos for pos, idx in enumerate(order)}

    def _priority(i: int) -> int:
        ins = active_universe[i]
        if is_capital_fx_major(ins.venue, ins.symbol) or is_liquid_equity(ins.venue, ins.symbol):
            return 0
        return 1

    promo_order = sorted(order, key=lambda i: (_priority(i), order_pos[i]))
    promotions: list[int] = []
    for src_idx in promo_order:  # priority names (FX majors / liquid equities) first, then score-desc
        if src_idx in selected:
            continue
        cls = active_universe[src_idx].asset_class
        need = quota.get(cls, 0) - in_count.get(cls, 0)
        if need > 0:
            promotions.append(src_idx)
            in_count[cls] = in_count.get(cls, 0) + 1

    # Curated-priority RESERVATION (live bug fix 2026-06-23): the count-based
    # promotion above only fires when a floored class is SHORT of its quota. But
    # when a class is dominated by high-ATR junk (live: 35 penny equities, ATR
    # 30-380%, vs megacaps at ~1-4% — crypto's trillion-scale vol flattens every
    # equity vol-z so ATR alone orders them), the penny names fill the ENTIRE
    # class quota in ``base`` (need == 0), so the curated megacaps/FX-majors are
    # never seated and the daily equity / FX-basket strategies get NO tradeable
    # symbol (Alpaca traded 0 while OKX/Capital traded). Reserve up to the class
    # quota for curated names that exist but are unseated, SWAPPING IN each one
    # for the WORST-scored NON-curated seated row of the SAME class. Pure flow-
    # routing: total size, every other class, and the per-class count are all
    # unchanged; it only re-picks WHICH rows of an already-floored class are
    # seated (curated alongside junk, never below the junk). No-op when a class
    # has no curated names (all-penny / all-exotic / OKX-only → existing tests
    # byte-identical) or when curated names already reached ``base``.
    seated = set(base) | set(promotions)
    swap_in: list[int] = []
    swap_out: set[int] = set()
    for cls, qslots in quota.items():
        if qslots <= 0:
            continue
        curated_unseated = [
            i for i in promo_order
            if active_universe[i].asset_class == cls
            and _priority(i) == 0
            and i not in seated
        ]
        if not curated_unseated:
            continue
        # Worst-scored NON-curated seated rows of this class, worst first.
        junk_seated = [
            i for i in reversed(order)
            if i in seated and i not in swap_out
            and active_universe[i].asset_class == cls
            and _priority(i) == 1
        ]
        # Reserve at most ``qslots`` curated seats for this class (seat the
        # best-scored curated names; displace the worst-scored junk one-for-one).
        n = min(len(curated_unseated), len(junk_seated), qslots)
        for k in range(n):
            swap_in.append(curated_unseated[k])
            swap_out.add(junk_seated[k])

    if not promotions and not swap_in:
        return base
    if swap_in:
        base = [i for i in base if i not in swap_out] + swap_in
        base.sort(key=lambda i: order_pos[i])
    if not promotions:
        return base

    # Displace the lowest-scored rows of OVER-quota classes (worst score first).
    # ``order`` is desc, so iterate reversed for the worst rows. crypto (quota 0)
    # is freely displaceable down to its share; a class is never cut below its
    # own quota.
    drop: set[int] = set()
    for src_idx in reversed(base):
        if len(drop) >= len(promotions):
            break
        cls = active_universe[src_idx].asset_class
        # Only drop rows whose class would still meet its quota afterwards.
        if in_count.get(cls, 0) - 1 >= quota.get(cls, 0):
            drop.add(src_idx)
            in_count[cls] = in_count.get(cls, 0) - 1

    kept = [i for i in base if i not in drop]
    # Add as many promotions as we freed room for (bounded by target_size).
    room = target_size - len(kept)
    merged = kept + promotions[: max(0, room)]
    # Re-sort the final selection by score order (desc) for a stable ranked list.
    merged.sort(key=lambda i: order_pos[i])
    return merged[:target_size]


def compute_dynamic_focus(
    active_universe: list[UniverseInstrument],
    *,
    cell_scores: dict[str, float] | None = None,
    cycle_ts: int | None = None,
    recent_signal_density_top_q: float = 0.0,
    top_score_concentration: float = 0.5,
    target_size: int | None = None,
    opportunity_scores: dict[str, float] | None = None,
    trade_eligible: dict[str, bool] | None = None,
) -> list[FocusSelection]:
    """Pure-function focus selection.

    Steps:
    1. Score every active instrument deterministically.
    2. Resolve dynamic target size (12-48).
    3. Sort desc by score, take top-N, then apply the per-asset-class min quota
       (guarantees under-represented classes — Capital FX/indices/gold, Alpaca
       equity — a floor of slots; flow_not_block, no-op for single-class venues).
    4. Bucket = listing_watch (<24h) | core (top-quartile cell AND active signal) | satellite.

    Increment 1: ``opportunity_scores`` (EntranceJudge [0,1] judgment) feeds the
    rank composite AND is persisted per FocusSelection; ``trade_eligible`` (the
    decoupled trade-set flag, default True per row) is persisted alongside. Both
    are RANKING / FLAG inputs only (flow_not_block, 9-stack untouched). Absent →
    score term is no-op and every row defaults trade_eligible=True.
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
    order = sorted(range(len(active_universe)), key=lambda i: scores[i], reverse=True)

    if target_size is None:
        target_size = compute_dynamic_target_size(
            active_count=len(active_universe),
            recent_signal_density_top_q=recent_signal_density_top_q,
            top_score_concentration=top_score_concentration,
        )

    selected = _apply_asset_class_quota(
        order, active_universe, target_size=target_size
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
    for rank_idx, src_idx in enumerate(selected, start=1):
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
    """Upsert focus rows for the given cycle into `watchlist_focus`.

    Increment 1: ``opportunity_score`` + ``trade_eligible`` (the EntranceJudge
    persistence) are upserted alongside. ``trade_eligible`` is stored as INT
    (True → 1) and defaults to 1 (flow-preserving) for a row with no judgment.
    """
    rows = [
        (
            f.cycle_ts, f.venue, f.symbol, f.focus_score, f.rank, f.bucket, None,
            f.opportunity_score, 1 if f.trade_eligible else 0,
        )
        for f in focus
    ]
    conn.executemany(
        """
        INSERT INTO watchlist_focus
            (cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket,
             evict_reason, opportunity_score, trade_eligible)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cycle_ts, venue, symbol) DO UPDATE SET
            focus_score=excluded.focus_score,
            focus_rank=excluded.focus_rank,
            target_bucket=excluded.target_bucket,
            evict_reason=excluded.evict_reason,
            opportunity_score=excluded.opportunity_score,
            trade_eligible=excluded.trade_eligible
        """,
        rows,
    )
