"""Step N (2026-06-23) + 2026-07-07 re-base — the two ledgers AGREE on a seeded
DB; the realised-R denominator is now per-trade ``risk_usd``.

End-to-end-ish: seed entry fills + entry_atr_pct + risk_usd on positions, run
``real_pnl_r_from_fills`` (the close path), and assert:
  * realised R == realised_r(pnl_usd, risk_usd) (the per-trade staked-risk
    rescaling — 2026-07-07 replaces the per-stream R_budget denominator),
  * sign(R) == sign(pnl_usd) for EVERY symbol (ledgers agree on sign),
  * R-ranking == $-ranking WITHIN a venue (same bleeders, same order),
  * the ±100 clamp holds,
  * the realised-R denominator now DOES depend on the per-trade risk_usd (the
    OPPOSITE of the retired Step N stream-common design) — two trades with the
    SAME $ but different risk_usd get DIFFERENT R, and a legacy NULL-risk_usd
    row yields R == 0.0 (unknowable risk, never guessed).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.metrics.risk_unit import R_CLAMP, realised_r, risk_usd_at_entry
from polaris.scripts._production_close_helpers import real_pnl_r_from_fills
from polaris.scripts._smoke_fills import SimulatedTrade


def _seed_trade(
    conn: sqlite3.Connection,
    *,
    pid: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    atr_pct: float,
    venue: str = "okx",
    persist_risk_usd: bool = True,
) -> SimulatedTrade:
    """Seed an entry fill + position (with entry_atr_pct + risk_usd) and a few
    recent bars, then return a SimulatedTrade ready for the close path."""
    inst = f"{venue}:{symbol}"
    size_usd = entry_price * qty
    risk_usd = (
        risk_usd_at_entry(entry_price=entry_price, entry_atr_pct=atr_pct, base_qty=qty)
        if persist_risk_usd
        else None
    )
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, entry_atr_pct, entry_atr_timeframe, risk_usd) "
        "VALUES (?, ?, ?, ?, 's', 's', 's', ?, ?, 'open', 1000, 0, ?, '1m', ?)",
        (pid, venue, symbol, f"crypto:{symbol}", side, qty, atr_pct, risk_usd),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
        " side, size_usd, fill_price, ts_ms, order_id, contribution_id, "
        " base_qty, is_close) "
        "VALUES (?, ?, ?, 's', ?, ?, ?, 1000, ?, ?, ?, 0)",
        (f"{pid}_o", venue, inst, side, size_usd, entry_price,
         f"{pid}_ord", pid, qty),
    )
    # A couple of recent bars so the close path has a window (the exit price is
    # passed as an override, so the bar trend does not drive pnl here).
    for i, ts in enumerate((900, 950, 1000)):
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
            " bar_interval, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 1.0)",
            (inst, f"crypto:{symbol}", venue, symbol, ts, entry_price,
             entry_price * (1 + atr_pct), entry_price * (1 - atr_pct),
             entry_price + i),
        )
    return SimulatedTrade(
        signal_id="sig", strategy_id="s", venue=venue, symbol=symbol, side=side,
        entry_price=entry_price, base_qty=qty, notional_usd=size_usd,
        open_ts=1000, position_id=pid,
    )


def test_ledgers_agree_sign_and_ranking(memdb: sqlite3.Connection) -> None:
    # Three symbols, varied move + atr, same long side + venue. Compute R + $ via
    # the close path and assert the two ledgers agree on sign AND ranking.
    specs = {
        "BNT": dict(entry_price=0.50, exit_price=0.41, qty=1000.0, atr_pct=0.02),
        "ADA": dict(entry_price=0.60, exit_price=0.57, qty=500.0, atr_pct=0.015),
        "BTC": dict(entry_price=60000.0, exit_price=63000.0, qty=0.05, atr_pct=0.01),
    }
    r_by_sym: dict[str, float] = {}
    usd_by_sym: dict[str, float] = {}
    for sym, kw in specs.items():
        trade = _seed_trade(memdb, pid=f"p_{sym}", symbol=sym, side="long", **kw)
        pnl_r, pnl_usd, _exit = real_pnl_r_from_fills(
            memdb, trade=trade, exit_price_override=kw["exit_price"],
        )
        r_by_sym[sym] = pnl_r
        usd_by_sym[sym] = pnl_usd

    # 1) sign agreement, symbol by symbol.
    for sym in specs:
        assert (r_by_sym[sym] < 0) == (usd_by_sym[sym] < 0)
        assert (r_by_sym[sym] > 0) == (usd_by_sym[sym] > 0)

    # 2) ranking agreement — worst $ == worst R, same order (one venue → R is a
    #    pure linear rescale of $).
    by_dollar = sorted(specs, key=lambda s: usd_by_sym[s])
    by_r = sorted(specs, key=lambda s: r_by_sym[s])
    assert by_dollar == by_r
    # BNT is the bleeder in BOTH ledgers (the audit's R-vs-$ disagreement gone).
    assert by_dollar[0] == "BNT"


def test_realised_r_equals_pnl_usd_over_risk_usd(memdb: sqlite3.Connection) -> None:
    """2026-07-07: the denominator is THIS position's own ``risk_usd``, NOT the
    per-stream R_budget."""
    trade = _seed_trade(
        memdb, pid="p1", symbol="ETH", side="long",
        entry_price=2000.0, exit_price=2060.0, qty=1.0, atr_pct=0.01, venue="okx",
    )
    pnl_r, pnl_usd, _exit = real_pnl_r_from_fills(
        memdb, trade=trade, exit_price_override=2060.0,
    )
    expected_risk_usd = risk_usd_at_entry(entry_price=2000.0, entry_atr_pct=0.01, base_qty=1.0)
    assert pnl_r == pytest.approx(realised_r(pnl_usd=pnl_usd, risk_usd=expected_risk_usd))


def test_realised_r_depends_on_risk_usd_not_venue(memdb: sqlite3.Connection) -> None:
    """2026-07-07 re-base: the OLD Step-N design made a same-$ OKX/Alpaca loss
    COMPARABLE via a per-stream constant regardless of the position's actual
    staked risk. The re-based ruler is the OPPOSITE — R now varies with the
    position's OWN risk_usd (bigger stake -> smaller |R| for the same $), which
    is the whole point (a real per-trade risk unit, not a venue-wide proxy)."""
    t_small_risk = _seed_trade(
        memdb, pid="o", symbol="OKXSYM", side="long",
        entry_price=100.0, exit_price=98.0, qty=1.0, atr_pct=0.001, venue="okx",
    )
    t_big_risk = _seed_trade(
        memdb, pid="a", symbol="ALPSYM", side="long",
        entry_price=100.0, exit_price=98.0, qty=1.0, atr_pct=0.1265, venue="alpaca",
    )
    r_small_risk, usd_1, _ = real_pnl_r_from_fills(memdb, trade=t_small_risk, exit_price_override=98.0)
    r_big_risk, usd_2, _ = real_pnl_r_from_fills(memdb, trade=t_big_risk, exit_price_override=98.0)
    # Same $ outcome (qty 1, entry 100, exit 98 → −$2) but DIFFERENT risk_usd
    # (atr_pct 0.001 vs 0.1265) -> DIFFERENT |R|, the small-risk trade reading a
    # much LARGER |R| for the identical dollar loss.
    assert usd_1 == pytest.approx(usd_2)
    assert r_small_risk < 0 and r_big_risk < 0
    assert abs(r_small_risk) > abs(r_big_risk) * 10.0


def test_realised_r_scales_with_atr_anchor_via_risk_usd(memdb: sqlite3.Connection) -> None:
    """Two SAME-$ same-venue trades with different ATR anchors now get
    DIFFERENT realised R — risk_usd (derived from the anchor) IS the
    denominator (the opposite of the retired Step-N independence property)."""
    kw = dict(entry_price=100.0, exit_price=97.0, qty=10.0, venue="okx")
    t_small_atr = _seed_trade(memdb, pid="sa", symbol="SA", side="long", atr_pct=0.001, **kw)
    t_big_atr = _seed_trade(memdb, pid="ba", symbol="BA", side="long", atr_pct=0.1265, **kw)
    r_small, _u1, _ = real_pnl_r_from_fills(memdb, trade=t_small_atr, exit_price_override=97.0)
    r_big, _u2, _ = real_pnl_r_from_fills(memdb, trade=t_big_atr, exit_price_override=97.0)
    assert r_small != pytest.approx(r_big)
    assert abs(r_small) > abs(r_big)


def test_legacy_null_risk_usd_yields_zero_r(memdb: sqlite3.Connection) -> None:
    """2026-07-07: R IS keyed on risk_usd now, so a NULL-risk_usd legacy row
    yields R == 0.0 (unknowable risk, never guessed) while a persisted-risk_usd
    row on the SAME $ outcome gets a real nonzero R."""
    kw = dict(entry_price=100.0, exit_price=97.0, qty=10.0, atr_pct=0.01, venue="okx")
    t_persisted = _seed_trade(
        memdb, pid="pp", symbol="AAA", side="long", persist_risk_usd=True, **kw,
    )
    t_legacy = _seed_trade(
        memdb, pid="ll", symbol="BBB", side="long", persist_risk_usd=False, **kw,
    )
    r_persisted, _u1, _e1 = real_pnl_r_from_fills(
        memdb, trade=t_persisted, exit_price_override=kw["exit_price"],
    )
    r_legacy, _u2, _e2 = real_pnl_r_from_fills(
        memdb, trade=t_legacy, exit_price_override=kw["exit_price"],
    )
    assert r_persisted != 0.0
    assert r_legacy == 0.0


def test_close_path_r_clamped_at_100(memdb: sqlite3.Connection) -> None:
    # A huge move must clamp at ±100 (no hidden ±10).
    trade = _seed_trade(
        memdb, pid="big", symbol="ZZZ", side="long",
        entry_price=100.0, exit_price=100000.0, qty=1000.0, atr_pct=0.01, venue="okx",
    )
    pnl_r, _u, _e = real_pnl_r_from_fills(
        memdb, trade=trade, exit_price_override=100000.0,
    )
    assert pnl_r == pytest.approx(R_CLAMP)
