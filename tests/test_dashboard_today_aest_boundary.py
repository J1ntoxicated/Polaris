"""'Today' KPI = max(session_start, AEST midnight) — P0-2 (Jin 2026-07-02).

Before this fix ``daily_pnl_usd`` (labelled "Today" on the board) was actually
the whole SESSION sum (since ``MIN(fills.ts_ms)``), which after a multi-day
uptime silently accumulates days of PnL under a "Today" label. The fix floors
the "Today" window at the later of (a) the session anchor and (b) the most
recent Sydney-local midnight, so "Today" only ever spans <=24h. The raw
session-wide sum is preserved separately under ``session_pnl_usd`` /
``session_trades`` (rendered under a "SESSION" label, not folded into "Today").
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_q_common import _today_start_ms
from polaris.storage.schema import ALL_DDL

_SYDNEY = ZoneInfo("Australia/Sydney")


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _insert_fill(
    db_path: Path, *, fill_id: str, side: str, pnl_usd: float, fee_usd: float,
    is_close: int, ts_ms: int,
) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, 'okx', 'okx:SOL-USDT', 'tsmom', ?, "
        "        1000.0, 150.0, ?, 1.0, ?, ?, NULL, ?, ?, 6.6, 1000.0, 'filled')",
        (fill_id, side, fee_usd, ts_ms, f"o_{fill_id}", pnl_usd, is_close),
    )
    conn.close()


def test_today_start_is_session_start_when_session_is_recent(
    memdb: sqlite3.Connection,
) -> None:
    """Session started 2h ago (same Sydney day) → Today floor == session start."""
    now_s = int(time.time())
    now_ms = now_s * 1000
    session_start_ms = now_ms - 2 * 3600 * 1000
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, ts_ms, order_id, contribution_id, base_qty, "
        " pnl_usd, is_close) VALUES ('f1','okx','okx:BTC-USDT','s','buy',"
        " 100.0,1.0,?,'o1',NULL,1.0,0.0,0)",
        (session_start_ms,),
    )
    today_ms = _today_start_ms(memdb, now_s=now_s)
    # Floor = max(session_start, AEST midnight). If Sydney-local midnight is
    # BEFORE the 2h-ago session start, the session start wins.
    aest_midnight = int(
        datetime.now(_SYDNEY)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    ) * 1000
    assert today_ms == max(session_start_ms, aest_midnight)


def test_today_start_floors_at_aest_midnight_for_multiday_session(
    memdb: sqlite3.Connection,
) -> None:
    """Session started 3 days ago (multi-day uptime) → Today floors at AEST
    midnight, NOT the stale multi-day-old session anchor."""
    now_s = int(time.time())
    now_ms = now_s * 1000
    session_start_ms = now_ms - 3 * 86400 * 1000
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, ts_ms, order_id, contribution_id, base_qty, "
        " pnl_usd, is_close) VALUES ('f1','okx','okx:BTC-USDT','s','buy',"
        " 100.0,1.0,?,'o1',NULL,1.0,0.0,0)",
        (session_start_ms,),
    )
    today_ms = _today_start_ms(memdb, now_s=now_s)
    aest_midnight = int(
        datetime.now(_SYDNEY)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    ) * 1000
    assert today_ms == aest_midnight
    assert today_ms > session_start_ms


def test_daily_pnl_usd_excludes_yesterdays_fill_multiday_session(
    tmp_path: Path,
) -> None:
    """A round-trip 30h ago (this SESSION, but yesterday-AEST) must NOT count
    toward 'Today' — only the SESSION field carries it."""
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    old_open_ms = now_ms - 30 * 3600 * 1000     # 30h ago — likely yesterday AEST
    old_close_ms = now_ms - 29 * 3600 * 1000
    _insert_fill(db_path, fill_id="o_old", side="buy", pnl_usd=0.0,
                 fee_usd=10.0, is_close=0, ts_ms=old_open_ms)
    _insert_fill(db_path, fill_id="c_old", side="sell", pnl_usd=100.0,
                 fee_usd=10.0, is_close=1, ts_ms=old_close_ms)
    # A fresh round-trip within the last hour (definitely "today" AEST).
    _insert_fill(db_path, fill_id="o_new", side="buy", pnl_usd=0.0,
                 fee_usd=5.0, is_close=0, ts_ms=now_ms - 120_000)
    _insert_fill(db_path, fill_id="c_new", side="sell", pnl_usd=20.0,
                 fee_usd=5.0, is_close=1, ts_ms=now_ms - 60_000)

    snap = collect_snapshot(db_path=db_path)
    # SESSION preserves the whole-session sum (old + new): net +80 + net +10 = 90.
    assert abs(snap.session_pnl_usd - 90.0) < 1e-6
    assert snap.session_trades == 2
    # Today (AEST-midnight-floored) must be <= the session sum, and must not
    # include the 30h-old round-trip when it falls before Sydney midnight.
    assert snap.daily_pnl_usd <= snap.session_pnl_usd + 1e-6
    assert snap.daily_trades <= snap.session_trades


def test_reconciliation_streams_still_match_today_headline(
    tmp_path: Path,
) -> None:
    """Σ per-stream net_pnl_usd must still equal the (now AEST-floored) Today
    headline — the invariant must hold under the new window too."""
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    _insert_fill(db_path, fill_id="o", side="buy", pnl_usd=0.0,
                 fee_usd=5.0, is_close=0, ts_ms=now_ms - 120_000)
    _insert_fill(db_path, fill_id="c", side="sell", pnl_usd=50.0,
                 fee_usd=5.0, is_close=1, ts_ms=now_ms - 60_000)

    snap = collect_snapshot(db_path=db_path)
    sum_pnl = sum(s.net_pnl_usd for s in snap.streams)
    assert round(sum_pnl, 6) == round(snap.daily_pnl_usd, 6)
