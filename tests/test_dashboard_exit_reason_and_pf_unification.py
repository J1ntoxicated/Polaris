"""Dashboard P0-4 — exit-reason SSOT, net$ %-color unification, PF/counter SSOT.

DEMO/PAPER, virtual funds. Pure display-layer tests (snapshot/visualizer read
path only) — no trading behavior, sizing, gating, or exit logic touched.
Aggressive bias preserved; honest numbers over rosy ones.

Covered:
① EXIT REASON SSOT — ``_exit_reason_by_position`` (the positions⋈segments
  lineage join) is the ONE source every closed-trade panel reads, so
  ``recent_trades`` (snapshot_sections) and ``streams.recent_closed``
  (snapshot_q_streams) show the SAME real reason (atr_trail_stop / thesis_cut /
  loser_timeout / exit / ...) for the SAME trade — not a synthesized
  TP/SL/TIME/FLAT guess from the pnl sign.
② $ UNIFICATION — ``pnl_pct`` is net_usd / entry_notional (not gross), so the
  %-color and the NET$-color never disagree on the same row (a gross-positive,
  net-negative scalp used to show a green % next to a red $).
③ PF/COUNTER SSOT — the header WINNING/LOSING judge + PF/win-rate read off
  ``since_reset`` (both legs, fee-net) instead of the stale all-time
  confidence panel; the streams TRADES counter is the closed-POSITION count
  (one row per trade), not the closed-FILL count (which over-counts a
  partial-close trade).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.core.economics.fees import real_fee_usd
from polaris.scripts.dashboard.snapshot_q_common import _exit_reason_by_position
from polaris.scripts.dashboard.snapshot_q_streams import _per_stream_summary
from polaris.scripts.dashboard.snapshot_sections import _recent_closed_trades
from polaris.storage.schema import ALL_DDL


def _mkdb(tmp_path: Path, name: str) -> Path:
    db_path = tmp_path / name
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _seed_closed_trade(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    entry_px: float,
    exit_px: float,
    qty: float,
    pnl: float,
    exit_reason: str,
    now_ms: int,
) -> None:
    """One closed LONG round-trip + its lineage segment row."""
    inst = f"{venue}:{symbol}"
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, product_class, stream_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, closed_ts, swap_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (position_id, venue, symbol, symbol, "spot", "A_okx_crypto", "tsmom",
         "tsmom", "tsmom", "long", qty, "closed",
         now_ms // 1000 - 300, now_ms // 1000 - 60, 0),
    )
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (f"{position_id}_open", venue, inst, "tsmom", "buy",
             entry_px * qty, entry_px, 0.0, 1.0, now_ms - 300_000,
             f"{position_id}_o1", position_id, 0.0, 0, qty, entry_px * qty,
             "filled"),
            (f"{position_id}_close", venue, inst, "tsmom", "sell",
             exit_px * qty, exit_px, 0.0, 1.0, now_ms - 60_000,
             f"{position_id}_o2", position_id, pnl, 1, qty, exit_px * qty,
             "filled"),
        ],
    )
    conn.execute(
        "INSERT INTO position_strategy_segments (position_id, segment_id, "
        "strategy_id, regime_at_start, started_ts, ended_ts, entry_reason, "
        "exit_reason, attribution_weight, pnl_r, trade_id, venue, ticker, "
        "pnl_usd, cell_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (position_id, f"{position_id}_seg1", "tsmom", "chop",
         now_ms // 1000 - 300, now_ms // 1000 - 60, "signal", exit_reason,
         1.0, pnl / 40.0, position_id, venue, symbol, pnl, ""),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# ① EXIT REASON SSOT
# ---------------------------------------------------------------------------


def test_exit_reason_lookup_reads_lineage_not_pnl_sign(tmp_path: Path) -> None:
    """A LOSING trade closed via ``atr_trail_stop`` (not a fast SL) must report
    that real reason, not a pnl-sign-derived "SL"/"TIME" guess."""
    db = _mkdb(tmp_path, "reason1.sqlite")
    conn = sqlite3.connect(db)
    try:
        now_ms = int(time.time() * 1000)
        _seed_closed_trade(
            conn, position_id="p1", venue="okx", symbol="BTC-USDT",
            entry_px=100.0, exit_px=95.0, qty=1.0, pnl=-5.0,
            exit_reason="atr_trail_stop", now_ms=now_ms,
        )
        lookup = _exit_reason_by_position(conn)
        assert lookup["p1"] == "atr_trail_stop"
    finally:
        conn.close()


def test_recent_trades_and_streams_agree_on_same_reason(tmp_path: Path) -> None:
    """The SAME closed trade must show the SAME exit_reason on the global
    recent_trades panel AND the per-stream recent_closed chip."""
    db = _mkdb(tmp_path, "reason2.sqlite")
    conn = sqlite3.connect(db)
    try:
        now_ms = int(time.time() * 1000)
        _seed_closed_trade(
            conn, position_id="p2", venue="okx", symbol="ETH-USDT",
            entry_px=2000.0, exit_px=1900.0, qty=1.0, pnl=-100.0,
            exit_reason="thesis_cut", now_ms=now_ms,
        )
        global_trades = _recent_closed_trades(conn, n=10)
        assert global_trades[0].exit_reason == "thesis_cut"

        streams = _per_stream_summary(conn, now_s=int(time.time()))
        okx = next(s for s in streams if s.venue == "okx")
        assert okx.recent_closed, "expected the seeded trade in the okx lane"
        assert okx.recent_closed[0].exit_reason == "thesis_cut"
    finally:
        conn.close()


def test_missing_segment_falls_back_to_exit_not_tp_sl(tmp_path: Path) -> None:
    """No matched segment row (legacy/smoke fill) → falls back to "exit", never
    the retired TP/SL/TIME/FLAT pnl-sign synthesis."""
    db = _mkdb(tmp_path, "reason3.sqlite")
    conn = sqlite3.connect(db)
    try:
        now_ms = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f_orphan", "okx", "okx:SOL-USDT", "tsmom", "sell", 100.0, 100.0,
             0.0, 1.0, now_ms - 60_000, "o1", None, 5.0, 1, 1.0, 100.0,
             "filled"),
        )
        conn.commit()
        trades = _recent_closed_trades(conn, n=10)
        assert trades[0].exit_reason == "exit"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ② $ UNIFICATION — pnl_pct tracks net_usd, not gross pnl_usd
# ---------------------------------------------------------------------------


def test_pnl_pct_sign_matches_net_usd_sign(tmp_path: Path) -> None:
    """A tiny gross-positive scalp that flips net-negative once real fees bite
    must report a NEGATIVE pnl_pct (matching the NET$ color), not a positive
    one derived from the gross pnl."""
    db = _mkdb(tmp_path, "pct1.sqlite")
    conn = sqlite3.connect(db)
    try:
        now_ms = int(time.time() * 1000)
        # entry 100 -> exit 100.05, qty 1: gross +0.05, but OKX round-trip real
        # fee (10bps/leg on ~100 notional each) ≈ 0.20 > gross → net negative.
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f_open", "okx", "okx:SOL-USDT", "tsmom", "buy", 100.0, 100.0,
             0.0, 1.0, now_ms - 300_000, "o1", "pthin", 0.0, 0, 1.0, 100.0,
             "filled"),
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("f_close", "okx", "okx:SOL-USDT", "tsmom", "sell", 100.05,
             100.05, 0.0, 1.0, now_ms - 60_000, "o2", "pthin", 0.05, 1, 1.0,
             100.05, "filled"),
        )
        conn.commit()
        trades = _recent_closed_trades(conn, n=10)
        t = trades[0]
        assert t.pnl_usd > 0.0, "gross must be positive (the Jin scenario)"
        assert t.net_usd < 0.0, "net must flip negative once real fees bite"
        assert t.pnl_pct < 0.0, (
            "pnl_pct must track NET (negative), not gross (positive) — "
            f"got {t.pnl_pct}"
        )
        entry_fee = real_fee_usd("okx", 100.0)
        close_fee = real_fee_usd("okx", 100.05)
        expected_pct = (t.pnl_usd - entry_fee - close_fee) / 100.0 * 100.0
        assert abs(t.pnl_pct - expected_pct) < 1e-6
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ③ PF / COUNTER SSOT — streams closed_n is POSITION-based, not FILL-based
# ---------------------------------------------------------------------------


def test_streams_closed_n_counts_positions_not_fills(tmp_path: Path) -> None:
    """A partial-close position (2 close fills, 1 closed position) must report
    closed_n == 1 (positions), while daily_trades stays 2 (fills) for the
    tooltip — the two numbers are allowed to differ, but closed_n is the one
    the board renders as TRADES."""
    db = _mkdb(tmp_path, "counter1.sqlite")
    conn = sqlite3.connect(db)
    try:
        now_s = int(time.time())
        now_ms = now_s * 1000
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, "
            "underlying_group_id, product_class, stream_id, strategy_id, "
            "entry_strategy_id, active_strategy_id, side, qty, status, "
            "opened_ts, closed_ts, swap_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("p_partial", "okx", "BTC-USDT", "BTC", "spot", "A_okx_crypto",
             "tsmom", "tsmom", "tsmom", "long", 1.0, "closed",
             now_s - 300, now_s - 60, 0),
        )
        conn.executemany(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("f_open", "okx", "okx:BTC-USDT", "tsmom", "buy", 100.0,
                 100.0, 0.0, 1.0, now_ms - 300_000, "o1", "p_partial", 0.0,
                 0, 1.0, 100.0, "filled"),
                # two PARTIAL close fills for the SAME position (one trade)
                ("f_close_a", "okx", "okx:BTC-USDT", "tsmom", "sell", 50.0,
                 105.0, 0.0, 1.0, now_ms - 120_000, "o2", "p_partial", 2.5,
                 1, 0.5, 50.0, "filled"),
                ("f_close_b", "okx", "okx:BTC-USDT", "tsmom", "sell", 50.0,
                 106.0, 0.0, 1.0, now_ms - 60_000, "o3", "p_partial", 3.0,
                 1, 0.5, 50.0, "filled"),
            ],
        )
        conn.commit()
        streams = _per_stream_summary(conn, now_s=now_s)
        okx = next(s for s in streams if s.venue == "okx")
        assert okx.daily_trades == 2, "2 close fills → fills-based count is 2"
        assert okx.closed_n == 1, (
            "1 closed POSITION → the board's TRADES counter must read 1, "
            f"got {okx.closed_n}"
        )
    finally:
        conn.close()
