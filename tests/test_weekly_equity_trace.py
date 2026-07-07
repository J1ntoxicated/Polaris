"""Weekly per-exchange equity/PnL trace — TDD spec (Jin 2026-07-07).

DEMO/PAPER, VIRTUAL ACCOUNT. Non-destructive: TRACE != RESET. These tests pin:

  1. SCHEMA: ``weekly_equity_curve`` table exists after DDL bootstrap.
  2. MONDAY ANCHOR: ``week_start_ts`` resolves to Monday 00:00 UTC for any day
     of the week.
  3. UPSERT ACCUMULATES: repeated same-week calls accumulate realized_pnl_usd
     + trades without resetting start_equity; current_equity always reflects
     the latest snapshot.
  4. WEEK-BOUNDARY TRACE != RESET: crossing Monday creates a NEW row (new
     week_start_ts) while the account's own equity value carries over
     unchanged (the caller passes the continuously-compounding equity in —
     this module never mutates the running balance).
  5. Property: sum of per-exchange realized pnl in a week == the week row's
     realized_pnl (each exchange keeps its own row; summing multiple upserts
     onto ONE exchange's row equals that row's realized_pnl_usd).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from polaris.storage.weekly_equity_trace import (
    all_current_week_rows,
    get_week_row,
    upsert_weekly_row,
    week_start_ts,
)


def test_schema_table_exists(memdb: sqlite3.Connection) -> None:
    row = memdb.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='weekly_equity_curve'"
    ).fetchone()
    assert row is not None
    cols = {r[1] for r in memdb.execute("PRAGMA table_info(weekly_equity_curve)")}
    assert cols == {
        "exchange", "week_start_ts", "start_equity", "current_equity",
        "realized_pnl_usd", "unrealized_pnl_usd", "trades", "updated_ts",
    }


def test_week_start_ts_monday_anchor_any_weekday() -> None:
    # 2026-07-06 is a Monday (per project "today"); check every day this week
    # resolves to the SAME Monday anchor.
    monday = datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC)
    monday_ts = int(monday.timestamp())
    for day_offset in range(7):  # Mon..Sun
        dt = datetime(2026, 7, 6 + day_offset, 15, 30, 0, tzinfo=UTC)
        assert week_start_ts(int(dt.timestamp())) == monday_ts


def test_week_start_ts_never_future() -> None:
    ts = int(datetime(2026, 7, 8, 3, 0, 0, tzinfo=UTC).timestamp())
    assert week_start_ts(ts) <= ts


def test_upsert_creates_row_with_start_equity(memdb: sqlite3.Connection) -> None:
    now_ts = int(datetime(2026, 7, 7, 10, 0, 0, tzinfo=UTC).timestamp())
    row = upsert_weekly_row(
        memdb, exchange="okx", now_ts=now_ts, account_equity=100_000.0,
        realized_pnl_delta_usd=250.0, trade_delta=1,
    )
    assert row.exchange == "okx"
    assert row.start_equity == 100_000.0
    assert row.current_equity == 100_000.0
    assert row.realized_pnl_usd == 250.0
    assert row.trades == 1


def test_upsert_same_week_accumulates_not_overwrites(memdb: sqlite3.Connection) -> None:
    t1 = int(datetime(2026, 7, 7, 10, 0, 0, tzinfo=UTC).timestamp())
    t2 = int(datetime(2026, 7, 8, 14, 0, 0, tzinfo=UTC).timestamp())
    upsert_weekly_row(
        memdb, exchange="okx", now_ts=t1, account_equity=100_000.0,
        realized_pnl_delta_usd=300.0, trade_delta=1,
    )
    row = upsert_weekly_row(
        memdb, exchange="okx", now_ts=t2, account_equity=100_450.0,
        realized_pnl_delta_usd=150.0, trade_delta=1,
    )
    # start_equity NEVER moves once set this week (trace != reset).
    assert row.start_equity == 100_000.0
    # realized accumulates across calls within the same week.
    assert row.realized_pnl_usd == 450.0
    assert row.trades == 2
    # current_equity reflects the LATEST snapshot passed in.
    assert row.current_equity == 100_450.0


def test_week_boundary_creates_new_row_account_carries_over(
    memdb: sqlite3.Connection,
) -> None:
    """Crossing Monday starts a NEW week row; the account equity (caller-owned,
    continuously compounding) is NOT reset by this module — it is simply
    whatever value the caller passes as the new week's start_equity."""
    week1_ts = int(datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC).timestamp())  # Wed
    week2_ts = int(datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC).timestamp())  # next Mon

    row1 = upsert_weekly_row(
        memdb, exchange="okx", now_ts=week1_ts, account_equity=100_000.0,
        realized_pnl_delta_usd=500.0, trade_delta=2,
    )
    # Simulate the account having compounded to 100,500 by the next week —
    # the caller passes this carried-over value; NOT reset to 100,000.
    row2 = upsert_weekly_row(
        memdb, exchange="okx", now_ts=week2_ts, account_equity=100_500.0,
        realized_pnl_delta_usd=75.0, trade_delta=1,
    )
    assert row1.week_start_ts != row2.week_start_ts
    # New week's start_equity is the carried-over account value, not a reset
    # back to the original $100k seed.
    assert row2.start_equity == 100_500.0
    # New week's realized_pnl_usd starts FRESH (this week's trace only), while
    # the OLD week row is untouched (still readable, non-destructive).
    assert row2.realized_pnl_usd == 75.0
    preserved = get_week_row(memdb, exchange="okx", week_start=row1.week_start_ts)
    assert preserved is not None
    assert preserved.realized_pnl_usd == 500.0
    assert preserved.trades == 2


