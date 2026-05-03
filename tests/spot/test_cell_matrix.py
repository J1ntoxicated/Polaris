"""Tier-based cell matrix — ADR-007 Phase α."""
import sqlite3

import pytest

from invasion.spot import store_spot
from invasion.spot.learning import cell_matrix


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "spot.sqlite"
    monkeypatch.setattr(store_spot, "_DB_PATH", str(db))
    store_spot.bootstrap()
    yield


def test_resolve_creates_cell_row():
    row = cell_matrix.resolve(
        tier="major", asset_group="crypto", session="asia",
        regime="neutral", strategy_id="bb_break_momentum",
    )
    assert row["tier"] == "major"
    assert row["exit_optim_n_samples"] == 0


def test_resolve_idempotent():
    cell_matrix.resolve(tier="large", asset_group="crypto", session="asia",
                          regime="neutral", strategy_id="macd_cross")
    cell_matrix.resolve(tier="large", asset_group="crypto", session="asia",
                          regime="neutral", strategy_id="macd_cross")
    with store_spot.get_conn() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM cell_matrix_spot "
            "WHERE tier='large' AND strategy_id='macd_cross'").fetchone()[0]
    assert n == 1


def test_get_thresholds_default_atr_mults():
    cell_matrix.resolve(tier="mid", asset_group="crypto", session="us",
                          regime="neutral", strategy_id="vol_compression")
    th = cell_matrix.get_thresholds(
        tier="mid", asset_group="crypto", session="us",
        regime="neutral", strategy_id="vol_compression")
    assert th["optimal_atr_tp_mult"] == 4.0
    assert th["optimal_atr_sl_mult"] == 1.5


def test_upsert_learning_accumulates_winner():
    cell_matrix.upsert_learning(
        tier="major", asset_group="crypto", session="asia",
        regime="neutral", strategy_id="bb_break_momentum",
        direction="long",
        trade={"pnl_pct": 0.020, "net_pnl_usd": 4.0},
    )
    cell_matrix.upsert_learning(
        tier="major", asset_group="crypto", session="asia",
        regime="neutral", strategy_id="bb_break_momentum",
        direction="long",
        trade={"pnl_pct": -0.005, "net_pnl_usd": -1.0},
    )
    th = cell_matrix.get_thresholds(
        tier="major", asset_group="crypto", session="asia",
        regime="neutral", strategy_id="bb_break_momentum")
    assert th["exit_optim_n_samples"] == 2
    assert th["win_count"] == 1
    assert th["loss_count"] == 1
    assert th["total_pnl_usd"] == 3.0


def test_cell_key_str_round_trip():
    key = cell_matrix.cell_key_str(
        tier="major", asset_group="crypto", session="asia",
        regime="neutral", strategy_id="bb_break_momentum")
    parts = cell_matrix.parse_cell_key(key)
    assert parts == {
        "tier": "major", "asset_group": "crypto", "session": "asia",
        "regime": "neutral", "strategy_id": "bb_break_momentum",
        "direction": "long",
    }
