"""Polaris SQLite DDL — Layer 0/1/4/7 table + index definitions.

Raw ``CREATE TABLE/INDEX IF NOT EXISTS`` strings consumed by ``schema.ALL_DDL``.
Split out of ``schema.py`` (DDL-only, no logic) to keep each module ≤500 LOC.
Spec sources: vault/30_components/layer-{0,1,4,7}-*.md + ADR-003 unified schema.
"""

from __future__ import annotations

# Layer 0 — Universe Discovery
DDL_UNIVERSE = """
CREATE TABLE IF NOT EXISTS universe (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    product_class TEXT NOT NULL DEFAULT '',
    stream_id TEXT NOT NULL DEFAULT '',
    quote_ccy TEXT NOT NULL,
    state TEXT NOT NULL,
    vol_24h_usd REAL NOT NULL DEFAULT 0.0,
    spread_bps REAL NOT NULL DEFAULT 0.0,
    atr_24h_pct REAL NOT NULL DEFAULT 0.0,
    depth_10bps_usd REAL NOT NULL DEFAULT 0.0,
    signal_density_7d REAL NOT NULL DEFAULT 0.0,
    last_price REAL NOT NULL DEFAULT 0.0,
    listing_ts INTEGER,
    last_seen_ts INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    active_reason TEXT,
    PRIMARY KEY (venue, symbol)
);
"""

DDL_WATCHLIST_FOCUS = """
CREATE TABLE IF NOT EXISTS watchlist_focus (
    cycle_ts INTEGER NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    focus_score REAL NOT NULL,
    focus_rank INTEGER NOT NULL,
    target_bucket TEXT NOT NULL,
    evict_reason TEXT,
    -- Increment 1 (2026-06-24): EntranceJudge persistence. ``opportunity_score``
    -- is the [0,1] multi-lens entrance judgment; ``trade_eligible`` (DEFAULT 1 =
    -- flow-preserving: a row with no judgment yet stays eligible) decouples the
    -- WATCH set (full focus) from the TRADE set (eligible subset). Neither is a
    -- sizing input (9-stack untouched). Nullable score = legacy/un-judged rows.
    opportunity_score REAL,
    trade_eligible INTEGER NOT NULL DEFAULT 1,
    -- STAGE 1 (rank-attention gradient 2026-06-24): ``tier`` ∈ {S,A,B,T} is the
    -- observation-CADENCE band from the single merit-rank percentile. It governs
    -- HOW OFTEN a watched row is polled/eval'd (S/A every cycle, B every K, T
    -- every M), NEVER membership (every active row is watched). DEFAULT 'T' keeps
    -- a legacy/un-tiered row at the tail cadence (flow_not_block; not a sizing input).
    tier TEXT NOT NULL DEFAULT 'T',
    PRIMARY KEY (cycle_ts, venue, symbol)
);
"""

# Layer 1 — Canonical Market Model + Ticker Baseline
DDL_BARS = """
CREATE TABLE IF NOT EXISTS bars (
    instrument_id TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bar_interval TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    notional_usd REAL NOT NULL DEFAULT 0.0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    vwap REAL NOT NULL DEFAULT 0.0,
    bid_close REAL NOT NULL DEFAULT 0.0,
    ask_close REAL NOT NULL DEFAULT 0.0,
    spread_bps_close REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT 'rest',
    PRIMARY KEY (instrument_id, bar_interval, ts)
);
"""

DDL_BARS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_bars_venue_symbol
    ON bars(venue, symbol, bar_interval, ts DESC);
"""

# Last-price lookup index: the dashboard's per-instrument MAX(ts) close lookup
# (`_last_prices`) groups by instrument_id; without an (instrument_id, ts) index
# it scans the whole 10^5-row PK. Read-only/display path.
DDL_BARS_INSTRUMENT_TS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_bars_instrument_ts
    ON bars(instrument_id, ts);
"""

# Tick-stream decouple (design: vault/50_research/debates/tick_stream_decouple_2026-06-24.md).
# SINGLE-ROW last-write-wins: PK=instrument_id (was (instrument_id, ts)). The old
# append-per-tick PK grew the table unbounded (645k rows / 215MB → ENOSPC) and
# forced a 15s retention DELETE that lock-contended the 1Hz INSERT writer. The
# only consumers (Dashboard + Sentinel-S1) read the LATEST-per-instrument row, so
# collapsing to one row per instrument is byte-equivalent for them while the
# table can no longer grow and the prune-DELETE contention is structurally gone.
# Per-tick rate / flow-size liveness (Sentinel-S6) moved to ``tick_inflow``.
DDL_QUOTE_TICKS = """
CREATE TABLE IF NOT EXISTS quote_ticks (
    instrument_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    mid REAL NOT NULL,
    spread_bps REAL NOT NULL,
    bid_size REAL NOT NULL DEFAULT 0.0,
    ask_size REAL NOT NULL DEFAULT 0.0,
    last_trade_price REAL NOT NULL DEFAULT 0.0,
    last_trade_size REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT 'rest',
    PRIMARY KEY (instrument_id)
);
"""