def test_unrealized_pnl_overwrite_vs_none_preserves(memdb: sqlite3.Connection) -> None:
    now_ts = int(datetime(2026, 7, 7, 10, 0, 0, tzinfo=UTC).timestamp())
    upsert_weekly_row(
        memdb, exchange="capital", now_ts=now_ts, account_equity=100_000.0,
        unrealized_pnl_usd=42.0,
    )
    # A subsequent realized-only close-path call (unrealized_pnl_usd=None)
    # must NOT clobber the last stored unrealized snapshot.
    row = upsert_weekly_row(
        memdb, exchange="capital", now_ts=now_ts + 60, account_equity=100_010.0,
        realized_pnl_delta_usd=10.0,
    )
    assert row.unrealized_pnl_usd == 42.0
    assert row.realized_pnl_usd == 10.0


def test_property_sum_of_realized_deltas_equals_week_row(
    memdb: sqlite3.Connection,
) -> None:
    now_ts = int(datetime(2026, 7, 7, 9, 0, 0, tzinfo=UTC).timestamp())
    deltas = [10.5, -3.25, 100.0, -50.75, 2.0]
    row = None
    for i, d in enumerate(deltas):
        row = upsert_weekly_row(
            memdb, exchange="alpaca", now_ts=now_ts + i * 3600,
            account_equity=100_000.0 + sum(deltas[: i + 1]),
            realized_pnl_delta_usd=d, trade_delta=1,
        )
    assert row is not None
    assert abs(row.realized_pnl_usd - sum(deltas)) < 1e-9
    assert row.trades == len(deltas)


def test_all_current_week_rows_multi_exchange(memdb: sqlite3.Connection) -> None:
    now_ts = int(datetime(2026, 7, 7, 9, 0, 0, tzinfo=UTC).timestamp())
    upsert_weekly_row(
        memdb, exchange="okx", now_ts=now_ts, account_equity=100_100.0,
        realized_pnl_delta_usd=100.0,
    )
    upsert_weekly_row(
        memdb, exchange="capital", now_ts=now_ts, account_equity=99_800.0,
        realized_pnl_delta_usd=-200.0,
    )
    rows = all_current_week_rows(memdb, now_ts=now_ts)
    by_exchange = {r.exchange: r for r in rows}
    assert set(by_exchange) == {"okx", "capital"}
    assert by_exchange["okx"].realized_pnl_usd == 100.0
    assert by_exchange["capital"].realized_pnl_usd == -200.0


def test_get_week_row_missing_returns_none(memdb: sqlite3.Connection) -> None:
    assert get_week_row(memdb, exchange="okx", week_start=0) is None
