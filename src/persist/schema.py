"""SQL schema definitions — Polaris trade ledger (P6 pure).

Version 1 (Phase 9, 2026-05-05) — initial unified ledger.

Tables:
    schema_version  — single-row version tracker
    positions       — all positions (open + closed) with full metadata
    balances        — per (hypo_id, ticker) cash + summary stats
    portfolio_snapshots — periodic equity / drawdown rolling state
    spread_skip_log — hourly aggregation of SPREAD-SKIP events
"""
from __future__ import annotations

SCHEMA_VERSION: int = 1

# WAL mode + busy timeout for concurrent reads (dashboard) + single asyncio writer.
PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
)

DDL_SCHEMA_VERSION: str = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""

DDL_POSITIONS: str = """
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    hypo_id TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    ticker TEXT NOT NULL,
    direction INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'closed')),

    -- entry
    entry_price REAL NOT NULL,
    entry_size_usd REAL NOT NULL,
    open_ts_ms INTEGER NOT NULL,
    entry_slip_bps REAL DEFAULT 0,

    -- exit (NULL while open)
    exit_price REAL,
    close_ts_ms INTEGER,
    exit_slip_bps REAL,
    exit_reason TEXT,

    -- accounting (computed at close)
    fee_round_trip REAL NOT NULL,
    gross_usd REAL,
    fee_usd REAL,
    net_usd REAL,

    -- metadata
    signal_confidence REAL,
    signal_reason TEXT,
    regime TEXT,

    -- audit
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
"""

DDL_BALANCES: str = """
CREATE TABLE IF NOT EXISTS balances (
    hypo_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    starting_usd REAL NOT NULL,
    cash_usd REAL NOT NULL,
    total_realized_usd REAL NOT NULL DEFAULT 0,
    total_n_trades INTEGER NOT NULL DEFAULT 0,
    total_wins INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (hypo_id, ticker)
);
"""

DDL_PORTFOLIO_SNAPSHOTS: str = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    ts_ms INTEGER PRIMARY KEY,
    total_equity_usd REAL NOT NULL,
    total_open_count INTEGER NOT NULL DEFAULT 0,
    total_realized_usd REAL NOT NULL DEFAULT 0,
    drawdown_pct REAL NOT NULL DEFAULT 0,
    active_hypos TEXT
);
"""

DDL_SPREAD_SKIP_LOG: str = """
CREATE TABLE IF NOT EXISTS spread_skip_log (
    hour_ts_ms INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    hypo_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    avg_spread_bps REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_ts_ms, ticker, hypo_id)
);
"""

INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_positions_hypo ON positions(hypo_id, ticker, status)",
    "CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, ticker)",
    "CREATE INDEX IF NOT EXISTS idx_positions_close_ts ON positions(close_ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_positions_open_ts ON positions(open_ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON portfolio_snapshots(ts_ms DESC)",
)

ALL_DDL: tuple[str, ...] = (
    DDL_SCHEMA_VERSION,
    DDL_POSITIONS,
    DDL_BALANCES,
    DDL_PORTFOLIO_SNAPSHOTS,
    DDL_SPREAD_SKIP_LOG,
)
