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
