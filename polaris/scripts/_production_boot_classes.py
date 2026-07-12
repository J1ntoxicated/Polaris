"""pts-classes (group WIRE) — boot sequence: hydrate + bootstrap replay.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved — ``strategy_class`` is a capital-ROUTING record;
seeding/hydrating it never blocks a strategy from signaling/learning/shadow-
pricing (aggressive_always_profit / no_block_filter_architecture).

Wires group A's ``hydrate_strategy_class`` / ``bootstrap_replay_strategy_class``
(storage layer, ``polaris.core.lifecycle.recover``) into the ACTUAL boot path
(``run_production_paper_loop``) — previously exercised only by
``tests/test_schema_ddl_classes.py``, never called at real boot (silent
INERT: registered but never fired). ``strategy_class`` itself needs no
in-memory mirror (``resolve_strategy_class`` already reads it fresh from the
DB on every G5 call, no cache layer) — hydrate here is a startup CORRECTNESS
check + one-shot bootstrap trigger, not a state-rebuild like
``hydrate_open_positions``.

Bootstrap fires ONLY when the ``strategy_class`` table is completely empty
(a fresh DB / this feature's first boot) — an already-seeded table is left
untouched (idempotent, restart must never reset a live-tracked class, mirrors
``bootstrap_replay_strategy_class``'s own per-candidate ON CONFLICT DO NOTHING
guard). Candidates are every ``(venue, strategy_id)`` in ``STRATEGY_REGISTRY``
(``cls.metadata.venue`` — the registry's own strategy->venue mapping).

The injected ``score_f`` callable aggregates group B's ``compute_score_f``
(``Σ score_contrib`` over closed lifecycles) restricted to the trailing
``lookback_days`` window ending ``now_ts`` — group B's function returns the
full unbounded history; this lookback-windowed SUM is WIRE's own assembly
(the same "no separate SSOT specifies HOW to source this" precedent as the
close-hook module). ``n_closed_fn``/``timeframe_bucket_fn`` wire group A's
BENCH minimum-evidence floor (recover_classes.py) the same way — the
lookback-windowed COUNT behind ``_lookback_score_f`` and the strategy-
metadata timeframe mapping already used by the close-hook module
(``_production_close_classes.py``'s own ``_TIMEFRAME_BUCKET``).
"""

from __future__ import annotations

import logging
import sqlite3

from polaris.core.classes.remap_table import (
    VENUE_POOLS,
    compute_track_remap,
    compute_venue_pool_remap,
    upsert_remap_entry,
)
from polaris.core.classes.score_f import compute_score_f
from polaris.core.lifecycle.recover import (
    bootstrap_replay_strategy_class,
    hydrate_strategy_class,
)
from polaris.core.lifecycle.recover_classes import Timeframe
from polaris.scripts._production_atr import strategy_timeframe
from polaris.strategies import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

__all__ = ["boot_hydrate_and_bootstrap_strategy_class"]

# Bootstrap replay window — mirrors the debate spec's "existing 5-week live
# history" bootstrap-replay basis (prove_then_scale_classes_2026-07-03.md
# 빌드 스펙 #7).
_BOOTSTRAP_LOOKBACK_DAYS = 35

# Mirrors _production_close_classes.py's own _TIMEFRAME_BUCKET (duplicated
# rather than imported — that module lives in polaris/scripts too, but
# importing it here would pull in its close-hook dependencies; this is the
# same small literal-dict precedent, not a new pattern).
_TIMEFRAME_BUCKET: dict[str, Timeframe] = {
    "5m": "intraday",
    "15m": "intraday",
    "1H": "intraday",
    "1D": "1d_plus",
}


def _timeframe_bucket_fn(strategy_id: str) -> Timeframe:
    return _TIMEFRAME_BUCKET.get(strategy_timeframe(strategy_id), "intraday")


def _lookback_score_f(
    conn: sqlite3.Connection, venue: str, strategy_id: str, lookback_days: int
) -> float:
    """``Σ legacy_score_contrib`` over closed lifecycles in the trailing
    ``lookback_days`` window (the ``ScoreFFn`` signature group A's
    ``bootstrap_replay_strategy_class`` expects).

    Deliberately the LEGACY axis, not the (default-flipped) judged
    ``score_contrib`` — ``recover_classes.py``'s bootstrap thresholds
    (``_BOOTSTRAP_EARN_THRESHOLD``/``_BOOTSTRAP_BENCH_THRESHOLD``/
    ``_BOOTSTRAP_BENCH_BAND_A_THRESHOLD``) are hardcoded constants calibrated
    on the legacy net/fee-ratio scale and are OUT OF the fee_split_flip_r2_
    2026-07-12 remap's scope (only transition.py's 7 thresholds are
    remapped — see remap_table.py). Summing gross_bps here would judge a
    bootstrap candidate against thresholds tuned for a scale roughly an
    order of magnitude smaller, mis-seeding EARN/PROVE/BENCH on a fresh DB."""
    results = compute_score_f(conn, venue=venue, strategy_id=strategy_id)
    if not results:
        return 0.0
    cutoff = max(r.closed_ts for r in results) - lookback_days * 86_400
    return sum(r.legacy_score_contrib for r in results if r.closed_ts >= cutoff)


