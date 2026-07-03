"""Polaris SQLite DDL — pts-classes probe reranker (group F).

Raw ``CREATE TABLE IF NOT EXISTS`` string consumed by ``schema.ALL_DDL``. Split
into its own module (new feature, own file) per the file-size cap, same
convention as ``schema_ddl_classes.py`` (group A).

``probe_slot_assignment`` is an APPEND-ONLY per-rerank-run snapshot of which
(venue, strategy_id) PROVE candidates hold one of that track's concurrent
probe slots (3/2/1 per track A/B/C — ``polaris.core.classes.r_pool.
PROBE_CONCURRENCY_CAP``) — same "no mutable aggregate column" shape as
``ladder_ledger`` / ``score_f_events``: the CURRENT slot state for a track is
always the latest ``run_ts`` batch, never an UPDATE-in-place row (a crash
mid-batch just leaves the previous run's snapshot as the last-known-good
state, replay-safe). This is a capital-ROUTING record (which PROVE candidates
get to fire a real probe vs. stay shadow-routed), never a block/reject filter
— a candidate that misses a slot keeps signaling/learning/shadow-pricing
regardless (aggressive_always_profit / no_block_filter_architecture).
"""

from __future__ import annotations

DDL_PROBE_SLOT_ASSIGNMENT = """
CREATE TABLE IF NOT EXISTS probe_slot_assignment (
    run_ts INTEGER NOT NULL,
    track TEXT NOT NULL,
    venue TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    slot_active INTEGER NOT NULL DEFAULT 0,
    shadow_score_mean REAL NOT NULL DEFAULT 0.0,
    fill_rate REAL NOT NULL DEFAULT 0.0,
    expected_fee_usd REAL NOT NULL DEFAULT 0.0,
    wait_age_sec INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_ts, track, venue, strategy_id)
);
"""

DDL_PROBE_SLOT_ASSIGNMENT_LATEST_INDEX = """
CREATE INDEX IF NOT EXISTS idx_probe_slot_assignment_track_run
    ON probe_slot_assignment(track, run_ts DESC);
"""
