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

# Component C (SHADOW) — edge-first entry admission shadow log. One row per
# entry-evaluation: the admit / would_suppress decision computed net of the REAL
# round-trip fee (behavior 0 — logged only, never blocks the pipeline). Mirrors
# the gate_shadow_events precedent so the acceptance analysis can split the
# would-suppress population by regime / strategy / basis. See
# polaris/core/economics/entry_admission.py + .claude/plans/
# okx_live_confidence_redesign_2026-05-31.md §C.
DDL_ENTRY_ADMISSION_SHADOW = """
CREATE TABLE IF NOT EXISTS entry_admission_shadow (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    signal_id TEXT,
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL DEFAULT '',
    regime TEXT NOT NULL DEFAULT '',
    cell_n_eff REAL NOT NULL DEFAULT 0.0,
    cell_avg_pnl_r REAL NOT NULL DEFAULT 0.0,
    expected_move_r REAL NOT NULL DEFAULT 0.0,
    real_round_trip_cost_r REAL NOT NULL DEFAULT 0.0,
    admit INTEGER NOT NULL DEFAULT 1,
    would_suppress INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    basis TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL
);
"""

DDL_ENTRY_ADMISSION_SHADOW_INDEX = """
CREATE INDEX IF NOT EXISTS idx_entry_admission_shadow_regime
    ON entry_admission_shadow(regime, strategy_id, would_suppress, created_ts);
"""

# Real-fee maker-fill shadow (#77 component C) — one row per maker (post-only)
# entry fill capturing entry-BASIS (fill vs touch) + the REAL-maker-fee net
# (OKX demo's flat 70 bps hides the maker edge; the net here is computed off
# real_fee_bps(is_maker=True) = the only place the weekend maker edge is
# measurable before go-live). Instrumentation only — never drives a decision.
DDL_MAKER_FILL_SHADOW = """
CREATE TABLE IF NOT EXISTS maker_fill_shadow (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    side TEXT NOT NULL DEFAULT '',
    touch_px REAL NOT NULL DEFAULT 0.0,
    fill_px REAL NOT NULL DEFAULT 0.0,
    entry_basis_bps REAL NOT NULL DEFAULT 0.0,
    real_maker_net_bps REAL NOT NULL DEFAULT 0.0,
    outcome TEXT NOT NULL DEFAULT '',
    reposts INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
"""

DDL_MAKER_FILL_SHADOW_INDEX = """
CREATE INDEX IF NOT EXISTS idx_maker_fill_shadow_strategy
    ON maker_fill_shadow(strategy_id, outcome, created_ts);
"""

# Shadow-first would-be orders ([[weekend_maker_honest_rerun_2026-06-28]]) — one
# row per SUPPRESSED order on a shadow_first strategy (the two weekend OKX makers).
# The SIGNAL still flowed the full pipeline (G1-G5 + sizing); this records the
# would-be entry (sized notional + decision-time mark + the exit target) so the
# live edge can be measured WITHOUT capital at risk. The forward would-be P&L is
# resolved offline from already-ingested bars at entry_mark + atr_pct (the same
# ATR-R basis the counterfactual sweep uses). Instrumentation only — no position /
# fill / venue order is ever written. flow_not_block: the order is deferred, never
# the signal.
DDL_WEEKEND_SHADOW_ORDERS = """
CREATE TABLE IF NOT EXISTS weekend_shadow_orders (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    side TEXT NOT NULL DEFAULT '',
    entry_mark REAL NOT NULL DEFAULT 0.0,
    notional_usd REAL NOT NULL DEFAULT 0.0,
    atr_pct REAL NOT NULL DEFAULT 0.0,
    profit_target_r REAL,
    created_ts INTEGER NOT NULL
);
"""

DDL_WEEKEND_SHADOW_ORDERS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_weekend_shadow_orders_strategy
    ON weekend_shadow_orders(strategy_id, created_ts);
