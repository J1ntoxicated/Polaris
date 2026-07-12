"""fee-split v1 FLIP (item 2) — persisted per-track / venue-pool threshold
remap table.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Spec:
vault/50_research/debates/fee_split_flip_r2_2026-07-12.md item 2 +
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item 5
(percentile-preserving migration, isotonic order). R2 red-team T1/T3 fixes:
per-track (n>=30) individual remap; below that, fall back to one of the 3
VENUE POOLS (okx/capital/alpaca) — NEVER a single global pool (T3: OKX real
fees vs Capital spread-embedded-in-fill-price are not the same friction
regime, so pooling across venues would smuggle venue bias into a track's
thresholds).

``evaluate_transition`` (transition.py) stays a pure function with zero DB
knowledge — this module is the (impure, fail-open) bridge that builds a
``TransitionThresholds`` for a live (venue, strategy_id) track from whatever
is persisted in ``score_f_remap_table``, falling back through
track -> venue-pool -> hardcoded-legacy-defaults on any gap (missing row,
missing table, DB error). ``resolve_thresholds`` is READ-ONLY — it never
computes/writes; only the refresh sweeper
(``tools/ops/scoref_remap_refresh.py``) does, on its own cadence (item 4),
so the thresholds a track sees stay STABLE within a trading day (Schmitt
hysteresis needs a fixed bar, not one that redraws every close).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field

from polaris.core.classes.score_f import compute_score_f
from polaris.core.classes.threshold_migration import (
    ks_statistic,
    migrate_thresholds,
    population_stability_index,
)
from polaris.core.classes.transition import TransitionThresholds

logger = logging.getLogger(__name__)

__all__ = [
    "KS_STALE_THRESHOLD",
    "PSI_STALE_THRESHOLD",
    "TRACK_MIN_N",
    "VENUE_POOLS",
    "RemapEntry",
    "compute_track_remap",
    "compute_venue_pool_remap",
    "pool_scope_key",
    "resolve_thresholds",
    "track_scope_key",
    "upsert_remap_entry",
]

# Per-track individual remap floor (R2 item 2) — below this, venue-pool
# fallback. 30 mirrors this project's usual "not too thin a sample" floor
# (e.g. survivor_gate.K_MIN_INDEPENDENT_CELLS's sibling precedent elsewhere).
TRACK_MIN_N = 30

# The 3 venue pools (T3 fix) — NEVER a global pool across venues.
VENUE_POOLS: tuple[str, ...] = ("okx", "capital", "alpaca")

# Staleness bar (item 4/T1) — auto-FLAG only, never blocks a resolve.
PSI_STALE_THRESHOLD = 0.20
KS_STALE_THRESHOLD = 0.15

# The 4 JUDGED-axis legacy threshold constants IN THEIR NATURAL INCREASING
# ORDER (Schmitt hysteresis + PROVE stagnation) — the source sequence
# migrate_thresholds percentile-maps + isotonic-repairs onto the new
# gross_bps population. The reentry-ladder's 4 thresholds (ladder_step0/1/2,
# ladder_decay) are DELIBERATELY EXCLUDED: they judge ``shadow_scores``
# (transition.py's ``_check_reentry_ladder``), which is populated by
# shadow_fill.py's ``shadow_score = mu_r - pessimistic_cost_r`` — an
# R-MULTIPLE-scale value, UNCHANGED by the fee-split flip. Remapping them
# onto the gross_bps population (as an earlier build did) silently breaks
# BENCH->PROVE reentry: ladder thresholds land ~10x too large for an
# R-multiple-scale mean to ever clear, permanently blocking step-up while
# decay fires on every check (aggressive_always_profit violation). See
# vault fee_split_flip_r2_2026-07-12 review, CRITICAL finding on this file.
_LEGACY_ORDER: tuple[float, ...] = (-4.0, -3.0, -1.0, 2.0)


@dataclass(slots=True, frozen=True)
class RemapEntry:
    """One scope's (track or venue-pool) persisted remap — mirrors one
    ``score_f_remap_table`` row."""

    scope_key: str
    scope_type: str  # "track" | "pool"
    n_samples: int
    computed_ts: int
    thresholds: TransitionThresholds
    ref_population: list[float] = field(default_factory=list)
    stale: bool = False
    psi: float | None = None
    ks: float | None = None


def track_scope_key(*, venue: str, strategy_id: str) -> str:
    return f"{venue}:{strategy_id}"


def pool_scope_key(*, venue: str) -> str:
    return f"{venue}:__pool__"


def _build_thresholds(old_population: list[float], new_population: list[float]) -> TransitionThresholds:
    migrated = migrate_thresholds(
        list(_LEGACY_ORDER), old_population=old_population, new_population=new_population,
    )
    return TransitionThresholds(
        schmitt_bench_partial=migrated[0],
        schmitt_bench_full=migrated[1],
        schmitt_prove=migrated[2],
        prove_stagnation=migrated[3],
        # ladder_step0/1/2/decay: NOT remapped — left at TransitionThresholds'
        # own legacy-literal defaults (R-multiple scale, matches shadow_scores;
        # see _LEGACY_ORDER note above).
    )


def _paired_populations(
    conn: sqlite3.Connection, *, venue: str, strategy_ids: list[str]
) -> tuple[list[float], list[float]]:
    """(old_population, new_population) over every closed lifecycle across
    ``strategy_ids`` on ``venue``, paired 1:1 per lifecycle. Only lifecycles
    with real notional data (``notional_usd > 0``) contribute a new-axis
    sample — no fabricated gross_bps (same "no fabrication" contract as
    ``gross_edge_proxy.py``'s v0 percentile-proxy)."""
    old_pop: list[float] = []
    new_pop: list[float] = []
    for strategy_id in strategy_ids:
        for r in compute_score_f(conn, venue=venue, strategy_id=strategy_id):
            if r.notional_usd <= 0.0:
                continue
            old_pop.append(r.legacy_score_contrib)
            new_pop.append(r.net_usd / r.notional_usd * 10_000.0)
    return old_pop, new_pop


def compute_track_remap(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, now_ts: int
) -> RemapEntry | None:
    """Fresh (not read from the table) remap for one (venue, strategy_id)
    track. ``None`` when fewer than :data:`TRACK_MIN_N` real-notional
    lifecycles exist yet — the caller falls back to the venue pool."""
    old_pop, new_pop = _paired_populations(conn, venue=venue, strategy_ids=[strategy_id])
    if len(new_pop) < TRACK_MIN_N:
        return None
    return RemapEntry(
        scope_key=track_scope_key(venue=venue, strategy_id=strategy_id),
        scope_type="track",
        n_samples=len(new_pop),
        computed_ts=now_ts,
        thresholds=_build_thresholds(old_pop, new_pop),
        ref_population=new_pop,
    )


def compute_venue_pool_remap(conn: sqlite3.Connection, *, venue: str, now_ts: int) -> RemapEntry | None:
    """Fresh remap pooling EVERY strategy_id on ``venue`` — the fallback for
    a track that has not yet accumulated :data:`TRACK_MIN_N` real-notional
    closes of its own. ``None`` on zero pooled samples (brand-new venue)."""
    rows = conn.execute(
        "SELECT DISTINCT strategy_id FROM positions WHERE venue = ? AND status = 'closed'",
        (venue,),
    ).fetchall()
    strategy_ids = [str(r[0]) for r in rows]
    old_pop, new_pop = _paired_populations(conn, venue=venue, strategy_ids=strategy_ids)
    if not new_pop:
        return None
    return RemapEntry(
        scope_key=pool_scope_key(venue=venue),
        scope_type="pool",
        n_samples=len(new_pop),
        computed_ts=now_ts,
        thresholds=_build_thresholds(old_pop, new_pop),
        ref_population=new_pop,
    )


def _load_prior_ref_population(conn: sqlite3.Connection, *, scope_key: str) -> list[float] | None:
    row = conn.execute(
        "SELECT ref_population_json FROM score_f_remap_table WHERE scope_key = ?",
        (scope_key,),
    ).fetchone()
    if row is None:
        return None
    try:
        decoded = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return [float(v) for v in decoded] if isinstance(decoded, list) else None


def _load_prior_schmitt_prove(conn: sqlite3.Connection, *, scope_key: str) -> float | None:
    row = conn.execute(
        "SELECT schmitt_prove FROM score_f_remap_table WHERE scope_key = ?", (scope_key,),
    ).fetchone()
    return float(row[0]) if row is not None else None


def upsert_remap_entry(conn: sqlite3.Connection, entry: RemapEntry) -> None:
    """Persist ``entry``, stamping ``stale``/``psi``/``ks`` by comparing its
    NEW ``ref_population`` against whatever this scope's PRIOR row held (item
    4 drift-detection — auto-FLAG only, this function never rejects/blocks a
    write on staleness, it just records the measurement). Also flags on
    ``schmitt_prove`` (the near-zero threshold — legacy -1.0, closest of the
    7 to the judgment boundary) drifting more than 3 gbps refresh-to-refresh
    (T5 "remap drift" tripwire input, ``tripwires.py``)."""
    prior = _load_prior_ref_population(conn, scope_key=entry.scope_key)
    psi = population_stability_index(prior, entry.ref_population) if prior else None
    ks = ks_statistic(prior, entry.ref_population) if prior else None
    prior_prove = _load_prior_schmitt_prove(conn, scope_key=entry.scope_key)
    threshold_drift = (
        abs(entry.thresholds.schmitt_prove - prior_prove) if prior_prove is not None else None
    )
    stale = bool(
        (psi is not None and psi > PSI_STALE_THRESHOLD)
        or (ks is not None and ks > KS_STALE_THRESHOLD)
        or (threshold_drift is not None and threshold_drift > 3.0)
    )
    t = entry.thresholds
    conn.execute(
        "INSERT INTO score_f_remap_table (scope_key, scope_type, n_samples, "
        "computed_ts, schmitt_bench_partial, schmitt_bench_full, "
        "schmitt_prove, prove_stagnation, ladder_step0, ladder_step1, "
        "ladder_step2, ladder_decay, ref_population_json, stale, psi, ks) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(scope_key) DO UPDATE SET "
        "scope_type=excluded.scope_type, n_samples=excluded.n_samples, "
        "computed_ts=excluded.computed_ts, "
        "schmitt_bench_partial=excluded.schmitt_bench_partial, "
        "schmitt_bench_full=excluded.schmitt_bench_full, "
        "schmitt_prove=excluded.schmitt_prove, "
        "prove_stagnation=excluded.prove_stagnation, "
        "ladder_step0=excluded.ladder_step0, ladder_step1=excluded.ladder_step1, "
        "ladder_step2=excluded.ladder_step2, ladder_decay=excluded.ladder_decay, "
        "ref_population_json=excluded.ref_population_json, "
        "stale=excluded.stale, psi=excluded.psi, ks=excluded.ks",
        (
            entry.scope_key, entry.scope_type, entry.n_samples, entry.computed_ts,
            t.schmitt_bench_partial, t.schmitt_bench_full, t.schmitt_prove,
            t.prove_stagnation, t.ladder_step0, t.ladder_step1, t.ladder_step2,
            t.ladder_decay, json.dumps(entry.ref_population), int(stale), psi, ks,
        ),
    )
    if stale:
        logger.warning(
            "[remap-table] %s stale=True psi=%s ks=%s (>%.2f/%.2f)",
            entry.scope_key, psi, ks, PSI_STALE_THRESHOLD, KS_STALE_THRESHOLD,
        )


def _row_to_thresholds(row: sqlite3.Row | tuple[float, ...]) -> TransitionThresholds:
    return TransitionThresholds(
        schmitt_bench_partial=float(row[0]), schmitt_bench_full=float(row[1]),
        schmitt_prove=float(row[2]), prove_stagnation=float(row[3]),
        ladder_step0=float(row[4]), ladder_step1=float(row[5]),
        ladder_step2=float(row[6]), ladder_decay=float(row[7]),
    )


def resolve_thresholds(conn: sqlite3.Connection, *, venue: str, strategy_id: str) -> TransitionThresholds:
    """Live, READ-ONLY threshold lookup for the close-hook wire
    (``_production_close_classes.py``): track row -> venue-pool row ->
    hardcoded legacy defaults (:class:`TransitionThresholds`'s own field
    defaults). Fail-open on ANY error (missing table on an old DB, a
    corrupt row, etc.) — a threshold-remap failure must never block a
    transition evaluation (flow_not_block)."""
    cols = (
        "schmitt_bench_partial, schmitt_bench_full, schmitt_prove, "
        "prove_stagnation, ladder_step0, ladder_step1, ladder_step2, ladder_decay"
    )
    try:
        row = conn.execute(
            f"SELECT {cols} FROM score_f_remap_table WHERE scope_key = ?",
            (track_scope_key(venue=venue, strategy_id=strategy_id),),
        ).fetchone()
        if row is not None:
            return _row_to_thresholds(row)
        row = conn.execute(
            f"SELECT {cols} FROM score_f_remap_table WHERE scope_key = ?",
            (pool_scope_key(venue=venue),),
        ).fetchone()
        if row is not None:
            return _row_to_thresholds(row)
    except sqlite3.Error as exc:
        logger.error("[remap-table] resolve_thresholds failed for %s/%s: %r", venue, strategy_id, exc)
    return TransitionThresholds()
