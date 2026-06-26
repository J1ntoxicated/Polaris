"""Dashboard PnL coherence: TRADES-tab net must reconcile with the headline,
and open-position uPnL/size must be USD for non-USD-quote instruments.

DEMO/PAPER, virtual funds. Seeds a temp SQLite DB and asserts the read-only
snapshot layer surfaces self-consistent dollar figures. Pure display layer —
these queries touch only the snapshot/visualizer read path; no trading
behavior, sizing, gating, exit, strategy, or per-fill dollar truth is altered.
Aggressive bias preserved.

Covered (Jin frustration: "내 계산이 안 맞아"):
- #49 ROUND-TRIP FEE — the per-position ``net_usd`` must subtract BOTH legs'
  real fee (entry + close), so Σ(per-position net) reconciles with the realised
  headline ``daily_pnl_usd`` (which nets ``SUM(fee_usd)`` over ALL fills). The
  old code subtracted only the close-leg fee, leaving the entry-leg fee as an
  unexplained gap between the two totals.
- #50 QUOTE→USD — for a non-USD-quote position (J225 = JPY), the open-position
  ``upnl_usd`` / ``size_usd`` must be converted quote→USD via the latest bar of
  the conversion pair (USDJPY), so the headline equity/DD/Sharpe (which sum
  ``upnl_usd``) are not polluted by raw-JPY magnitudes. ``delta_pct`` is a ratio
  and stays unconverted; the price cells keep their quote-ccy label.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.core.economics.fees import real_fee_usd
from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_queries import (
    _cell_mult_lookup,
    _entry_price_lookup,
    _last_prices,
    _now_s,
    _read_positions,
)
from polaris.scripts.dashboard.snapshot_sections import _recent_closed_trades
from polaris.storage.schema import ALL_DDL, init_db

# ---------------------------------------------------------------------------
# #49 — per-position net subtracts BOTH legs' real fee → reconciles headline
# ---------------------------------------------------------------------------


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "coherence.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _seed_okx_roundtrip(conn: sqlite3.Connection, *, now_ms: int) -> None:
    """One OKX long round-trip: open @100 (notional 1000), close @110 (1100)."""
    fills = [
        # entry leg — is_close=0, size_usd = entry notional 1000
        ("f_open", "okx", "okx:SOL-USDT", "tsmom", "buy", 1000.0, 100.0, 0.0,
         1.0, now_ms - 300_000, "o1", "pos1", 0.0, 0, 10.0, 1000.0, "filled"),
        # close leg — is_close=1, size_usd = close notional 1100, gross +100
        ("f_close", "okx", "okx:SOL-USDT", "tsmom", "sell", 1100.0, 110.0, 0.0,
         1.0, now_ms - 100_000, "o2", "pos1", 100.0, 1, 10.0, 1100.0, "filled"),
    ]
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        fills,
    )
    conn.commit()


def test_per_position_net_subtracts_both_leg_fees(tmp_path: Path) -> None:
    """net_usd = gross − close_real_fee − entry_real_fee (full round trip)."""
    db_path = _mkdb(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        _seed_okx_roundtrip(conn, now_ms=int(time.time() * 1000))
        trades = _recent_closed_trades(conn, n=10)
        assert trades, "expected one closed round-trip"
        t = trades[0]
        # entry leg reconstructed from the paired open fill: entry_px=100,
        # qty=base_qty=10 → entry notional 1000. close notional = size_usd 1100.
        entry_fee = real_fee_usd("okx", 100.0 * 10.0)  # entry notional 1000
        close_fee = real_fee_usd("okx", 1100.0)        # close notional 1100
        assert t.real_fee_usd == close_fee
        # net subtracts BOTH legs (the regression: it used to drop the entry leg)
        assert abs(t.net_usd - (t.pnl_usd - close_fee - entry_fee)) < 1e-9
        # the FEE$ column (gross − net) is now the full round-trip fee
        assert abs((t.pnl_usd - t.net_usd) - (entry_fee + close_fee)) < 1e-9
    finally:
        conn.close()


def test_trades_net_sum_reconciles_headline(tmp_path: Path) -> None:
    """Σ(per-position net) == realised headline daily_pnl_usd.

    The headline nets ``SUM(fee_usd)`` over ALL fills. Seed both legs with a
    stored real fee equal to the schedule recompute so the two paths agree to
    the cent — proving the entry-leg fee no longer leaks between them.
    """
    db_path = _mkdb(tmp_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    entry_fee = real_fee_usd("okx", 1000.0)
    close_fee = real_fee_usd("okx", 1100.0)
    fills = [
        ("f_open", "okx", "okx:SOL-USDT", "tsmom", "buy", 1000.0, 100.0,
         entry_fee, 1.0, int(time.time() * 1000) - 300_000, "o1", "pos1",
         0.0, 0, 10.0, 1000.0, "filled"),
        ("f_close", "okx", "okx:SOL-USDT", "tsmom", "sell", 1100.0, 110.0,
         close_fee, 1.0, int(time.time() * 1000) - 100_000, "o2", "pos1",
         100.0, 1, 10.0, 1100.0, "filled"),
    ]
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        fills,
    )
    conn.close()

    snap = collect_snapshot(db_path=db_path)
    net_sum = sum(t.net_usd for t in snap.recent_trades)
    assert abs(net_sum - snap.daily_pnl_usd) < 1e-6, (
        f"Σ per-position net ({net_sum}) must equal headline "
        f"daily_pnl_usd ({snap.daily_pnl_usd})"
    )


# ---------------------------------------------------------------------------
# #50 — open-position uPnL / size_usd are USD even for a JPY-quote instrument
# ---------------------------------------------------------------------------


def _seed_j225_position(conn: sqlite3.Connection) -> float:
    """Open J225 LONG (JPY quote) + a USDJPY conversion bar. Returns the rate."""
    now_s = _now_s()
    now_ms = now_s * 1000
    usdjpy = 150.0  # 1 USD = 150 JPY → rate JPY→USD = 1/150
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, product_class, stream_id, signal_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
        "status, opened_ts, closed_ts, swap_count, stop_price, peak_price, "
        "trough_price, mfe_r, mae_r, exit_state, entry_regime) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("p_j225", "capital", "J225", "J225", "cfd", "B_capital_cfd", "s2",
         "tick_micro_rever", "tick_micro_rever", "tick_micro_rever", "long",
         2.0, "open", now_s - 120, None, 0, 71000.0, 72500.0, 71000.0, 0.8,
         -0.2, "open", "chop"),
    )
    # entry fill so the entry-price lookup resolves @72000 JPY
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("f_j", "capital", "capital:J225", "tick_micro_rever", "buy",
         0.0, 72000.0, 0.0, 1.0, now_ms - 120_000, "oj", "p_j225",
         0.0, 0, 2.0, 0.0, "filled"),
    )
    # last bar for J225 (current px 72300 JPY) + the USDJPY conversion bar
    conn.executemany(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("capital:J225", "J225", "capital", "J225", "1m", now_s - 1,
             72200.0, 72400.0, 72100.0, 72300.0, 5.0),
            ("capital:USDJPY", "USDJPY", "capital", "USDJPY", "1m", now_s - 1,
             usdjpy, usdjpy, usdjpy, usdjpy, 1.0),
        ],
    )
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, "
        "underlying_group_id, asset_class, product_class, stream_id, "
        "quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
        "depth_10bps_usd, signal_density_7d, last_price, listing_ts, "
        "last_seen_ts, is_active, active_reason) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("capital", "J225", "capital:J225", "J225", "indices", "cfd",
         "B_capital_cfd", "USD", "live", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
         None, now_ms, 1, None),
    )
    conn.commit()
    return 1.0 / usdjpy


def test_jpy_position_upnl_and_size_are_usd(tmp_path: Path) -> None:
    """J225 (JPY quote): upnl_usd / size_usd converted via USDJPY; delta_pct not."""
    db = tmp_path / "j225.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        rate = _seed_j225_position(conn)  # JPY→USD = 1/150
        positions = _read_positions(
            conn,
            now_s=_now_s(),
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup={},
        )
        j = next(p for p in positions if p.symbol == "J225")
        # raw JPY: (72300 − 72000) × 2 = 600 JPY; size = 72000 × 2 = 144000 JPY
        raw_upnl_jpy = (72300.0 - 72000.0) * 2.0
        raw_size_jpy = 72000.0 * 2.0
        assert abs(j.upnl_usd - raw_upnl_jpy * rate) < 1e-6, (
            f"upnl must be USD ({raw_upnl_jpy * rate}), got {j.upnl_usd}"
        )
        assert abs(j.size_usd - raw_size_jpy * rate) < 1e-6, (
            f"size_usd must be USD ({raw_size_jpy * rate}), got {j.size_usd}"
        )
        # delta_pct is a ratio → unaffected by the conversion (≈ +0.4167%)
        assert abs(j.delta_pct - ((72300.0 - 72000.0) / 72000.0 * 100.0)) < 1e-6
        # the price cell keeps its quote-ccy label (#11 untouched)
        assert j.quote_ccy == "JPY"
    finally:
        conn.close()


def test_usd_quote_position_rate_is_identity(tmp_path: Path) -> None:
    """A USD/USDT-quote position is unchanged (rate = 1.0, no regression)."""
    db = tmp_path / "btc.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    now_s = _now_s()
    now_ms = now_s * 1000
    try:
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, "
            "underlying_group_id, product_class, stream_id, signal_id, "
            "strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
            "status, opened_ts, closed_ts, swap_count, stop_price, peak_price, "
            "trough_price, mfe_r, mae_r, exit_state, entry_regime) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p_btc", "okx", "BTC-USDT", "BTC", "spot", "A_okx_crypto", "s1",
             "tsmom", "tsmom", "tsmom", "long", 1.0, "open", now_s - 120, None,
             0, 95.0, 110.0, 98.0, 1.5, -0.4, "open", "bull_trend"),
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f_b", "okx", "okx:BTC-USDT", "tsmom", "buy", 100.0, 100.0, 0.0,
             1.0, now_ms - 120_000, "ob", "p_btc", 0.0, 0, 1.0, 100.0, "filled"),
        )
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, "
            "symbol, bar_interval, ts, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("okx:BTC-USDT", "BTC", "okx", "BTC-USDT", "1m", now_s - 1,
             110.0, 111.0, 109.0, 110.0, 5.0),
        )
        conn.execute(
            "INSERT INTO universe (venue, symbol, instrument_id, "
            "underlying_group_id, asset_class, product_class, stream_id, "
            "quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
            "depth_10bps_usd, signal_density_7d, last_price, listing_ts, "
            "last_seen_ts, is_active, active_reason) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("okx", "BTC-USDT", "okx:BTC-USDT", "BTC", "crypto", "spot",
             "A_okx_crypto", "USDT", "live", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
             None, now_ms, 1, None),
        )
        conn.commit()
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup={},
        )
        b = next(p for p in positions if p.symbol == "BTC-USDT")
        # USDT is USD-equivalent → rate 1.0 → values are the raw numbers
        assert abs(b.upnl_usd - (110.0 - 100.0) * 1.0) < 1e-6
        assert abs(b.size_usd - 100.0 * 1.0) < 1e-6
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# open-position uPnL NET of the expected round-trip real fee (Jin 2026-06-26)
# ---------------------------------------------------------------------------


def test_jpy_position_upnl_net_subtracts_roundtrip_fee(tmp_path: Path) -> None:
    """J225 upnl_net = upnl_usd − (entry + expected-close) Capital real fee."""
    db = tmp_path / "j225net.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        rate = _seed_j225_position(conn)  # JPY→USD = 1/150, long qty 2 @72000→72300
        positions = _read_positions(
            conn,
            now_s=_now_s(),
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup={},
        )
        j = next(p for p in positions if p.symbol == "J225")
        entry_notional = 72000.0 * 2.0 * rate
        current_notional = 72300.0 * 2.0 * rate
        rt_fee = real_fee_usd("capital", entry_notional) + real_fee_usd(
            "capital", current_notional
        )
        assert abs(j.upnl_net_usd - (j.upnl_usd - rt_fee)) < 1e-9, (
            f"upnl_net must net the round-trip fee: got {j.upnl_net_usd}, "
            f"expected {j.upnl_usd - rt_fee}"
        )
        # both fee legs are subtracted → net is strictly below gross
        assert j.upnl_net_usd < j.upnl_usd
    finally:
        conn.close()


def test_gross_positive_but_net_negative_reads_as_loss(tmp_path: Path) -> None:
    """A tiny gross-positive OKX position flips NET-negative once both fee legs
    bite — exactly the case Jin saw (open profit, closed loss). The net field
    must be negative so the board colours the row red on net."""
    db = tmp_path / "thin.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    now_s = _now_s()
    now_ms = now_s * 1000
    try:
        # OKX long, entry 100, last 100.05 → gross +$0.05 on qty 1 (notional ~100)
        # OKX real taker = 10 bps/leg → round-trip ≈ 0.0010 × (100 + 100.05)
        # ≈ $0.20 > $0.05 gross → net ≈ −$0.15.
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, "
            "underlying_group_id, product_class, stream_id, signal_id, "
            "strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
            "status, opened_ts, closed_ts, swap_count, stop_price, peak_price, "
            "trough_price, mfe_r, mae_r, exit_state, entry_regime) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p_thin", "okx", "SOL-USDT", "SOL", "spot", "A_okx_crypto", "s1",
             "tsmom", "tsmom", "tsmom", "long", 1.0, "open", now_s - 60, None,
             0, 99.0, 100.05, 100.0, 0.1, -0.1, "open", "chop"),
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f_t", "okx", "okx:SOL-USDT", "tsmom", "buy", 100.0, 100.0, 0.0,
             1.0, now_ms - 60_000, "ot", "p_thin", 0.0, 0, 1.0, 100.0, "filled"),
        )
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, "
            "symbol, bar_interval, ts, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("okx:SOL-USDT", "SOL", "okx", "SOL-USDT", "1m", now_s - 1,
             100.05, 100.1, 100.0, 100.05, 5.0),
        )
        conn.execute(
            "INSERT INTO universe (venue, symbol, instrument_id, "
            "underlying_group_id, asset_class, product_class, stream_id, "
            "quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
            "depth_10bps_usd, signal_density_7d, last_price, listing_ts, "
            "last_seen_ts, is_active, active_reason) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("okx", "SOL-USDT", "okx:SOL-USDT", "SOL", "crypto", "spot",
             "A_okx_crypto", "USDT", "live", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
             None, now_ms, 1, None),
        )
        conn.commit()
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup={},
        )
        p = next(x for x in positions if x.symbol == "SOL-USDT")
        # gross is positive (open profit) ...
        assert p.upnl_usd > 0.0
        # ... but net is NEGATIVE once both fee legs bite → row reads as a loss
        assert p.upnl_net_usd < 0.0
        entry_fee = real_fee_usd("okx", 100.0)
        close_fee = real_fee_usd("okx", 100.05)
        assert abs(p.upnl_net_usd - (p.upnl_usd - entry_fee - close_fee)) < 1e-9
    finally:
        conn.close()