"""

# Sector/dual-momentum rotation context SHADOW (frontgate-scan item #8, G1,
# behavior-0, 2026-07-11). One row per (rebalance-boundary cycle × sector ETF):
# the 11-SPDR-sector-ETF relative-momentum rank + z, recorded on the monthly
# rebalance-boundary bar only (mirrors ``index_dual_momentum_rotation
# ._is_rebalance_bar`` — first 1D bar of a new UTC calendar month, NOT every
# 5-10min L0 cycle). Stage 1 (this table, buildable now): ETF-only ranking,
# no per-stock sector mapping. ``candidate_symbols`` stays '' until a ticker→
# sector mapping source is decided (Stage 2, /debate item — no calendar
# deadline). ``delta_from_prior`` = this cycle's momentum_z minus the SAME
# symbol's last recorded momentum_z (NULL on a symbol's first-ever row).
# NEVER read by any live trading path — RANKING/ROUTING context, not applied
# (flow_not_block; routing-only per the integration blueprint, no directional
# weight overlap with item #3's universe rank).
DDL_SECTOR_RANK_SHADOW = """
CREATE TABLE IF NOT EXISTS sector_rank_shadow (
    rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_ts INTEGER NOT NULL,
    sector_etf_symbol TEXT NOT NULL,
    momentum_rank INTEGER NOT NULL,
    momentum_z REAL NOT NULL DEFAULT 0.0,
    candidate_symbols TEXT NOT NULL DEFAULT '',
    delta_from_prior REAL,
    created_ts INTEGER NOT NULL
);
"""

DDL_SECTOR_RANK_SHADOW_INDEX = """
CREATE INDEX IF NOT EXISTS idx_sector_rank_shadow_symbol
    ON sector_rank_shadow(sector_etf_symbol, cycle_ts DESC);
"""

# Gate→outcome instrumentation (BUILD, behavior 0) — one row per G3/G4 GPT
# decision on the bar entry pipeline. ``decision='KILL'`` rows are the killed
# signals whose counterfactual forward marks (1h/4h/24h first-1m-bar closes,
# converted to fee-adjusted R) the sweep resolves from already-ingested bars;
# ``decision='PASS'`` rows share the SAME mark/horizon estimator so the
# KILL-vs-PASS comparison is apples-to-apples (realized pnl_r is an auxiliary
# join via gate_events.position_id → positions.pnl_r). NEVER read by any
# trading path — it feeds the G3/G4 GPT-value /debate query only.
# ``cost_r`` is the round-trip REAL fee in the SAME per-unit ATR-R basis as
# fwd_r (2 × real_fee_usd(venue, mark) / atr_usd — price-level independent).
# ``unresolvable`` terminates rows whose symbol never re-ingests bars (focus
# rotation) so they cannot starve the LIMIT-batched sweep forever.
DDL_GATE_KILL_COUNTERFACTUALS = """
CREATE TABLE IF NOT EXISTS gate_kill_counterfactuals (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    signal_id TEXT,
    gate_id INTEGER NOT NULL,
    decision TEXT NOT NULL DEFAULT 'KILL',
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL DEFAULT '',
    side TEXT NOT NULL DEFAULT '',
    regime TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    model_used TEXT NOT NULL DEFAULT '',
    decision_ts INTEGER NOT NULL,
    mark_price REAL NOT NULL DEFAULT 0.0,
    mark_ts INTEGER NOT NULL DEFAULT 0,
    mark_source TEXT NOT NULL DEFAULT '',
    atr_pct REAL NOT NULL DEFAULT 0.0,
    atr_usd REAL NOT NULL DEFAULT 0.0,
    cost_r REAL NOT NULL DEFAULT 0.0,
    fwd_close_1h REAL,
    fwd_ts_1h INTEGER,
    fwd_r_1h REAL,
    fwd_close_4h REAL,
    fwd_ts_4h INTEGER,
    fwd_r_4h REAL,
    fwd_close_24h REAL,
    fwd_ts_24h INTEGER,
    fwd_r_24h REAL,
    resolve_attempts INTEGER NOT NULL DEFAULT 0,
    unresolvable INTEGER NOT NULL DEFAULT 0,
    created_ts INTEGER NOT NULL
);
"""

# Partial index over the sweep's pending set: a row leaves it when its 24h
# horizon resolves (1h/4h resolve in the same pass — any bar serving 24h also
# serves the earlier horizons) or when it is terminally ``unresolvable``.
DDL_GATE_KILL_COUNTERFACTUALS_PENDING_INDEX = """
CREATE INDEX IF NOT EXISTS idx_gkc_pending
    ON gate_kill_counterfactuals(decision_ts)
    WHERE fwd_r_24h IS NULL AND unresolvable = 0;
