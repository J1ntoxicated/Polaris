"""Shadow acceptance-metrics — read-only analysis feeding a FUTURE cutover.

Pins the conductor_g3g4_cutover_2026-05-31 follow-up (#1): honest, read-only
metrics over the live shadow log + ledger. These NEVER write and NEVER touch a
live trading decision — they are analysis used to ground a later cutover. The
tests exercise each metric plus edge cases (no rows, all-cold, mixed) and assert
the module is read-only (no writes).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.pipeline.agents.shadow_acceptance import (
    chop_churn,
    fee_adjusted_realized_r,
    gate_agreement,
    gpt_kill_counterfactual,
    summarize,
)
from polaris.core.pipeline.gate_state import GATE_SIGNAL_VALIDATOR

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_shadow(
    conn: sqlite3.Connection,
    *,
    gate_id: int,
    regime: str,
    technical: str,
    gpt: str,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    cell_warm: int = 1,
    ts: int = NOW,
) -> None:
    import uuid

    mismatch = 1 if (gpt != "" and technical != gpt) else 0
    conn.execute(
        """
        INSERT INTO gate_shadow_events
            (event_id, run_id, signal_id, gate_id, venue, symbol, regime,
             technical_decision, technical_scalar, technical_reason,
             technical_flags, gpt_decision, mismatch, cell_warm, created_ts)
        VALUES (?, 'r', 's', ?, ?, ?, ?, ?, 0.0, '', '', ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex, gate_id, venue, symbol, regime, technical,
            gpt, mismatch, cell_warm, ts,
        ),
    )


def _seed_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    status: str,
    opened_ts: int,
    closed_ts: int | None,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    exit_state: str = "open",
) -> None:
    conn.execute(
        """
        INSERT INTO positions
            (position_id, venue, symbol, strategy_id, entry_strategy_id,
             active_strategy_id, side, qty, status, opened_ts, closed_ts,
             exit_state)
        VALUES (?, ?, ?, 'vb', 'vb', 'vb', 'buy', 1.0, ?, ?, ?, ?)
        """,
        (position_id, venue, symbol, status, opened_ts, closed_ts, exit_state),
    )


def _seed_fill(
    conn: sqlite3.Connection,
    *,
    fill_id: str,
    is_close: int,
    pnl_usd: float = 0.0,
    fee_usd: float = 0.0,
    slippage_bps: float = 0.0,
    size_usd: float = 1000.0,
    contribution_id: str | None = None,
    venue: str = "okx",
    instrument_id: str = "BTC-USDT",
    ts_ms: int = NOW * 1000,
) -> None:
    conn.execute(
        """
        INSERT INTO fills
            (fill_id, venue, instrument_id, strategy_id, side, size_usd,
             fill_price, fee_usd, slippage_bps, ts_ms, order_id,
             contribution_id, pnl_usd, is_close)
        VALUES (?, ?, ?, 'vb', 'buy', ?, 100.0, ?, ?, ?, 'o', ?, ?, ?)
        """,
        (
            fill_id, venue, instrument_id, size_usd, fee_usd, slippage_bps,
            ts_ms, contribution_id, pnl_usd, is_close,
        ),
    )


def _seed_cell(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    ticker: str,
    regime: str,
    avg_pnl_r: float,
    venue: str = "okx",
    n_eff: float = 10.0,
) -> None:
    conn.execute(
        """
        INSERT INTO cell_matrix_p0
            (exchange, strategy, ticker, regime, n_eff, wins_eff,
             pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts)
        VALUES (?, ?, ?, ?, ?, 0.0, 0.0, ?, 0.0, ?)
        """,
        (venue, strategy, ticker, regime, n_eff, avg_pnl_r, NOW),
    )


# ===========================================================================
# gate_agreement
# ===========================================================================


def test_gate_agreement_empty(memdb: sqlite3.Connection) -> None:
    out = gate_agreement(memdb, GATE_SIGNAL_VALIDATOR)
    assert out["n"] == 0
    assert out["overall"]["n"] == 0
    assert out["by_regime"] == {}


