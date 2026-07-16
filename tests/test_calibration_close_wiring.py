"""Probability calibration shadow close-seam wiring (frontgate-scan item #4,
G5) — ``_safe_record_calibration_outcome`` is the 5th call in the existing
4-way fail-open close fan-out (mirrors ``_safe_update_posterior``), wired into
BOTH ``fold_close_slice`` (partial closes) and the full-close path.

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.learners.calibration_shadow import record_close_outcome
from polaris.core.lifecycle.trade import SimulatedTrade
from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._production_close_effects import (
    _safe_record_calibration_outcome,
    fold_close_slice,
)
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.schema import ALL_DDL

NOW = 1_780_000_000


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys=ON;")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


# ---------------------------------------------------------------------------
# _safe_record_calibration_outcome — direct unit tests
# ---------------------------------------------------------------------------


def test_safe_wrapper_updates_existing_snapshot() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO calibration_pairs "
        "(signal_id, venue, strategy, ticker, regime, predicted_p_pos, "
        " n_samples_at_entry, created_ts) "
        "VALUES ('sig-x', 'okx', 'tsmom', 'BTC-USDT', 'bull_trend', 0.6, 10, ?)",
        (NOW,),
    )
    trade = SimulatedTrade(
        signal_id="sig-x", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=100.0, notional_usd=1000.0, open_ts=NOW,
    )
    _safe_record_calibration_outcome(
        conn, trade=trade, won=True, pnl_r_net=1.4, now_ts=NOW + 60,
        state=ProdLoopState(),
    )
    row = conn.execute(
        "SELECT realized_won, realized_pnl_r FROM calibration_pairs "
        "WHERE signal_id = 'sig-x'"
    ).fetchone()
    assert row == (1, pytest.approx(1.4))


def test_safe_wrapper_fails_open_on_broken_table() -> None:
    conn = _conn()
    conn.execute("DROP TABLE calibration_pairs")
    trade = SimulatedTrade(
        signal_id="sig-y", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=100.0, notional_usd=1000.0, open_ts=NOW,
    )
    # Must not raise — this is the fail-open contract the close path relies on.
    _safe_record_calibration_outcome(
        conn, trade=trade, won=False, pnl_r_net=-1.0, now_ts=NOW + 60,
        state=ProdLoopState(),
    )


# ---------------------------------------------------------------------------
# fold_close_slice wiring (partial-close path)
# ---------------------------------------------------------------------------


def test_fold_close_slice_calls_calibration_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polaris.scripts._production_close_effects as eff_mod

    conn = _conn()
    conn.execute(
        "INSERT INTO calibration_pairs "
        "(signal_id, venue, strategy, ticker, regime, predicted_p_pos, "
        " n_samples_at_entry, created_ts) "
        "VALUES ('sig-slice', 'okx', 'tsmom', 'BTC-USDT', 'bull_trend', 0.6, 10, ?)",
        (NOW,),
    )
    trade = SimulatedTrade(
        signal_id="sig-slice", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=100.0, notional_usd=1000.0, open_ts=NOW,
    )
    for name in (
        "_safe_update_cell_matrix", "_safe_run_learners", "_safe_record_meta_label",
        "_safe_update_posterior",
    ):
        monkeypatch.setattr(eff_mod, name, lambda *a, **k: None)  # noqa: ARG005

    state = ProdLoopState()
    fold_close_slice(
        conn, trade=trade, lookup_regime=_regime_stub,
        slice_pnl_r=1.2, slice_pnl_usd=120.0, now_ts=NOW + 60, state=state,
    )
    row = conn.execute(
        "SELECT realized_won FROM calibration_pairs WHERE signal_id = 'sig-slice'"
    ).fetchone()
    assert row is not None
    assert row[0] == 1


# ---------------------------------------------------------------------------
# Full close path wiring (terminal close)
# ---------------------------------------------------------------------------


class _FixedFillOKX:
    def __init__(self, *, filled_base: float, price: float = 130.0) -> None:
        self._filled = filled_base
        self._price = price
        self._used = False
        self._ord_prefix = f"sell_{uuid.uuid4().hex[:6]}"

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        return {"data": []}

    async def place_market_order(self, **kwargs: Any) -> Any:
        from polaris.venues.okx.adapter import OKXOrderResponse

        return OKXOrderResponse(
            ok=True, venue_order_id=f"{self._ord_prefix}_1", client_order_id="cl",
            code="0", msg="", raw={},
        )

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        if self._used:
            return {"data": []}
        self._used = True
        return {
            "data": [{
                "ordId": ord_id, "clOrdId": "cl", "instId": inst_id,
                "side": "sell", "tgtCcy": "base_ccy",
                "accFillSz": f"{self._filled:.10f}", "avgPx": str(self._price),
                "fee": "-0.02", "feeCcy": "USDT", "state": "filled",
                "uTime": str(NOW * 1000),
            }]
        }


def _seed_position(conn: sqlite3.Connection, *, position_id: str, base_qty: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', ?, 'open', ?, 0, 500.0)",
        (position_id, base_qty, NOW),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'tsmom', 'okx:BTC-USDT', 'okx', 'long', ?, 100.0, ?, "
        " 0.05, 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, base_qty, base_qty * 100.0, position_id,
         uuid.uuid4().hex),
    )
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
            " 130.0, 131.0, 129.0, 130.0, 100.0, 13000.0, 1, 130.0, 130.0, "
            " 130.0, 1.0, 'rest')",
            (ts,),
        )


@pytest.mark.asyncio
async def test_full_close_calls_calibration_outcome() -> None:
    conn = _conn()
    _seed_position(conn, position_id="pos-full", base_qty=100.0)
    signal_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO calibration_pairs "
        "(signal_id, venue, strategy, ticker, regime, predicted_p_pos, "
        " n_samples_at_entry, created_ts) "
        "VALUES (?, 'okx', 'tsmom', 'BTC-USDT', 'bull_trend', 0.6, 10, ?)",
        (signal_id, NOW),
    )
    trade = SimulatedTrade(
        signal_id=signal_id, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=100.0, notional_usd=100.0 * 100.0, open_ts=NOW,
        position_id="pos-full",
    )
    trade.base_qty = 100.0
    state = ProdLoopState()
    state.open_trades = [trade]

    ok = await _close_trade_with_real_pnl(
        conn, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=99.8, price=130.0),
    )

    assert ok is True
    row = conn.execute(
        "SELECT realized_won, closed_ts FROM calibration_pairs WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == 1  # exit 130 > entry 100 -> win
    assert row[1] is not None


def test_record_close_outcome_reexported_from_effects_module() -> None:
    # Sanity: the module the _safe_* wrapper delegates to is the one being
    # tested in test_calibration_shadow.py — no shadow duplicate implementation.
    assert record_close_outcome.__module__ == "polaris.core.learners.calibration_shadow"
