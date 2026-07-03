"""Polaris SQLite DDL — Performance-Tiered Strategy classes (pts-classes).

Raw ``CREATE TABLE IF NOT EXISTS`` string consumed by ``schema.ALL_DDL``.
Split into its own module (new feature, own file) per the file-size cap.

``strategy_class`` is the SSOT for capital-routing tier state: which class
(EARN/PROVE/BENCH/KILL — enum owned by the classifier group, not this
storage layer) a (venue, strategy_id) currently occupies, its track_R cap,
dwell/epoch bookkeeping for hysteresis, and lifecycle accounting (qty,
cumulative fees/pnl). This is a **routing** record, not a block/reject
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
    qty REAL NOT NULL DEFAULT 0.0,
    cum_fees REAL NOT NULL DEFAULT 0.0,
    cum_pnl REAL NOT NULL DEFAULT 0.0,
    intent_ring TEXT NOT NULL DEFAULT '[]',
    shadow_ring TEXT NOT NULL DEFAULT '[]',
    probe_fee_24h REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (venue, strategy_id)
);
"""
