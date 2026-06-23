"""Go-live confidence metrics — real-fee-net per (strategy × regime) (Component A).

READ-ONLY accounting. Per the plan: per (strategy_id × regime) real-fee-net
expected R + sample n + posterior lower-confidence-bound; plus overall
win_rate, profit_factor, turnover_ratio, and fee_drag_real_r vs fee_drag_demo_r.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.core.economics.fees import real_fee_usd
from polaris.core.metrics.risk_unit import r_budget_for_venue
from polaris.core.pipeline.agents.confidence import confidence_summary
from polaris.storage.schema import ALL_DDL

# Step N: per-trade R is now the stream-common R_budget(venue), not the flat
# proxy. All seeded trades below are OKX → R_budget = 0.02 × $79k = $1,580.
_OKX_R_BUDGET = r_budget_for_venue("okx")


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _insert_position(
    conn: sqlite3.Connection, *, pid: str, venue: str, symbol: str,
    strategy: str, group_id: str, opened_ts: int, closed_ts: int,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, closed_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 1.0, 'closed', ?, ?)",
        (pid, venue, symbol, group_id, strategy, strategy, strategy,
         opened_ts, closed_ts),
    )


def _insert_fill(
    conn: sqlite3.Connection, *, fill_id: str, venue: str, symbol: str,
    strategy: str, side: str, size_usd: float, pnl_usd: float, fee_usd: float,
    is_close: int, ts_ms: int, contribution_id: str | None,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, ?, ?, ?, ?, ?, 150.0, ?, 0.0, ?, ?, ?, ?, ?, 1.0, 1.0, 'filled')",
        (fill_id, venue, f"{venue}:{symbol}", strategy, side, size_usd, fee_usd,
         ts_ms, f"o_{fill_id}", contribution_id, pnl_usd, is_close),
    )


def _insert_regime(
    conn: sqlite3.Connection, *, venue: str, group_id: str, regime: str
) -> None:
    conn.execute(
        "INSERT INTO regime_state (venue, underlying_group_id, regime, updated_ts) "
        "VALUES (?, ?, ?, 0)",
        (venue, group_id, regime),
    )


def test_empty_db_zero_metrics(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = confidence_summary(conn, starting_equity=130_000.0)
    finally:
        conn.close()
    assert out["overall"]["n_closed"] == 0
    assert out["overall"]["win_rate_pct"] == 0.0
    assert out["overall"]["profit_factor"] == 0.0
    assert out["overall"]["turnover_ratio"] == 0.0
    assert out["by_strategy_regime"] == []
    assert out["overall"]["fee_drag_real_r"] == 0.0
    assert out["overall"]["fee_drag_demo_r"] == 0.0


def test_overall_win_rate_profit_factor_turnover(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    base = int(time.time() * 1000)
    # 3 wins (+$100 each), 1 loss (-$50). notional 1000 per leg.
    seq = [(+100.0, "g1", "bull_trend"), (+100.0, "g1", "bull_trend"),
           (+100.0, "g2", "chop"), (-50.0, "g2", "chop")]
    for i, (pnl, grp, _reg) in enumerate(seq):
        pid = f"p{i}"
        _insert_position(conn, pid=pid, venue="okx", symbol="SOL-USDT",
                         strategy="tsmom", group_id=grp,
                         opened_ts=(base // 1000) - 100, closed_ts=base // 1000)
        _insert_fill(conn, fill_id=f"o{i}", venue="okx", symbol="SOL-USDT",
                     strategy="tsmom", side="buy", size_usd=1000.0, pnl_usd=0.0,
                     fee_usd=7.0, is_close=0, ts_ms=base + i * 10,
                     contribution_id=pid)
        _insert_fill(conn, fill_id=f"c{i}", venue="okx", symbol="SOL-USDT",
                     strategy="tsmom", side="sell", size_usd=1000.0, pnl_usd=pnl,
                     fee_usd=7.0, is_close=1, ts_ms=base + i * 10 + 5,
                     contribution_id=pid)
    for grp, reg in [("g1", "bull_trend"), ("g2", "chop")]:
        _insert_regime(conn, venue="okx", group_id=grp, regime=reg)
    conn.close()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = confidence_summary(conn, starting_equity=100_000.0)
    finally:
        conn.close()

    ov = out["overall"]
    assert ov["n_closed"] == 4
    assert abs(ov["win_rate_pct"] - 75.0) < 1e-9  # 3 of 4 gross-positive
    # profit_factor = gross_win / gross_loss = 300 / 50 = 6.0
    assert abs(ov["profit_factor"] - 6.0) < 1e-9
    # turnover = Σ all-fill notional = 8 legs * 1000 = 8000; ratio = 8000/100000
    assert abs(ov["turnover_ratio"] - (8000.0 / 100_000.0)) < 1e-9
    # fee drag (Step N, stream-common R_budget(okx)): demo = 8 legs * 7.0 /
    # R_budget ; real = 8 legs * (10bps*1000=1.0) / R_budget
    assert abs(ov["fee_drag_demo_r"] - (8 * 7.0 / _OKX_R_BUDGET)) < 1e-9
    real_leg = real_fee_usd("okx", 1000.0)  # 1.0
    assert abs(ov["fee_drag_real_r"] - (8 * real_leg / _OKX_R_BUDGET)) < 1e-9


def test_per_strategy_regime_real_fee_net_r_and_lcb(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    base = int(time.time() * 1000)
    # All tsmom in bull_trend; consistent +$3200 gross wins → positive net R and
    # an LCB that should be positive with enough samples. (Step N: R is now
    # scaled by R_budget(okx)=$1,580, so a meaningful ≈+2R edge needs a ~$3.2k
    # gross win rather than the old $100 — same edge-confidence intent.)
    for i in range(12):
        pid = f"p{i}"
        _insert_position(conn, pid=pid, venue="okx", symbol="HYPE-USDT",
                         strategy="tsmom", group_id="g1",
                         opened_ts=(base // 1000) - 100, closed_ts=base // 1000)
        _insert_fill(conn, fill_id=f"o{i}", venue="okx", symbol="HYPE-USDT",
                     strategy="tsmom", side="buy", size_usd=1000.0, pnl_usd=0.0,
                     fee_usd=7.0, is_close=0, ts_ms=base + i * 10,
                     contribution_id=pid)
        _insert_fill(conn, fill_id=f"c{i}", venue="okx", symbol="HYPE-USDT",
                     strategy="tsmom", side="sell", size_usd=1000.0,
                     pnl_usd=3200.0, fee_usd=7.0, is_close=1,
                     ts_ms=base + i * 10 + 5, contribution_id=pid)
    _insert_regime(conn, venue="okx", group_id="g1", regime="bull_trend")
    conn.close()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = confidence_summary(conn, starting_equity=100_000.0)
    finally:
        conn.close()

    cells = out["by_strategy_regime"]
    assert len(cells) == 1
    cell = cells[0]
    assert cell["strategy_id"] == "tsmom"
    assert cell["regime"] == "bull_trend"
    assert cell["n"] == 12
    # net real-fee R per trade (Step N) = (3200 - 2*1.0) / R_budget(okx)
    assert abs(cell["expected_real_fee_net_r"] - (3198.0 / _OKX_R_BUDGET)) < 1e-6
    # consistent positive sample → LCB strictly positive (edge confidence).
    assert cell["lcb_real_fee_net_r"] > 0.0
    assert cell["lcb_real_fee_net_r"] <= cell["expected_real_fee_net_r"] + 1e-9
    assert cell["lcb_sign"] == "+"


def test_regime_join_falls_back_to_chop_when_no_regime_state(tmp_path: Path) -> None:
    """No regime_state row → documented fallback to 'chop' (current-regime join)."""
    db_path = _mkdb(tmp_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    base = int(time.time() * 1000)
    pid = "p0"
    _insert_position(conn, pid=pid, venue="okx", symbol="BTC-USDT",
                     strategy="rsi_bb", group_id="gX",
                     opened_ts=(base // 1000) - 100, closed_ts=base // 1000)
    _insert_fill(conn, fill_id="o0", venue="okx", symbol="BTC-USDT",
                 strategy="rsi_bb", side="buy", size_usd=500.0, pnl_usd=0.0,
                 fee_usd=3.5, is_close=0, ts_ms=base, contribution_id=pid)
    _insert_fill(conn, fill_id="c0", venue="okx", symbol="BTC-USDT",
                 strategy="rsi_bb", side="sell", size_usd=500.0, pnl_usd=-20.0,
                 fee_usd=3.5, is_close=1, ts_ms=base + 5, contribution_id=pid)
    conn.close()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = confidence_summary(conn, starting_equity=100_000.0)
    finally:
        conn.close()
    cells = out["by_strategy_regime"]
    assert len(cells) == 1
    assert cells[0]["regime"] == "chop"
    assert cells[0]["lcb_sign"] == "-"  # losing cell → negative expectation
    assert "join_limitation" in out["notes"]
