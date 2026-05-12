"""Dashboard v0 — snapshot collection + ANSI render smoke."""

from __future__ import annotations

from pathlib import Path

from polaris.scripts.dashboard_v0 import (
    DashboardSnapshot,
    collect_snapshot,
    render,
)
from polaris.storage.schema import init_db


def test_collect_empty_db_returns_zero_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    conn.close()
    snap = collect_snapshot(db_path)
    assert snap.universe_focus_count == 0
    assert snap.positions == []
    assert snap.cell_dist == {"top": 0, "mid": 0, "bottom": 0}
    assert snap.daily_pnl_usd == 0.0


def test_collect_missing_db_returns_zero_snapshot(tmp_path: Path) -> None:
    snap = collect_snapshot(tmp_path / "nope.sqlite")
    assert snap.universe_focus_count == 0
    assert snap.recent_fills == []


def test_render_handles_empty_snapshot() -> None:
    snap = DashboardSnapshot(
        universe_focus_count=0,
        universe_last_refresh="n/a",
        positions=[],
        cell_dist={"top": 0, "mid": 0, "bottom": 0},
        learner_state=[],
        recent_fills=[],
        daily_pnl_usd=0.0,
    )
    out = render(snap, width=120)
    assert "POLARIS DASHBOARD v0" in out
    assert "Universe (Layer 0)" in out
    assert "Active positions" in out
    assert "Cell routing distribution" in out
    assert "Recent fills" in out


def test_render_pnl_color_negative() -> None:
    snap = DashboardSnapshot(
        universe_focus_count=10,
        universe_last_refresh="12:34:56",
        positions=[],
        cell_dist={"top": 1, "mid": 2, "bottom": 1},
        learner_state=[],
        recent_fills=[],
        daily_pnl_usd=-1.5,
    )
    out = render(snap, width=80)
    # Negative PnL renders inside the box.
    assert "-1.5000" in out


def test_render_includes_active_position() -> None:
    snap = DashboardSnapshot(
        universe_focus_count=4,
        universe_last_refresh="01:00:00",
        positions=[
            {
                "venue": "okx",
                "symbol": "BTC-USDT",
                "strategy_id": "volume_burst",
                "side": "long",
                "qty": 0.001,
                "status": "active",
                "opened_ts": 0,
            }
        ],
        cell_dist={"top": 0, "mid": 0, "bottom": 0},
        learner_state=[],
        recent_fills=[],
        daily_pnl_usd=0.0,
    )
    out = render(snap, width=140)
    assert "BTC-USDT" in out
    assert "volume_burst" in out
