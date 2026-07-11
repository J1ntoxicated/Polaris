"""Tests for the frontgate-scan shared universe-symbols helper + the G3/G4
proximity SHADOW loggers (behavior-0, TAG-ONLY — never read by a live gate).

DEMO/PAPER only — virtual funds.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.altdata._universe_symbols import active_alpaca_symbols
from polaris.core.altdata.earnings_proximity_shadow import (
    log_earnings_proximity_shadow,
)
from polaris.core.altdata.filing_proximity_shadow import log_filing_proximity_shadow
from polaris.storage.schema import init_db

# ── active_alpaca_symbols ────────────────────────────────────────────────


def _insert_universe_row(
    conn: sqlite3.Connection, *, venue: str, symbol: str, is_active: int
) -> None:
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, last_seen_ts, is_active) "
        "VALUES (?, ?, ?, 'G', 'equity', 'USD', 'live', 1, ?)",
        (venue, symbol, f"{venue}:{symbol}", is_active),
    )


def test_active_alpaca_symbols_filters_venue_and_active(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    _insert_universe_row(conn, venue="alpaca", symbol="AAPL", is_active=1)
    _insert_universe_row(conn, venue="alpaca", symbol="TSLA", is_active=0)
    _insert_universe_row(conn, venue="okx", symbol="BTC-USDT", is_active=1)
    conn.commit()

    assert active_alpaca_symbols(conn) == ("AAPL",)
    conn.close()


def test_active_alpaca_symbols_none_conn_is_empty() -> None:
    assert active_alpaca_symbols(None) == ()


def test_active_alpaca_symbols_degrades_on_closed_conn(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    conn.close()
    assert active_alpaca_symbols(conn) == ()


# ── filing_proximity_shadow ──────────────────────────────────────────────


def test_log_filing_proximity_shadow_writes_row(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    import datetime as dt

    now = dt.datetime(2026, 7, 11, tzinfo=dt.UTC)
    n = log_filing_proximity_shadow(
        conn, symbol="AAPL", cycle_ts=int(now.timestamp()), form_type="8-K",
        accession_number="0001-8K", acceptance_ts=int(now.timestamp()) - 86400,
        now=now,
    )
    assert n == 1
    row = conn.execute(
        "SELECT symbol, form_type, days_since_filing FROM filing_proximity_shadow"
    ).fetchone()
    assert row[0] == "AAPL"
    assert row[1] == "8-K"
    assert row[2] == 1.0
    conn.close()


def test_log_filing_proximity_shadow_none_conn_is_noop() -> None:
    import datetime as dt

    assert log_filing_proximity_shadow(
        None, symbol="AAPL", cycle_ts=1, form_type="8-K",
        accession_number="a", acceptance_ts=None, now=dt.datetime.now(dt.UTC),
    ) == 0


def test_log_filing_proximity_shadow_missing_acceptance_ts_is_none_days(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    log_filing_proximity_shadow(
        conn, symbol="AAPL", cycle_ts=int(now.timestamp()), form_type="8-K",
        accession_number="a", acceptance_ts=None, now=now,
    )
    days = conn.execute("SELECT days_since_filing FROM filing_proximity_shadow").fetchone()[0]
    assert days is None
    conn.close()


# ── earnings_proximity_shadow ────────────────────────────────────────────


def test_log_earnings_proximity_shadow_writes_row(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    n = log_earnings_proximity_shadow(
        conn, symbol="AAPL", cycle_ts=1_700_000_000, days_to_earnings=3.5,
        hour="bmo", surprise_pct=None,
    )
    assert n == 1
    row = conn.execute(
        "SELECT symbol, days_to_earnings, hour FROM earnings_proximity_shadow"
    ).fetchone()
    assert row == ("AAPL", 3.5, "bmo")
    conn.close()


def test_log_earnings_proximity_shadow_none_conn_is_noop() -> None:
    assert log_earnings_proximity_shadow(
        None, symbol="AAPL", cycle_ts=1, days_to_earnings=1.0, hour="bmo",
        surprise_pct=None,
    ) == 0
