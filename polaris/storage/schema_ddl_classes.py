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
    last_promotion_ts INTEGER,
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
    score_contrib REAL NOT NULL DEFAULT 0.0,
    gross_usd REAL,
    notional_usd REAL,
    fee_raw_usd REAL,
    fee_drag_bps REAL
);
"""
# gross_usd / notional_usd / fee_raw_usd — fee-split v0 additive columns
# (vault/50_research/debates/fee_split_judgment_2026-07-10.md, item 7).
# Nullable, NO DEFAULT (NULL = legacy/pre-migration row — explicitly
# distinguished from a real 0.0, so the v0 percentile-proxy / v1 real-bps
# scorers can tell "no data" apart from "measured zero"). rollup_score_f
# populates all three for every NEW row going forward; ``gross_usd`` mirrors
# the already-computed ``net_usd`` (fills.pnl_usd is fee-EXCLUSIVE — see
# score_f.py's compute_lifecycle_fee docstring, so "gross" and "net" are the
# SAME value here; the column exists as its own name for the fee-split
# scorers' clarity, not because the underlying number differs).
#
# fee_drag_bps — fee-split v1 FLIP additive column (fee_split_flip_r2_
# 2026-07-12, item 1). ``score_contrib`` now carries the JUDGED axis
# (gross_bps by default, legacy net/fee_denom under
# ``POLARIS_SCOREF_NET_LEGACY=1`` — see score_f.py's ``compute_score_f``);
# ``fee_drag_bps`` is the REPORTING-ONLY fee axis (fee_usd/notional_usd in
# bps) — dashboards/digests/tripwires read it, nothing in the survival FSM
# or SCALE gate treats it as a demotion input. Nullable like the v0 trio
# above (NULL = pre-flip row, no notional_usd to derive it from).
#
# ADDITIVE ONLY — existing net_usd/fee_denom_usd columns and their consumer
# (f_track_cap, the capital-routing fee cap) are UNCHANGED.

DDL_SCORE_F_EVENTS_TRACK_DAY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_score_f_events_track_day
    ON score_f_events(venue, strategy_id, day);
"""

# ---------------------------------------------------------------------------
# score_f_remap_table (fee-split v1 FLIP, item 2) — persisted per-track /
# per-venue-pool threshold remap, one row per scope. Spec source:
# vault/50_research/debates/fee_split_flip_r2_2026-07-12.md (R2) item 2:
# "임계값 = 백분위 매핑 — per-track n>=30은 개별, 미달은 venue-pool fallback
# (okx/capital/alpaca 3세트, 글로벌 pooled 금지)."
#
# Persisted (not recomputed per-close) so the Schmitt/stagnation/ladder
# thresholds transition.py reads stay STABLE across a track's whole trading
# day — recomputing on every close would let the remap drift close-to-close
# and defeat Schmitt hysteresis (T4 label-churn concern, R2 red-team). Refresh
# cadence is an external sweeper (tools/ops/scoref_remap_refresh.py) — this
# table is its write target, and ``polaris.core.classes.remap_table.
# resolve_thresholds`` is its (read-only, fail-open) live consumer.
#
# ``scope_key`` = "{venue}:{strategy_id}" (scope_type='track') or
# "{venue}:__pool__" (scope_type='pool', one of the 3 venue pools — NEVER a
# single global pool, R2 T3 fix). ``stale``/``psi``/``ks`` are the item-4
# staleness flag the refresh sweeper stamps (auto-flag only — nothing here
# blocks/throttles a transition; a stale row still resolves and is used,
# same flow_not_block contract as every other pts-classes signal).
# ---------------------------------------------------------------------------

DDL_SCORE_F_REMAP_TABLE = """
CREATE TABLE IF NOT EXISTS score_f_remap_table (
    scope_key TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    n_samples INTEGER NOT NULL,
    computed_ts INTEGER NOT NULL,
    schmitt_bench_partial REAL NOT NULL,
    schmitt_bench_full REAL NOT NULL,
    schmitt_prove REAL NOT NULL,
    prove_stagnation REAL NOT NULL,
    ladder_step0 REAL NOT NULL,
    ladder_step1 REAL NOT NULL,
    ladder_step2 REAL NOT NULL,
    ladder_decay REAL NOT NULL,
    ref_population_json TEXT NOT NULL DEFAULT '[]',
    stale INTEGER NOT NULL DEFAULT 0,
    psi REAL,
    ks REAL
);
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
