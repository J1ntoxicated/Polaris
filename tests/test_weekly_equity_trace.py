"""Weekly per-exchange equity/PnL trace — TDD spec (Jin 2026-07-07).

DEMO/PAPER, VIRTUAL ACCOUNT. Non-destructive: TRACE != RESET. These tests pin:

  1. SCHEMA: ``weekly_equity_curve`` table exists after DDL bootstrap.
  2. MONDAY ANCHOR: ``week_start_ts`` resolves to Monday 00:00 UTC for any day
     of the week.
  3. FRESH-SUM (ledger-reconcile forensic 2026-07-12, bug② fix): repeated
     same-week calls recompute ``realized_pnl_usd`` + ``trades`` FRESH from
     ``fills`` — never an accumulated delta — so a close whose update was
     dropped (fail-open exception) self-heals the moment the next close
     successfully upserts. ``start_equity`` never moves once set this week.
  4. WEEK-BOUNDARY TRACE != RESET: crossing Monday creates a NEW row (new
     week_start_ts) while the account's own equity value carries over
     unchanged (the caller passes the continuously-compounding equity in —
     this module never mutates the running balance).
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


def _insert_close_fill(
    conn: sqlite3.Connection,
    *,
    fill_id: str,
    exchange: str,
    ts_ms: int,
    pnl_usd: float,
    fee_usd: float = 0.0,
) -> None:
    """Seed a single CLOSE fill (is_close=1) — the fresh-SUM ledger source."""
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " is_close, pnl_usd, contribution_id) "
        "VALUES (?, ?, 'x:SYM', 's', 'sell', 100.0, 1.0, ?, 0.0, ?, ?, 1, ?, '')",
        (fill_id, exchange, fee_usd, ts_ms, f"o_{fill_id}", pnl_usd),
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
    _insert_close_fill(
        memdb, fill_id="f1", exchange="okx", ts_ms=now_ts * 1000, pnl_usd=250.0,
    )
    row = upsert_weekly_row(memdb, exchange="okx", now_ts=now_ts, account_equity=100_000.0)
    assert row.exchange == "okx"
    assert row.start_equity == 100_000.0
    assert row.current_equity == 100_000.0
    assert row.realized_pnl_usd == 250.0
    assert row.trades == 1


def test_upsert_same_week_fresh_sums_not_accumulates(memdb: sqlite3.Connection) -> None:
    t1 = int(datetime(2026, 7, 7, 10, 0, 0, tzinfo=UTC).timestamp())
    t2 = int(datetime(2026, 7, 8, 14, 0, 0, tzinfo=UTC).timestamp())
    _insert_close_fill(memdb, fill_id="f1", exchange="okx", ts_ms=t1 * 1000, pnl_usd=300.0)
    upsert_weekly_row(memdb, exchange="okx", now_ts=t1, account_equity=100_000.0)
    _insert_close_fill(memdb, fill_id="f2", exchange="okx", ts_ms=t2 * 1000, pnl_usd=150.0)
    row = upsert_weekly_row(memdb, exchange="okx", now_ts=t2, account_equity=100_450.0)
    # start_equity NEVER moves once set this week (trace != reset).
    assert row.start_equity == 100_000.0
    # realized is the FRESH SUM of both close fills this week, not a delta add.
    assert row.realized_pnl_usd == 450.0
    assert row.trades == 2
    # current_equity reflects the LATEST snapshot passed in.
    assert row.current_equity == 100_450.0


def test_dropped_close_self_heals_on_next_upsert(memdb: sqlite3.Connection) -> None:
    """The bug② regression: a close's fill lands in the ledger but the
    weekly-trace upsert for it never ran (e.g. a fail-open exception
    mid-``safe_update_virtual_trace``). Fresh-SUM means the NEXT successful
    upsert recomputes the WHOLE week from ``fills`` and silently recovers the
    'lost' close — no accumulator state to have dropped it from."""
    t1 = int(datetime(2026, 7, 7, 9, 0, 0, tzinfo=UTC).timestamp())
    t2 = int(datetime(2026, 7, 7, 10, 0, 0, tzinfo=UTC).timestamp())
    # Close #1's fill is written (real fill ledger), but its upsert call is
    # simulated as having been dropped — NO upsert_weekly_row for it here.
    _insert_close_fill(memdb, fill_id="f1", exchange="okx", ts_ms=t1 * 1000, pnl_usd=500.0)
    # Close #2 DOES successfully call upsert.
    _insert_close_fill(memdb, fill_id="f2", exchange="okx", ts_ms=t2 * 1000, pnl_usd=25.0)
    row = upsert_weekly_row(memdb, exchange="okx", now_ts=t2, account_equity=100_525.0)
    # Both closes are reflected — the "lost" first delta self-healed.
    assert row.realized_pnl_usd == 525.0
    assert row.trades == 2


def test_week_boundary_creates_new_row_account_carries_over(
    memdb: sqlite3.Connection,
) -> None:
    """Crossing Monday starts a NEW week row; the account equity (caller-owned,
    continuously compounding) is NOT reset by this module — it is simply
    whatever value the caller passes as the new week's start_equity."""
    week1_ts = int(datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC).timestamp())  # Wed
    week2_ts = int(datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC).timestamp())  # next Mon

    _insert_close_fill(
        memdb, fill_id="f1", exchange="okx", ts_ms=week1_ts * 1000, pnl_usd=500.0,
    )
    row1 = upsert_weekly_row(memdb, exchange="okx", now_ts=week1_ts, account_equity=100_000.0)
    # Simulate the account having compounded to 100,500 by the next week —
    # the caller passes this carried-over value; NOT reset to 100,000.
    _insert_close_fill(
        memdb, fill_id="f2", exchange="okx", ts_ms=week2_ts * 1000, pnl_usd=75.0,
    )
    row2 = upsert_weekly_row(memdb, exchange="okx", now_ts=week2_ts, account_equity=100_500.0)
    assert row1.week_start_ts != row2.week_start_ts
    # New week's start_equity is the carried-over account value, not a reset
    # back to the original $100k seed.
    assert row2.start_equity == 100_500.0
    # New week's realized_pnl_usd starts FRESH (this week's fills only), while
    # the OLD week row is untouched (still readable, non-destructive).
    assert row2.realized_pnl_usd == 75.0
    preserved = get_week_row(memdb, exchange="okx", week_start=row1.week_start_ts)
    assert preserved is not None
    assert preserved.realized_pnl_usd == 500.0
    assert preserved.trades == 1


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
    )
    assert row.unrealized_pnl_usd == 42.0


