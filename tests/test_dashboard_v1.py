"""Polaris dashboard v1 — panel-level tests + 220×55 grid invariants.

Tests cover the 10 panels demanded by Jin 2026-05-07 redesign:
1.  Top bar (equity / pnl / DD / Sharpe)
2.  24h equity sparkline (288 ticks downsample)
3.  Live positions sort by uPnL desc
4.  Per-strategy stats WR-band color
5.  Gate funnel pass-rate bars
6.  Cell matrix top + bottom (5+5)
7.  Regime heatmap
8.  Recent closed trades (paired open/close → net PnL + R + held + reason)
9.  Learner state (3 P0 with 1h delta arrow)
10. GPT cost projection
11. Alert log (last 1)
+ grid invariants: 220×55 fit + zero-snapshot fallback.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from polaris.scripts.dashboard.ansi_palette import (
    NEGATIVE,
    POSITIVE,
    WARNING,
    sparkline,
    vlen,
)
from polaris.scripts.dashboard.render import (
    TARGET_HEIGHT,
    TARGET_WIDTH,
    render_alert_row,
    render_cell_bottom_panel,
    render_cell_top_panel,
    render_dashboard,
    render_edge_validation_panel,
    render_equity_panel,
    render_gate_panel,
    render_learner_gpt_row,
    render_positions_panel,
    render_regime_panel,
    render_strategy_panel,
    render_top_bar,
    render_trades_panel,
)
from polaris.scripts.dashboard.snapshot import (
    STARTING_CAPITAL,
    AlertRow,
    CellRow,
    ClosedTrade,
    DashboardSnapshot,
    EdgeValidationRow,
    GateDecisionRow,
    GptStat,
    LearnerSlot,
    PositionRow,
    RegimeBar,
    StrategyStat,
    collect_snapshot,
)
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _full_snap() -> DashboardSnapshot:
    """Hand-built snapshot exercising every panel."""
    now = int(time.time())
    return DashboardSnapshot(
        ts_now=now,
        starting_capital=STARTING_CAPITAL,
        equity_now=10_750.50,
        exposed_usd=2_000.00,
        upnl_total=125.30,
        daily_pnl_usd=325.10,
        daily_trades=42,
        drawdown_pct=1.8,
        peak_equity=10_950.00,
        sharpe_24h=1.85,
        open_positions_n=8,
        active_cells_n=23,
        universe_focus_n=24,
        universe_last_refresh="20:00:00",
        equity_curve=[10_000.0 + i * 5 for i in range(288)],
        equity_curve_ts=[now - (288 - i) * 300 for i in range(288)],
        positions=[
            PositionRow(
                venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
                side="long", qty=0.001, entry_price=80_000.0, last_price=81_500.0,
                delta_pct=1.875, upnl_usd=1.50, size_usd=80.0, held_sec=1800.0,
                cell_mult=1.5,
            ),
            PositionRow(
                venue="okx", symbol="ETH-USDT", strategy_id="tsmom",
                side="long", qty=0.5, entry_price=2_300.0, last_price=2_280.0,
                delta_pct=-0.87, upnl_usd=-10.0, size_usd=1_150.0, held_sec=600.0,
                cell_mult=1.0,
            ),
        ],
        strategy_stats=[
            StrategyStat(
                strategy_id="volume_burst", open_n=2, closed_n=15, wr_pct=66.7,
                avg_r=0.45, pf=2.10, pnl_usd=120.0, notional_usd=200.0,
            ),
            StrategyStat(
                strategy_id="tsmom", open_n=3, closed_n=20, wr_pct=45.0,
                avg_r=0.05, pf=1.05, pnl_usd=15.0, notional_usd=2_300.0,
            ),
            StrategyStat(
                strategy_id="rsi_bb_pullback", open_n=1, closed_n=8, wr_pct=37.5,
                avg_r=-0.30, pf=0.80, pnl_usd=-25.0, notional_usd=500.0,
            ),
        ],
        gate_decisions=[
            GateDecisionRow(gate_id=1, label="Universe",
                            headline="focus 30 · excluded 64 below floor · 16:34", n=3,
                            metrics={"focus_n": 30, "excluded_n": 64}),
            GateDecisionRow(gate_id=2, label="Strategy",
                            headline="80 signals fired", n=80, metrics={"fired_n": 80}),
            GateDecisionRow(gate_id=3, label="Validator",
                            headline="64 validated · scalar~0.95", n=80, metrics={}),
            GateDecisionRow(gate_id=4, label="PreEntry",
                            headline="50 proceeded / 14 holding", n=64, metrics={}),
            GateDecisionRow(gate_id=5, label="Sizer",
                            headline="50 sized · risk~1.50% · $24,000 · tier 1x:50", n=50,
                            metrics={"sized_n": 50, "median_risk_pct": 1.5}),
            GateDecisionRow(gate_id=6, label="Monitor",
                            headline="100 monitored · hold 90 / adjust 8 / exit 2", n=100,
                            metrics={}),
            GateDecisionRow(gate_id=7, label="Exit",
                            headline="hold 95 / adjust 3 / exit 2 · above_bep", n=100,
                            metrics={}),
            GateDecisionRow(gate_id=8, label="Reflector",
                            headline="42 lessons · entry_timing:42 · conf~0.70", n=42,
                            metrics={}),
        ],
        cell_top=[
            CellRow(exchange="okx", strategy="tsmom", ticker="BTC-USDT",
                    regime="bull_trend", n_eff=80.0, score=0.62, mult=1.5),
            CellRow(exchange="okx", strategy="volume_burst", ticker="ETH-USDT",
                    regime="bull_trend", n_eff=55.0, score=0.45, mult=1.5),
        ],
        cell_bottom=[
            CellRow(exchange="okx", strategy="tsmom", ticker="DOGE-USDT",
                    regime="chop", n_eff=22.0, score=-0.05, mult=0.5),
            CellRow(exchange="okx", strategy="rsi_bb_pullback",
                    ticker="SHIB-USDT", regime="chop", n_eff=21.0, score=-0.10,
                    mult=0.5),
        ],
        regime_bars=[
            RegimeBar(regime="chop", count=12),
            RegimeBar(regime="bull_trend", count=4),
            RegimeBar(regime="bear_trend", count=2),
            RegimeBar(regime="crisis", count=0),
        ],
        recent_trades=[
            ClosedTrade(
                ts_close=now - 60, venue="okx", symbol="BTC-USDT",
                strategy_id="volume_burst", side_close="sell",
                entry_price=80_000.0, exit_price=80_500.0, pnl_usd=5.0,
                r_units=0.5, held_sec=420.0, exit_reason="TP",
            ),
            ClosedTrade(
                ts_close=now - 180, venue="okx", symbol="ETH-USDT",
                strategy_id="tsmom", side_close="sell",
                entry_price=2_300.0, exit_price=2_290.0, pnl_usd=-5.0,
                r_units=-0.5, held_sec=900.0, exit_reason="TIME",
            ),
        ],
        learners=[
            LearnerSlot(learner_id="session_mult", key="tsmom:asia", value=1.20,
                        delta_1h=0.05, n_eff=300.0),
            LearnerSlot(learner_id="regime_mult", key="tsmom:bull_trend",
                        value=1.50, delta_1h=-0.10, n_eff=200.0),
            LearnerSlot(learner_id="max_hold", key="tsmom", value=20.0,
                        delta_1h=0.0, n_eff=500.0),
        ],
        gpt_stats=[
            GptStat(model="gpt-5-mini", calls_per_h=1200, cost_per_h_usd=0.27,
                    cost_24h_proj_usd=6.48),
        ],
        alerts=[
            AlertRow(ts=now - 30, level="WARN", module="risk",
                     msg="cluster_cap_breached: BTC group >40% NAV"),
        ],
    )


# ---------------------------------------------------------------------------
# Panel rendering tests
# ---------------------------------------------------------------------------


def test_top_bar_renders_equity_pnl_dd_sharpe() -> None:
    snap = _full_snap()
    line = render_top_bar(snap, width=TARGET_WIDTH)
    assert "EQUITY" in line and "10,750.50" in line
    assert "EXPOSED" in line and "2,000" in line
    assert "uPnL" in line and "+125.30" in line
    assert "Daily" in line and "+325.10" in line
    assert "DD" in line and "1.80%" in line
    assert "Sharpe" in line and "1.85" in line
    assert "Open" in line and "8" in line
    assert "Cells" in line and "23" in line
    assert vlen(line) == TARGET_WIDTH


def test_equity_curve_sparkline_288_ticks_downsamples() -> None:
    series = list(range(288))
    spark = sparkline([float(v) for v in series], 60)
    assert len(spark) == 60
    # Monotonically increasing series → last char should be max block
    assert spark[-1] == "█"


def test_equity_panel_three_rows_with_min_max_delta() -> None:
    snap = _full_snap()
    rows = render_equity_panel(snap, width=TARGET_WIDTH)
    assert len(rows) == 3
    assert "24H EQUITY" in rows[0]
    # min/max/range/Δ all in annotation row
    assert "min" in rows[2]
    assert "max" in rows[2]
    assert "Δ" in rows[2]
    assert all(vlen(r) == TARGET_WIDTH for r in rows)


def test_live_positions_sort_by_uPnL_desc() -> None:
    snap = _full_snap()
    rows = render_positions_panel(snap, width=TARGET_WIDTH, max_rows=6)
    # uPnL +1.50 (BTC) should appear above uPnL -10 (ETH)
    btc_idx = next(i for i, r in enumerate(rows) if "BTC-USDT" in r)
    eth_idx = next(i for i, r in enumerate(rows) if "ETH-USDT" in r)
    assert btc_idx < eth_idx
    assert all(vlen(r) == TARGET_WIDTH for r in rows)


def test_positions_panel_overflow_shows_more_hint() -> None:
    snap = _full_snap()
    # Inflate to 10 positions
    snap.positions = snap.positions * 5
    rows = render_positions_panel(snap, width=TARGET_WIDTH, max_rows=6)
    last_row = rows[-1]
    assert "more" in last_row.lower()


def test_per_strategy_stats_color_codes_wr_bands() -> None:
    snap = _full_snap()
    rows = render_strategy_panel(snap, width=TARGET_WIDTH, max_rows=7)
    # WR 66.7 → green, 45.0 → yellow, 37.5 → red
    vb_row = next(r for r in rows if "volume_burst" in r)
    ts_row = next(r for r in rows if "tsmom" in r)
    rsi_row = next(r for r in rows if "rsi_bb_pullback" in r)
    assert POSITIVE in vb_row    # WR ≥55 green
    assert WARNING in ts_row     # 40–55 yellow
    assert NEGATIVE in rsi_row   # <40 red


def test_gate_decisions_8_rows_and_headline_visible() -> None:
    snap = _full_snap()
    rows = render_gate_panel(snap, width=TARGET_WIDTH)
    assert len(rows) == 9       # 1 hline + 8 gate rows
    assert "G1" in rows[1] and "Universe" in rows[1]
    assert "G8" in rows[8] and "Reflector" in rows[8]
    # The meaningful per-gate headline is rendered (G5 size line surfaces).
    assert any("sized" in r for r in rows[1:])


def test_cell_top_panel_renders_5_rows() -> None:
    snap = _full_snap()
    rows = render_cell_top_panel(snap, width=TARGET_WIDTH, max_rows=5)
    # 1 hline + 1 header + max_rows = 7
    assert len(rows) == 7
    assert "TOP 5" in rows[0]
    assert "BTC-USDT" in rows[2] or "BTC-USDT" in rows[3]


def test_cell_bottom_panel_renders_5_rows() -> None:
    snap = _full_snap()
    rows = render_cell_bottom_panel(snap, width=TARGET_WIDTH, max_rows=5)
    # 1 hline + max_rows = 6
    assert len(rows) == 6
    assert "BOTTOM 5" in rows[0]


def test_regime_heatmap_renders_all_4_regimes() -> None:
    """Codex P0 fix: crisis must show even when other regimes are non-zero."""
    snap = _full_snap()
    # Force a non-zero crisis count to verify it isn't truncated
    snap.regime_bars[3] = RegimeBar(regime="crisis", count=1)
    rows = render_regime_panel(snap, width=TARGET_WIDTH)
    # 1 hline + 4 regime rows = 5
    assert len(rows) == 5
    assert "chop" in rows[1]
    assert "bull_trend" in rows[2]
    assert "bear_trend" in rows[3]
    assert "crisis" in rows[4]


def test_recent_closed_trades_show_entry_exit_pnl_r_held_reason() -> None:
    snap = _full_snap()
    rows = render_trades_panel(snap, width=TARGET_WIDTH, max_rows=2)
    # Headers + 2 trades
    assert "ENTRY" in rows[1] and "EXIT" in rows[1] and "NET_PnL" in rows[1]
    assert "REASON" in rows[1]
    assert "TP" in rows[2]    # first trade exit reason
    assert "TIME" in rows[3]  # second trade exit reason


def test_learner_state_3_p0_with_delta_arrow() -> None:
    snap = _full_snap()
    line = render_learner_gpt_row(snap, width=TARGET_WIDTH)
    assert "session_mult" in line
    assert "regime_mult" in line
    assert "max_hold" in line
    # Delta arrows present
    assert ("↑" in line) or ("↓" in line) or ("→" in line)


def test_gpt_cost_projection_visible() -> None:
    snap = _full_snap()
    line = render_learner_gpt_row(snap, width=TARGET_WIDTH)
    assert "GPT" in line
    assert "gpt-5-mini" in line
    assert "1200c/h" in line
    assert "24h≈" in line


def test_alert_log_shows_last_1_with_warn_color() -> None:
    snap = _full_snap()
    line = render_alert_row(snap, width=TARGET_WIDTH)
    assert "ALERT" in line
    assert "cluster_cap_breached" in line
    # Universe focus tail rendered
    assert "Universe" in line and "24" in line


def test_alert_row_when_no_incidents() -> None:
    snap = _full_snap()
    snap.alerts = []
    line = render_alert_row(snap, width=TARGET_WIDTH)
    assert "no incidents" in line


# ---------------------------------------------------------------------------
# Grid / fit invariants
# ---------------------------------------------------------------------------


def test_220_char_width_per_row() -> None:
    snap = _full_snap()
    rows = render_dashboard(snap, width=TARGET_WIDTH, height=TARGET_HEIGHT)
    for i, r in enumerate(rows):
        assert vlen(r) == TARGET_WIDTH, f"row {i} vlen={vlen(r)}"


def test_55_row_fit_exact() -> None:
    snap = _full_snap()
    rows = render_dashboard(snap, width=TARGET_WIDTH, height=TARGET_HEIGHT)
    assert len(rows) == TARGET_HEIGHT


def _edge_rows(n: int) -> list[EdgeValidationRow]:
    return [
        EdgeValidationRow(
            exchange="okx", strategy="volume_burst", ticker=f"T{i}-USDT",
            regime="bull_trend", cost_adj_exp=0.4 - i * 0.05, p_pos=0.9 - i * 0.05,
            n_samples=30 - i, verdict="validated-alpha" if i == 0 else "unproven",
            est_cost=(i % 2 == 1),
        )
        for i in range(n)
    ]


def test_edge_validation_panel_in_dashboard_grid() -> None:
    """P1-3: edge-validation panel is wired into the 55-row grid."""
    snap = _full_snap()
    snap.edge_validation = _edge_rows(2)
    rows = render_dashboard(snap, width=TARGET_WIDTH, height=TARGET_HEIGHT)
    assert len(rows) == TARGET_HEIGHT
    assert any("EDGE VALIDATION" in r for r in rows)
    assert any("validated-alpha" in r for r in rows)


def test_edge_panel_with_data_keeps_55_rows_and_full_width() -> None:
    snap = _full_snap()
    snap.edge_validation = _edge_rows(8)
    rows = render_dashboard(snap, width=TARGET_WIDTH, height=TARGET_HEIGHT)
    assert len(rows) == TARGET_HEIGHT
    for i, r in enumerate(rows):
        assert vlen(r) == TARGET_WIDTH, f"row {i} vlen={vlen(r)}"


def test_regime_crisis_still_visible_with_edge_panel() -> None:
    """P1-3 regression guard: adding the edge panel must NOT silently truncate
    the regime heatmap — crisis (the 4th regime) must still render in-grid.
    """
    snap = _full_snap()
    snap.edge_validation = _edge_rows(4)
    snap.regime_bars[3] = RegimeBar(regime="crisis", count=3)
    rows = render_dashboard(snap, width=TARGET_WIDTH, height=TARGET_HEIGHT)
    assert any("crisis" in r for r in rows), "crisis regime silently dropped"


def test_cell_top_overflow_shows_more_hint() -> None:
    """P1-3: cell-top truncation is announced (not a silent data drop)."""
    snap = _full_snap()
    snap.cell_top = snap.cell_top * 5  # 10 eligible cells
    rows = render_cell_top_panel(snap, width=TARGET_WIDTH, max_rows=4)
    assert any("more" in r.lower() for r in rows), "silent cell-top truncation"


def test_cell_bottom_overflow_shows_more_hint() -> None:
    snap = _full_snap()
    snap.cell_bottom = snap.cell_bottom * 5
    rows = render_cell_bottom_panel(snap, width=TARGET_WIDTH, max_rows=3)
    assert any("more" in r.lower() for r in rows), "silent cell-bottom truncation"


def test_trades_overflow_shows_more_hint() -> None:
    snap = _full_snap()
    snap.recent_trades = snap.recent_trades * 5
    rows = render_trades_panel(snap, width=TARGET_WIDTH, max_rows=1)
    assert any("more" in r.lower() for r in rows), "silent trades truncation"


def test_edge_validation_panel_standalone_renders_rows() -> None:
    snap = _full_snap()
    snap.edge_validation = _edge_rows(2)
    rows = render_edge_validation_panel(snap, width=TARGET_WIDTH, max_rows=2)
    assert "EDGE VALIDATION" in rows[0]
    assert any("validated-alpha" in r for r in rows)


def test_zero_snapshot_renders_zero_grid_safely() -> None:
    snap = DashboardSnapshot()
    rows = render_dashboard(snap, width=TARGET_WIDTH, height=TARGET_HEIGHT)
    assert len(rows) == TARGET_HEIGHT
    for r in rows:
        assert vlen(r) == TARGET_WIDTH


# ---------------------------------------------------------------------------
# DB-backed snapshot collection
# ---------------------------------------------------------------------------


def test_collect_empty_db_returns_zero_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    init_db(db_path).close()
    snap = collect_snapshot(db_path)
    assert snap.equity_now == STARTING_CAPITAL
    assert snap.exposed_usd == 0.0
    assert snap.upnl_total == 0.0
    assert snap.daily_pnl_usd == 0.0
    assert snap.positions == []
    assert snap.gate_decisions  # 8 per-gate decision rows
    assert len(snap.gate_decisions) == 8


def test_collect_missing_db_returns_zero_snapshot(tmp_path: Path) -> None:
    snap = collect_snapshot(tmp_path / "nope.sqlite")
    assert snap.equity_now == STARTING_CAPITAL
    assert snap.positions == []


def test_collect_snapshot_with_fills_computes_realised_pnl(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_ms = int(time.time() * 1000)
    # Insert open + close fill pair (closed trade with $25 PnL)
    conn.executemany(
        """INSERT INTO fills (
            fill_id, venue, instrument_id, strategy_id, side, size_usd,
            fill_price, fee_usd, slippage_bps, ts_ms, order_id,
            contribution_id, pnl_usd, is_close, base_qty, quote_qty, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("f1", "okx", "okx:BTC-USDT", "volume_burst", "buy", 100.0,
             80_000.0, 0.05, 0.5, now_ms - 600_000, "o1", None, 0.0, 0,
             0.00125, 100.0, "filled"),
            ("f2", "okx", "okx:BTC-USDT", "volume_burst", "sell", 125.0,
             80_500.0, 0.06, 0.5, now_ms - 60_000, "o2", None, 25.0, 1,
             0.00125, 125.0, "filled"),
        ],
    )
    conn.commit()
    conn.close()
    snap = collect_snapshot(db_path)
    # Net of fees (forensic 2026-05-29 P0): gross +25.0 − fees (0.05 + 0.06).
    assert snap.daily_pnl_usd == pytest.approx(24.89)
    assert snap.daily_trades == 1
    assert len(snap.recent_trades) == 1
    t = snap.recent_trades[0]
    assert t.symbol == "BTC-USDT"
    assert t.exit_reason == "TP"           # PnL>0 → TP heuristic
    assert t.pnl_usd == pytest.approx(25.0)
    assert t.held_sec == pytest.approx(540.0, abs=2.0)


