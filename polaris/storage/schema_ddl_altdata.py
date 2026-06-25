"""Polaris SQLite DDL — alt-data EVIDENCE snapshot table (#6).

Additive table only. Holds a per-source raw alt-data snapshot row each time the
``_altdata_producer`` refreshes a collector. This is read-only EVIDENCE context
(news/macro/funding/F&G); it NEVER drives sizing, blocking, exits, or halts.

Spec source: Layer-6 alt-data wire (#6) — SIGNAL/EVIDENCE only.
"""

from __future__ import annotations

DDL_ALTDATA_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS altdata_snapshot (
    ts INTEGER NOT NULL,
    source TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (ts, source)
);
"""

DDL_ALTDATA_SNAPSHOT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_altdata_snapshot_source
    ON altdata_snapshot(source, ts DESC);
"""

# STEP① static-ground (2026-06-25): per-ACTIVE-TICKER sentiment/event ground.
# ``altdata_snapshot`` is keyed (ts, source) — a GLOBAL audit trail, not per
# ticker. The candidate sweep (②) needs a per-active-ticker ground input: for
# every active instrument, the fused sentiment/event EVIDENCE (or a graceful
# EMPTY row when no source covers it). One LWW row per instrument (PK =
# instrument_id) — bounded by universe size, never grows unbounded. EVIDENCE
# only: this table is read by the candidate sweep as SIGNAL ground; it NEVER
# drives sizing / blocking / exits / halts (flow_not_block). ``has_sentiment`` /
# ``has_event`` are 0/1 presence flags so the sweep can cheaply tell a covered
# ticker from a graceful-empty one without parsing the JSON.
DDL_TICKER_GROUND = """
CREATE TABLE IF NOT EXISTS ticker_ground (
    instrument_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    underlying_group_id TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    updated_ts INTEGER NOT NULL,
    has_sentiment INTEGER NOT NULL DEFAULT 0,
    has_event INTEGER NOT NULL DEFAULT 0,
    ground_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (instrument_id)
);
"""

DDL_TICKER_GROUND_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ticker_ground_group
    ON ticker_ground(underlying_group_id);
"""
