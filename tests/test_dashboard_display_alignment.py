"""Tests for the dashboard display-alignment fixes (4 issues).

DEMO/PAPER, virtual funds. Seeds a temp SQLite DB and asserts the read-only
snapshot layer surfaces correct display fields. Pure display layer — these
queries touch only the snapshot/visualizer read path; no trading behavior,
sizing, gating, exit, strategy, or venue calls. Aggressive bias preserved.

Covered (Jin frustration: DB is right, the screen confuses):
- (1) PROFIT vs FEE separated — ClosedTrade carries gross (pnl_usd) / fee
  (real_fee_usd) / net (net_usd), with net == gross - fee as the single truth.
- (2) POSITION DIRECTION — a close fill side 'sell' (a long exit) must not read
  as a short. ClosedTrade carries position_side (long/short) from positions.side.
- (3) ENTRY REGIME — positions.entry_regime is populated (chop/bull_trend/...);
  ClosedTrade.entry_regime + PositionRow.entry_regime carry it (not "-").
- (4) NON-USD QUOTE CCY — a J225 price is JPY, not raw. ClosedTrade.quote_ccy +
  PositionRow.quote_ccy carry the true quote currency; size_usd/pnl stay USD.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.scripts.dashboard.snapshot_queries import (
    _cell_mult_lookup,
    _entry_price_lookup,
    _last_prices,
    _now_s,
    _quote_ccy_for_symbol,
    _read_positions,
)
from polaris.scripts.dashboard.snapshot_sections import _recent_closed_trades
from polaris.storage.schema import init_db


def _seed(conn: sqlite3.Connection) -> None:
    now_s = _now_s()
    now_ms = now_s * 1000

    # --- positions: long BTC (okx) + short J225 (capital JPY) ----------------
    # column tuple mirrors test_dashboard_e2_tabs (through exit_state), then
    # entry_regime appended via a second column list below.
    positions = [
        # p_btc: a LONG okx position, closed (matches close fill c1 below).
        ("p_btc", "okx", "BTC-USDT", "BTC", "spot", "A_okx_crypto", "s1",
         "tsmom", "tsmom", "tsmom", "long", 1.0, "closed", now_s - 300,
         now_s - 100, 0, 95.0, 110.0, 98.0, 1.5, -0.4, "exited", "bull_trend"),
        # p_j225: a SHORT capital position (JPY quote), still open.
        ("p_j225", "capital", "J225", "J225", "cfd", "B_capital_cfd", "s2",
         "tick_micro_rever", "tick_micro_rever", "tick_micro_rever", "short",
         2.0, "open", now_s - 120, None, 0, 72000.0, 71000.0, 71800.0, 0.8,
         -0.2, "open", "crisis"),
    ]
    conn.executemany(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, product_class, stream_id, signal_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
        "status, opened_ts, closed_ts, swap_count, stop_price, peak_price, "
        "trough_price, mfe_r, mae_r, exit_state, entry_regime) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        positions,
    )

    # --- fills: an open + a close for the LONG BTC trade ---------------------
    # contribution_id == position_id ('p_btc') so the trade pairs exactly and
    # the position_side / entry_regime joins resolve. The CLOSE side is 'sell'
    # (a long exit) — issue (2): this must NOT make the trade read as a short.
    fills = [
        ("f_open", "okx", "okx:BTC-USDT", "tsmom", "buy", 1000.0, 100.0, 1.0,
         10.0, now_ms - 300000, "o1", "p_btc", 0.0, 0, 10.0, 1000.0, "filled"),
        ("f_close", "okx", "okx:BTC-USDT", "tsmom", "sell", 1100.0, 110.0, 1.1,
         10.0, now_ms - 100000, "o2", "p_btc", 100.0, 1, 10.0, 1100.0,
         "filled"),
    ]
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        fills,
    )

    # last price for J225 so entry/last resolve in the positions reader.
    conn.executemany(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [("capital:J225", "J225", "capital", "J225", "1m", now_s - 1,
          71800.0, 71900.0, 71700.0, 71847.0, 5.0)],
    )

    # universe rows — OKX quote_ccy correct; Capital J225 carries the wrong
    # 'USD' placeholder (issue 4 ground truth: discovery stamps USD for CFDs).
    conn.executemany(
        "INSERT INTO universe (venue, symbol, instrument_id, "
        "underlying_group_id, asset_class, product_class, stream_id, "
        "quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
        "depth_10bps_usd, signal_density_7d, last_price, listing_ts, "
        "last_seen_ts, is_active, active_reason) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("okx", "BTC-USDT", "okx:BTC-USDT", "BTC", "crypto", "spot",
             "A_okx_crypto", "USDT", "live", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
             None, now_ms, 1, None),
            ("capital", "J225", "capital:J225", "J225", "indices", "cfd",
             "B_capital_cfd", "USD", "live", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
             None, now_ms, 1, None),
        ],
    )
    conn.commit()


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "disp.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    _seed(conn)
    return conn


# --------------------------------------------------------------------------
# Issue (1) — gross / fee / net separated
# --------------------------------------------------------------------------


def test_trade_carries_gross_fee_net_split(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        trades = _recent_closed_trades(conn, n=10)
        assert trades, "expected one closed BTC trade"
        t = trades[0]
        # gross is the raw realised pnl from the close fill (pre-fee)
        assert t.pnl_usd == 100.0
        # net == gross − real fee is the single truth (real fee = the venue fee
        # the snapshot re-prices on the close notional; net subtracts the SAME).
        assert t.net_usd == t.pnl_usd - t.real_fee_usd
        # honest mirror of live: a small gross can net negative once fee bites.
        assert t.real_fee_usd > 0.0
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Issue (2) — position direction (long/short), not the close-fill side
# --------------------------------------------------------------------------


def test_trade_carries_position_side_not_close_side(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        trades = _recent_closed_trades(conn, n=10)
        t = trades[0]
        # the close FILL side is 'sell' (a long exit) ...
        assert t.side_close.lower() == "sell"
        # ... but the POSITION direction is LONG — the display must use this.
        assert t.position_side == "long"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Issue (3) — entry_regime carried (not "-")
# --------------------------------------------------------------------------


def test_trade_carries_entry_regime(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        trades = _recent_closed_trades(conn, n=10)
        t = trades[0]
        # entry_regime is the immutable regime at entry, from positions.
        assert t.entry_regime == "bull_trend"
    finally:
        conn.close()


def test_open_position_carries_entry_regime(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        now_s = _now_s()
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup={},
        )
        j225 = next(p for p in positions if p.symbol == "J225")
        assert j225.entry_regime == "crisis"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Issue (4) — non-USD quote currency label
# --------------------------------------------------------------------------


def test_quote_ccy_for_symbol_derivation() -> None:
    # Capital index J225 is JPY-quoted (the universe 'USD' placeholder is wrong).
    assert _quote_ccy_for_symbol("capital", "J225", "USD") == "JPY"
    # Capital FX pair: quote = trailing 3 letters of the 6-char pair.
    assert _quote_ccy_for_symbol("capital", "EURUSD", "USD") == "USD"
    assert _quote_ccy_for_symbol("capital", "AUDJPY", "USD") == "JPY"
    # Unsupported / non-pair Capital epics (commodity, equity, USD index) → USD.
    assert _quote_ccy_for_symbol("capital", "GOLD", "USD") == "USD"
    assert _quote_ccy_for_symbol("capital", "US100", "USD") == "USD"
    # OKX trusts the (correct) universe value.
    assert _quote_ccy_for_symbol("okx", "BTC-USDT", "USDT") == "USDT"


def test_trade_and_position_carry_quote_ccy(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        now_s = _now_s()
        trades = _recent_closed_trades(conn, n=10)
        btc = trades[0]
        assert btc.quote_ccy == "USDT"  # okx universe quote

        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup={},
        )
        j225 = next(p for p in positions if p.symbol == "J225")
        # corrected: index J225 is JPY despite the universe USD placeholder.
        assert j225.quote_ccy == "JPY"
    finally:
        conn.close()