def test_recent_trades_pairing_uses_contribution_id_exact(tmp_path: Path) -> None:
    """Codex round-2 P0 regression: when contribution_id is set, pairing must
    use the exact position link, NOT FIFO order, so two same-bucket positions
    with deliberately reversed close order still pair to their own entries.
    """
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    base_ms = int(time.time() * 1000) - 3_600_000
    conn.executemany(
        """INSERT INTO fills (
            fill_id, venue, instrument_id, strategy_id, side, size_usd,
            fill_price, fee_usd, slippage_bps, ts_ms, order_id,
            contribution_id, pnl_usd, is_close, base_qty, quote_qty, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            # Open position A at $100, contribution_id="posA"
            ("openA", "okx", "okx:BTC-USDT", "tsmom", "buy", 100.0, 100.0,
             0.0, 0.0, base_ms, "ordA", "posA", 0.0, 0, 1.0, 100.0, "filled"),
            # Open position B at $110, contribution_id="posB"
            ("openB", "okx", "okx:BTC-USDT", "tsmom", "buy", 110.0, 110.0,
             0.0, 0.0, base_ms + 60_000, "ordB", "posB", 0.0, 0, 1.0, 110.0,
             "filled"),
            # Close B FIRST (reversed FIFO order) → must match openB ($110), PnL +5
            ("closeB", "okx", "okx:BTC-USDT", "tsmom", "sell", 115.0, 115.0,
             0.0, 0.0, base_ms + 600_000, "ordCB", "posB", 5.0, 1, 1.0, 115.0,
             "filled"),
            # Close A second → must match openA ($100), PnL +5
            ("closeA", "okx", "okx:BTC-USDT", "tsmom", "sell", 105.0, 105.0,
             0.0, 0.0, base_ms + 660_000, "ordCA", "posA", 5.0, 1, 1.0, 105.0,
             "filled"),
        ],
    )
    conn.commit()
    conn.close()
    snap = collect_snapshot(db_path)
    assert len(snap.recent_trades) == 2
    # Recent first → closeA (ts later) → must pair with openA at $100 NOT $110
    t_recent, t_older = snap.recent_trades[0], snap.recent_trades[1]
    assert t_recent.entry_price == pytest.approx(100.0), (
        f"close A must pair with open A via contribution_id, got entry={t_recent.entry_price}"
    )
    assert t_recent.exit_price == pytest.approx(105.0)
    assert t_older.entry_price == pytest.approx(110.0)
    assert t_older.exit_price == pytest.approx(115.0)


def test_recent_trades_pairing_no_fifo_when_contribution_missing(
    tmp_path: Path,
) -> None:
    """Codex round-3 P0 regression: a close with non-NULL contribution_id whose
    matching open is ABSENT must NOT FIFO-pair with a same-bucket open that
    belongs to a different position. Should reconstruct from PnL instead.
    """
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    base_ms = int(time.time() * 1000) - 3_600_000
    conn.executemany(
        """INSERT INTO fills (
            fill_id, venue, instrument_id, strategy_id, side, size_usd,
            fill_price, fee_usd, slippage_bps, ts_ms, order_id,
            contribution_id, pnl_usd, is_close, base_qty, quote_qty, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            # Open A (contribution_id="posA") at $200 — still active
            ("openA", "okx", "okx:BTC-USDT", "tsmom", "buy", 200.0, 200.0,
             0.0, 0.0, base_ms, "ordA", "posA", 0.0, 0, 1.0, 200.0, "filled"),
            # Close B (contribution_id="posB") — open B was never persisted (data loss)
            # Close at $130 with $30 PnL → reconstruct entry = $130 - $30/1qty = $100
            ("closeB", "okx", "okx:BTC-USDT", "tsmom", "sell", 130.0, 130.0,
             0.0, 0.0, base_ms + 600_000, "ordCB", "posB", 30.0, 1, 1.0,
             130.0, "filled"),
        ],
    )
    conn.commit()
    conn.close()
    snap = collect_snapshot(db_path)
    assert len(snap.recent_trades) == 1
    t = snap.recent_trades[0]
    # Reconstructed entry from PnL: 130 - 30/1 = 100.0 (NOT openA's $200)
    assert t.entry_price == pytest.approx(100.0), (
        f"close with missing contribution_id must reconstruct, got {t.entry_price}"
    )
    assert t.exit_price == pytest.approx(130.0)
    # held_sec is 0 because no open ts available
    assert t.held_sec == 0.0


