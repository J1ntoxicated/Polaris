"""Wave 1 — emitted L1 signals are PERSISTED to the signals table (observability).

DEMO/PAPER. The signals table was EMPTY (0 rows) — G2 emit was log-only, so
dashboards + learners could not see strategy output. Each EMITTED signal is now
written (fail-open: a persistence error never breaks the tick). A no-emit writes
nothing. Pure observability — no gate / sizing / block change.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.data.signal_persist import persist_emitted_signal
from polaris.storage.schema import ALL_DDL


def _mkconn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "p.sqlite", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def test_emitted_signal_lands_a_row(tmp_path: Path) -> None:
    conn = _mkconn(tmp_path)
    persist_emitted_signal(
        conn, signal_id="sig1", venue="capital", symbol="GOLD",
        strategy_id="xau_indices_trend", side="long", strength=0.73,
        timeframe="1H", ts=1_780_000_000, correlation_group="cfd_index",
        thesis="donchian_30+mom",
    )
    rows = conn.execute(
        "SELECT strategy_id, signal_id, instrument_id, direction, score, ts "
        "FROM signals"
    ).fetchall()
    assert len(rows) == 1
    strategy_id, signal_id, instrument_id, direction, score, ts = rows[0]
    assert strategy_id == "xau_indices_trend"
    assert signal_id == "sig1"
    assert instrument_id == "capital:GOLD"
    assert direction == "long"
    assert abs(score - 0.73) < 1e-9
    assert ts == 1_780_000_000


def test_payload_carries_venue_symbol_tf(tmp_path: Path) -> None:
    conn = _mkconn(tmp_path)
    persist_emitted_signal(
        conn, signal_id="sig2", venue="capital", symbol="US100",
        strategy_id="session_breakout", side="short", strength=0.61,
        timeframe="5m", ts=1_780_000_500, correlation_group=None, thesis="brk",
    )
    import json

    payload = conn.execute(
        "SELECT payload_json FROM signals WHERE signal_id = 'sig2'"
    ).fetchone()[0]
    data = json.loads(payload)
    assert data["venue"] == "capital"
    assert data["symbol"] == "US100"
    assert data["tf"] == "5m"


def test_persistence_error_is_fail_open(tmp_path: Path) -> None:
    # A DB error must NOT propagate (the tick never breaks on observability).
    conn = _mkconn(tmp_path)
    conn.close()  # using a closed conn raises sqlite3.ProgrammingError on execute
    # Should swallow the error and return without raising.
    persist_emitted_signal(
        conn, signal_id="sigX", venue="capital", symbol="GOLD",
        strategy_id="xau_indices_trend", side="long", strength=0.5,
        timeframe="1H", ts=1, correlation_group=None, thesis="t",
    )


def test_no_emit_writes_nothing(tmp_path: Path) -> None:
    # Sanity: a tick with no emit never calls persist → the table stays empty.
    conn = _mkconn(tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count == 0
