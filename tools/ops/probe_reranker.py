"""07:30 daily probe slot reranker (pts-classes, group F).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Reads every current PROVE-class ``strategy_class`` row, ranks each track's
candidates via ``polaris.core.classes.probe_reranker`` (pessimistic-shadow
score_F desc, tie-break fill_rate -> expected fee -> wait age; group F's own
pure module, see its docstring for the full spec + interpretation notes), and
persists ONE append-only snapshot batch per run into ``probe_slot_assignment``
(idempotent per ``run_ts`` — PK collision on a re-run at the same timestamp is
a silent no-op, mirrors ``ladder.materialize_credits`` / ``score_f.rollup_score_f``).

Batch-commit sweeper — zero writes on any hot path
(feedback_db_lock_is_architecture_signal). Never blocks/throttles a strategy:
an unslotted PROVE candidate keeps signaling/learning/shadow-pricing exactly
as before this run (aggressive_always_profit / no_block_filter_architecture)
— ``slot_active`` is a routing signal for the (separate, out-of-scope for
this module) G5 compute_size consumer to read, not a filter this module
itself applies.

Venue -> track resolution reuses ``polaris.core.streams.config.resolve_stream``
(the existing SSOT venue/track mapping, A=okx/B=capital/C=alpaca) — an
unregistered venue is skipped (fail-open, never crashes the whole sweep over
one bad row).
"""

from __future__ import annotations

import json
import sqlite3
import time

from polaris.core.classes.probe_reranker import (
    ProbeCandidate,
    assign_slots,
    rank_candidates,
)
from polaris.core.classes.score_f import f_track_cap
from polaris.core.sizing.probe_notional import probe_notional_usd, round_trip_fee_rate
from polaris.core.streams.config import Track, resolve_stream

__all__ = ["run_rerank"]

# score_f.f_track_cap's own rolling window (task spec ⑥ reuses the SAME
# window_w the classifier group already tracks per strategy, but this
# sweeper needs one fixed lookback to scope its own median-fee read — 20
# mirrors the default ``strategy_class.window_w`` DDL default so a
# brand-new/never-configured row's cap read matches its own class window).
_F_TRACK_CAP_WINDOW_W = 20

# Same 24h grain as ``probe_fee_24h`` / ``_production_close_classes``'s own
# recent-activity window.
_RECENT_WINDOW_SEC = 86_400


def _shadow_score_mean(shadow_ring_json: str) -> float:
    try:
        ring = json.loads(shadow_ring_json) if shadow_ring_json else []
    except (TypeError, ValueError):
        return 0.0
    values = [float(v) for v in ring] if isinstance(ring, list) else []
    return sum(values) / len(values) if values else 0.0


def _fill_rate(conn: sqlite3.Connection, *, strategy_id: str, watermark_ts: int) -> float:
    """Simple 24h fills/signals ratio — this reranker's own coarse read (NOT
    ``transition.py``'s last-50-signals fill gate; that lives in the close
    hook, group WIRE's scope). No signals in the window -> 0.0 (a candidate
    with no recent signal activity has no evidence of a good fill rate,
    never defaults to a favorable 1.0)."""
    n_signals = int(
        conn.execute(
            "SELECT COUNT(*) FROM signals WHERE strategy_id = ? AND ts >= ?",
            (strategy_id, watermark_ts),
        ).fetchone()[0]
    )
    if n_signals == 0:
        return 0.0
    n_fills = int(
        conn.execute(
            "SELECT COUNT(*) FROM fills WHERE strategy_id = ? AND ts_ms >= ?",
            (strategy_id, watermark_ts * 1000),
        ).fetchone()[0]
    )
    return n_fills / n_signals


def _expected_fee_usd(venue: str) -> float:
    """Round-trip fee this venue/track's fixed probe notional is expected to
    incur — same calc shape as ``probe_fee.accrue_probe_fee`` (re-derived,
    not imported, to keep this module's dependency footprint to the
    ``polaris.core`` read-only surface it already needs)."""
    return probe_notional_usd(venue) * round_trip_fee_rate(venue)


def _resolve_track(venue: str) -> Track | None:
    try:
        return resolve_stream(venue).track
    except KeyError:
        return None


def _load_candidates(
    conn: sqlite3.Connection, *, now_ts: int
) -> dict[Track, list[ProbeCandidate]]:
    rows = conn.execute(
        "SELECT venue, strategy_id, shadow_ring, probe_fee_24h, "
        "last_transition_ts FROM strategy_class WHERE strategy_class = 'PROVE'"
    ).fetchall()

    watermark = now_ts - _RECENT_WINDOW_SEC
    by_track: dict[Track, list[ProbeCandidate]] = {}
    for venue, strategy_id, shadow_ring_json, probe_fee_24h, last_transition_ts in rows:
        track = _resolve_track(str(venue))
        if track is None:
            continue
        cap_result = f_track_cap(
            conn,
            venue=str(venue),
            strategy_id=str(strategy_id),
            window_w=_F_TRACK_CAP_WINDOW_W,
            prove_fallback_usd=probe_notional_usd(str(venue)),
        )
        candidate = ProbeCandidate(
            venue=str(venue),
            strategy_id=str(strategy_id),
            track=track,
            shadow_score_mean=_shadow_score_mean(str(shadow_ring_json)),
            fill_rate=_fill_rate(conn, strategy_id=str(strategy_id), watermark_ts=watermark),
            expected_fee_usd=_expected_fee_usd(str(venue)),
            wait_age_sec=max(0, now_ts - int(last_transition_ts)),
            probe_fee_24h_usd=float(probe_fee_24h),
            f_track_cap_usd=cap_result.f_track_cap_usd,
        )
        by_track.setdefault(track, []).append(candidate)
    return by_track


def run_rerank(conn: sqlite3.Connection, *, now_ts: int) -> int:
    """Rank + assign probe slots for every track with PROVE candidates,
    persisting one snapshot batch at ``run_ts = now_ts``. Returns the count
    of NEW rows inserted (0 on a clean no-candidates run or a re-run at an
    already-recorded ``run_ts``)."""
    by_track = _load_candidates(conn, now_ts=now_ts)

    inserted = 0
    for track, candidates in by_track.items():
        ranked = rank_candidates(candidates)
        result = assign_slots(ranked, track=track)
        venue_by_strategy = {c.strategy_id: c.venue for c in candidates}
        for a in result.assignments:
            cur = conn.execute(
                "INSERT OR IGNORE INTO probe_slot_assignment "
                "(run_ts, track, venue, strategy_id, rank, slot_active, "
                "shadow_score_mean, fill_rate, expected_fee_usd, wait_age_sec, "
                "reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now_ts, track, venue_by_strategy[a.strategy_id], a.strategy_id,
                    a.rank, int(a.slot_active), a.shadow_score_mean, a.fill_rate,
                    a.expected_fee_usd, a.wait_age_sec, a.reason,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
    conn.commit()
    return inserted


def main() -> int:
    from polaris.storage.schema import init_db
    from tools.ops.ops_config import OpsConfig

    cfg = OpsConfig.default()
    conn = init_db(cfg.db_path)
    try:
        inserted = run_rerank(conn, now_ts=int(time.time()))
        print(f"[probe-reranker] inserted={inserted}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
