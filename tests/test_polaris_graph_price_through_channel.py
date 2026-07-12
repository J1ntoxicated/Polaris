"""price_through_shadow join into polaris_graph's SCOUT SHADOW channel gauge.

DEMO/PAPER, display-only. Confirms ``_query_shadow_channels`` exposes the
price-through shadow (maker_fill_sim R1 2026-07-12 debate) with the SAME
n/target/fresh_ts shape every sibling channel uses, plus the traded-through%/
avg-price-improve-bps pair. Read-only — never gates/sizes/throttles a trade.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema import ALL_DDL
from tools.visualizer import polaris_graph as pg


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "shadow_channels.sqlite"
    conn = sqlite3.connect(db_path)
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    conn.execute(
        "INSERT INTO price_through_shadow "
        "(event_id, run_id, strategy_id, venue, symbol, side, fill_px, "
        " touch_px, current_fee_bps, created_ts) "
        "VALUES ('e1', 'r', 's', 'okx', 'BTC-USDT', 'buy', 100.0, 99.0, "
        " 10.0, 1000)",
    )
    conn.commit()
    conn.close()
    return db_path


def test_shadow_channels_reports_price_through_shape(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    pg._shadow_channels_cache["ts"] = 0.0  # force a fresh read past the TTL
    out = pg._query_shadow_channels(db_path)
    assert "price_through_shadow" in out
    row = out["price_through_shadow"]
    assert row["n"] == 1
    assert row["fresh_ts"] == 1000
    assert row["target"] is None
    assert "traded_through_pct" in row
    assert "avg_price_improve_bps" in row


def test_shadow_channels_missing_db_degrades_gracefully(tmp_path: Path) -> None:
    pg._shadow_channels_cache["ts"] = 0.0
    out = pg._query_shadow_channels(tmp_path / "does-not-exist.sqlite")
    assert out["price_through_shadow"]["n"] is None
