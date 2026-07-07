"""Step N (2026-06-23) — dashboard rollup invariants for the measurement redesign.

Pins the three locked guarantees end-to-end on a seeded DB:
  1. fills.$ is the source of truth — every R aggregation re-derives R from
     ``fills.pnl_usd`` (never the stored, possibly venue-skewed ``positions.pnl_r``),
     and the per-strategy / per-ticker / per-venue $ rollups MATCH the fills sum.
  2. pnl_r is the STREAM-COMMON comparable R — the same $ outcome maps to a
     comparable R across venues (not 200× apart).
  3. RECONCILED positions are EXCLUDED from every realized-R aggregation (count +
     $ drift surface separately, never summed into R/PF).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.metrics.risk_unit import r_budget_for_venue, realised_r_stream
from polaris.scripts.dashboard.snapshot_queries import (
    _avg_r_by_strategy,
    _closed_position_r,
    _recent_closed_by_venue,
    _strategy_stats,
    _ticker_stats,
)

_OKX_BUDGET = r_budget_for_venue("okx")


def _seed(
    conn: sqlite3.Connection,
    *,
    pid: str,
    venue: str,
    symbol: str,
    strategy: str,
    pnl_usd: float,
    status: str = "closed",
    entry_fee_usd: float = 0.0,
    close_fee_usd: float = 0.0,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, closed_ts, swap_count, pnl_r) "
        # Stamp a DELIBERATELY WRONG legacy pnl_r (the old venue-skewed value) so
        # the test proves the read path re-derives from fills and ignores it.
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 1.0, ?, 1000, 2000, 0, -999.0)",
        (pid, venue, symbol, f"g:{symbol}", strategy, strategy, strategy, status),
    )
    if entry_fee_usd:
        # The OPEN leg (is_close=0) — carries its own real fee, zero pnl (pnl
        # only realizes on the close leg). audit2 P0-2 ②: win/PF must count it.
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
            " size_usd, fill_price, ts_ms, order_id, contribution_id, base_qty, "
            " pnl_usd, fee_usd, is_close) "
            "VALUES (?, ?, ?, ?, 'buy', 1000.0, 100.0, 1000, ?, ?, 1.0, 0.0, ?, 0)",
            (f"{pid}_o", venue, f"{venue}:{symbol}", strategy, f"{pid}_ord",
             pid, entry_fee_usd),
        )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, ts_ms, order_id, contribution_id, base_qty, "
        " pnl_usd, fee_usd, is_close) "
        "VALUES (?, ?, ?, ?, 'sell', 1000.0, 100.0, 1500, ?, ?, 1.0, ?, ?, 1)",
        (f"{pid}_c", venue, f"{venue}:{symbol}", strategy, f"{pid}_ord", pid,
         pnl_usd, close_fee_usd),
    )


def test_r_aggregations_ignore_stored_pnl_r_use_fills(memdb: sqlite3.Connection) -> None:
    # Stored pnl_r is -999 (legacy garbage); the read path must re-derive from
    # fills.pnl_usd / R_budget(venue), NOT trust the stored value.
    _seed(memdb, pid="p1", venue="okx", symbol="BTC", strategy="tsmom", pnl_usd=_OKX_BUDGET)
    rows = _closed_position_r(memdb)
    assert len(rows) == 1
    assert rows[0].r == pytest.approx(1.0)  # 1R / 1R, not -999
    assert _avg_r_by_strategy(memdb)["tsmom"] == pytest.approx(1.0)
    assert _ticker_stats(memdb, now_s=3000)[0].sum_r == pytest.approx(1.0)


def test_same_dollar_loss_comparable_r_across_venues(memdb: sqlite3.Connection) -> None:
    _seed(memdb, pid="o", venue="okx", symbol="X", strategy="s", pnl_usd=-2.0)
    _seed(memdb, pid="a", venue="alpaca", symbol="Y", strategy="s", pnl_usd=-2.0)
    r_by_sym = {t.symbol: t.sum_r for t in _ticker_stats(memdb, now_s=3000)}
    ratio = abs(r_by_sym["X"]) / abs(r_by_sym["Y"])
    assert 0.2 < ratio < 5.0  # not the 200× ATR-anchor skew


def test_reconciled_excluded_from_every_r_surface(memdb: sqlite3.Connection) -> None:
    _seed(memdb, pid="ok", venue="okx", symbol="BTC", strategy="s", pnl_usd=_OKX_BUDGET)
    # A reconciled tracking-failure with a big loss fill — must NOT enter R/PF.
    _seed(memdb, pid="rec", venue="okx", symbol="DOGE", strategy="s",
          pnl_usd=-20 * _OKX_BUDGET, status="reconciled")

    # _closed_position_r (the shared source) excludes it.
    syms = {r.symbol for r in _closed_position_r(memdb)}
    assert "DOGE" not in syms and "BTC" in syms
    # avg_r / ticker R exclude it.
    assert _avg_r_by_strategy(memdb)["s"] == pytest.approx(1.0)
    assert {t.symbol for t in _ticker_stats(memdb, now_s=3000)} == {"BTC"}
    # recent-closed-by-venue: the close FILL still appears (it is a real fill),
    # but its R is the stream-common per-fill R (not summed into the R ledger).
    recent = _recent_closed_by_venue(memdb)
    okx_syms = {c.symbol for c in recent.get("okx", [])}
    assert "DOGE" in okx_syms  # the fill is real; reconcile excludes only the R-rollup


def test_strategy_pf_is_dollar_based_and_matches_fills(memdb: sqlite3.Connection) -> None:
    # $-based (gross_win/gross_loss) → denominator-agnostic. No fees seeded here,
    # so NET == GROSS (see test_strategy_win_pf_are_fee_net below for the fee case).
    _seed(memdb, pid="w1", venue="okx", symbol="BTC", strategy="s", pnl_usd=300.0)
    _seed(memdb, pid="w2", venue="okx", symbol="ETH", strategy="s", pnl_usd=100.0)
    _seed(memdb, pid="l1", venue="okx", symbol="SOL", strategy="s", pnl_usd=-200.0)
    stats = {st.strategy_id: st for st in _strategy_stats(memdb, now_s=3000, positions=[])}
    s = stats["s"]
    assert s.pf == pytest.approx(400.0 / 200.0)  # gross_win 400 / gross_loss 200
    assert s.pnl_usd == pytest.approx(200.0)     # Σ fills.pnl_usd
    # avg_r is the stream-common R: mean((300+100-200)/1580 per trade).
    expected_avg_r = sum(
        realised_r_stream(pnl_usd=p, venue="okx") for p in (300.0, 100.0, -200.0)
    ) / 3.0
    assert s.avg_r == pytest.approx(expected_avg_r)
    # and the $ rollup matches the raw fills sum (truth check).
    fills_sum = memdb.execute(
        "SELECT SUM(pnl_usd) FROM fills WHERE is_close=1 AND strategy_id='s'"
    ).fetchone()[0]
    assert s.pnl_usd == pytest.approx(fills_sum)


def test_strategy_win_pf_are_fee_net(memdb: sqlite3.Connection) -> None:
    """audit2 P0-2 ②: win_rate/PF must count the real fee on BOTH legs (entry +
    close), matching the core ``won = pnl_r_net > 0`` verdict direction — not the
    fee-free GROSS ``fills.pnl_usd > 0`` the old query used. Repro shape of the
    live 2026-07-02 audit finding (gross 29 wins vs net 9 wins): three small
    +gross-pnl closes that are actually net losses once round-trip fee is
    counted, alongside one real net winner."""
    # Small +gross scalp, round-trip fee > gross pnl → net LOSS.
    _seed(memdb, pid="s1", venue="okx", symbol="BTC", strategy="s", pnl_usd=6.0,
          entry_fee_usd=5.0, close_fee_usd=5.0)
    _seed(memdb, pid="s2", venue="okx", symbol="ETH", strategy="s", pnl_usd=4.0,
          entry_fee_usd=5.0, close_fee_usd=5.0)
    _seed(memdb, pid="s3", venue="okx", symbol="SOL", strategy="s", pnl_usd=2.0,
          entry_fee_usd=5.0, close_fee_usd=5.0)
    # One real net winner: gross pnl comfortably exceeds the round-trip fee.
    _seed(memdb, pid="w1", venue="okx", symbol="DOGE", strategy="s", pnl_usd=300.0,
          entry_fee_usd=1.0, close_fee_usd=1.0)
    stats = {st.strategy_id: st for st in _strategy_stats(memdb, now_s=3000, positions=[])}
    s = stats["s"]
    # GROSS would have shown 4/4 wins (100%); NET is 1/4 (25%).
    assert s.wr_pct == pytest.approx(25.0)
    # PF net: win $ = (300-2)=298; loss $ = (6-10)+(4-10)+(2-10) → abs = 4+6+8=18.
    assert s.pf == pytest.approx(298.0 / 18.0)