def test_reconciled_position_fills_excluded_from_realized(
    memdb: sqlite3.Connection,
) -> None:
    """Mirrors ``virtual_account_equity``/``snapshot_q_equity``: a close fill
    whose position is tagged RECONCILED (tracking-failure) is excluded from
    the fresh-SUM, same single-owner convention throughout the ledger."""
    now_ts = int(datetime(2026, 7, 7, 9, 0, 0, tzinfo=UTC).timestamp())
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status) "
        "VALUES ('pos1', 'okx', 'SYM', 's', 's', 's', 'sell', 1.0, 'reconciled')"
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " is_close, pnl_usd, contribution_id) "
        "VALUES ('f1', 'okx', 'x:SYM', 's', 'sell', 100.0, 1.0, 0.0, 0.0, ?, "
        " 'o1', 1, 999.0, 'pos1')",
        (now_ts * 1000,),
    )
    row = upsert_weekly_row(memdb, exchange="okx", now_ts=now_ts, account_equity=100_000.0)
    assert row.realized_pnl_usd == 0.0
    assert row.trades == 0


def test_all_current_week_rows_multi_exchange(memdb: sqlite3.Connection) -> None:
    now_ts = int(datetime(2026, 7, 7, 9, 0, 0, tzinfo=UTC).timestamp())
    _insert_close_fill(memdb, fill_id="f1", exchange="okx", ts_ms=now_ts * 1000, pnl_usd=100.0)
    _insert_close_fill(
        memdb, fill_id="f2", exchange="capital", ts_ms=now_ts * 1000, pnl_usd=-200.0,
    )
    upsert_weekly_row(memdb, exchange="okx", now_ts=now_ts, account_equity=100_100.0)
    upsert_weekly_row(memdb, exchange="capital", now_ts=now_ts, account_equity=99_800.0)
    rows = all_current_week_rows(memdb, now_ts=now_ts)
    by_exchange = {r.exchange: r for r in rows}
    assert set(by_exchange) == {"okx", "capital"}
    assert by_exchange["okx"].realized_pnl_usd == 100.0
    assert by_exchange["capital"].realized_pnl_usd == -200.0


def test_get_week_row_missing_returns_none(memdb: sqlite3.Connection) -> None:
    assert get_week_row(memdb, exchange="okx", week_start=0) is None
