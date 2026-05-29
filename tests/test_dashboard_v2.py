"""Tests for dashboard_v2 dynamic visualization.

Covers the Task-D additions: 24h equity sparkline row, inline bar gauges in the
core-metrics / system-status sections, and per-row heat tiles in the cell
ranking — all while keeping the fixed HEIGHT×WIDTH frame invariant.
"""

from __future__ import annotations

import re

from polaris.scripts.dashboard.ansi_palette import BLOCK
from polaris.scripts.dashboard.snapshot_models import (
    CellRow,
    DashboardSnapshot,
    GptStat,
    RegimeBar,
    StrategyStat,
)
from polaris.scripts.dashboard_v2 import (
    HEIGHT,
    WIDTH,
    render_core_metrics,
    render_dashboard_v2,
    render_equity_spark,
    render_system_status,
    render_top_bottom,
)


def _vlen(s: str) -> int:
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def _full_snap() -> DashboardSnapshot:
    return DashboardSnapshot(
        ts_now=1_780_000_000,
        equity_now=66_000.0,
        starting_capital=65_000.0,
        exposed_usd=12_000.0,
        upnl_total=120.0,
        daily_pnl_usd=250.0,
        daily_trades=42,
        drawdown_pct=3.5,
        peak_equity=67_000.0,
        sharpe_24h=1.4,
        open_positions_n=8,
        active_cells_n=5,
        universe_focus_n=30,
        equity_curve=[65_000.0 + i * 20.0 for i in range(60)],
        cell_top=[CellRow("okx", "tsmom", "SOL-USDT", "bull_trend", 63.0, 0.42, 1.5)],
        cell_bottom=[CellRow("okx", "rsi_bb", "LTC-USDT", "chop", 40.0, -0.30, 0.5)],
        strategy_stats=[
            StrategyStat("tsmom", 3, 20, 60.0, 0.4, 1.5, 250.0, 12_000.0),
        ],
        gpt_stats=[GptStat("gpt-5-mini", 120, 0.05, 1.20)],
        regime_bars=[RegimeBar("bull_trend", 30), RegimeBar("chop", 10)],
    )


def test_height_exact_full() -> None:
    rows = render_dashboard_v2(_full_snap())
    assert len(rows) == HEIGHT
    for i, r in enumerate(rows):
        assert _vlen(r) == WIDTH, f"row {i} vlen={_vlen(r)}"


def test_height_exact_empty() -> None:
    rows = render_dashboard_v2(DashboardSnapshot())
    assert len(rows) == HEIGHT
    for i, r in enumerate(rows):
        assert _vlen(r) == WIDTH, f"row {i} vlen={_vlen(r)}"


def test_equity_spark_has_ticks_when_curve_present() -> None:
    row = render_equity_spark(_full_snap())
    assert _vlen(row) == WIDTH
    assert any(c in row for c in "▁▂▃▄▅▆▇█")


def test_equity_spark_graceful_when_empty() -> None:
    row = render_equity_spark(DashboardSnapshot())
    assert _vlen(row) == WIDTH  # padded, no crash


def test_core_metrics_renders_gauge_bar() -> None:
    joined = "".join(render_core_metrics(_full_snap()))
    assert BLOCK in joined, "no gauge bar in core metrics"


def test_system_status_renders_gauge_bar() -> None:
    joined = "".join(render_system_status(_full_snap()))
    assert BLOCK in joined, "no gauge bar in system status"


def test_top_bottom_renders_heat_tile() -> None:
    joined = "".join(render_top_bottom(_full_snap()))
    assert BLOCK in joined, "no heat tile in cell ranking"