def test_gate_agreement_excludes_empty_gpt(memdb: sqlite3.Connection) -> None:
    # 2 rows with a GPT decision, 1 with empty gpt (must be excluded).
    _seed_shadow(memdb, gate_id=3, regime="chop", technical="PASS", gpt="KILL")
    _seed_shadow(memdb, gate_id=3, regime="chop", technical="KILL", gpt="KILL")
    _seed_shadow(memdb, gate_id=3, regime="chop", technical="PASS", gpt="")
    out = gate_agreement(memdb, 3)
    assert out["n"] == 2  # empty-gpt row excluded
    assert out["overall"]["gpt_kill"] == 2
    assert out["overall"]["technical_kill"] == 1
    assert out["overall"]["mismatch"] == 1
    assert out["overall"]["mismatch_pct"] == pytest.approx(50.0)


def test_gate_agreement_per_regime(memdb: sqlite3.Connection) -> None:
    _seed_shadow(memdb, gate_id=3, regime="chop", technical="PASS", gpt="KILL")
    _seed_shadow(memdb, gate_id=3, regime="bull", technical="PASS", gpt="PASS")
    out = gate_agreement(memdb, 3)
    assert set(out["by_regime"]) == {"chop", "bull"}
    assert out["by_regime"]["chop"]["gpt_kill"] == 1
    assert out["by_regime"]["chop"]["gpt_kill_pct"] == pytest.approx(100.0)
    assert out["by_regime"]["bull"]["gpt_kill"] == 0


def test_gate_agreement_gate_scoped(memdb: sqlite3.Connection) -> None:
    _seed_shadow(memdb, gate_id=3, regime="chop", technical="PASS", gpt="KILL")
    _seed_shadow(memdb, gate_id=4, regime="chop", technical="PROCEED", gpt="KILL")
    out3 = gate_agreement(memdb, 3)
    assert out3["n"] == 1


# ===========================================================================
# chop_churn
# ===========================================================================


def test_chop_churn_empty(memdb: sqlite3.Connection) -> None:
    out = chop_churn(memdb)
    assert out["n_closed"] == 0
    assert out["entries_per_closed_trade"] == 0.0
    assert out["loser_timeout_closes"] == 0


def test_chop_churn_metrics(memdb: sqlite3.Connection) -> None:
    # 2 closed positions: one quick winner, one stale loser (>900s, negative).
    _seed_position(
        memdb, position_id="p1", status="closed",
        opened_ts=NOW, closed_ts=NOW + 60,
    )
    _seed_position(
        memdb, position_id="p2", status="closed",
        opened_ts=NOW, closed_ts=NOW + 1200,  # 1200s hold (>900)
    )
    # 1 still-open position (an "entry" that hasn't closed).
    _seed_position(
        memdb, position_id="p3", status="open",
        opened_ts=NOW, closed_ts=None,
    )
    # close fills (contribution_id = position_id, is_close=1).
    _seed_fill(memdb, fill_id="f1", is_close=1, pnl_usd=25.0, contribution_id="p1")
    _seed_fill(memdb, fill_id="f2", is_close=1, pnl_usd=-30.0, contribution_id="p2")
    out = chop_churn(memdb)
    assert out["n_closed"] == 2
    # 3 total entries (positions) / 2 closed = 1.5
    assert out["entries_per_closed_trade"] == pytest.approx(1.5)
    # p2 = negative pnl + hold 1200 >= 900 → loser-timeout heuristic.
    assert out["loser_timeout_closes"] == 1
    assert out["avg_hold_sec"] == pytest.approx((60 + 1200) / 2)


