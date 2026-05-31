"""Polaris SQLite DDL — Layer 2/3/5/6 + fill-ledger table + index definitions.

Raw ``CREATE TABLE/INDEX IF NOT EXISTS`` strings consumed by ``schema.ALL_DDL``.
Split out of ``schema.py`` (DDL-only, no logic) to keep each module ≤500 LOC.
Spec sources: vault/30_components/layer-{2,3,5,6}-*.md + ADR-003/006/007.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Layer 2 — Per-Gate Pipeline (gate_events + ai_lessons + segments)
# Spec source: vault/30_components/layer-2-per-gate-pipeline.md (Schema)
# ---------------------------------------------------------------------------

DDL_GATE_EVENTS = """
CREATE TABLE IF NOT EXISTS gate_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    signal_id TEXT,
    position_id TEXT,
    gate_id INTEGER NOT NULL,
    phase TEXT NOT NULL,
    decision TEXT,
    model_used TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT,
    error_text TEXT,
    created_ts INTEGER NOT NULL
);
"""

DDL_GATE_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_gate_events_run
    ON gate_events(run_id, gate_id, created_ts);
"""

# Dashboard covering index: the snapshot's funnel / GPT-stat / per-stream AI-cost
# aggregates filter by created_ts over a 1h window and read only these thin
# columns. Without a covering index they scan the fat ``payload_json`` rows
# (~10s each on a 10^5-row table). Leading ``created_ts`` serves the window and
# the trailing columns make all three queries index-only. Read-only/display
# path — no trading behavior depends on it.
DDL_GATE_EVENTS_DASH_INDEX = """
CREATE INDEX IF NOT EXISTS idx_gate_events_dash
    ON gate_events(created_ts, gate_id, decision, model_used, phase,
                   position_id, input_tokens, output_tokens);
"""

# AI-conductor P0 SHADOW — deterministic technical rule vs live GPT decision.
# One row per gate execution that ran a shadow technical rule (G3/G4 in P0).
# INSTRUMENTATION ONLY — never read by the pipeline; feeds the next-session
# acceptance gate (KILL/PASS-rate + agreement by regime / cell warmth).
# Spec: .claude/plans/ai_conductor_architecture_2026-05-30.md (P0 shadow).
DDL_GATE_SHADOW_EVENTS = """
CREATE TABLE IF NOT EXISTS gate_shadow_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    signal_id TEXT,
    gate_id INTEGER NOT NULL,
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    regime TEXT NOT NULL DEFAULT '',
    technical_decision TEXT NOT NULL,
    technical_scalar REAL NOT NULL DEFAULT 1.0,
    technical_reason TEXT NOT NULL DEFAULT '',
    technical_flags TEXT NOT NULL DEFAULT '',
    gpt_decision TEXT NOT NULL DEFAULT '',
    mismatch INTEGER NOT NULL DEFAULT 0,
    cell_warm INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
"""

DDL_GATE_SHADOW_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_gate_shadow_events_gate
    ON gate_shadow_events(gate_id, regime, cell_warm, created_ts);
"""

DDL_AI_LESSONS = """
CREATE TABLE IF NOT EXISTS ai_lessons (
    lesson_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    regime TEXT,
    session TEXT,
    confidence REAL NOT NULL,
    lesson_type TEXT NOT NULL,
    delta_json TEXT NOT NULL DEFAULT '{}',
    created_ts INTEGER NOT NULL
);
"""

DDL_AI_LESSONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ai_lessons_strategy
    ON ai_lessons(strategy_id, created_ts DESC);
"""

# Meta-labeling (#10) — triple-barrier label collection (collection-only;
# never gates sizing/exits). One row per closed trade; ``trade_id`` UNIQUE so
# re-labeling overwrites. Future 2nd-stage act/skip model trains on this table
# once sample-count is sufficient.
DDL_META_LABELS = """
CREATE TABLE IF NOT EXISTS meta_labels (
    label_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    ticker TEXT NOT NULL,
    regime TEXT,
    session TEXT,
    barrier TEXT NOT NULL,
    pnl_sign INTEGER NOT NULL,
    r REAL NOT NULL,
    hit_horizontal INTEGER NOT NULL,
    holding_bars INTEGER NOT NULL,
    expected_holding_bars INTEGER NOT NULL,
    horizon_fraction REAL NOT NULL,
    created_ts INTEGER NOT NULL
);
"""

