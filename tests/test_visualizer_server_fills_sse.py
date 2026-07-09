"""``tools.visualizer.server._fills_since`` SSE fill-event tests (DEMO/PAPER,
display-only, READ-ONLY).

Covers the visual-wall director-mode kill-feed extension (2026-07-10): exit
events must carry ``strategy_id`` + the canonical risk-unit ``r_multiple``
(``positions.pnl_r`` via ``fills.contribution_id``), matching the SAME R the
ticker/strategy dashboard panels already show. Nothing here touches
sizing/risk/orders — every table is a throwaway on-disk SQLite fixture read
via a fresh ``mode=ro`` connection, exactly as the running server does.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.visualizer import server


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "fills_sse.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE fills (
            fill_id TEXT PRIMARY KEY, venue TEXT, instrument_id TEXT,
            strategy_id TEXT, side TEXT, pnl_usd REAL, is_close INTEGER,
            ts_ms INTEGER, contribution_id TEXT
        );
        CREATE TABLE positions (
            position_id TEXT PRIMARY KEY, status TEXT, pnl_r REAL
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def _restore_db_path() -> Iterator[None]:
    prev = server._DB_PATH
    yield
    server._DB_PATH = prev


def test_exit_event_carries_strategy_id_and_r_multiple(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO positions VALUES ('pos1', 'closed', 0.09)"
    )
    conn.execute(
        "INSERT INTO fills VALUES "
        "('f1', 'okx', 'okx:BTC-USDT', 'donchian55', 'sell', 12.3, 1, 5000, 'pos1')"
    )
    conn.commit()
    conn.close()
    server._DB_PATH = db_path

    events = server._fills_since(0)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "exit"
    assert ev["strategy_id"] == "donchian55"
    assert ev["r_multiple"] == pytest.approx(0.09)


def test_exit_event_r_multiple_none_when_no_matched_position(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO fills VALUES "
        "('f1', 'okx', 'okx:BTC-USDT', 'donchian55', 'sell', 12.3, 1, 5000, NULL)"
    )
    conn.commit()
    conn.close()
    server._DB_PATH = db_path

    events = server._fills_since(0)
    assert events[0]["r_multiple"] is None


def test_entry_event_unaffected_by_the_join(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO fills VALUES "
        "('f1', 'capital', 'capital:EURUSD', 'donchian55', 'buy', 0.0, 0, 5000, NULL)"
    )
    conn.commit()
    conn.close()
    server._DB_PATH = db_path

    events = server._fills_since(0)
    assert events[0]["type"] == "entry"
    assert events[0]["strategy_id"] == "donchian55"