# Tick-stream decouple (design: vault/50_research/debates/tick_stream_decouple_2026-06-24.md).
# Bounded per-venue inflow scalars for Sentinel S6 (tick-rate + flow-size death),
# UPSERTed 1Hz by the writer from in-mem rolling buckets — replaces the full-table
# COUNT-over-quote_ticks scan so quote_ticks can collapse to single-row last-write-wins.
# window_started_at lets S6a report WARMING for ~600s after a restart (no silent OK/FAIL).
DDL_TICK_INFLOW = """
CREATE TABLE IF NOT EXISTS tick_inflow (
    venue TEXT PRIMARY KEY,
    last_tick_ts INTEGER NOT NULL,
    ticks_600s INTEGER NOT NULL DEFAULT 0,
    max_flow_size_600s REAL NOT NULL DEFAULT 0.0,
    window_started_at INTEGER NOT NULL DEFAULT 0
);
"""

DDL_TICKER_BASELINE_STATE = """
CREATE TABLE IF NOT EXISTS ticker_baseline_state (
    instrument_id TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    asset_class TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL,
    baseline_p50 REAL NOT NULL,
    baseline_p75 REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    lookback_sec INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, metric)
);
"""

DDL_TICKER_BASELINE_INDEX_GROUP = """
CREATE INDEX IF NOT EXISTS idx_baseline_group_metric
    ON ticker_baseline_state(underlying_group_id, metric);
"""

DDL_TICKER_BASELINE_INDEX_CLASS = """
CREATE INDEX IF NOT EXISTS idx_baseline_class_metric
    ON ticker_baseline_state(asset_class, metric);
"""

DDL_TICKER_BASELINE_SAMPLES = """
CREATE TABLE IF NOT EXISTS ticker_baseline_samples (
    instrument_id TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    ts INTEGER NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (instrument_id, metric, ts)
);
"""

DDL_MARKET_EVENTS = """
CREATE TABLE IF NOT EXISTS market_events (
    ts INTEGER NOT NULL,
    type TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (ts, type, venue, symbol)
);
"""

# Hot-path indexes for universe reads.
DDL_UNIVERSE_INDEX_ACTIVE = """
CREATE INDEX IF NOT EXISTS idx_universe_active_venue
    ON universe(is_active, venue);
"""

DDL_UNIVERSE_INDEX_GROUP = """
CREATE INDEX IF NOT EXISTS idx_universe_group
    ON universe(underlying_group_id);
"""