DDL_META_LABELS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_meta_labels_strategy
    ON meta_labels(strategy_id, created_ts DESC);
"""

DDL_POSITION_STRATEGY_SEGMENTS = """
CREATE TABLE IF NOT EXISTS position_strategy_segments (
    position_id TEXT NOT NULL,
    segment_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    regime_at_start TEXT NOT NULL DEFAULT '',
    started_ts INTEGER NOT NULL,
    ended_ts INTEGER,
    entry_reason TEXT,
    exit_reason TEXT,
    attribution_weight REAL NOT NULL DEFAULT 0.0,
    pnl_r REAL NOT NULL DEFAULT 0.0
);
"""

DDL_POSITION_STRATEGY_SEGMENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_position_strategy_segments_pos
    ON position_strategy_segments(position_id, started_ts);
"""

# ---------------------------------------------------------------------------
# Layer 3 — Sizing/Risk State (kelly + tier amplifier)
# Spec source: vault/30_components/layer-3-sizing-risk.md (Schema)
# ---------------------------------------------------------------------------

DDL_STRATEGY_RISK_STATE = """
CREATE TABLE IF NOT EXISTS strategy_risk_state (
    venue TEXT NOT NULL,
    strategy TEXT NOT NULL,
    closed_trades INTEGER NOT NULL DEFAULT 0,
    kelly_p REAL NOT NULL DEFAULT 0.0,
    kelly_q REAL NOT NULL DEFAULT 0.0,
    kelly_fraction REAL NOT NULL DEFAULT 0.0,
    win_streak INTEGER NOT NULL DEFAULT 0,
    hit_rate_10 REAL NOT NULL DEFAULT 0.0,
    updated_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (venue, strategy)
);
"""

DDL_POSITION_RISK_STATE = """
CREATE TABLE IF NOT EXISTS position_risk_state (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    cluster_id TEXT,
    strategy TEXT NOT NULL,
    track TEXT NOT NULL,
    signal_strength REAL NOT NULL DEFAULT 0.0,
    open_risk_pct REAL NOT NULL DEFAULT 0.0,
    notional_usd REAL NOT NULL DEFAULT 0.0,
    opened_ts INTEGER NOT NULL,
    PRIMARY KEY (venue, symbol, strategy, opened_ts)
);
"""

# ---------------------------------------------------------------------------
# Layer 5 — Learner Network (incremental stats + hourly commit + triple block)
# Spec source: vault/30_components/layer-5-learner-network.md (Schema)
# ADR-007 — adaptive_learner_attack 4 principles
# ---------------------------------------------------------------------------

DDL_LEARNER_STATE = """
CREATE TABLE IF NOT EXISTS learner_state (
    learner_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value REAL NOT NULL,
    n_eff REAL NOT NULL DEFAULT 0.0,
    wins_eff REAL NOT NULL DEFAULT 0.0,
    pnl_r_sum_eff REAL NOT NULL DEFAULT 0.0,
    pending_delta REAL NOT NULL DEFAULT 0.0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (learner_id, key)
);
"""

DDL_LEARNER_BLOCKS = """
CREATE TABLE IF NOT EXISTS learner_blocks (
    ticker TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    regime TEXT NOT NULL,
    size_mult REAL NOT NULL DEFAULT 0.3,
    reason TEXT NOT NULL,
    source_learner TEXT NOT NULL,
    blocked_until_ts INTEGER NOT NULL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (ticker, strategy_id, regime)
);
"""

DDL_LEARNER_BLOCKS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_learner_blocks_until
    ON learner_blocks(blocked_until_ts);
"""

DDL_LEARNER_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS learner_snapshot (
    snapshot_ts INTEGER NOT NULL,
    learner_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_ts, learner_id)
);
"""