def test_recent_trades_pairing_is_fifo_safe_multi_leg(tmp_path: Path) -> None:
    """Codex P0 regression: 2 opens then 2 closes must pair FIFO, not latest-open."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    base_ms = int(time.time() * 1000) - 3_600_000
    conn.executemany(
        """INSERT INTO fills (
            fill_id, venue, instrument_id, strategy_id, side, size_usd,
            fill_price, fee_usd, slippage_bps, ts_ms, order_id,
            contribution_id, pnl_usd, is_close, base_qty, quote_qty, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            # Open 1 at $100
            ("oA", "okx", "okx:BTC-USDT", "tsmom", "buy", 100.0, 100.0, 0.0,
             0.0, base_ms,             "o1a", None, 0.0, 0, 1.0, 100.0, "filled"),
            # Open 2 at $110
            ("oB", "okx", "okx:BTC-USDT", "tsmom", "buy", 110.0, 110.0, 0.0,
             0.0, base_ms +   60_000,  "o2a", None, 0.0, 0, 1.0, 110.0, "filled"),
            # Close 1 at $115 → pairs with Open 1 (FIFO) → PnL +15, held 600s
            ("cA", "okx", "okx:BTC-USDT", "tsmom", "sell", 115.0, 115.0, 0.0,
             0.0, base_ms +  600_000,  "o3a", None, 15.0, 1, 1.0, 115.0, "filled"),
            # Close 2 at $108 → pairs with Open 2 (FIFO) → PnL -2, held 540s
            ("cB", "okx", "okx:BTC-USDT", "tsmom", "sell", 108.0, 108.0, 0.0,
             0.0, base_ms +  600_000 + 60_000,  "o4a", None, -2.0, 1, 1.0, 108.0, "filled"),
        ],
    )
    conn.commit()
    conn.close()
    snap = collect_snapshot(db_path)
    # Two paired trades expected
    assert len(snap.recent_trades) == 2
    # Recent first → Close 2 first (sort desc by ts)
    t_recent, t_older = snap.recent_trades[0], snap.recent_trades[1]
    assert t_recent.entry_price == pytest.approx(110.0)
    assert t_recent.exit_price == pytest.approx(108.0)
    assert t_recent.pnl_usd == pytest.approx(-2.0)
    assert t_older.entry_price == pytest.approx(100.0)
    assert t_older.exit_price == pytest.approx(115.0)
    assert t_older.pnl_usd == pytest.approx(15.0)


