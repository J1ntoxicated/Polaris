"""Layer 3 — cell-mult application tests (sole production caller of L4 SSOT).

Spec source:
- vault/30_components/layer-3-sizing-risk.md
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.sizing import (
    SizingInput,
    apply_cell_routing_mult,
    resolve_cell_routing_mult,
)

NOW = 1_780_000_000


def _ctx() -> CellContext:
    return CellContext(group="spot_intraday", session="asia", direction="long", liquidity_tier="high")


def _populate_pool(conn: sqlite3.Connection) -> None:
    """Build a 25-cell pool with a clear top + bottom quartile."""
    for i in range(25):
        for j in range(25):
            tr = TradeClose(
                key=CellKeyP0("okx", "volume_burst", f"PL{i:02d}-USDT", "bull_trend"),
                context=_ctx(),
                pnl_r=(i - 12) * 0.1,
                won=(i - 12) * 0.1 > 0,
                closed_ts=NOW + j,
            )
            update_on_trade_close(conn, tr)


def test_apply_cell_routing_mult_top_quartile_amplifies(memdb: sqlite3.Connection) -> None:
    _populate_pool(memdb)
    sizing = SizingInput(
        pre_cell_notional=1000.0,
        cell_key=CellKeyP0("okx", "volume_burst", "PL24-USDT", "bull_trend"),
        now_ts=NOW + 100,
    )
    out = apply_cell_routing_mult(memdb, sizing)
    assert out["cell_routing_mult"] == 1.5
    assert out["post_cell_notional"] == pytest.approx(1500.0)


def test_apply_cell_routing_mult_bottom_quartile_suppresses(memdb: sqlite3.Connection) -> None:
    _populate_pool(memdb)
    sizing = SizingInput(
        pre_cell_notional=1000.0,
        cell_key=CellKeyP0("okx", "volume_burst", "PL00-USDT", "bull_trend"),
        now_ts=NOW + 100,
    )
    out = apply_cell_routing_mult(memdb, sizing)
    assert out["cell_routing_mult"] == 0.5
    assert out["post_cell_notional"] == pytest.approx(500.0)


def test_apply_cell_routing_mult_unknown_cell_neutral(memdb: sqlite3.Connection) -> None:
    """A cell with no history routes ×1.0 (cold)."""
    sizing = SizingInput(
        pre_cell_notional=1000.0,
        cell_key=CellKeyP0("okx", "volume_burst", "GHOST-USDT", "bull_trend"),
        now_ts=NOW,
    )
    out = apply_cell_routing_mult(memdb, sizing)
    assert out["cell_routing_mult"] == 1.0
    assert out["post_cell_notional"] == pytest.approx(1000.0)


def test_apply_cell_routing_mult_rejects_nan() -> None:
    import sqlite3 as _s

    conn = _s.connect(":memory:")
    sizing = SizingInput(
        pre_cell_notional=float("nan"),
        cell_key=CellKeyP0("okx", "vb", "BTC-USDT", "bull_trend"),
        now_ts=NOW,
    )
    with pytest.raises(ValueError):
        apply_cell_routing_mult(conn, sizing)


def test_apply_cell_routing_mult_rejects_negative() -> None:
    import sqlite3 as _s

    conn = _s.connect(":memory:")
    sizing = SizingInput(
        pre_cell_notional=-1.0,
        cell_key=CellKeyP0("okx", "vb", "BTC-USDT", "bull_trend"),
        now_ts=NOW,
    )
    with pytest.raises(ValueError):
        apply_cell_routing_mult(conn, sizing)


def test_resolve_cell_routing_mult_passthrough(memdb: sqlite3.Connection) -> None:
    """Production resolver must defer to the L4 SSOT (no shadow logic at P0)."""
    _populate_pool(memdb)
    out = resolve_cell_routing_mult(
        memdb, CellKeyP0("okx", "volume_burst", "PL24-USDT", "bull_trend"), now_ts=NOW + 100
    )
    assert out == 1.5