DDL_ROLLBACK_CANDIDATES = """
CREATE TABLE IF NOT EXISTS rollback_candidates (
    snapshot_ts INTEGER PRIMARY KEY,
    learner_scope TEXT NOT NULL,
    expectancy_pre REAL NOT NULL,
    expectancy_post REAL NOT NULL,
    trade_count INTEGER NOT NULL,
    status TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Edge-validation Phase 1 — Bayesian posterior on cost-adjusted expectancy.
# MEASUREMENT + DISPLAY ONLY (never read by Layer 3 sizing). New tables; the
# learner_state PK/semantics are left untouched.
# Spec source: edge-validation Phase 1 (Jin approved 2026-05-29).
# ---------------------------------------------------------------------------

DDL_LEARNER_POSTERIOR = """
CREATE TABLE IF NOT EXISTS learner_posterior (
    exchange TEXT NOT NULL,
    strategy TEXT NOT NULL,
    ticker TEXT NOT NULL,
    regime TEXT NOT NULL,
    mu REAL NOT NULL DEFAULT 0.0,
    kappa REAL NOT NULL DEFAULT 1.0,
    alpha REAL NOT NULL DEFAULT 1.0,
    beta REAL NOT NULL DEFAULT 1.0,
    m2 REAL NOT NULL DEFAULT 0.0,
    running_mean REAL NOT NULL DEFAULT 0.0,
    n_samples INTEGER NOT NULL DEFAULT 0,
    p_pos REAL NOT NULL DEFAULT 0.5,
    updated_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (exchange, strategy, ticker, regime)
);
"""

DDL_STRATEGY_REGIME_PRIOR = """
CREATE TABLE IF NOT EXISTS strategy_regime_prior (
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    mu0 REAL NOT NULL DEFAULT 0.0,
    kappa0 REAL NOT NULL DEFAULT 1.0,
    alpha0 REAL NOT NULL DEFAULT 1.0,
    beta0 REAL NOT NULL DEFAULT 1.0,
    m2 REAL NOT NULL DEFAULT 0.0,
    n_samples INTEGER NOT NULL DEFAULT 0,
    updated_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy, regime)
);
"""

# ---------------------------------------------------------------------------
# Layer 6 — Live Recalc (per-position dirty state + regime SSOT)
# Spec source: vault/30_components/layer-6-live-recalc.md (Schema)
# ---------------------------------------------------------------------------

DDL_POSITION_LIVE_RECALC_STATE = """
CREATE TABLE IF NOT EXISTS position_live_recalc_state (
    position_id TEXT PRIMARY KEY,
    last_check_ts INTEGER NOT NULL,
    last_override_ts INTEGER,
    override_count INTEGER NOT NULL DEFAULT 0,
    dirty_reason TEXT,
    dirty_ts INTEGER,
    cooldown_until_ts INTEGER NOT NULL DEFAULT 0,
    last_seen_mid REAL,
    last_seen_unrealized_pnl_r REAL,
    last_eval_regime TEXT,
    locked_widen INTEGER NOT NULL DEFAULT 0
);
"""

DDL_REGIME_STATE = """
CREATE TABLE IF NOT EXISTS regime_state (
    venue TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    regime TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    consecutive_candidate TEXT,
    consecutive_count INTEGER NOT NULL DEFAULT 0,
    updated_ts INTEGER NOT NULL,
    PRIMARY KEY (venue, underlying_group_id)
);
"""

DDL_POSITION_CONVICTION_LAYERS = """
CREATE TABLE IF NOT EXISTS position_conviction_layers (
    layer_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    parent_position_id TEXT,
    layer_index INTEGER NOT NULL,
    size_mult REAL NOT NULL,
    opened_ts INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL
);
"""

DDL_POSITION_CONVICTION_LAYERS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_conviction_layers_position
    ON position_conviction_layers(position_id, layer_index);
"""

# ---------------------------------------------------------------------------
# Layer 1 — Fill ledger (Day 6: per-fill audit row backing the dashboard
# "recent_fills" panel and Layer 8 post-trade reflector).
#
# Spec source:
#   - vault/10_decisions/ADR-003-8-layer-architecture.md (Unified SQLite Schema)
#   - vault/30_components/layer-1-canonical-baseline.md  (Fill section)
# ---------------------------------------------------------------------------