def test_chop_churn_same_symbol_repeat_loss_max(memdb: sqlite3.Connection) -> None:
    # 3 consecutive same-symbol losers on BTC, then a winner.
    for i, (h, p) in enumerate([(100, -10.0), (100, -10.0), (100, -10.0), (100, 5.0)]):
        _seed_position(
            memdb, position_id=f"b{i}", status="closed",
            opened_ts=NOW + i * 10, closed_ts=NOW + i * 10 + h, symbol="BTC-USDT",
        )
        _seed_fill(
            memdb, fill_id=f"bf{i}", is_close=1, pnl_usd=p, contribution_id=f"b{i}",
            instrument_id="BTC-USDT",
        )
    out = chop_churn(memdb)
    assert out["same_symbol_repeat_loss_max"] == 3


# ===========================================================================
# fee_adjusted_realized_r
# ===========================================================================


def test_fee_adjusted_realized_r_empty(memdb: sqlite3.Connection) -> None:
    out = fee_adjusted_realized_r(memdb)
    assert out["n"] == 0
    assert out["gross_realized_r"] == 0.0
    assert out["net_realized_r"] == 0.0


def test_fee_adjusted_realized_r_drag(memdb: sqlite3.Connection) -> None:
    # Two close fills: gross pnl 100 & -40; fees 5 & 3; slippage drag via bps.
    _seed_fill(
        memdb, fill_id="f1", is_close=1, pnl_usd=100.0, fee_usd=5.0,
        slippage_bps=10.0, size_usd=1000.0, contribution_id="p1",
    )
    _seed_fill(
        memdb, fill_id="f2", is_close=1, pnl_usd=-40.0, fee_usd=3.0,
        slippage_bps=20.0, size_usd=2000.0, contribution_id="p2",
    )
    out = fee_adjusted_realized_r(memdb)
    assert out["n"] == 2
    # gross/net/drag are consistent: net = gross - drag (all in R units).
    assert out["net_realized_r"] == pytest.approx(
        out["gross_realized_r"] - out["fee_slippage_drag_r"]
    )
    # Drag is non-negative (fees + slippage always cost).
    assert out["fee_slippage_drag_r"] >= 0.0


def test_fee_adjusted_realized_r_adds_real_and_demo_keys(
    memdb: sqlite3.Connection,
) -> None:
    """Component A: existing keys keep their meaning; real_*/demo_* added."""
    from polaris.core.economics.fees import demo_fee_usd, real_fee_usd

    _seed_fill(
        memdb, fill_id="f1", is_close=1, pnl_usd=100.0, fee_usd=7.0,
        slippage_bps=0.0, size_usd=1000.0, contribution_id="p1", venue="okx",
    )
    _seed_fill(
        memdb, fill_id="f2", is_close=1, pnl_usd=-40.0, fee_usd=14.0,
        slippage_bps=0.0, size_usd=2000.0, contribution_id="p2", venue="okx",
    )
    out = fee_adjusted_realized_r(memdb)
    denom = 50.0
    # Existing keys unchanged in meaning (stored-fee + slippage drag).
    assert out["n"] == 2
    assert out["gross_realized_r"] == pytest.approx(60.0 / denom)
    # NEW real-fee keys: real round-trip fee schedule (0.10% taker, both legs).
    real_drag = (
        2 * real_fee_usd("okx", 1000.0) + 2 * real_fee_usd("okx", 2000.0)
    ) / denom
    demo_drag = (
        2 * demo_fee_usd("okx", 1000.0) + 2 * demo_fee_usd("okx", 2000.0)
    ) / denom
    assert out["real_fee_drag_r"] == pytest.approx(real_drag)
    assert out["demo_fee_drag_r"] == pytest.approx(demo_drag)
    assert out["net_realized_real_fee_r"] == pytest.approx(
        out["gross_realized_r"] - real_drag
    )
    assert out["net_realized_demo_fee_r"] == pytest.approx(
        out["gross_realized_r"] - demo_drag
    )
    # Real fee is far cheaper than demo → real net strictly above demo net.
    assert out["net_realized_real_fee_r"] > out["net_realized_demo_fee_r"]