"""

DDL_GATE_KILL_COUNTERFACTUALS_GATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_gkc_gate
    ON gate_kill_counterfactuals(gate_id, decision, created_ts);
"""

# DISPLAY-ONLY cohort view — "G3/G4 KILL fee-adj forward-R distribution vs the
# PASS cohort" in ONE SELECT. Unresolved horizons are excluded (fwd_r IS NULL);
# ``fwd_lag_sec`` audits how far past the nominal horizon the resolving bar
# was (weekend/session gaps are legitimate; multi-day lag = focus rotation).
# ``realized_pnl_r`` joins the PASS cohort's actual outcome via the run_id →
# gate_events.position_id backfill → positions.pnl_r (NULL for KILL rows /
# never-opened PASS rows / reconciled positions — generated by
# ``schema._apply_post_migrations`` AFTER the positions.pnl_r ALTER it reads).
DDL_V_G34_COHORT_OUTCOMES = """
CREATE VIEW IF NOT EXISTS v_g34_cohort_outcomes AS
SELECT
    c.decision AS cohort,
    h.horizon AS horizon,
    c.gate_id AS gate_id,
    c.venue AS venue,
    c.symbol AS symbol,
    c.strategy_id AS strategy_id,
    c.side AS side,
    c.regime AS regime,
    c.reason AS reason,
    c.model_used AS model_used,
    h.fwd_r AS fwd_r,
    c.cost_r AS cost_r,
    h.fwd_r - c.cost_r AS fee_adj_fwd_r,
    h.fwd_ts - (c.decision_ts + h.horizon_sec) AS fwd_lag_sec,
    (SELECT p.pnl_r FROM positions p WHERE p.position_id =
        (SELECT ge.position_id FROM gate_events ge
          WHERE ge.run_id = c.run_id AND ge.position_id IS NOT NULL
          LIMIT 1)
    ) AS realized_pnl_r,
    c.mark_price AS mark_price,
    c.mark_source AS mark_source,
    c.decision_ts AS decision_ts,
    c.run_id AS run_id,
    c.signal_id AS signal_id,
    c.event_id AS event_id
FROM gate_kill_counterfactuals c
JOIN (
    SELECT event_id, '1h' AS horizon, 3600 AS horizon_sec,
           fwd_r_1h AS fwd_r, fwd_ts_1h AS fwd_ts
    FROM gate_kill_counterfactuals
    UNION ALL
    SELECT event_id, '4h', 14400, fwd_r_4h, fwd_ts_4h
    FROM gate_kill_counterfactuals
    UNION ALL
    SELECT event_id, '24h', 86400, fwd_r_24h, fwd_ts_24h
    FROM gate_kill_counterfactuals
) h ON h.event_id = c.event_id
WHERE h.fwd_r IS NOT NULL;
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
    pnl_r REAL NOT NULL DEFAULT 0.0,
    trade_id TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL DEFAULT '',
    pnl_usd REAL NOT NULL DEFAULT 0.0,
    cell_key TEXT NOT NULL DEFAULT ''
);
"""

DDL_POSITION_STRATEGY_SEGMENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_position_strategy_segments_pos
    ON position_strategy_segments(position_id, started_ts);