DDL_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    strategy_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    correlation_group TEXT,
    direction TEXT NOT NULL,
    score REAL NOT NULL,
    thesis TEXT NOT NULL,
    ts INTEGER NOT NULL,
    payload_json TEXT,
    PRIMARY KEY (strategy_id, signal_id)
);
"""

# ---------------------------------------------------------------------------
# Layer 4 — Cell Matrix (4-dim P0 + parent3 + parent2 + shadow context)
# Spec source: vault/30_components/layer-4-cell-matrix.md (Schema)
# ADR-006 patches: n_eff (EWMA-decayed), ewma_score, parent3_score, parent2_score
# ---------------------------------------------------------------------------

DDL_CELL_MATRIX_P0 = """
CREATE TABLE IF NOT EXISTS cell_matrix_p0 (
    exchange TEXT NOT NULL,
    strategy TEXT NOT NULL,
    ticker TEXT NOT NULL,
    regime TEXT NOT NULL,
    n_eff REAL NOT NULL DEFAULT 0.0,
    wins_eff REAL NOT NULL DEFAULT 0.0,
    pnl_r_sum_eff REAL NOT NULL DEFAULT 0.0,
    avg_pnl_r REAL NOT NULL DEFAULT 0.0,
    score REAL NOT NULL DEFAULT 0.0,
    last_closed_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (exchange, strategy, ticker, regime)
);
"""

DDL_CELL_MATRIX_PARENT3 = """
CREATE TABLE IF NOT EXISTS cell_matrix_parent3 (
    exchange TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    n_eff REAL NOT NULL DEFAULT 0.0,
    pnl_r_sum_eff REAL NOT NULL DEFAULT 0.0,
    avg_pnl_r REAL NOT NULL DEFAULT 0.0,
    score REAL NOT NULL DEFAULT 0.0,
    last_closed_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (exchange, strategy, regime)
);
"""

DDL_CELL_MATRIX_PARENT2 = """
CREATE TABLE IF NOT EXISTS cell_matrix_parent2 (
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    n_eff REAL NOT NULL DEFAULT 0.0,
    pnl_r_sum_eff REAL NOT NULL DEFAULT 0.0,
    avg_pnl_r REAL NOT NULL DEFAULT 0.0,
    score REAL NOT NULL DEFAULT 0.0,
    last_closed_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy, regime)
);
"""

DDL_CELL_MATRIX_SHADOW = """
CREATE TABLE IF NOT EXISTS cell_matrix_shadow_context (
    exchange TEXT NOT NULL,
    strategy TEXT NOT NULL,
    ticker TEXT NOT NULL,
    regime TEXT NOT NULL,
    grp TEXT NOT NULL,
    session TEXT NOT NULL,
    n_eff REAL NOT NULL DEFAULT 0.0,
    pnl_r_sum_eff REAL NOT NULL DEFAULT 0.0,
    avg_pnl_r REAL NOT NULL DEFAULT 0.0,
    score REAL NOT NULL DEFAULT 0.0,
    last_closed_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (exchange, strategy, ticker, regime, grp, session)
);
"""

# ---------------------------------------------------------------------------
# Layer 7 — Isolation Primitives (halts + faults + reservations + intents)
# Spec source: vault/30_components/layer-7-strategy-isolation.md (Schema)
# ---------------------------------------------------------------------------

DDL_STRATEGY_HALTS = """
CREATE TABLE IF NOT EXISTS strategy_halts (
    halt_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    opened_ts INTEGER NOT NULL,
    unblock_ts INTEGER,
    reset_by TEXT,
    reset_ts INTEGER,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""

DDL_STRATEGY_HALTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_strategy_halts_active
    ON strategy_halts(strategy_id, opened_ts DESC);
"""

DDL_STRATEGY_FAULT_EVENTS = """
CREATE TABLE IF NOT EXISTS strategy_fault_events (
    event_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    fault_type TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""

DDL_STRATEGY_FAULT_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_strategy_faults_lookup
    ON strategy_fault_events(strategy_id, fault_type, event_ts DESC);
"""

DDL_ALLOCATOR_RESERVATIONS = """
CREATE TABLE IF NOT EXISTS allocator_reservations (
    reservation_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    correlation_group TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    order_key TEXT NOT NULL,
    requested_notional REAL NOT NULL,
    requested_risk REAL NOT NULL,
    status TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    expires_ts INTEGER NOT NULL,
    confirmed_ts INTEGER,
    released_ts INTEGER,
    venue_order_ref TEXT
);
"""

DDL_ALLOCATOR_RESERVATIONS_KEY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_allocator_pending_order_key
    ON allocator_reservations(order_key)
    WHERE status IN ('pending', 'confirmed');
"""

DDL_ORDER_INTENTS = """
CREATE TABLE IF NOT EXISTS order_intents (
    order_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_ts INTEGER NOT NULL,
    side TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    venue_client_id TEXT,
    venue_order_id TEXT,
    created_ts INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL
);
"""

DDL_ORDER_INTENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_order_intents_strategy
    ON order_intents(strategy_id, created_ts DESC);
"""

DDL_POSITIONS = """
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL DEFAULT '',
    product_class TEXT NOT NULL DEFAULT '',
    stream_id TEXT NOT NULL DEFAULT '',
    signal_id TEXT NOT NULL DEFAULT '',
    strategy_id TEXT NOT NULL,
    entry_strategy_id TEXT NOT NULL,
    active_strategy_id TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    status TEXT NOT NULL,
    opened_ts INTEGER NOT NULL DEFAULT 0,
    closed_ts INTEGER,
    swap_count INTEGER NOT NULL DEFAULT 0,
    stop_price REAL,
    peak_price REAL,
    trough_price REAL,
    mfe_r REAL,
    mae_r REAL,
    exit_state TEXT DEFAULT 'open',
    deal_id TEXT,
    entry_atr_pct REAL,
    entry_atr_timeframe TEXT,
    pnl_r REAL,
    risk_usd REAL,
    entry_regime TEXT,
    exit_cadence TEXT
);
"""

DDL_POSITIONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_positions_strategy_status
    ON positions(strategy_id, status);
"""

DDL_ORDERS = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    order_key TEXT NOT NULL,
    venue_client_id TEXT,
    venue_order_id TEXT,
    status TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_ts INTEGER NOT NULL DEFAULT 0,
    updated_ts INTEGER NOT NULL DEFAULT 0
);
"""

DDL_ORDERS_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_strategy_order_key
    ON orders(strategy_id, order_key);
"""

DDL_RISK_EVENTS = """
CREATE TABLE IF NOT EXISTS risk_events (
    risk_event_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
"""

DDL_RISK_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_risk_events_strategy
    ON risk_events(strategy_id, created_ts DESC);
"""
