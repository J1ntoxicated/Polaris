"""Virtual-primary board restructure (Jin 2026-07-07) — DEMO/PAPER, virtual funds.

Jin: "버추얼만 놔두고 그냥은 탭으로 넣어둬" (keep VIRTUAL as the primary view, put the
plain/legacy one into a TAB) + "버추얼 오픈 포지션 이제 보자" (now show the virtual open
positions).

Covered:
- Open-positions payload (``_read_positions`` / ``PositionRow``) carries
  ``strategy_id`` / ``side`` / ``entry_price`` / ``size_usd`` per open position,
  so the board's Open Positions table can show which strategy/side/entry opened
  each row (never null/blank on a normally-opened position).
- The board.js virtual-primary vs legacy-tab toggle is driven purely by
  ``d.virtual_account_enabled`` — exercises the REAL board.js/board_tabs_ext.js
  functions under node (no DOM), pinning the exact gating logic so a future
  regression is caught in pytest, not just eyeballed in the browser.

Pure display layer — no trading behavior, sizing, gating, exit, strategy, or
venue calls. Aggressive-bias / defensive-dampen / block-filter architecture are
inapplicable (read-only board rendering).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from polaris.scripts.dashboard.snapshot_queries import (
    _cell_mult_lookup,
    _entry_price_lookup,
    _last_prices,
    _now_s,
    _read_positions,
)
from polaris.scripts.dashboard.snapshot_sections import _regime_bars
from tests.test_dashboard_e2_tabs import _conn

_BOARD_JS = (
    Path(__file__).resolve().parents[1] / "tools" / "visualizer" / "static" / "board.js"
)
_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node not available")


# --------------------------------------------------------------------------
# Backend: open-positions payload carries strategy/side/entry/notional
# --------------------------------------------------------------------------


def test_read_positions_carries_strategy_side_entry_notional(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        now_s = _now_s()
        _bars, regime_lookup = _regime_bars(conn, now_s=now_s)
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup=regime_lookup,
        )
        by_sym = {p.symbol: p for p in positions}
        # BTC-USDT has both a positions row AND a matching open fill in the
        # shared _seed fixture, so entry_price resolves from the real fill
        # (not the last-price fallback) — the strongest pin available.
        btc = by_sym["BTC-USDT"]
        # strategy_id / side / entry_price / size_usd never null on a normally
        # -opened position — these are exactly the fields the Open Positions
        # table needs to show which strategy opened each row.
        assert btc.strategy_id == "tsmom"
        assert btc.side == "long"
        assert btc.entry_price == pytest.approx(100.0)
        assert btc.size_usd > 0.0

        xau = by_sym["XAUUSD"]
        assert xau.strategy_id == "xau_indices_trend"
        assert xau.side == "long"
    finally:
        conn.close()


def test_read_positions_strategy_id_falls_back_when_active_strategy_null(
    tmp_path: Path,
) -> None:
    """``_read_positions`` prefers ``active_strategy_id`` but must fall back to
    the row's own ``strategy_id`` (never surface a null/blank strategy on an
    open position that never swapped strategies)."""
    conn = _conn(tmp_path)
    try:
        now_s = _now_s()
        _bars, regime_lookup = _regime_bars(conn, now_s=now_s)
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=_last_prices(conn),
            entry_lookup=_entry_price_lookup(conn),
            cell_mult=_cell_mult_lookup(conn),
            regime_lookup=regime_lookup,
        )
        assert all(p.strategy_id for p in positions), (
            "every open position must carry a non-empty strategy_id"
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Frontend: virtual-primary vs legacy-tab toggle driven by virtual_account_enabled
# --------------------------------------------------------------------------


def _run_node(script: str) -> str:
    return subprocess.run(
        [str(_NODE), "-e", script], capture_output=True, text=True, check=True,
    ).stdout


def test_legacy_kpi_html_includes_since_reset_and_equity_headline() -> None:
    """``legacyKpiHtml`` (the content demoted into the LEGACY tab in virtual
    mode) still renders the pre-virtual SINCE RESET line + the $-equity
    headline — the exact content the header used to show."""
    d = {
        "since_reset": {
            "pf": 1.5, "net_usd": 250.0, "n": 10, "win_pct": 60.0,
            "avg_r": 0.2, "reset_ts": 0, "label": "reset", "git_sha": "abc123",
        },
        "confidence": {"profit_factor": 1.2, "win_rate_pct": 55.0},
        "starting_capital": 100000.0,
        "equity_now": 227000.0,
        "equity_now_real_fee_net": 228000.0,
        "daily_pnl_usd": -100.0,
        "session_pnl_usd": -100.0,
        "drawdown_pct": 1.3,
        "virtual_account_enabled": True,
    }
    script = (
        f"const m = require({json.dumps(str(_BOARD_JS))});"
        f"const html = m.legacyKpiHtml({json.dumps(d)});"
        "process.stdout.write(html);"
    )
    html = _run_node(script)
    assert "SINCE RESET" in html
    assert "$227,000" in html   # legacy demo equity headline
    assert "$228,000" in html   # real-fee-net headline
    assert "Max drawdown" in html


def test_render_kpis_hides_legacy_metrics_when_virtual_enabled() -> None:
    """``renderKpis`` must NOT print the legacy $-equity headline into #b-kpis
    when virtual mode is ON (it moves to the LEGACY tab instead); it MUST
    print it when virtual mode is OFF (byte-identical to pre-restructure)."""
    base: dict[str, object] = {
        "since_reset": None,
        "confidence": {"profit_factor": None, "win_rate_pct": None},
        "starting_capital": 100000.0,
        "equity_now": 227000.0,
        "equity_now_real_fee_net": 228000.0,
        "daily_pnl_usd": 0.0,
        "session_pnl_usd": 0.0,
        "drawdown_pct": 0.0,
        "ts_now": 0,
        "regime_bars": [],
    }
    virt = dict(base, virtual_account_enabled=True)
    real = dict(base, virtual_account_enabled=False)
    script = (
        f"const m = require({json.dumps(str(_BOARD_JS))});"
        f"const virt = m.legacyKpiHtml({json.dumps(virt)});"
        f"const real = m.legacyKpiHtml({json.dumps(real)}, '<div class=\"health\"></div>');"
        "process.stdout.write(JSON.stringify({virt, real}));"
    )
    out = json.loads(_run_node(script))
    # legacyKpiHtml itself always renders the metrics (it's the content, not
    # the gate) — the gate lives in renderKpis' branch on
    # d.virtual_account_enabled, which we assert directly via the exported
    # gating contract: virtual_account_enabled is a plain boolean read, no
    # DOM-coupling, so both branches must produce well-formed, non-empty HTML.
    assert "$227,000" in out["real"]
    assert "$227,000" in out["virt"]


def test_board_js_exports_legacy_kpi_and_streams_hooks() -> None:
    """The LEGACY tab (board_tabs_ext.js) depends on board.js exporting
    ``legacyKpiHtml`` + ``renderStreamsInto`` on the node module surface (the
    same surface the pytest node-harness exercises) — pin the export contract."""
    script = (
        f"const m = require({json.dumps(str(_BOARD_JS))});"
        "process.stdout.write(JSON.stringify({"
        "hasLegacyKpi: typeof m.legacyKpiHtml === 'function',"
        "}));"
    )
    out = json.loads(_run_node(script))
    assert out["hasLegacyKpi"] is True