def test_pad_does_not_truncate_in_non_tty_mode(tmp_path: Path) -> None:
    """Regression: the early-bug where pad() lost content in piped (non-TTY) mode.

    `RESET=""` made `s.endswith(RESET)` always True → `s[:0]` blanked the row.
    """
    from polaris.scripts.dashboard.ansi_palette import pad

    s = "abcdef"
    out = pad(s, 20)
    assert out.startswith("abcdef")
    assert vlen(out) == 20


# ---------------------------------------------------------------------------
# GPT silent-degradation: per-model ok% aggregation (#13 observability)
# ---------------------------------------------------------------------------


def _insert_gate_event(
    conn: object, *, model: str, phase: str, ts: int, gate_id: int = 4
) -> None:
    conn.execute(  # type: ignore[attr-defined]
        """INSERT INTO gate_events
            (event_id, run_id, gate_id, phase, decision, model_used,
             latency_ms, created_ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"{model}-{phase}-{ts}-{gate_id}-{time.time_ns()}",
            "run1",
            gate_id,
            phase,
            "KILL" if phase == "fail" else "PASS",
            model,
            10,
            ts,
        ),
    )


def test_gpt_stats_ok_pct_per_model(tmp_path: Path) -> None:
    """#13: per-model ok% surfaces silent degradation (gpt-5.5 100% fail)."""
    from polaris.scripts.dashboard.snapshot_sections import _gpt_stats

    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_s = int(time.time())
    # gpt (P0): 8 success + 2 fail → 80% ok, err_n=2
    for i in range(8):
        _insert_gate_event(conn, model="gpt", phase="success", ts=now_s - i)
    for i in range(2):
        _insert_gate_event(conn, model="gpt", phase="fail", ts=now_s - i)
    # gpt_p1 (P1): 5 fail, 0 success → 0% ok (silent fail), err_n=5
    for i in range(5):
        _insert_gate_event(conn, model="gpt_p1", phase="fail", ts=now_s - i)
    # python rows must be excluded entirely
    _insert_gate_event(conn, model="python", phase="success", ts=now_s)
    conn.commit()

    stats = _gpt_stats(conn, now_s=now_s + 1)
    by_model = {s.model: s for s in stats}
    conn.close()

    assert set(by_model) == {"gpt", "gpt_p1"}  # python excluded
    assert by_model["gpt"].calls_per_h == 10
    assert by_model["gpt"].ok_pct == pytest.approx(80.0)
    assert by_model["gpt"].err_n == 2
    assert by_model["gpt_p1"].calls_per_h == 5
    assert by_model["gpt_p1"].ok_pct == pytest.approx(0.0)
    assert by_model["gpt_p1"].err_n == 5


def test_gpt_stats_empty_returns_no_rows(tmp_path: Path) -> None:
    """No gate_events → empty list (no divide-by-zero)."""
    from polaris.scripts.dashboard.snapshot_sections import _gpt_stats

    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    stats = _gpt_stats(conn, now_s=int(time.time()))
    conn.close()
    assert stats == []


# ---------------------------------------------------------------------------
# Per-gate DECISION summary (replaces pass/kill funnel) — G5 T4 decode
# ---------------------------------------------------------------------------


def _insert_g5_sized(
    conn: object, *, risk_pct: float, notional: float, tier: float, ts: int
) -> None:
    payload = json.dumps(
        {
            "sized": {
                "final_risk_pct": risk_pct,
                "final_notional_usd": notional,
                "leverage": 20.0,
                "binding_cap": "proposed",
                "proposal": {
                    "base_risk_pct": 0.02,
                    "continuous_scalar": 0.76,
                    "tier_amplifier": tier,
                    "cell_routing_mult": 1.0,
                    "listing_watchdog_mult": 1.0,
                    "proposed_risk_pct": risk_pct,
                },
            }
        }
    )
    conn.execute(  # type: ignore[attr-defined]
        """INSERT INTO gate_events
            (event_id, run_id, gate_id, phase, decision, model_used,
             payload_json, latency_ms, created_ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"g5-{ts}-{time.time_ns()}",
            "run1",
            5,
            "success",
            "SIZED",
            "python",
            payload,
            10,
            ts,
        ),
    )


def test_gate_decisions_g5_t4_headline(tmp_path: Path) -> None:
    """G5 Sizer row aggregates the T4 size: median risk%, notional, tier mix."""
    from polaris.scripts.dashboard.snapshot_sections import _gate_decisions

    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_s = int(time.time())
    # Three SIZED decisions: median risk 0.015 → 1.5%, median notional 24000,
    # tier mix two 1x + one 2x.
    _insert_g5_sized(conn, risk_pct=0.010, notional=20000.0, tier=1.0, ts=now_s - 3)
    _insert_g5_sized(conn, risk_pct=0.015, notional=24000.0, tier=1.0, ts=now_s - 2)
    _insert_g5_sized(conn, risk_pct=0.020, notional=30000.0, tier=2.0, ts=now_s - 1)
    conn.commit()

    rows = _gate_decisions(
        conn, now_s=now_s + 1, universe_focus_n=30, universe_refresh="16:34:12"
    )
    by_gate = {r.gate_id: r for r in rows}
    conn.close()

    assert len(rows) == 8
    g5 = by_gate[5]
    assert g5.n == 3
    assert g5.metrics["sized_n"] == 3
    assert g5.metrics["median_risk_pct"] == pytest.approx(1.5, abs=0.01)
    assert g5.metrics["median_notional_usd"] == pytest.approx(24000.0)
    assert g5.metrics["tier_mix"] == {"1x": 2, "2x": 1}
    assert "sized" in g5.headline and "1.5" in g5.headline
    # G1 row reflects the passed focus count + derived exclusion (no universe rows
    # → excluded clamps to 0, focus echoes the input).
    assert by_gate[1].metrics["focus_n"] == 30
