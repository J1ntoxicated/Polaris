"""Retention/pruning + WAL hygiene — destructive-op safety tests.

Validates against the REAL schema (``schema.init_db``), not a hand-rolled
fixture, so the prune targets and the protected ledger tables are exactly the
production columns. Covers the adversarial-review properties:

* ledger/state tables are never touched (even ``signals``, which HAS a ``ts``
  column that a naive prune would catch — the allowlist guard blocks it);
* only rows strictly older than the window are deleted, in-window kept;
* exact boundary (== cutoff is kept, < cutoff deleted);
* idempotent (second run deletes 0);
* the reclaiming WAL checkpoint shrinks the -wal file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from polaris.storage.retention import (
    RETENTION_SPEC,
    checkpoint_wal,
    prune_table,
    run_retention,
    run_retention_job,
)
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


def _insert_quote(conn: sqlite3.Connection, ts: int) -> None:
    conn.execute(
        "INSERT INTO quote_ticks (instrument_id, venue, symbol, ts, bid, ask, "
        "mid, spread_bps) VALUES ('I','okx','BTC-USDT',?,1,1,1,1)",
        (ts,),
    )


def _insert_baseline_sample(conn: sqlite3.Connection, ts: int) -> None:
    conn.execute(
        "INSERT INTO ticker_baseline_samples (instrument_id, underlying_group_id, "
        "metric, ts, value) VALUES ('I','G','atr',?,1.0)",
        (ts,),
    )


def _insert_focus(conn: sqlite3.Connection, cycle_ts: int) -> None:
    conn.execute(
        "INSERT INTO watchlist_focus (cycle_ts, venue, symbol, focus_score, "
        "focus_rank, target_bucket) VALUES (?,'okx','BTC-USDT',1.0,1,'core')",
        (cycle_ts,),
    )


def _insert_gate_event(conn: sqlite3.Connection, created_ts: int) -> None:
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, gate_id, phase, created_ts) "
        "VALUES (?, 'run', 1, 'eval', ?)",
        (f"ev-{created_ts}", created_ts),
    )


# --- ledger / state tables: NEVER touched ----------------------------------


def test_ledger_tables_untouched(db: sqlite3.Connection) -> None:
    """positions/fills/signals/measurement_resets/universe survive retention.

    ``signals`` deliberately gets an ancient ``ts`` — far outside any window —
    to prove the allowlist (not a per-table heuristic) is what protects it.
    """
    db.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
        "VALUES ('p1','okx','BTC-USDT','s','s','s','long',1,'closed',1)"
    )
    db.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, ts_ms, order_id) "
        "VALUES ('f1','okx','I','s','buy',1,1,1000,'o1')"
    )
    db.execute(
        "INSERT INTO signals (strategy_id, signal_id, instrument_id, direction, "
        "score, thesis, ts) VALUES ('s','sig1','I','long',1,'t',1)"  # ancient ts
    )
    db.execute(
        "INSERT INTO measurement_resets (reset_ts, label) VALUES (1, 'r')"
    )
    db.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, last_seen_ts) "
        "VALUES ('okx','BTC-USDT','I','G','crypto','USDT','live',1)"
    )
    db.commit()

    run_retention(db, now_ts=NOW)

    for table in ("positions", "fills", "signals", "measurement_resets", "universe"):
        n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n == 1, f"{table} must be preserved"


def test_prune_table_rejects_non_allowlisted() -> None:
    """A ledger table can never reach a DELETE through prune_table."""
    conn = sqlite3.connect(":memory:")
    for bad in ("signals", "positions", "fills", "orders", "universe"):
        with pytest.raises(ValueError, match="non-allowlisted"):
            prune_table(conn, bad, "ts", 86_400, now_ts=NOW)


def test_prune_table_rejects_wrong_column() -> None:
    """Right table, wrong ts column is also refused (no column injection)."""
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="non-allowlisted"):
        prune_table(conn, "bars", "opened_ts", 86_400, now_ts=NOW)


# --- window correctness -----------------------------------------------------


def test_window_keeps_inside_deletes_outside(db: sqlite3.Connection) -> None:
    for rule in RETENTION_SPEC:
        cutoff = NOW - rule.retain_sec
        inserter = {
            "bars": _insert_bar,
            "quote_ticks": _insert_quote,
            "ticker_baseline_samples": _insert_baseline_sample,
            "watchlist_focus": _insert_focus,
            "gate_events": _insert_gate_event,
        }[rule.table]
        inserter(db, cutoff - 1)   # strictly older -> deleted
        inserter(db, cutoff)       # exactly at cutoff -> kept (< is the test)
        inserter(db, cutoff + 1)   # newer -> kept
        inserter(db, NOW)          # now -> kept
    db.commit()

    deleted = run_retention(db, now_ts=NOW)

    for rule in RETENTION_SPEC:
        cutoff = NOW - rule.retain_sec
        assert deleted[rule.table] == 1, f"{rule.table}: exactly one old row"
        rows = [
            r[0]
            for r in db.execute(
                f"SELECT {rule.ts_column} FROM {rule.table}"  # noqa: S608 test-local
            ).fetchall()
        ]
        assert cutoff - 1 not in rows, f"{rule.table}: old row gone"
        assert sorted(rows) == [cutoff, cutoff + 1, NOW], f"{rule.table}: in-window kept"


def test_idempotent_second_run_deletes_zero(db: sqlite3.Connection) -> None:
    _insert_bar(db, NOW - 500 * 86_400)         # outside 400d
    _insert_quote(db, NOW - 3 * 3600)           # outside 2h
    _insert_gate_event(db, NOW - 40 * 86_400)   # outside 30d
    db.commit()

    first = run_retention(db, now_ts=NOW)
    assert first["bars"] == 1
    assert first["quote_ticks"] == 1
    assert first["gate_events"] == 1

    second = run_retention(db, now_ts=NOW)
    assert all(v == 0 for v in second.values()), second


def test_signal_lookback_bars_preserved(db: sqlite3.Connection) -> None:
    """A 330d-old 1D bar (deepest live lookback) MUST survive."""
    _insert_bar(db, NOW - 330 * 86_400, interval="1D")
    db.commit()
    run_retention(db, now_ts=NOW)
    assert db.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1


# --- WAL checkpoint ---------------------------------------------------------


def test_checkpoint_wal_shrinks_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "wal.sqlite"
    conn = init_db(db_path)
    for i in range(5000):
        _insert_quote(conn, NOW + i)
    conn.commit()
    wal_path = Path(str(db_path) + "-wal")
    grew = wal_path.exists() and wal_path.stat().st_size > 0
    conn.close()  # close so the reclaiming checkpoint can take the exclusive lock

    busy, _log, checkpointed = checkpoint_wal(db_path)

    assert busy == 0
    assert checkpointed >= 0
    if grew:
        after = wal_path.stat().st_size if wal_path.exists() else 0
        assert after == 0, "reclaiming checkpoint should truncate the -wal file"


def test_run_retention_job_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "job.sqlite"
    conn = init_db(db_path)
    _insert_bar(conn, NOW - 500 * 86_400)
    _insert_bar(conn, NOW)
    _insert_quote(conn, NOW - 3 * 3600)
    conn.commit()
    conn.close()

    result = run_retention_job(db_path, now_ts=NOW)

    assert result["bars"] == 1
    assert result["quote_ticks"] == 1
    assert "__wal_checkpointed__" in result

    check = sqlite3.connect(str(db_path))
    try:
        assert check.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1
    finally:
        check.close()
