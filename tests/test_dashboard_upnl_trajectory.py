"""uPnL trajectory sparkline (Jin 2026-06-27) — "실시간 피 어케 되는지".

The POSITIONS tab draws a compact inline sparkline of HOW each open position's
unrealised PnL has moved since entry (not just its current value). The path is
derived purely from fields already on the snapshot row — the symbol's recent
price history (``spark[]``, oldest→newest) + ``entry_price`` + ``qty`` + ``side``:

    uPnL_t = (price_t − entry_price) × qty × (+1 long / −1 short)

with the path's own peak (MFE) / trough (MAE) marked. DISPLAY-ONLY — derived
client-side, never feeds sizing/gating/exit. The math lives in
``tools/visualizer/static/board.js`` (``upnlTrajectory``); these tests pin the
formula + MFE/MAE indexing by exercising the REAL JS function via node, so a
regression in the dashboard math is caught in ``pytest``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_BOARD_JS = (
    Path(__file__).resolve().parents[1]
    / "tools" / "visualizer" / "static" / "board.js"
)

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node not available")


def _traj(
    spark: list[float], entry: float, qty: float, side: str,
) -> dict[str, Any] | None:
    """Run the real board.js ``upnlTrajectory`` under node and return its result."""
    script = (
        f"const m = require({json.dumps(str(_BOARD_JS))});"
        f"const r = m.upnlTrajectory("
        f"{json.dumps(spark)}, {json.dumps(entry)}, {json.dumps(qty)}, "
        f"{json.dumps(side)});"
        "process.stdout.write(JSON.stringify(r));"
    )
    out = subprocess.run(
        [str(_NODE), "-e", script],
        capture_output=True, text=True, check=True,
    ).stdout
    result: dict[str, Any] | None = json.loads(out)
    return result


def test_long_trajectory_formula() -> None:
    # Long, entry 100, qty 2. uPnL_t = (price − 100) × 2 × (+1).
    r = _traj([100.0, 105.0, 95.0, 110.0], 100.0, 2.0, "long")
    assert r is not None
    assert r["series"] == [0.0, 10.0, -10.0, 20.0]
    assert r["mfeIdx"] == 3          # peak = +20 at the last point
    assert r["maeIdx"] == 2          # trough = −10 at index 2
    assert r["last"] == 20.0


def test_short_trajectory_sign_inverts() -> None:
    # Short, entry 100, qty 2. uPnL_t = (price − 100) × 2 × (−1) — a price RISE
    # is a LOSS for a short, so the path sign inverts vs the long case.
    r = _traj([100.0, 105.0, 95.0, 110.0], 100.0, 2.0, "short")
    assert r is not None
    assert r["series"] == [0.0, -10.0, 10.0, -20.0]
    assert r["mfeIdx"] == 2          # peak = +10 (price dropped) at index 2
    assert r["maeIdx"] == 3          # trough = −20 at the last point
    assert r["last"] == -20.0


def test_mfe_mae_pick_first_extreme_index() -> None:
    # Flat-then-equal extremes: the FIRST occurrence of the max/min wins (strict
    # >/< comparison), so the markers are deterministic.
    r = _traj([10.0, 12.0, 12.0, 8.0, 8.0], 10.0, 1.0, "long")
    assert r is not None
    # series = [0, 2, 2, -2, -2]; first max idx = 1, first min idx = 3.
    assert r["series"] == [0.0, 2.0, 2.0, -2.0, -2.0]
    assert r["mfeIdx"] == 1
    assert r["maeIdx"] == 3


def test_degenerate_inputs_return_none() -> None:
    # < 2 points, zero qty, or zero/garbage entry → no derivable path (the cell
    # stays blank rather than drawing a misleading line).
    assert _traj([100.0], 100.0, 1.0, "long") is None
    assert _traj([100.0, 101.0], 100.0, 0.0, "long") is None
    assert _traj([100.0, 101.0], 0.0, 1.0, "long") is None
    assert _traj([], 100.0, 1.0, "long") is None


def test_svg_renders_for_valid_series() -> None:
    # The shared renderer produces a <svg class="traj"> with a breakeven baseline
    # (dashed <line>), the path <polyline>, and peak/trough <circle> markers.
    script = (
        f"const m = require({json.dumps(str(_BOARD_JS))});"
        "const svg = m._upnlSvgFromSeries([0, 10, -5, 8]);"
        "process.stdout.write(svg);"
    )
    svg = subprocess.run(
        [str(_NODE), "-e", script], capture_output=True, text=True, check=True,
    ).stdout
    assert 'class="traj"' in svg
    assert "<polyline" in svg
    assert svg.count("<circle") == 2          # peak + trough markers
    assert "stroke-dasharray" in svg          # breakeven baseline
    # last point (+8) is a profit → green stroke on the path.
    assert "var(--p-grn)" in svg
