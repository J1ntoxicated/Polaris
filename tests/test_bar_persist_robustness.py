"""Persist robustness — one malformed candle must not lose a whole batch.

Jin 2026-06-27 ("데이터 proactive"): yfinance hands a NULL/NaN OHLCV candle on
some thin tickers AND on some Capital index epics (J225/HK50/AU200 via the
``_CAPITAL_INDEX_YF`` Yahoo map). SQLite treats a Python ``float('nan')`` as
NULL, so a NaN ``open`` (Alpaca) or NaN ``close`` (Capital index) row trips
``IntegrityError: NOT NULL constraint failed`` on the whole INSERT batch — and
the static-ground SAVEPOINT then rolls back EVERY bar for that symbol, including
the good liquid ones. Net effect: DB holds 0 Alpaca bars / 0 Capital-index bars.

Fix: ``persist_bars`` is venue-agnostic — it filters per-row (any non-finite or
None OHLCV → skip that row) so the GOOD bars in the same batch still land. A bad
bar never fails the batch. DEMO/PAPER only; flow_not_block (this only WIDENS what
survives ingest — no entry/size/exit is touched).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.storage.schema import init_db


def _bar(
    venue: str,
    symbol: str,
    ts: int,
    *,
    bar_interval: str = "1D",
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 1_000_000.0,
) -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"equity:{symbol}",
        venue=venue,
        symbol=symbol,
        bar_interval=bar_interval,
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        notional_usd=close * volume if volume > 0 else 0.0,
        trade_count=0,
        vwap=0.0,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="yahoo",
    )


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "persist.sqlite")
    yield c
    c.close()


def _count(conn: sqlite3.Connection, symbol: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE symbol = ?", (symbol,)
    ).fetchone()
    return int(row[0])


def test_nan_open_row_skipped_good_rows_land(conn: sqlite3.Connection) -> None:
    """Alpaca NULL-open case: a NaN-open bar is dropped, the good bars persist."""
    nan = float("nan")
    batch = [
        _bar("alpaca", "AAPL", 1_700_000_000),
        _bar("alpaca", "AAPL", 1_700_086_400, open_=nan),  # bad — NaN open
        _bar("alpaca", "AAPL", 1_700_172_800),
    ]
    n = persist_bars(conn, batch)
    assert n == 2, "only the 2 good rows are counted as persisted"
    assert _count(conn, "AAPL") == 2, "the NaN-open bar must not block the good bars"


def test_nan_close_row_skipped_capital_index(conn: sqlite3.Connection) -> None:
    """Capital index NULL-close case (J225/HK50/AU200): good bars still land."""
    nan = float("nan")
    batch = [
        _bar("capital", "J225", 1_700_000_000, bar_interval="1H"),
        _bar("capital", "J225", 1_700_003_600, bar_interval="1H", close=nan),  # bad
        _bar("capital", "J225", 1_700_007_200, bar_interval="1H"),
    ]
    n = persist_bars(conn, batch)
    assert n == 2
    assert _count(conn, "J225") == 2, "NaN-close must not nuke the Capital index batch"


def test_nan_high_low_rows_skipped(conn: sqlite3.Connection) -> None:
    """A NaN in ANY OHLC field drops the row (open/high/low/close all guarded)."""
    nan = float("nan")
    batch = [
        _bar("alpaca", "MSFT", 1_700_000_000, high=nan),
        _bar("alpaca", "MSFT", 1_700_086_400, low=nan),
        _bar("alpaca", "MSFT", 1_700_172_800),  # the only fully-finite bar
    ]
    n = persist_bars(conn, batch)
    assert n == 1
    assert _count(conn, "MSFT") == 1


def test_inf_and_none_volume_handled(conn: sqlite3.Connection) -> None:
    """Non-finite volume is sanitized to 0.0 (a valid OHLC bar still lands)."""
    inf = float("inf")
    batch = [_bar("okx", "BTC-USDT", 1_700_000_000, bar_interval="1m", volume=inf)]
    n = persist_bars(conn, batch)
    assert n == 1, "an infinite VOLUME must not drop an otherwise-valid OHLC bar"
    row = conn.execute(
        "SELECT volume FROM bars WHERE symbol = 'BTC-USDT'"
    ).fetchone()
    assert row[0] == 0.0, "non-finite volume is sanitized to 0.0, not stored as NaN"


def test_all_good_batch_unchanged(conn: sqlite3.Connection) -> None:
    """Back-compat: an all-finite batch persists byte-identically (count == len)."""
    batch = [_bar("alpaca", "NVDA", 1_700_000_000 + i * 86400) for i in range(4)]
    n = persist_bars(conn, batch)
    assert n == 4
    assert _count(conn, "NVDA") == 4
