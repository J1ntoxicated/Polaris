"""Polaris dashboard v1 — snapshot collection (DB → DashboardSnapshot).

Pure read-only query layer. Renderer (`render.py`) consumes this dataclass and
produces the 220×55 ANSI grid. All queries best-effort: missing tables / empty
data return zero-defaults so the dashboard never crashes a paper loop.

Sources (data/polaris.sqlite):
- ``fills`` (ADR-003) — realised PnL, fill stream, USD notional
- ``positions`` — open positions
- ``cell_matrix_p0`` — Layer 4 routing scores
- ``learner_state`` — Layer 5 P0 mults (key column is ``key``, NOT ``key_dims``)
- ``gate_events`` — Layer 2 funnel (last 1h)
- ``regime_state`` — per-group regime
- ``bars`` — last close per instrument
- ``risk_events`` / ``strategy_fault_events`` — alert log
- ``watchlist_focus`` / ``universe`` — focus count

Starting capital: pulled from ``polaris.core.sizing.constants`` (Day 9 F12
SSOT fix) — total demo equity ≈ USD $130K (OKX SPOT $79K + Capital CFD $51K).
Per-venue values are exposed on the snapshot for the renderer's split label.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

from polaris.core.sizing.constants import (
    demo_starting_equity_capital,
    demo_starting_equity_okx,
    demo_starting_equity_total,
)
from polaris.scripts.dashboard.snapshot_models import (
    STARTING_CAPITAL,
    AlertRow,
    CellRow,
    ClosedTrade,
    DashboardSnapshot,
    GateRow,
    GptStat,
    LearnerSlot,
    PositionRow,
    RegimeBar,
    StrategyStat,
)
from polaris.scripts.dashboard.snapshot_queries import (
    _build_equity_curve,
    _cell_mult_lookup,
    _daily_realised_pnl,
    _drawdown_and_sharpe,
    _entry_price_lookup,
    _last_prices,
    _now_s,
    _read_positions,
    _strategy_stats,
)
from polaris.scripts.dashboard.snapshot_sections import (
    _alerts,
    _cell_top_bottom,
    _gate_funnel,
    _gpt_stats,
    _learner_slots,
    _recent_closed_trades,
    _regime_bars,
    _universe,
)

__all__ = [
    "STARTING_CAPITAL",
    "DEFAULT_DB_PATH",
    "AlertRow",
    "CellRow",
    "ClosedTrade",
    "DashboardSnapshot",
    "GateRow",
    "GptStat",
    "LearnerSlot",
    "PositionRow",
    "RegimeBar",
    "StrategyStat",
    "collect_snapshot",
]

DEFAULT_DB_PATH: Final[Path] = Path("data/polaris.sqlite")


def _starting_capital() -> float:
    """Resolved live each call so env-overrides (POLARIS_DEMO_STARTING_EQUITY_*) win."""
    return demo_starting_equity_total()


# ---------------------------------------------------------------------------
# Top-level collector
# ---------------------------------------------------------------------------


def collect_snapshot(db_path: Path = DEFAULT_DB_PATH) -> DashboardSnapshot:
    """Single-pass DB read → DashboardSnapshot. Returns zero-snapshot on missing DB."""
    starting_capital = _starting_capital()
    starting_okx = demo_starting_equity_okx()
    starting_capital_cap = demo_starting_equity_capital()
    if not db_path.exists():
        return DashboardSnapshot(
            ts_now=_now_s(),
            starting_capital=starting_capital,
            starting_capital_okx=starting_okx,
            starting_capital_capital=starting_capital_cap,
            equity_now=starting_capital,
            peak_equity=starting_capital,
        )

    now_s = _now_s()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = None
    try:
        bucket_ts, equity, _total_realised = _build_equity_curve(
            conn, now_s=now_s, starting_capital=starting_capital,
        )
        equity_now = equity[-1] if equity else starting_capital
        dd_pct, peak, sharpe = _drawdown_and_sharpe(
            equity, starting_capital=starting_capital,
        )
        daily_pnl, daily_n = _daily_realised_pnl(conn, now_s=now_s)
        last_prices = _last_prices(conn)
        entry_lookup = _entry_price_lookup(conn)
        cell_mult = _cell_mult_lookup(conn)
        regime_bars, regime_lookup = _regime_bars(conn)
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=last_prices,
            entry_lookup=entry_lookup,
            cell_mult=cell_mult,
            regime_lookup=regime_lookup,
        )
        upnl_total = sum(p.upnl_usd for p in positions)
        notional_open = sum(p.size_usd for p in positions)
        # P0 fix (codex): expose the truthful "deployed notional" instead of a
        # mislabelled CASH proxy. Real free-cash / margin-headroom requires a
        # venue balance probe that lives outside the snapshot path.
        exposed_usd = notional_open
        equity_with_upnl = equity_now + upnl_total
        # Re-compute DD/Peak with uPnL-included current point
        equity_full = (
            equity + [equity_with_upnl] if equity else [equity_with_upnl]
        )
        dd_pct, peak, sharpe = _drawdown_and_sharpe(
            equity_full, starting_capital=starting_capital,
        )
        strategy_stats = _strategy_stats(conn, now_s=now_s, positions=positions)
        gate_funnel = _gate_funnel(conn, now_s=now_s)
        cell_top, cell_bot, eligible_n = _cell_top_bottom(conn, cell_mult=cell_mult, n=5)
        recent_trades = _recent_closed_trades(conn, n=10)
        learners = _learner_slots(conn, now_s=now_s)
        gpt_stats = _gpt_stats(conn, now_s=now_s)
        alerts = _alerts(conn, n=3)
        focus_n, focus_ts = _universe(conn)
        return DashboardSnapshot(
            ts_now=now_s,
            starting_capital=starting_capital,
            starting_capital_okx=starting_okx,
            starting_capital_capital=starting_capital_cap,
            equity_now=equity_with_upnl,
            exposed_usd=exposed_usd,
            upnl_total=upnl_total,
            daily_pnl_usd=daily_pnl,
            daily_trades=daily_n,
            drawdown_pct=dd_pct,
            peak_equity=peak,
            sharpe_24h=sharpe,
            open_positions_n=len(positions),
            active_cells_n=eligible_n,
            universe_focus_n=focus_n,
            universe_last_refresh=focus_ts,
            equity_curve=equity_full,
            equity_curve_ts=bucket_ts + [now_s],
            positions=positions,
            strategy_stats=strategy_stats,
            gate_funnel=gate_funnel,
            cell_top=cell_top,
            cell_bottom=cell_bot,
            regime_bars=regime_bars,
            recent_trades=recent_trades,
            learners=learners,
            gpt_stats=gpt_stats,
            alerts=alerts,
        )
    finally:
        conn.close()
