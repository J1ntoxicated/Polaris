"""Step N (2026-06-23) — per-ticker cumulative STREAM-COMMON R, derived from fills.

Surfaces where the bot bleeds / wins BY SYMBOL using the stream-common realised R
(``fills.pnl_usd / R_budget(stream)``), re-derived at read time so the R bleeders
AGREE with the dollar bleeders AND are comparable across venues (no venue-skewed
ATR denominator, no stored-pnl_r read). RECONCILED (tracking-failure) positions
are EXCLUDED from the R ledger (Jin locked) — a reconcile is not a trade; its
drift is a SEPARATE $ counter. open / no-close-fill rows excluded too.
Display-only; sorted worst-first.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.metrics.risk_unit import r_budget_for_venue
from polaris.scripts.dashboard.snapshot_queries import (
    _avg_r_by_strategy,
    _ticker_stats,
)

_OKX_BUDGET = r_budget_for_venue("okx")


def _closed(
    conn: sqlite3.Connection,
    *,
    pid: str,
    symbol: str,
    pnl_usd: float,
    venue: str = "okx",
    strategy: str = "s",
    status: str = "closed",
    with_close_fill: bool = True,
) -> None:
    """Seed a position + (optionally) its close fill carrying the realized $."""
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, closed_ts, swap_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 1.0, ?, 1000, 2000, 0)",
        (pid, venue, symbol, f"crypto:{symbol}", strategy, strategy, strategy, status),
    )
    if with_close_fill:
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
            " size_usd, fill_price, ts_ms, order_id, contribution_id, base_qty, "
            " pnl_usd, is_close) "
            "VALUES (?, ?, ?, ?, 'sell', 1000.0, 100.0, 1500, ?, ?, 1.0, ?, 1)",
            (f"{pid}_c", venue, f"{venue}:{symbol}", strategy, f"{pid}_ord", pid, pnl_usd),
        )


def test_ticker_stats_stream_common_r_worst_first(memdb: sqlite3.Connection) -> None:
    # $ outcomes per symbol → R = pnl_usd / R_budget(okx).
    _closed(memdb, pid="p1", symbol="BTC", pnl_usd=3160.0)   # +2R
    _closed(memdb, pid="p2", symbol="BTC", pnl_usd=1580.0)   # +1R
    _closed(memdb, pid="p3", symbol="ETH", pnl_usd=-7900.0)  # −5R
    # reconciled (tracking failure) must be EXCLUDED from the R ledger — even with
    # a close fill carrying $, its status gates it out of the JOIN.
    _closed(memdb, pid="p4", symbol="DOGE", pnl_usd=-31600.0, status="reconciled")
    # open + no close fill must be EXCLUDED.
    _closed(memdb, pid="p5", symbol="SOL", pnl_usd=0.0, status="open",
            with_close_fill=False)

    out = _ticker_stats(memdb, now_s=3000)
    by_sym = {t.symbol: t for t in out}

    assert "SOL" not in by_sym
    assert "DOGE" not in by_sym  # reconciled excluded from R aggregation
    assert by_sym["BTC"].sum_r == pytest.approx(3.0)  # (3160+1580)/1580
    assert by_sym["BTC"].n == 2
    assert by_sym["BTC"].wr_pct == pytest.approx(100.0)
    assert by_sym["ETH"].sum_r == pytest.approx(-5.0)
    # worst (most negative R) first; only real-fill closes ranked.
    assert [t.symbol for t in out] == ["ETH", "BTC"]


def test_ticker_stats_comparable_across_venues(memdb: sqlite3.Connection) -> None:
    """Same −$2 loss on OKX and Alpaca → comparable R (not 200× apart)."""
    _closed(memdb, pid="o", symbol="OKXSYM", pnl_usd=-2.0, venue="okx")
    _closed(memdb, pid="a", symbol="ALPSYM", pnl_usd=-2.0, venue="alpaca")
    by_sym = {t.symbol: t for t in _ticker_stats(memdb, now_s=3000)}
    r_okx = by_sym["OKXSYM"].sum_r
    r_alp = by_sym["ALPSYM"].sum_r
    assert r_okx < 0 and r_alp < 0
    assert 0.2 < abs(r_okx) / abs(r_alp) < 5.0


def test_avg_r_by_strategy_stream_common(memdb: sqlite3.Connection) -> None:
    # Two strategies, mean stream-common R per strategy; reconciled excluded.
    _closed(memdb, pid="a1", symbol="BTC", pnl_usd=1580.0, strategy="tsmom")   # +1R
    _closed(memdb, pid="a2", symbol="ETH", pnl_usd=4740.0, strategy="tsmom")   # +3R
    _closed(memdb, pid="b1", symbol="SOL", pnl_usd=-1580.0, strategy="rsi")    # −1R
    _closed(memdb, pid="r1", symbol="DOGE", pnl_usd=-9999.0, strategy="tsmom",
            status="reconciled")
    avg = _avg_r_by_strategy(memdb)
    assert avg["tsmom"] == pytest.approx(2.0)   # mean(1, 3) — reconciled excluded
    assert avg["rsi"] == pytest.approx(-1.0)


def test_ticker_stats_limit_caps_to_worst_n(memdb: sqlite3.Connection) -> None:
    for i in range(20):
        _closed(memdb, pid=f"q{i}", symbol=f"T{i}", pnl_usd=float(-i) * _OKX_BUDGET)
    out = _ticker_stats(memdb, now_s=3000, limit=5)
    assert len(out) == 5
    assert out[0].symbol == "T19"  # −19R, the worst
