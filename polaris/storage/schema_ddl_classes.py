"""Polaris SQLite DDL — Performance-Tiered Strategy classes (pts-classes).

Raw ``CREATE TABLE IF NOT EXISTS`` string consumed by ``schema.ALL_DDL``.
Split into its own module (new feature, own file) per the file-size cap.

``strategy_class`` is the SSOT for capital-routing tier state: which class
(EARN/PROVE/BENCH/KILL — enum owned by the classifier group, not this
storage layer) a (venue, strategy_id) currently occupies, its track_R cap,
dwell/epoch bookkeeping for hysteresis, and lifecycle accounting
(``open_lifecycle_id`` anchor + qty, cumulative fees/pnl accrued against
that open lifecycle). This is a **routing** record, not a block/reject
filter — BENCH-classed strategies keep signaling/learning/shadow-pricing;
only capital allocation reads this table (aggressive_always_profit /
no_block_filter_architecture preserved).

Ring buffers (``intent_ring`` bounded 50 entries, ``shadow_ring`` bounded to
the row's own ``window_w``) are stored as JSON-encoded TEXT rather than a
child table — one row per (venue, strategy_id) is the natural shape and a
bounded JSON array avoids an unbounded-append table for what is fundamentally
a fixed-size rolling window (matches the ``learner_state`` / single-row LWW
precedent, not the append-only ``ladder_ledger`` precedent).

PK = (venue, strategy_id) with ON CONFLICT DO UPDATE at the call site
(UPSERT) — mirrors ``strategy_risk_state`` / ``learner_state``.

Spec source: pts-classes build task (2026-07-03, group A/B split — this
module is group A's storage layer; ``score_F`` classification logic is
group B's and is injected into ``bootstrap_replay_strategy_class`` as a
callable so this module has zero import coupling to the classifier).
"""

from __future__ import annotations

DDL_STRATEGY_CLASS = """
CREATE TABLE IF NOT EXISTS strategy_class (
    venue TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_class TEXT NOT NULL DEFAULT 'PROVE',
    window_w INTEGER NOT NULL DEFAULT 20,
    f_track_cap REAL NOT NULL DEFAULT 1.0,
    dwell INTEGER NOT NULL DEFAULT 0,
    epoch_id INTEGER NOT NULL DEFAULT 1,
    last_transition_ts INTEGER NOT NULL DEFAULT 0,
    kill_state TEXT NOT NULL DEFAULT 'ACTIVE',
    ladder_step INTEGER NOT NULL DEFAULT 0,
    open_lifecycle_id TEXT NOT NULL DEFAULT '',
    qty REAL NOT NULL DEFAULT 0.0,
    cum_fees REAL NOT NULL DEFAULT 0.0,
    cum_pnl REAL NOT NULL DEFAULT 0.0,
    intent_ring TEXT NOT NULL DEFAULT '[]',
    shadow_ring TEXT NOT NULL DEFAULT '[]',
    probe_fee_24h REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (venue, strategy_id)
);
"""

# ---------------------------------------------------------------------------
# score_F event ledger (group B — polaris.core.classes.score_f)
#
# score_F = Σ(net_i / max(abs(fee_i), 0.0001 * notional_i)) over every CLOSED
# flat->nonzero->flat lifecycle (``positions`` row) for a (venue, strategy_id)
# track. ``score_f_events`` is an APPEND-ONLY per-lifecycle event log — ONE
# row per closed ``position_id`` (UNIQUE, so a re-scan of an already-scored
# lifecycle is a no-op INSERT OR IGNORE) — same "no mutable aggregate column"
# shape as ``ladder_ledger``: every daily/window total is a live ``SUM()``
# projection over these rows, never a hand-maintained running counter, so a
# crash mid-batch can never corrupt a rollup (replay re-derives it). Written
# by a batch-commit sweeper (never inline in the position-close hot path,
# mirrors ``materialize_credits`` / feedback_db_lock_is_architecture_signal).
# ---------------------------------------------------------------------------

DDL_SCORE_F_EVENTS = """
CREATE TABLE IF NOT EXISTS score_f_events (
    position_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    day TEXT NOT NULL,
    closed_ts INTEGER NOT NULL,
    net_usd REAL NOT NULL DEFAULT 0.0,
    fee_denom_usd REAL NOT NULL DEFAULT 0.0001,
    score_contrib REAL NOT NULL DEFAULT 0.0
);
"""

DDL_SCORE_F_EVENTS_TRACK_DAY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_score_f_events_track_day
    ON score_f_events(venue, strategy_id, day);
"""

# Scan-window watermark — how far the score_F sweeper has progressed
# (closed_ts of the last position folded in). Advances only after a batch's
# INSERTs all commit; a crash mid-batch just re-scans the same window next
# pass, safe because ``position_id`` PRIMARY KEY makes the INSERT idempotent
# (mirrors ``ladder_credit_checkpoint``).
DDL_SCORE_F_CHECKPOINT = """
CREATE TABLE IF NOT EXISTS score_f_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_scanned_closed_ts INTEGER NOT NULL DEFAULT 0
);
"""
