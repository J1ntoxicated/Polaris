"""Periodic retention producer — the missing live-loop wiring.

Validates the wiring that was absent (retention code existed but was never
called from the running loop → unbounded growth):

* the producer prunes BOTH the live DB and the probe sidecar on each pass,
  on its OWN dedicated connections (never the loop-owned handle);
* the live-DB ledger (positions/fills/signals) is NEVER touched;
* the producer tears down promptly on stop_evt;
* a broken probe DB degrades (live prune still runs, no crash).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from polaris.core.probes.tuning_log import open_probe_db
from polaris.scripts._production_retention import (
    _prune_blocking,
    retention_producer,
)
from polaris.storage.schema import init_db

NOW = 1_782_000_000


def _seed_live(db_path: Path) -> None:
    conn = init_db(db_path)
    try:
        # Old 1m bar (outside the NIT-A 30d intraday window) → pruned; recent kept.
        # 1m (not 1D): the deep 1D/4H canvas now keeps a 1200d window, so only the
        # dense intraday streams prune on this timescale.
        for ts in (NOW - 40 * 86_400, NOW):
            conn.execute(
                "INSERT INTO bars (instrument_id, underlying_group_id, venue, "
                "symbol, bar_interval, ts, open, high, low, close, volume) "
                "VALUES ('I','G','okx','BTC-USDT','1m',?,1,1,1,1,1)",
                (ts,),
            )
        # Ledger rows with ANCIENT ts — must survive (allowlist protection).
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
            "VALUES ('p1','okx','BTC-USDT','s','s','s','long',1,'closed',1)"
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
            "size_usd, fill_price, ts_ms, order_id) "
            "VALUES ('f1','okx','I','s','buy',1,1,1000,'o1')"
        )
        conn.execute(
            "INSERT INTO signals (strategy_id, signal_id, instrument_id, direction, "
            "score, thesis, ts) VALUES ('s','sig1','I','long',1,'t',1)"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_probe(db_path: Path) -> None:
    conn = open_probe_db(db_path)
    try:
        conn.execute(
            "INSERT INTO probe_decisions (decision_id, eval_id, ts, run_id, "
            "position_id, mode, composite_lean, action) "
            "VALUES ('d-old','e1',?,'run','p','observe',0.0,'hold')",
            (NOW - 60 * 86_400,),  # outside 45d → pruned
        )
        conn.execute(
            "INSERT INTO probe_decisions (decision_id, eval_id, ts, run_id, "
            "position_id, mode, composite_lean, action) "
            "VALUES ('d-new','e2',?,'run','p','observe',0.0,'hold')",
            (NOW,),  # kept
        )
    finally:
        conn.close()


def _count(db_path: Path, table: str) -> int:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table}"  # noqa: S608 test-local
        ).fetchone()[0]
    finally:
        conn.close()


def test_prune_blocking_prunes_streams_and_keeps_ledger(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)

    _prune_blocking(live, probe)

    # Stream pruned to the in-window rows.
    assert _count(live, "bars") == 1
    assert _count(probe, "probe_decisions") == 1
    # Ledger fully preserved.
    assert _count(live, "positions") == 1
    assert _count(live, "fills") == 1
    assert _count(live, "signals") == 1


def test_prune_blocking_degrades_on_missing_live_db(tmp_path: Path) -> None:
    """A live DB with no schema (no tables) must not raise — probe prune still
    runs. run_retention_live swallows the missing-table sqlite error."""
    live = tmp_path / "empty.sqlite"
    probe = tmp_path / "probes.sqlite"
    # Touch an empty (schema-less) live DB.
    import sqlite3

    sqlite3.connect(str(live)).close()
    _seed_probe(probe)

    _prune_blocking(live, probe)  # must not raise

    assert _count(probe, "probe_decisions") == 1


@pytest.mark.asyncio
async def test_retention_producer_runs_then_stops(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)

    stop = asyncio.Event()
    # Tiny interval so the first pass fires immediately; then stop.
    task = asyncio.create_task(
        retention_producer(
            live_db=live, probe_db=probe, stop_evt=stop, interval_sec=0.01
        )
    )
    # Let at least one prune pass complete.
    for _ in range(200):
        await asyncio.sleep(0.01)
        if _count(live, "bars") == 1:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert _count(live, "bars") == 1            # old bar pruned
    assert _count(live, "positions") == 1       # ledger intact
    assert _count(probe, "probe_decisions") == 1


@pytest.mark.asyncio
async def test_retention_producer_stops_promptly_without_pass(tmp_path: Path) -> None:
    """stop_evt set before any interval elapses → task returns at once."""
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)

    stop = asyncio.Event()
    stop.set()
    task = asyncio.create_task(
        retention_producer(
            live_db=live, probe_db=probe, stop_evt=stop, interval_sec=9999.0
        )
    )
    await asyncio.wait_for(task, timeout=1.0)
    # No pass ran — the old bar is still present.
    assert _count(live, "bars") == 2
