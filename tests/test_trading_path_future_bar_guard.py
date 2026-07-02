"""FIX 1/2 — trading-path FUTURE-dated bar guard.

The Capital REST ts source-fix only corrects NEW bars; the live DB still holds
~5,793 stale +10h FUTURE-dated Capital bars (the AEST-naive snapshotTime parse).
The trading-path bar readers had NO upper-ts bound, so on restart Capital
strategies / regime / exit / ATR would consume a +10h bar as the "most
recent" canvas for ~10h.

These tests pin the guard (mirrors the dashboard ``_last_prices`` ts<=now belt):
no bar with ``ts > now + CLOCK_SKEW_SLACK_SEC`` is ever returned to the
trading path. The latest PAST bar is returned instead; a normal past bar is
always returned.

DEMO/PAPER only. This is a correctness guard (use the real most-recent bar),
not a throttle — sizing / gating / aggressive bias untouched.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator

import pytest

from polaris.scripts._production_bars import read_recent_bars
from polaris.scripts._production_close_helpers import _latest_bar_close
from polaris.scripts._production_rotation_reader import _held_pnl_r

_VENUE = "capital"
_SYMBOL = "GOLD"
_INST = f"{_VENUE}:{_SYMBOL}"
_TEN_HOURS = 10 * 3600


@pytest.fixture
def memdb() -> Generator[sqlite3.Connection]:
    from polaris.storage.schema_ddl_core import DDL_BARS, DDL_BARS_INDEX
    from polaris.storage.schema_ddl_ext import DDL_FILLS

    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL_BARS)
    conn.executescript(DDL_BARS_INDEX)
    conn.executescript(DDL_FILLS)
    try:
        yield conn
    finally:
        conn.close()


def _insert_bar(
    conn: sqlite3.Connection, *, ts: int, close: float, bar_interval: str = "1m"
) -> None:
    conn.execute(
        """
        INSERT INTO bars (
            instrument_id, underlying_group_id, venue, symbol, bar_interval,
            ts, open, high, low, close, volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _INST,
            "commodity:GOLD",
            _VENUE,
            _SYMBOL,
            bar_interval,
            ts,
            close,
            close + 1.0,
            close - 1.0,
            close,
            100.0,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# read_recent_bars — strategies / regime canvas
# ---------------------------------------------------------------------------


def test_read_recent_bars_excludes_future_bar(memdb: sqlite3.Connection) -> None:
    now = int(time.time())
    _insert_bar(memdb, ts=now - 120, close=2000.0)  # latest PAST bar
    _insert_bar(memdb, ts=now + _TEN_HOURS, close=9999.0)  # +10h FUTURE bar
    bars = read_recent_bars(memdb, venue=_VENUE, symbol=_SYMBOL, limit=240)
    assert len(bars) == 1
    # The +10h future bar (close=9999) must NOT be the most-recent canvas.
    assert bars[-1].close == 2000.0
    assert all(b.close != 9999.0 for b in bars)


def test_read_recent_bars_returns_normal_past_bar(memdb: sqlite3.Connection) -> None:
    now = int(time.time())
    _insert_bar(memdb, ts=now - 300, close=1990.0)
    _insert_bar(memdb, ts=now - 60, close=2010.0)
    bars = read_recent_bars(memdb, venue=_VENUE, symbol=_SYMBOL, limit=240)
    assert len(bars) == 2
    assert bars[-1].close == 2010.0  # newest last, all past → all returned


def test_read_recent_bars_within_clock_skew_slack_kept(
    memdb: sqlite3.Connection,
) -> None:
    # A bar a few seconds ahead (clock skew) is legitimately current → kept.
    now = int(time.time())
    _insert_bar(memdb, ts=now + 30, close=2020.0)
    bars = read_recent_bars(memdb, venue=_VENUE, symbol=_SYMBOL, limit=240)
    assert len(bars) == 1
    assert bars[-1].close == 2020.0


# ---------------------------------------------------------------------------
# _latest_bar_close — close-split fresh mark
# ---------------------------------------------------------------------------


def test_latest_bar_close_excludes_future_bar(memdb: sqlite3.Connection) -> None:
    now = int(time.time())
    _insert_bar(memdb, ts=now - 120, close=2000.0)
    _insert_bar(memdb, ts=now + _TEN_HOURS, close=9999.0)
    px = _latest_bar_close(memdb, venue=_VENUE, symbol=_SYMBOL)
    # Returns the latest PAST bar, not the +10h future bar.
    assert px == 2000.0


def test_latest_bar_close_returns_past_bar(memdb: sqlite3.Connection) -> None:
    now = int(time.time())
    _insert_bar(memdb, ts=now - 60, close=2010.0)
    px = _latest_bar_close(memdb, venue=_VENUE, symbol=_SYMBOL)
    assert px == 2010.0


# ---------------------------------------------------------------------------
# _held_pnl_r — opportunity-cost rotation drift (latest bar vs entry)
# ---------------------------------------------------------------------------


def test_held_pnl_r_uses_past_close_not_future_bar(
    memdb: sqlite3.Connection,
) -> None:
    # A LONG entered at 2000 with a recent PAST close at 2010 is in profit
    # (positive R). A +10h FUTURE bar at 1000 would flip the drift deeply
    # negative (making a winner look like a rotation victim). The guard must
    # measure drift against the PAST close.
    now = int(time.time())
    _insert_bar(memdb, ts=now - 60, close=2010.0)
    _insert_bar(memdb, ts=now + _TEN_HOURS, close=1000.0)
    pnl_r = _held_pnl_r(
        memdb, venue=_VENUE, symbol=_SYMBOL, side="long", entry_price=2000.0
    )
    assert pnl_r > 0.0  # measured vs the 2010 past close, not the 1000 ghost
