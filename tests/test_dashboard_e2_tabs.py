"""Tests for the E2 Trading-IA rebuild BACKEND — read-only tab queries.

DEMO/PAPER, virtual funds. Seeds a temp SQLite DB and asserts the new read-only
query helpers that back the rebuilt full-width tabs (POSITIONS / TRADES / REGIME
/ EXIT / AI) surface the expected data. Pure display layer — these queries touch
only the snapshot/visualizer read path; no trading behavior, sizing, gating,
exit, strategy, or venue calls.

Covered:
- POSITIONS enrichment — PositionRow carries regime / exit_state / stop_price /
  mfe_r / mae_r / upnl_pct.
- TRADES enrichment — ClosedTrade carries regime / pnl_pct / fee_usd /
  real_fee_usd.
- REGIME tab — _regime_states reads regime_state with confidence + evidence +
  2-consecutive.
- EXIT tab — _exit_surface rolls up exit_state FSM distribution + reason
  histogram + loser-timeout + G6/G7 gate decision counts.
- AI tab — _shadow_agreement (gate_shadow_events agree/mismatch by gate×regime)
  + _entry_admission_stats (entry_admission_shadow would-suppress by regime).
- Graceful zero — missing tables / empty data degrade to empty rollups.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_models import (
    AiShadowPanel,
    ExitSurface,
)
from polaris.scripts.dashboard.snapshot_queries import (
    _cell_mult_lookup,
    _entry_price_lookup,
    _last_prices,
    _now_s,
    _read_positions,
)
from polaris.scripts.dashboard.snapshot_sections import (
    _entry_admission_stats,
    _exit_surface,
    _regime_states,
    _shadow_agreement,
)
from polaris.storage.schema import init_db


def _seed(conn: sqlite3.Connection) -> None:
    now_s = _now_s()
    now_ms = now_s * 1000

    # --- positions: enriched columns (exit_state / stop / mfe_r / mae_r) ----
    positions = [
        # (position_id, venue, symbol, underlying_group_id, product_class,
        #  stream_id, signal_id, strategy_id, entry_strategy_id,
        #  active_strategy_id, side, qty, status, opened_ts, closed_ts,
        #  swap_count, stop_price, peak_price, trough_price, mfe_r, mae_r,
        #  exit_state)
        ("p1", "okx", "BTC-USDT", "BTC", "spot", "A_okx_crypto", "s1", "tsmom",
         "tsmom", "tsmom", "long", 0.1, "open", now_s - 300, None, 0,
         95.0, 110.0, 98.0, 1.5, -0.4, "protected"),
        ("p2", "capital", "XAUUSD", "XAU", "cfd", "B_capital_cfd", "s2",
         "xau_indices_trend", "xau_indices_trend", "xau_indices_trend",
         "long", 1.0, "open", now_s - 120, None, 0,
         1880.0, 1920.0, 1895.0, 0.8, -0.2, "touched"),
    ]
    conn.executemany(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, product_class, stream_id, signal_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
        "status, opened_ts, closed_ts, swap_count, stop_price, peak_price, "
        "trough_price, mfe_r, mae_r, exit_state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        positions,
    )

    # --- fills: an open + a close (so entry/last + closed trade exist) ------
    fills = [
        ("f1", "okx", "okx:BTC-USDT", "tsmom", "buy", 1000.0, 100.0, 1.0, 10.0,
         now_ms - 5000, "o1", None, 0.0, 0, 10.0, 1000.0, "filled"),
        ("f2", "okx", "okx:ETH-USDT", "tsmom", "buy", 1000.0, 50.0, 1.0, 10.0,
         now_ms - 4500, "o3", "c1", 0.0, 0, 20.0, 1000.0, "filled"),
        ("f3", "okx", "okx:ETH-USDT", "tsmom", "sell", 1100.0, 55.0, 7.7, 10.0,
         now_ms - 1000, "o4", "c1", 100.0, 1, 20.0, 1100.0, "filled"),
    ]
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        fills,
    )
    # last price for BTC so entry/last + delta resolve
    conn.executemany(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [("okx:BTC-USDT", "BTC", "okx", "BTC-USDT", "1m", now_s - 1,
          100.0, 112.0, 99.0, 110.0, 5.0)],
    )

    # --- regime_state: confidence + evidence + 2-consecutive -----------------
    regime_rows = [
        ("okx", "BTC", "bull_trend", 0.82,
         '{"l1_macro":"risk_on","l2_asset_class":"crypto_bull",'
         '"l3_price_action":"breakout"}', "bull_trend", 2, now_s - 30),
        ("capital", "XAU", "chop", 0.45, "{}", "bull_trend", 1, now_s - 20),
    ]
    conn.executemany(
        "INSERT INTO regime_state (venue, underlying_group_id, regime, "
        "confidence, evidence_json, consecutive_candidate, consecutive_count, "
        "updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        regime_rows,
    )

    # --- gate_events: G6 / G7 decisions for the EXIT tab ---------------------
    gate_rows = [
        ("g1", "run1", None, "p1", 6, "success", "HOLD", "python", 5, 0, 0,
         "{}", None, now_s - 60),
        ("g2", "run1", None, "p1", 6, "success", "HOLD", "python", 5, 0, 0,
         "{}", None, now_s - 50),
        ("g3", "run1", None, "p2", 7, "success", "EXIT", "python", 5, 0, 0,
         "{}", None, now_s - 40),
        ("g4", "run1", None, "p1", 7, "success", "HOLD", "python", 5, 0, 0,
         "{}", None, now_s - 30),
    ]
    conn.executemany(
        "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
        "gate_id, phase, decision, model_used, latency_ms, input_tokens, "
        "output_tokens, payload_json, error_text, created_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        gate_rows,
    )

    # --- gate_shadow_events: conductor shadow agreement (G3/G4 by regime) ----
    shadow_rows = [
        # (event_id, run_id, signal_id, gate_id, venue, symbol, regime,
        #  technical_decision, technical_scalar, technical_reason,
        #  technical_flags, gpt_decision, mismatch, cell_warm, created_ts)
        ("sh1", "run1", "s1", 3, "okx", "BTC-USDT", "bull_trend",
         "PASS", 1.0, "ok", "", "PASS", 0, 1, now_s - 70),
        ("sh2", "run1", "s2", 3, "okx", "ETH-USDT", "bull_trend",
         "PASS", 1.0, "ok", "", "KILL", 1, 1, now_s - 65),
        ("sh3", "run1", "s3", 4, "capital", "XAUUSD", "chop",
         "PASS", 1.0, "ok", "", "", 0, 0, now_s - 60),  # GPT absent
    ]
    conn.executemany(
        "INSERT INTO gate_shadow_events (event_id, run_id, signal_id, gate_id, "
        "venue, symbol, regime, technical_decision, technical_scalar, "
        "technical_reason, technical_flags, gpt_decision, mismatch, cell_warm, "
        "created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        shadow_rows,
    )

    # --- entry_admission_shadow: would-suppress by regime --------------------
    adm_rows = [
        # (event_id, run_id, signal_id, venue, symbol, strategy_id, regime,
        #  cell_n_eff, cell_avg_pnl_r, expected_move_r, real_round_trip_cost_r,
        #  admit, would_suppress, reason, basis, created_ts)
        ("a1", "run1", "s1", "okx", "BTC-USDT", "tsmom", "bull_trend",
         30.0, 0.5, 1.2, 0.3, 1, 0, "edge_ok", "cell", now_s - 90),
        ("a2", "run1", "s2", "okx", "ETH-USDT", "tsmom", "bull_trend",
         12.0, -0.1, 0.2, 0.3, 1, 1, "below_cost", "cell", now_s - 80),
        ("a3", "run1", "s3", "capital", "XAUUSD", "xau", "chop",
         5.0, 0.0, 0.1, 0.4, 1, 1, "below_cost", "cell", now_s - 70),
    ]
    conn.executemany(
        "INSERT INTO entry_admission_shadow (event_id, run_id, signal_id, "
        "venue, symbol, strategy_id, regime, cell_n_eff, cell_avg_pnl_r, "
        "expected_move_r, real_round_trip_cost_r, admit, would_suppress, "
        "reason, basis, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        adm_rows,
    )
    conn.commit()


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "e2.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    _seed(conn)
    return conn


# --------------------------------------------------------------------------
# POSITIONS enrichment
# --------------------------------------------------------------------------


def test_positions_carry_regime_exit_stop_mfe_mae(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        now_s = _now_s()
        from polaris.scripts.dashboard.snapshot_sections import _regime_bars

        _bars, regime_lookup = _regime_bars(conn)
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup=regime_lookup,
        )
        by_sym = {p.symbol: p for p in positions}
        btc = by_sym["BTC-USDT"]
        assert btc.exit_state == "protected"
        assert btc.stop_price == 95.0
        # Hardening #6: excursion R surfaced as the *_atr_r payload fields.
        assert btc.mfe_atr_r == 1.5
        assert btc.mae_atr_r == -0.4
        # regime resolved from regime_state on (venue, group)
        assert btc.regime == "bull_trend"
        # upnl_pct present and finite
        assert isinstance(btc.upnl_pct, float)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# TRADES enrichment
# --------------------------------------------------------------------------


def test_trades_carry_fee_split_and_regime(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        from polaris.scripts.dashboard.snapshot_sections import (
            _recent_closed_trades,
        )

        trades = _recent_closed_trades(conn, n=10)
        assert trades, "expected at least one closed trade"
        t = trades[0]
        # demo fee = stored fee on close fill (7.7), real fee recomputed lower
        assert t.fee_usd == 7.7
        assert t.real_fee_usd >= 0.0
        assert t.real_fee_usd < t.fee_usd  # real OKX schedule < 0.7% demo drain
        # pnl_pct derived from entry/exit
        assert isinstance(t.pnl_pct, float)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# REGIME tab
# --------------------------------------------------------------------------


def test_regime_states_confidence_evidence_consecutive(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        rows = _regime_states(conn)
        by_key = {(r.venue, r.group_id): r for r in rows}
        btc = by_key[("okx", "BTC")]
        assert btc.regime == "bull_trend"
        assert abs(btc.confidence - 0.82) < 1e-9
        assert btc.consecutive_candidate == "bull_trend"
        assert btc.consecutive_count == 2
        # layered evidence decoded
        assert btc.evidence_l1 == "risk_on"
        assert btc.evidence_l2 == "crypto_bull"
        assert btc.evidence_l3 == "breakout"
        # empty evidence_json degrades to empty labels
        xau = by_key[("capital", "XAU")]
        assert xau.evidence_l1 == ""
    finally:
        conn.close()


def test_regime_states_graceful_when_empty(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert _regime_states(conn) == []
    finally:
        conn.close()


# --------------------------------------------------------------------------
# EXIT tab
# --------------------------------------------------------------------------


def test_exit_surface_fsm_reasons_gate_counts(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        from polaris.scripts.dashboard.snapshot_sections import (
            _recent_closed_trades,
        )

        trades = _recent_closed_trades(conn, n=40)
        surf = _exit_surface(conn, now_s=_now_s(), recent_trades=trades)
        assert isinstance(surf, ExitSurface)
        # FSM distribution from open positions' exit_state
        assert surf.fsm_states.get("protected") == 1
        assert surf.fsm_states.get("touched") == 1
        # G6 / G7 decision tallies
        assert surf.g6_decisions.get("HOLD") == 2
        assert surf.g7_decisions.get("EXIT") == 1
        assert surf.g7_decisions.get("HOLD") == 1
        # exit-reason histogram (from recent closed trades passed in)
        total_reason = sum(b.count for b in surf.reasons)
        assert total_reason >= 1
    finally:
        conn.close()


def test_exit_surface_graceful_when_empty(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        surf = _exit_surface(conn, now_s=_now_s())
        assert surf.fsm_states == {}
        assert surf.g6_decisions == {}
        assert surf.reasons == []
    finally:
        conn.close()


# --------------------------------------------------------------------------
# AI tab
# --------------------------------------------------------------------------


def test_shadow_agreement_by_gate_regime(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        rows = _shadow_agreement(conn, now_s=_now_s())
        # gate 3 / bull_trend: 2 rows w/ GPT, 1 mismatch → 50% agree
        g3 = [r for r in rows if r.gate_id == 3 and r.regime == "bull_trend"]
        assert g3, "expected a G3/bull_trend shadow row"
        assert g3[0].n == 2
        assert g3[0].mismatch_n == 1
        assert abs(g3[0].agree_pct - 50.0) < 1e-9
        # gate 4 / chop: GPT absent → n=0, n_no_gpt=1
        g4 = [r for r in rows if r.gate_id == 4 and r.regime == "chop"]
        assert g4
        assert g4[0].n == 0
        assert g4[0].n_no_gpt == 1
    finally:
        conn.close()


def test_entry_admission_stats_would_suppress(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        rows = _entry_admission_stats(conn, now_s=_now_s())
        by_regime = {r.regime: r for r in rows}
        bull = by_regime["bull_trend"]
        assert bull.n == 2
        assert bull.would_suppress_n == 1
        assert abs(bull.suppress_pct - 50.0) < 1e-9
        chop = by_regime["chop"]
        assert chop.n == 1
        assert chop.would_suppress_n == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Integration: collect_snapshot wires the new fields
# --------------------------------------------------------------------------


def test_collect_snapshot_populates_e2_fields(tmp_path: Path) -> None:
    db = tmp_path / "snap.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    _seed(conn)
    conn.close()
    snap = collect_snapshot(db)
    assert snap.regime_states, "regime_states should be populated"
    assert isinstance(snap.exit_surface, ExitSurface)
    assert isinstance(snap.ai_shadow, AiShadowPanel)
    assert snap.exit_surface.fsm_states.get("protected") == 1
    assert snap.ai_shadow.admission_total_n == 3
    assert snap.ai_shadow.admission_suppress_n == 2
    # positions enriched
    assert any(p.regime == "bull_trend" for p in snap.positions)
    # trades enriched
    assert any(t.real_fee_usd < t.fee_usd for t in snap.recent_trades if t.fee_usd)
