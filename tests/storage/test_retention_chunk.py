"""``prune_table_chunk`` — the bounded-DELETE building block for the LIVE
loop's db_writer-routed retention prune (split out of ``test_retention.py`` to
stay under the 500 LOC file cap — [[md_max_60_lines_split]] sibling rule).

Forensic hunt (2026-07-09): the LIVE prune's OWN dedicated autocommit conn was
issuing 11 unwrapped DELETEs back to back, monopolizing the WAL write-lock for
90-140s every ~30min ([[feedback_db_lock_is_architecture_signal]]). This
function is the chunked replacement each rule submits, one bounded call at a
time, to the shared ``DBWriter`` — see ``polaris/scripts/_production_retention.py``
and ``tests/test_production_retention.py`` for the db_writer-routed producer +
the end-to-end lock-0 proof.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from polaris.storage.retention import prune_table_chunk
from polaris.storage.schema import init_db

NOW = 1_782_000_000  # fixed reference epoch-seconds


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(tmp_path / "t.sqlite")
    yield conn
    conn.close()


def _insert_bar(conn: sqlite3.Connection, ts: int, *, interval: str = "1D") -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) "
        "VALUES ('I','G','okx','BTC-USDT',?,?,1,1,1,1,1)",
        (interval, ts),
    )


def _insert_gate_event(conn: sqlite3.Connection, created_ts: int) -> None:
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, gate_id, phase, created_ts) "
        "VALUES (?, 'run', 1, 'eval', ?)",
        (f"ev-{created_ts}", created_ts),
    )


def test_prune_table_chunk_rejects_non_allowlisted() -> None:
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="non-allowlisted"):
        prune_table_chunk(conn, "positions", "ts", 86_400, now_ts=NOW, chunk_rows=10)


def test_prune_table_chunk_rejects_non_positive_chunk_rows(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="chunk_rows must be positive"):
        prune_table_chunk(
            db, "gate_events", "created_ts", 86_400, now_ts=NOW, chunk_rows=0,
        )


def test_prune_table_chunk_bounds_to_chunk_rows(db: sqlite3.Connection) -> None:
    """5 aged rows, chunk_rows=2 -> exactly 2 deleted, 3 remain (bounded call)."""
    for i in range(5):
        _insert_gate_event(db, NOW - 40 * 86_400 - i)  # all outside 30d window
    db.commit()

    n = prune_table_chunk(
        db, "gate_events", "created_ts", 30 * 86_400, now_ts=NOW, chunk_rows=2,
    )

    assert n == 2
    assert db.execute("SELECT COUNT(*) FROM gate_events").fetchone()[0] == 3


def test_prune_table_chunk_looped_drains_fully_keeps_recent(
    db: sqlite3.Connection,
) -> None:
    """Repeated chunk_rows=3 calls fully drain the aged rows; in-window kept."""
    for i in range(10):
        _insert_gate_event(db, NOW - 40 * 86_400 - i)  # outside 30d
    _insert_gate_event(db, NOW)  # inside window -> survives every pass
    db.commit()

    total = 0
    while True:
        n = prune_table_chunk(
            db, "gate_events", "created_ts", 30 * 86_400, now_ts=NOW, chunk_rows=3,
        )
        total += n
        if n < 3:
            break

    assert total == 10
    assert db.execute("SELECT COUNT(*) FROM gate_events").fetchone()[0] == 1


def test_prune_table_chunk_scopes_bar_interval(db: sqlite3.Connection) -> None:
    """Same allowlisted table, different interval scoping — mirrors prune_table."""
    _insert_bar(db, NOW - 40 * 86_400, interval="1m")  # outside 1m's 30d
    _insert_bar(db, NOW - 40 * 86_400, interval="1D")  # inside 1D's 1200d
    db.commit()

    n = prune_table_chunk(
        db, "bars", "ts", 30 * 86_400, now_ts=NOW, chunk_rows=100, bar_interval="1m",
    )

    assert n == 1
    assert db.execute(
        "SELECT COUNT(*) FROM bars WHERE bar_interval = '1D'"
    ).fetchone()[0] == 1