# ===========================================================================
# gpt_kill_counterfactual
# ===========================================================================


def test_gpt_kill_counterfactual_empty(memdb: sqlite3.Connection) -> None:
    out = gpt_kill_counterfactual(memdb)
    assert out["n_gpt_kill"] == 0
    assert out["over_block_pct"] == 0.0
    assert "proxy" in out


def test_gpt_kill_counterfactual_classifies(memdb: sqlite3.Connection) -> None:
    # G3 GPT-KILL row in regime 'chop' for BTC — matched cell mean avg_pnl_r > 0
    # → potential over-block of a winning cell.
    _seed_shadow(
        memdb, gate_id=3, regime="chop", technical="PASS", gpt="KILL",
        symbol="BTC-USDT", venue="okx",
    )
    _seed_cell(memdb, strategy="vb", ticker="BTC-USDT", regime="chop", avg_pnl_r=0.5)
    _seed_cell(memdb, strategy="ts", ticker="BTC-USDT", regime="chop", avg_pnl_r=0.3)
    # G3 GPT-KILL for ETH — matched cell mean < 0 → justified (avoided loser).
    _seed_shadow(
        memdb, gate_id=3, regime="chop", technical="PASS", gpt="KILL",
        symbol="ETH-USDT", venue="okx",
    )
    _seed_cell(memdb, strategy="vb", ticker="ETH-USDT", regime="chop", avg_pnl_r=-0.4)
    # G3 GPT-KILL for SOL — no matching cell → no-match (cold/unknown).
    _seed_shadow(
        memdb, gate_id=3, regime="chop", technical="PASS", gpt="KILL",
        symbol="SOL-USDT", venue="okx",
    )
    out = gpt_kill_counterfactual(memdb)
    assert out["n_gpt_kill"] == 3
    assert out["over_block"] == 1  # BTC (mean > 0)
    assert out["justified"] == 1  # ETH (mean < 0)
    assert out["no_match"] == 1  # SOL (no cell)
    assert out["over_block_pct"] == pytest.approx(100.0 / 3)


def test_gpt_kill_counterfactual_ignores_non_kill_and_other_gates(
    memdb: sqlite3.Connection,
) -> None:
    # G3 PASS (not a KILL) — ignored.
    _seed_shadow(memdb, gate_id=3, regime="chop", technical="PASS", gpt="PASS")
    # G4 KILL — different gate, ignored (counterfactual is G3-only).
    _seed_shadow(memdb, gate_id=4, regime="chop", technical="PROCEED", gpt="KILL")
    out = gpt_kill_counterfactual(memdb)
    assert out["n_gpt_kill"] == 0


# ===========================================================================
# summarize + read-only invariant
# ===========================================================================


def test_summarize_returns_all_sections(memdb: sqlite3.Connection) -> None:
    out = summarize(memdb)
    assert set(out) >= {
        "g3_agreement",
        "g4_agreement",
        "chop_churn",
        "fee_adjusted_realized_r",
        "gpt_kill_counterfactual",
    }


def test_module_is_read_only(memdb: sqlite3.Connection) -> None:
    """No metric writes to the DB (acceptance analysis is read-only)."""
    _seed_shadow(memdb, gate_id=3, regime="chop", technical="PASS", gpt="KILL")
    _seed_position(
        memdb, position_id="p1", status="closed", opened_ts=NOW, closed_ts=NOW + 60
    )
    _seed_fill(memdb, fill_id="f1", is_close=1, pnl_usd=10.0, contribution_id="p1")

    def _counts() -> tuple[int, int, int, int]:
        return (
            memdb.execute("SELECT COUNT(*) FROM gate_shadow_events").fetchone()[0],
            memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0],
            memdb.execute("SELECT COUNT(*) FROM fills").fetchone()[0],
            memdb.execute("SELECT COUNT(*) FROM cell_matrix_p0").fetchone()[0],
        )

    before = _counts()
    summarize(memdb)
    assert _counts() == before