def _lookback_n_closed(
    conn: sqlite3.Connection, venue: str, strategy_id: str, lookback_days: int
) -> int:
    """Closed-lifecycle COUNT over the same trailing ``lookback_days`` window
    ``_lookback_score_f`` sums (the ``NClosedFn`` signature group A's BENCH
    minimum-evidence floor expects)."""
    results = compute_score_f(conn, venue=venue, strategy_id=strategy_id)
    if not results:
        return 0
    cutoff = max(r.closed_ts for r in results) - lookback_days * 86_400
    return sum(1 for r in results if r.closed_ts >= cutoff)


def _registry_candidates() -> list[tuple[str, str]]:
    return [
        (cls.metadata.venue, strategy_id)
        for strategy_id, cls in STRATEGY_REGISTRY.items()
    ]


def _seed_remap_table(conn: sqlite3.Connection, *, now_ts: int) -> None:
    """Boot-time ``score_f_remap_table`` seed (CRITICAL fix — cold-start
    remap gap). The refresh sweeper (``tools/ops/scoref_remap_refresh.py``)
    only runs on its own 07:40 cadence, so a boot shortly after the
    fee-split flip goes live would otherwise judge the flipped gross_bps
    axis against un-remapped, legacy-scale thresholds until the first
    sweep (up to ~24h). Mirrors ``refresh_all``'s own track -> venue-pool
    sweep directly against ``remap_table``'s public API (this package never
    imports ``tools/`` — that dependency runs the other way). Idempotent
    (UPSERT) and its own fail-open try/except, independent of hydrate/
    bootstrap's — a remap-seed failure must never block strategy_class
    hydrate (flow_not_block)."""
    try:
        tracks = conn.execute(
            "SELECT DISTINCT venue, strategy_id FROM positions WHERE status = 'closed'"
        ).fetchall()
        for venue, strategy_id in tracks:
            entry = compute_track_remap(
                conn, venue=str(venue), strategy_id=str(strategy_id), now_ts=now_ts,
            )
            if entry is not None:
                upsert_remap_entry(conn, entry)
        for venue in VENUE_POOLS:
            pool_entry = compute_venue_pool_remap(conn, venue=venue, now_ts=now_ts)
            if pool_entry is not None:
                upsert_remap_entry(conn, pool_entry)
        conn.commit()
    except Exception:  # noqa: BLE001 — boot must never crash on this
        logger.exception(
            "[pts-classes/boot] remap-table seed failed — thresholds fall "
            "back to venue-pool/hardcoded-legacy-default until the next sweep"
        )


def boot_hydrate_and_bootstrap_strategy_class(
    conn: sqlite3.Connection, *, now_ts: int
) -> int:
    """Hydrate the persisted ``strategy_class`` table; bootstrap-replay every
    registered candidate ONLY when the table is completely empty.

    Returns the number of candidates seeded (0 when the table already had
    rows — restart must never reset a live-tracked class). Fail-open: any
    exception is logged and swallowed (boot must never crash on a
    classification-layer failure, flow_not_block); returns 0 on failure.
    """
    _seed_remap_table(conn, now_ts=now_ts)
    try:
        existing = hydrate_strategy_class(conn)
        if existing:
            logger.info(
                "[pts-classes/boot] %d strategy_class row(s) already tracked "
                "— hydrate only, no bootstrap replay",
                len(existing),
            )
            return 0
        candidates = _registry_candidates()
        bootstrap_replay_strategy_class(
            conn,
            candidates=candidates,
            score_f=_lookback_score_f,
            lookback_days=_BOOTSTRAP_LOOKBACK_DAYS,
            now_ts=now_ts,
            n_closed_fn=_lookback_n_closed,
            timeframe_bucket_fn=_timeframe_bucket_fn,
        )
        conn.commit()
        logger.info(
            "[pts-classes/boot] bootstrap-replayed %d candidate(s) over a "
            "%d-day lookback",
            len(candidates), _BOOTSTRAP_LOOKBACK_DAYS,
        )
        return len(candidates)
    except Exception:  # noqa: BLE001 — boot must never crash on this
        logger.exception(
            "[pts-classes/boot] hydrate/bootstrap failed — strategy_class "
            "left as-is (G5 falls back to EARN default per-row)"
        )
        return 0