DDL_FILLS = """
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    side TEXT NOT NULL,
    size_usd REAL NOT NULL,
    fill_price REAL NOT NULL,
    fee_usd REAL NOT NULL DEFAULT 0.0,
    slippage_bps REAL NOT NULL DEFAULT 0.0,
    ts_ms INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    contribution_id TEXT,
    pnl_usd REAL NOT NULL DEFAULT 0.0,
    is_close INTEGER NOT NULL DEFAULT 0,
    base_qty REAL NOT NULL DEFAULT 0.0,
    quote_qty REAL NOT NULL DEFAULT 0.0,
    state TEXT NOT NULL DEFAULT 'filled'
);
"""

DDL_FILLS_INDEX_TS = """
CREATE INDEX IF NOT EXISTS idx_fills_ts
    ON fills(ts_ms DESC);
"""

DDL_FILLS_INDEX_VENUE_SYMBOL = """
CREATE INDEX IF NOT EXISTS idx_fills_venue_instrument
    ON fills(venue, instrument_id, ts_ms DESC);
"""

DDL_FILLS_INDEX_ORDER = """
CREATE INDEX IF NOT EXISTS idx_fills_order
    ON fills(order_id);
"""

# ---------------------------------------------------------------------------
# Layer 7 — runtime non-tradeable venue blocklist (compliance / 51155).
# A (venue, symbol) the venue permanently refuses is skipped by focus + the
# order guard so the rest of the universe keeps flowing (flow_not_block).
# ---------------------------------------------------------------------------

DDL_VENUE_BLOCKLIST = """
CREATE TABLE IF NOT EXISTS venue_blocklist (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    reason TEXT NOT NULL,
    code TEXT NOT NULL,
    first_ts INTEGER NOT NULL,
    last_ts INTEGER NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (venue, symbol)
);
"""

# ---------------------------------------------------------------------------
# Dashboard telemetry — capital-rotation + session-forced-exit observability.
#
# DISPLAY-ONLY, additive. The live loop holds these counters in-memory on
# ``ProdLoopState`` (``state.rotations`` / ``recalc_session_forced_exit``) and
# only logs them; the read-only dashboard cannot reach process memory. The
# rotation/forced-exit wires append a row here best-effort so the snapshot can
# surface a count + the last rotation detail (victim / E$_new / E$_held / margin
# / cost). NEVER read by sizing / gating / the rotation evaluator — pure
# observability (the rotation telemetry "REQUIRED before any live run"). One row
# per fire; a missing table degrades to a zeroed dashboard panel (graceful zero).
# ---------------------------------------------------------------------------

DDL_LOOP_ROTATION_EVENTS = """
CREATE TABLE IF NOT EXISTS loop_rotation_events (
    rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    venue TEXT NOT NULL DEFAULT '',
    victim_symbol TEXT NOT NULL DEFAULT '',
    victim_strategy TEXT NOT NULL DEFAULT '',
    winner_symbol TEXT NOT NULL DEFAULT '',
    e_new REAL NOT NULL DEFAULT 0.0,
    e_held REAL NOT NULL DEFAULT 0.0,
    margin REAL NOT NULL DEFAULT 0.0,
    cost REAL NOT NULL DEFAULT 0.0
);
"""

DDL_LOOP_ROTATION_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_loop_rotation_events_ts
    ON loop_rotation_events(ts);
"""

# Session-forced-exit (CALENDAR INTEGRITY, TIME-only) telemetry — one row per
# force-flatten so the dashboard can show a count + last venue/symbol. Same
# display-only / never-gating contract as the rotation events above.
DDL_LOOP_SESSION_EXIT_EVENTS = """
CREATE TABLE IF NOT EXISTS loop_session_exit_events (
    rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT ''
);
"""

DDL_LOOP_SESSION_EXIT_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_loop_session_exit_events_ts
    ON loop_session_exit_events(ts);
"""
