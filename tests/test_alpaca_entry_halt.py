"""Alpaca equity NEW-entry halt — dead-feed data-health gate (not a throttle).

Jin 2026-06-22 (coherence audit): the Alpaca feed went 98.7h stale. With no
live price an equity entry would size/exit against a dead quote and become an
unexitable zombie. ``alpaca_equity_entries_halted`` HALTS new Alpaca entries
while the feed is dead, and AUTO-CLEARS the instant the feed recovers.

Two halt sources, OR-combined:
- DATA-HEALTH (primary): the newest Alpaca bar is older than the freshness
  window -> feed dead -> halt. Pure function of the stored data, so it clears
  automatically when fresh bars land (no manual reset).
- OPERATOR OVERRIDE: ``POLARIS_ALPACA_ENTRIES_HALT`` truthy forces the halt on
  regardless of feed state (an explicit kill-switch).

flow_not_block preserved: this is a "no live price = cannot trade" gate, NOT a
defensive throttle. It touches ZERO sizing — a healthy feed sizes/enters
exactly as before. It applies ONLY to Alpaca (OKX/Capital untouched) and ONLY
to NEW entries (existing positions still managed/exited).

DEMO/PAPER only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.core.streams.alpaca_health import alpaca_equity_entries_halted
from polaris.storage.schema import init_db


def _alpaca_bar(symbol: str, ts: int) -> Bar:
    return Bar(
        instrument_id=f"alpaca:{symbol}",
        underlying_group_id=f"equity:{symbol}",
        venue="alpaca",
        symbol=symbol,
        bar_interval="1D",
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1_000_000.0,
        notional_usd=100_500_000.0,
        trade_count=5_000,
        vwap=100.4,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="alpaca_rest",
    )


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "halt.sqlite")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_ALPACA_ENTRIES_HALT", raising=False)


def test_fresh_alpaca_feed_does_not_halt(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("AAPL", now - 3600)])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False


def test_dead_alpaca_feed_halts_entries(conn: sqlite3.Connection) -> None:
    """The 98.7h incident: newest Alpaca bar 99h old -> halt new entries."""
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("CAST", now - int(99 * 3600))])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True


def test_halt_auto_clears_on_feed_recovery(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("GPUS", now - int(99 * 3600))])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True
    # Feed recovers.
    persist_bars(conn, [_alpaca_bar("GPUS", now - 600)])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False


def test_no_alpaca_bars_at_all_halts(conn: sqlite3.Connection) -> None:
    """No Alpaca bars in the DB == no live price == cannot trade -> halt."""
    now = int(time.time())
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True


def test_operator_override_forces_halt_even_when_fresh(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("MSFT", now - 600)])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False
    monkeypatch.setenv("POLARIS_ALPACA_ENTRIES_HALT", "1")
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True


def test_operator_override_falsey_does_not_force(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("MSFT", now - 600)])
    monkeypatch.setenv("POLARIS_ALPACA_ENTRIES_HALT", "0")
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False
