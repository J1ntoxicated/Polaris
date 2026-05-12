"""SQLite schema bootstrap for Polaris (`data/polaris.sqlite`).

Layer 0 + Layer 1 P0 schema. Idempotent: `init_db()` runs `CREATE TABLE IF NOT EXISTS`.

Spec sources:
- vault/30_components/layer-0-universe-discovery.md (Schema)
- vault/30_components/layer-1-canonical-baseline.md (Schema)
- vault/10_decisions/ADR-003-8-layer-architecture.md (Unified SQLite Schema)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path("data/polaris.sqlite")

# Layer 0 — Universe Discovery
DDL_UNIVERSE = """
CREATE TABLE IF NOT EXISTS universe (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    quote_ccy TEXT NOT NULL,
    state TEXT NOT NULL,
    vol_24h_usd REAL NOT NULL DEFAULT 0.0,
    spread_bps REAL NOT NULL DEFAULT 0.0,
    atr_24h_pct REAL NOT NULL DEFAULT 0.0,
    depth_10bps_usd REAL NOT NULL DEFAULT 0.0,
    signal_density_7d REAL NOT NULL DEFAULT 0.0,
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
    PRIMARY KEY (instrument_id, ts)
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
    strategy_id TEXT NOT NULL,
    entry_strategy_id TEXT NOT NULL,
    active_strategy_id TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    status TEXT NOT NULL,
    opened_ts INTEGER NOT NULL DEFAULT 0,
    closed_ts INTEGER,
    swap_count INTEGER NOT NULL DEFAULT 0
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
    payload_json TEXT,
    error_text TEXT,
    created_ts INTEGER NOT NULL
);
"""

DDL_GATE_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_gate_events_run
    ON gate_events(run_id, gate_id, created_ts);
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

ALL_DDL: tuple[str, ...] = (
    DDL_UNIVERSE,
    DDL_UNIVERSE_INDEX_ACTIVE,
    DDL_UNIVERSE_INDEX_GROUP,
    DDL_WATCHLIST_FOCUS,
    DDL_BARS,
    DDL_BARS_INDEX,
    DDL_QUOTE_TICKS,
    DDL_TICKER_BASELINE_STATE,
    DDL_TICKER_BASELINE_INDEX_GROUP,
    DDL_TICKER_BASELINE_INDEX_CLASS,
    DDL_TICKER_BASELINE_SAMPLES,
    DDL_MARKET_EVENTS,
    DDL_SIGNALS,
    # Layer 4 — Cell Matrix
    DDL_CELL_MATRIX_P0,
    DDL_CELL_MATRIX_PARENT3,
    DDL_CELL_MATRIX_PARENT2,
    DDL_CELL_MATRIX_SHADOW,
    # Layer 7 — Isolation
    DDL_STRATEGY_HALTS,
    DDL_STRATEGY_HALTS_INDEX,
    DDL_STRATEGY_FAULT_EVENTS,
    DDL_STRATEGY_FAULT_EVENTS_INDEX,
    DDL_ALLOCATOR_RESERVATIONS,
    DDL_ALLOCATOR_RESERVATIONS_KEY_INDEX,
    DDL_ORDER_INTENTS,
    DDL_ORDER_INTENTS_INDEX,
    DDL_POSITIONS,
    DDL_POSITIONS_INDEX,
    DDL_ORDERS,
    DDL_ORDERS_INDEX,
    DDL_RISK_EVENTS,
    DDL_RISK_EVENTS_INDEX,
    # Layer 2 — Pipeline
    DDL_GATE_EVENTS,
    DDL_GATE_EVENTS_INDEX,
    DDL_AI_LESSONS,
    DDL_AI_LESSONS_INDEX,
    DDL_POSITION_STRATEGY_SEGMENTS,
    DDL_POSITION_STRATEGY_SEGMENTS_INDEX,
    # Layer 3 — Sizing risk state
    DDL_STRATEGY_RISK_STATE,
    DDL_POSITION_RISK_STATE,
    # Layer 5 — Learner Network
    DDL_LEARNER_STATE,
    DDL_LEARNER_BLOCKS,
    DDL_LEARNER_BLOCKS_INDEX,
    DDL_LEARNER_SNAPSHOT,
    DDL_ROLLBACK_CANDIDATES,
    # Layer 6 — Live Recalc
    DDL_POSITION_LIVE_RECALC_STATE,
    DDL_REGIME_STATE,
    DDL_POSITION_CONVICTION_LAYERS,
    DDL_POSITION_CONVICTION_LAYERS_INDEX,
    # Layer 1 — Fill ledger (Day 6)
    DDL_FILLS,
    DDL_FILLS_INDEX_TS,
    DDL_FILLS_INDEX_VENUE_SYMBOL,
    DDL_FILLS_INDEX_ORDER,
)


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with foreign keys enabled."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create all tables/indexes if not already present and return a live connection.

    Idempotent ALTER TABLE migrations are applied for columns added after the
    initial CREATE — SQLite does not honour additions inside ``CREATE TABLE IF
    NOT EXISTS`` once the table exists.
    """
    conn = connect(db_path)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    _apply_post_migrations(conn)
    return conn


def _apply_post_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced after their parent table existed (idempotent)."""
    # positions.swap_count — added Day 4 (Layer 6 strategy swap).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "swap_count" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN swap_count INTEGER NOT NULL DEFAULT 0"
        )
    # positions.underlying_group_id — added Day 4 R2 (Layer 6 regime SSOT key
    # is (venue, underlying_group_id); without this column swap segments lose
    # attribution context).
    if "underlying_group_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN "
            "underlying_group_id TEXT NOT NULL DEFAULT ''"
        )
    # positions.entry_strategy_id + active_strategy_id — added Day 4
    # (Layer 6 §Q3 strategy-swap invariants). Pre-Day-4 DBs that
    # bootstrapped the table prior to these additions need an idempotent
    # ALTER so the swap apply path does not fail with no-such-column.
    if "entry_strategy_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN "
            "entry_strategy_id TEXT NOT NULL DEFAULT ''"
        )
    if "active_strategy_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN "
            "active_strategy_id TEXT NOT NULL DEFAULT ''"
        )
    # Backfill runs unconditionally — partially-migrated DBs that already
    # had the columns but kept the legacy ``''`` defaults must also be
    # repaired (codex Day 4 R5 fix). Layer 6 §Q3 invariant requires both
    # ``entry_strategy_id`` and ``active_strategy_id`` to be populated for
    # any open position. Idempotent.
    conn.execute(
        "UPDATE positions SET entry_strategy_id = strategy_id "
        "WHERE entry_strategy_id = '' AND strategy_id != ''"
    )
    conn.execute(
        "UPDATE positions SET active_strategy_id = strategy_id "
        "WHERE active_strategy_id = '' AND strategy_id != ''"
    )
    # position_strategy_segments.regime_at_start + attribution_weight —
    # added Day 4 R2 (Layer 6 §Q3 attribution + segment-per-regime spec).
    seg_cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(position_strategy_segments)"
        ).fetchall()
    }
    if seg_cols and "regime_at_start" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN regime_at_start TEXT NOT NULL DEFAULT ''"
        )
    if seg_cols and "attribution_weight" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN attribution_weight REAL NOT NULL DEFAULT 0.0"
        )
