"""Virtual-account measurement DDL — weekly equity trace + ruin events.

DEMO/PAPER measurement hygiene (Jin 2026-07-07): a $100k-per-exchange virtual
seed, a Monday-anchored (UTC) weekly PnL trace per exchange (non-destructive —
TRACE never RESETS the compounding account), and a reset-only-on-ruin ledger
(flags a blowup + re-seeds that exchange's virtual balance, never blocks a
trade). Split into its own module (not ``schema_ddl_ext.py``, already 850+
LOC) per the ≤500 LOC file budget.
"""

from __future__ import annotations

# ``weekly_equity_curve`` — one row per (exchange, week_start_ts). Upserted on
# every close (+ periodically for unrealized) WITHOUT touching the running
# account: the account compounds continuously across week boundaries; this
# row only marks "this week so far" per exchange. week_start_ts = Monday
# 00:00 UTC of the week (the Monday anchor), so crossing Monday starts a NEW
# row instead of overwriting the prior week (trace != reset).
DDL_WEEKLY_EQUITY_CURVE = """
CREATE TABLE IF NOT EXISTS weekly_equity_curve (
    exchange TEXT NOT NULL,
    week_start_ts INTEGER NOT NULL,
    start_equity REAL NOT NULL DEFAULT 0.0,
    current_equity REAL NOT NULL DEFAULT 0.0,
    realized_pnl_usd REAL NOT NULL DEFAULT 0.0,
    unrealized_pnl_usd REAL NOT NULL DEFAULT 0.0,
    trades INTEGER NOT NULL DEFAULT 0,
    updated_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (exchange, week_start_ts)
);
"""

DDL_WEEKLY_EQUITY_CURVE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_weekly_equity_curve_week
    ON weekly_equity_curve(week_start_ts DESC);
"""

# ``virtual_ruin_events`` — append-only flag ledger. A ruin (virtual equity
# fell below the floor) never blocks/skips a trade; it only logs a WARNING,
# records this row, and re-seeds that exchange's virtual balance so
# measurement continues. Never deleted — a full history of blowups per
# exchange for the "this leg failed, here is when" signal.
DDL_VIRTUAL_RUIN_EVENTS = """
CREATE TABLE IF NOT EXISTS virtual_ruin_events (
    ruin_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    ruined_ts INTEGER NOT NULL,
    low_equity REAL NOT NULL DEFAULT 0.0,
    week_start_ts INTEGER NOT NULL DEFAULT 0,
    reseeded_to REAL NOT NULL DEFAULT 0.0
);
"""

DDL_VIRTUAL_RUIN_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_virtual_ruin_events_exchange
    ON virtual_ruin_events(exchange, ruined_ts DESC);
"""