"""

# Lineage read-model index (P3 self-evolve): cell_key → segments lookup for
# ``lineage_for_cell`` + recent-lineage scans. Read-only; live trading never
# reads this table.
DDL_POSITION_STRATEGY_SEGMENTS_CELL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_position_strategy_segments_cell
    ON position_strategy_segments(cell_key, started_ts DESC);
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
    last_advanced_bar_id INTEGER,
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

# Reject-anchor anti-churn (audit1 P0-4 ①): the re-entry cooldown window
# (``reentry_cooldown_active``) reads ``MAX(opened_ts)`` off ``positions`` —
# a venue reject or a pre-submit clamp (e.g. Alpaca insufficient_buying_power)
# NEVER writes a ``positions`` row, so the cooldown window had no anchor and
# ``reentry_cooldown_active`` fell through to "no prior row → allow" even
# though the in-process novelty stamp (``state.last_entry_by_key``) correctly
# marked the re-fire as NOT novel (PANW: 58 intents/6.1h — every reject reset
# the cooldown to zero). This table gives the cooldown a PERSISTENT anchor per
# (venue, symbol, strategy_id) that survives a reject/clamp AND a process
# restart. Same TTL semantics as ``venue_blocklist``: UPSERT bumps ``last_ts``;
# a novel signal (new bar / side flip) still exempts via ``is_novel_reentry`` —
# this only fixes the window CHECK, never widens a block (flow_not_block).
DDL_REENTRY_ANCHOR = """
CREATE TABLE IF NOT EXISTS reentry_anchor (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    last_ts INTEGER NOT NULL,
    PRIMARY KEY (venue, symbol, strategy_id)
);
"""

# Capital market-closed delay-route (P0 timetable-aware submit gap): a reject
# during a closed window (SG25 '16/16 reject') stamps the venue's own
# per-epic ``seconds_to_next_open`` countdown here — ``reopen_at_ts`` — so the
# NEXT submit attempt on the exact (venue, symbol, strategy_id, side) key is
# suppressed until that instant, instead of re-submitting/re-rejecting every
# tick. flow_not_block: a DEFERRAL, never a permanent block — the row simply
# stops suppressing once ``now_ts >= reopen_at_ts`` (see
# ``polaris.venues.capital.reopen_route``).
DDL_CAPITAL_REOPEN_PENDING = """
CREATE TABLE IF NOT EXISTS capital_reopen_pending (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    side TEXT NOT NULL,
    reopen_at_ts INTEGER NOT NULL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (venue, symbol, strategy_id, side)
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

# ---------------------------------------------------------------------------
# P1 replay / benchmark READ-MODEL (display-only, offline-written).
#
# DISPLAY-ONLY, additive, and NEVER read by any live trading path (sizing /
# gating / rotation / exit). The offline replay+benchmark harness
# (``scripts/run_replay.py``) persists one ``replay_runs`` row per run and one
# ``benchmark_results`` row per (run × baseline-comparison) so the dashboard
# confidence/EDGE tab can surface the real-fee-net edge evidence + 3-tier
# verdict + CIs. The live loop's main DB (``data/polaris.sqlite``) may carry
# these tables for the dashboard to read, but the replay engine itself writes
# its cell-matrix updates only to a throwaway ``:memory:`` sandbox — the live
# trading state is immutable by construction. A missing table degrades to a
# zeroed dashboard panel (graceful zero), exactly like the rotation telemetry.
# ---------------------------------------------------------------------------

DDL_REPLAY_RUNS = """
CREATE TABLE IF NOT EXISTS replay_runs (
    run_id TEXT PRIMARY KEY,
    created_ts INTEGER NOT NULL,
    bar_interval TEXT NOT NULL DEFAULT '1H',
    start_ts INTEGER,
    end_ts INTEGER,
    fee_model TEXT NOT NULL DEFAULT 'okx_real',
    instrument_ids TEXT NOT NULL DEFAULT '',
    n_trades INTEGER NOT NULL DEFAULT 0,
    net_pnl_real_fee REAL NOT NULL DEFAULT 0.0,
    sharpe REAL NOT NULL DEFAULT 0.0,
    max_dd REAL NOT NULL DEFAULT 0.0,
    win_rate REAL NOT NULL DEFAULT 0.0,
    profit_factor REAL NOT NULL DEFAULT 0.0,
    turnover REAL NOT NULL DEFAULT 0.0,
    fee_drag_real_r REAL NOT NULL DEFAULT 0.0,
    psr REAL NOT NULL DEFAULT 0.0,
    deflated_sharpe REAL NOT NULL DEFAULT 0.0,
    net_pnl_r_lcb REAL NOT NULL DEFAULT 0.0,
    net_pnl_r_ucb REAL NOT NULL DEFAULT 0.0,
    is_oos_spread REAL NOT NULL DEFAULT 0.0,
    verdict TEXT NOT NULL DEFAULT ''
);
"""

DDL_REPLAY_RUNS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_replay_runs_created
    ON replay_runs(created_ts);
"""

# One row per (replay run × tier/baseline comparison) — the 3-tier breakdown +
# per-baseline Sharpe spread + the tier verdict note the dashboard chips render.
DDL_BENCHMARK_RESULTS = """
CREATE TABLE IF NOT EXISTS benchmark_results (
    rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    baseline TEXT NOT NULL DEFAULT '',
    sharpe_spread REAL NOT NULL DEFAULT 0.0,
    passed INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
"""

DDL_BENCHMARK_RESULTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_benchmark_results_run
    ON benchmark_results(run_id, tier);
"""

# Measurement-reset baseline (Jin 2026-06-23) — a FORWARD measurement window that
# restarts clean at each main-logic change. One row per stamped reset: the ts the
# new logic went live, the real-fee-net equity baseline at that ts, a human label,
# and the git sha of the batch. The since-reset rollup measures only trades OPENED
# at/after the LATEST reset_ts. OBSERVABILITY-ONLY: never read by sizing/gating/
# exit; old positions/fills are NEVER deleted — only the measurement window moves.
DDL_MEASUREMENT_RESETS = """
CREATE TABLE IF NOT EXISTS measurement_resets (
    reset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_ts INTEGER NOT NULL,
    equity_baseline_usd REAL NOT NULL DEFAULT 0.0,
    label TEXT NOT NULL DEFAULT '',
    git_sha TEXT NOT NULL DEFAULT ''
);
"""

DDL_MEASUREMENT_RESETS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_measurement_resets_ts
    ON measurement_resets(reset_ts DESC);
"""

# ---------------------------------------------------------------------------
# Profit-sweep ladder (vault/50_research/debates/profit_sweep_ladder_2026-07-03.md
# — codex 3-round consensus). Append-only event ledger for the 1단(close)→3단
# (Alpaca auto-draw) capital-recycling pipeline. Balance is a SUM() projection
# over these rows — no aggregate/mutable balance column (crash/replay safe).
#
# event_type: 'CREDIT' (position closed net profit × sweep_pct, one row per
# position — source_position_id UNIQUE makes materialize idempotent),
# 'DRAW' (3단 auto-draw consumed some bucket_available, draw_id UNIQUE),
# 'RELEASE' (a DRAW's reservation released back to the bucket — open position
# AND live/pending order both absent, OR reject/cancel/expiry; NEVER creates
# credit — loss_debit=0 invariant). amount_usd is ALWAYS >= 0; sign is implied
# by event_type when computing the SUM (CREDIT/RELEASE add, DRAW subtracts —
# see ladder.bucket_available_usd). closed_ts=1 dedupe hot-path stays
# write-free at position-close time — CREDIT rows are materialized by a
# separate on-demand (pre-DRAW) + 5-min sweeper pass, never inline in the
# close transaction (feedback_db_lock_is_architecture_signal).
# ---------------------------------------------------------------------------

DDL_LADDER_LEDGER = """
CREATE TABLE IF NOT EXISTS ladder_ledger (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_position_id TEXT,
    draw_id TEXT,
    venue TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL DEFAULT '',
    signal_id TEXT NOT NULL DEFAULT '',
    amount_usd REAL NOT NULL DEFAULT 0.0,
    reason TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL
);
"""

DDL_LADDER_LEDGER_CREDIT_UNIQUE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_ladder_ledger_credit_position
    ON ladder_ledger(source_position_id)
    WHERE event_type = 'CREDIT';
"""

DDL_LADDER_LEDGER_DRAW_UNIQUE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_ladder_ledger_draw_id
    ON ladder_ledger(draw_id)
    WHERE event_type = 'DRAW';
"""

DDL_LADDER_LEDGER_TS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ladder_ledger_ts
    ON ladder_ledger(event_type, created_ts);
"""

# Materializer checkpoint — how far the CREDIT scan has progressed (closed_ts
# watermark). Advances ONLY after a scan batch's INSERTs all succeeded (never
# rewound on partial failure — a crash mid-batch just re-scans the same
# window next pass, safe because source_position_id UNIQUE makes the INSERT
# idempotent).
DDL_LADDER_CREDIT_CHECKPOINT = """
CREATE TABLE IF NOT EXISTS ladder_credit_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_scanned_closed_ts INTEGER NOT NULL DEFAULT 0
);
"""
