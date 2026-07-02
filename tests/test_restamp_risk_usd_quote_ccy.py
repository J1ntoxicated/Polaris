"""One-shot risk_usd quote-ccy restamp migration (audit rank 4 companion).

DEMO/PAPER only. Pins: a JPY-quoted OPEN position's risk_usd is recomputed with
the quote->USD conversion (never left raw-quote-ccy); a USD-quoted position is
left untouched; a position with no conversion-pair bars is SKIPPED (never
guessed); CLOSED positions are never touched; dry-run performs no writes.
"""

from __future__ import annotations

import sqlite3
import time

from polaris.storage.schema import init_db
from tools.restamp_risk_usd_quote_ccy import apply_restamps, compute_restamps


def _memdb() -> sqlite3.Connection:
    return init_db(":memory:")


def _seed_open_position(
    conn: sqlite3.Connection, *, position_id: str, venue: str, symbol: str,
    entry_price: float, qty: float, entry_atr_pct: float, risk_usd: float | None,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " entry_atr_pct, risk_usd) "
        "VALUES (?, ?, ?, 'volume_burst', 'volume_burst', 'volume_burst', "
        "        'long', ?, 'open', ?, ?, ?)",
        (position_id, venue, symbol, qty, int(time.time()), entry_atr_pct, risk_usd),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, ?, ?, 'volume_burst', 'buy', "
        "        100.0, ?, 0.1, 1.0, ?, 'o1', ?, 0.0, 0, ?, 100.0, 'filled')",
        (
            f"f_{position_id}", venue, f"{venue}:{symbol}", entry_price,
            int(time.time() * 1000), position_id, qty,
        ),
    )
    conn.commit()


def _seed_bar(conn: sqlite3.Connection, *, instrument_id: str, close: float) -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) VALUES "
        " (?, 'x', 'capital', 'x', '1m', ?, ?, ?, ?, ?, 0.0)",
        (instrument_id, int(time.time()), close, close, close, close),
    )
    conn.commit()


def test_jpy_position_risk_usd_converted_to_usd() -> None:
    conn = _memdb()
    try:
        _seed_bar(conn, instrument_id="capital:USDJPY", close=150.0)
        _seed_open_position(
            conn, position_id="pjpy", venue="capital", symbol="USDJPY",
            entry_price=150.0, qty=1000.0, entry_atr_pct=0.01,
            risk_usd=3000.0,  # the pre-fix raw-JPY stamp (150*0.01*2*1000)
        )
        results = compute_restamps(conn)
        assert len(results) == 1
        r = results[0]
        assert r.skipped_reason is None
        assert r.quote_ccy == "JPY"
        assert r.new_risk_usd is not None
        # raw would be 3000.0; converted (rate 1/150) ~= 20.0.
        assert r.new_risk_usd < 3000.0 / 100.0
        assert r.new_risk_usd > 0.0
    finally:
        conn.close()


def test_usd_quoted_position_is_not_touched() -> None:
    conn = _memdb()
    try:
        _seed_open_position(
            conn, position_id="pusd", venue="okx", symbol="BTC-USDT",
            entry_price=60_000.0, qty=0.01, entry_atr_pct=0.01, risk_usd=12.0,
        )
        results = compute_restamps(conn)
        assert results == []
    finally:
        conn.close()


def test_no_conversion_bars_is_skipped_not_guessed() -> None:
    conn = _memdb()
    try:
        # No capital:USDJPY bar seeded -> rate unresolvable.
        _seed_open_position(
            conn, position_id="pnobars", venue="capital", symbol="USDJPY",
            entry_price=150.0, qty=1000.0, entry_atr_pct=0.01, risk_usd=3000.0,
        )
        results = compute_restamps(conn)
        assert len(results) == 1
        assert results[0].skipped_reason is not None
        assert results[0].new_risk_usd is None
    finally:
        conn.close()


def test_closed_position_is_never_touched() -> None:
    conn = _memdb()
    try:
        _seed_bar(conn, instrument_id="capital:USDJPY", close=150.0)
        _seed_open_position(
            conn, position_id="pclosed", venue="capital", symbol="USDJPY",
            entry_price=150.0, qty=1000.0, entry_atr_pct=0.01, risk_usd=3000.0,
        )
        conn.execute(
            "UPDATE positions SET status = 'closed' WHERE position_id = 'pclosed'"
        )
        conn.commit()
        results = compute_restamps(conn)
        assert results == []
    finally:
        conn.close()


def test_apply_writes_only_resolved_results_dry_run_writes_nothing() -> None:
    conn = _memdb()
    try:
        _seed_bar(conn, instrument_id="capital:USDJPY", close=150.0)
        _seed_open_position(
            conn, position_id="pjpy2", venue="capital", symbol="USDJPY",
            entry_price=150.0, qty=1000.0, entry_atr_pct=0.01, risk_usd=3000.0,
        )
        results = compute_restamps(conn)
        # Dry-run: compute_restamps alone performs no writes.
        row_before = conn.execute(
            "SELECT risk_usd FROM positions WHERE position_id = 'pjpy2'"
        ).fetchone()
        assert row_before[0] == 3000.0
        n = apply_restamps(conn, results)
        assert n == 1
        row_after = conn.execute(
            "SELECT risk_usd FROM positions WHERE position_id = 'pjpy2'"
        ).fetchone()
        assert row_after[0] != 3000.0
        assert row_after[0] == results[0].new_risk_usd
    finally:
        conn.close()
