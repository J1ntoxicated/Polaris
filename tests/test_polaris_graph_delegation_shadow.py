"""delegation_shadow join into polaris_graph's SCOUT SHADOW channel gauge.

DEMO/PAPER, display-only. Confirms ``_query_shadow_channels`` exposes the
delegation-gate shadow (P2b, vault/50_research/delegation-gate-blueprint.md)
with the SAME n/target/fresh_ts shape every sibling channel uses, plus the
mismatch%/ambiguous% pair. Read-only — never gates/sizes/throttles a trade.

storage-split: ``delegation_shadow`` is marketdata-domain — read from the
trading db_path's marketdata SIBLING file, not ``db_path`` itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema import ALL_DDL
from polaris.storage.schema_marketdata import marketdata_db_path_for
from tools.visualizer import polaris_graph as pg


def _make_db(tmp_path: Path, *, rows: list[tuple[str, int, int]]) -> Path:
    db_path = tmp_path / "shadow_channels.sqlite"
    conn = sqlite3.connect(db_path)
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    conn.commit()
    conn.close()
    md_path = marketdata_db_path_for(db_path)
    md_conn = sqlite3.connect(md_path)
    for stmt in ALL_DDL:
        md_conn.executescript(stmt)
    for event_id, mismatch, ambiguous in rows:
        md_conn.execute(
            "INSERT INTO delegation_shadow "
            "(event_id, run_id, venue, symbol, dispatched_strategy_id, "
            " fit_top_strategy_id, mismatch, ambiguous, created_ts) "
            "VALUES (?, 'r', 'capital', 'XAUUSD', 'a', 'b', ?, ?, 1000)",
            (event_id, mismatch, ambiguous),
        )
    md_conn.commit()
    md_conn.close()
    return db_path


def test_shadow_channels_reports_delegation_shape(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path, rows=[("e1", 1, 0), ("e2", 0, 1)])
    pg._shadow_channels_cache["ts"] = 0.0  # force a fresh read past the TTL
    out = pg._query_shadow_channels(db_path)
    assert "delegation_shadow" in out
    ch = out["delegation_shadow"]
    assert ch["n"] == 2
    assert ch["fresh_ts"] == 1000
    assert ch["mismatch_pct"] == 50.0
    assert ch["ambiguous_pct"] == 50.0


def test_shadow_channels_no_rows_leaves_pct_none(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path, rows=[])
    pg._shadow_channels_cache["ts"] = 0.0
    out = pg._query_shadow_channels(db_path)
    ch = out["delegation_shadow"]
    assert ch["n"] == 0
    assert ch["mismatch_pct"] is None
    assert ch["ambiguous_pct"] is None
