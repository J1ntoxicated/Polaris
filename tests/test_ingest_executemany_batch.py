"""TDD — ingest bulk-write lever (forensic wf_1f586d0a tick-body fix #1).

Forensic: DBWriter runs each ingest job inside its own SAVEPOINT (already
inside the writer's per-batch ``BEGIN``/``COMMIT`` — see ``db_writer.py``), so
``persist_bars`` / ``update_baseline_from_bars``'s per-row ``conn.execute()``
loop pays Python/C round-trip overhead PER ROW while holding that transaction
open — the measured 5-47s write-lock hold on large batches. Swapping the
row-by-row loop for one ``conn.executemany()`` call keeps the SAME rows, the
SAME (caller-owned) transaction, and the SAME INSERT OR REPLACE upsert
semantics — only the bulk-write API changes.

These tests pin the API-level change (single ``executemany`` call instead of
N ``execute`` calls) via a counting connection subclass, independent of
whatever transaction wrapping the caller does.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Iterator

import pytest

from polaris.core.data.ingest import persist_bars, update_baseline_from_bars
from polaris.core.data.schema import Bar
from polaris.storage.schema import ALL_DDL

NOW = 1_780_000_000


class _CountingConnection(sqlite3.Connection):
    """sqlite3.Connection subclass that counts execute/executemany calls.

    A plain ``unittest.mock.patch`` cannot monkeypatch ``sqlite3.Connection``
    (immutable C type) — subclassing via the ``factory=`` connect kwarg is the
    supported seam.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.execute_calls = 0
        self.executemany_calls = 0

    def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        self.execute_calls += 1
        return super().execute(*args, **kwargs)  # type: ignore[arg-type]

    def executemany(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        self.executemany_calls += 1
        return super().executemany(*args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def counting_memdb() -> Iterator[_CountingConnection]:
    conn = sqlite3.connect(
        ":memory:", isolation_level=None, check_same_thread=False,
        factory=_CountingConnection,
    )
    conn.execute("PRAGMA foreign_keys=ON;")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    # Reset counters AFTER schema setup so only the code-under-test's calls count.
    conn.execute_calls = 0
    conn.executemany_calls = 0
    try:
        yield conn
    finally:
        conn.close()


def _bar(symbol: str = "BTC-USDT", ts: int = NOW, *, venue: str = "okx") -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id="crypto:BTC",
        venue=venue,
        symbol=symbol,
        bar_interval="1m",
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        notional_usd=1005.0,
        trade_count=20,
    )


def test_persist_bars_issues_one_executemany_call(
    counting_memdb: _CountingConnection,
) -> None:
    bars = [_bar(ts=NOW + i * 60) for i in range(10)]
    n = persist_bars(counting_memdb, bars)
    assert n == 10
    assert counting_memdb.executemany_calls == 1, (
        "persist_bars must batch all rows into ONE executemany call, not "
        "N individual execute() calls."
    )
    assert counting_memdb.execute_calls == 0, (
        "persist_bars must not fall back to per-row execute() for the INSERT."
    )
    rows = counting_memdb.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert rows == 10


def test_persist_bars_empty_input_issues_no_executemany(
    counting_memdb: _CountingConnection,
) -> None:
    n = persist_bars(counting_memdb, [])
    assert n == 0
    assert counting_memdb.executemany_calls == 0


def test_persist_bars_skips_non_finite_ohlc_before_batching(
    counting_memdb: _CountingConnection,
) -> None:
    """Per-row OHLC guard must survive the batch conversion: a single
    malformed bar in the batch is dropped, the rest still persist in ONE
    executemany call (batch-survivable — codex Day 6 contract, unchanged)."""
    good = [_bar(ts=NOW + i * 60) for i in range(3)]
    bad = dataclasses.replace(_bar(ts=NOW + 999 * 60), close=float("nan"))
    bars = [*good, bad]
    n = persist_bars(counting_memdb, bars)
    assert n == 3
    assert counting_memdb.executemany_calls == 1
    rows = counting_memdb.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert rows == 3


def test_update_baseline_from_bars_batches_samples_into_one_executemany(
    counting_memdb: _CountingConnection,
) -> None:
    bars = [_bar(ts=NOW + i * 60) for i in range(10)]
    n = update_baseline_from_bars(counting_memdb, bars, asset_class="crypto")
    assert n == 30  # 3 metrics x 10 bars
    # Exactly one executemany call carries all 30 sample rows — the window
    # recompute (upsert_baseline_state, once per (instrument, metric)) still
    # uses individual execute() calls (unchanged — 3 metrics x 1 instrument).
    assert counting_memdb.executemany_calls == 1, (
        "update_baseline_from_bars must batch all per-bar samples into ONE "
        "executemany call, not N*3 individual execute() calls."
    )
    n_samples = counting_memdb.execute(
        "SELECT COUNT(*) FROM ticker_baseline_samples"
    ).fetchone()[0]
    assert n_samples == 30
    # Window recompute ran once per (instrument, metric) — 3 upserts, byte
    # identical to the pre-batch behaviour (regression guard).
    n_states = counting_memdb.execute(
        "SELECT COUNT(*) FROM ticker_baseline_state"
    ).fetchone()[0]
    assert n_states == 3


def test_update_baseline_from_bars_empty_input_issues_no_executemany(
    counting_memdb: _CountingConnection,
) -> None:
    n = update_baseline_from_bars(counting_memdb, [], asset_class="crypto")
    assert n == 0
    assert counting_memdb.executemany_calls == 0
