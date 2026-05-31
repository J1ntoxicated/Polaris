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

from polaris.core.pipeline.agents.confidence import confidence_summary
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
    ConfidenceCell,
    ConfidencePanel,
    DashboardSnapshot,
    EdgeValidationRow,
    GateRow,
    GptStat,
    LearnerSlot,
    PositionRow,
    RegimeBar,
    StrategyStat,
    StreamSummary,
)
from polaris.scripts.dashboard.snapshot_queries import (
    _build_dual_equity_curve,
    _cell_mult_lookup,
    _daily_realised_pnl,
    _drawdown_and_sharpe,
    _entry_price_lookup,
    _last_prices,
    _now_s,
    _per_stream_summary,
    _read_positions,
    _strategy_stats,
)
from polaris.scripts.dashboard.snapshot_sections import (
    _ai_shadow_panel,
    _alerts,
    _cell_top_bottom,
    _edge_validation,
    _exit_surface,
    _gate_funnel,
    _gpt_stats,
    _learner_slots,
    _recent_closed_trades,
    _regime_bars,
    _regime_states,
    _rotation_telemetry,
    _universe,
)

__all__ = [
    "STARTING_CAPITAL",
    "DEFAULT_DB_PATH",
    "AlertRow",
    "CellRow",
    "ClosedTrade",
    "ConfidenceCell",
    "ConfidencePanel",
    "DashboardSnapshot",
    "EdgeValidationRow",
    "GateRow",
    "GptStat",
    "LearnerSlot",
    "PositionRow",
    "RegimeBar",
    "StreamSummary",
    "StrategyStat",
    "collect_snapshot",
]

DEFAULT_DB_PATH: Final[Path] = Path("data/polaris.sqlite")


def _starting_capital() -> float:
    """Resolved live each call so env-overrides (POLARIS_DEMO_STARTING_EQUITY_*) win."""
    return demo_starting_equity_total()


def _confidence_panel(
    conn: sqlite3.Connection, *, starting_equity: float, n_cells: int = 8
) -> ConfidencePanel:
    """Build the go-live confidence panel from ``confidence.confidence_summary``.

    Read-only display rollup — the per-(strategy×regime) cells are capped to the
    top ``n_cells`` (most-sampled first, the summary already sorts them)."""
    summary = confidence_summary(conn, starting_equity=starting_equity)
    ov = summary["overall"]
    cells = [
        ConfidenceCell(
            strategy_id=str(c["strategy_id"]),
            regime=str(c["regime"]),
            n=int(c["n"]),
            expected_real_fee_net_r=float(c["expected_real_fee_net_r"]),
            lcb_real_fee_net_r=float(c["lcb_real_fee_net_r"]),
            lcb_sign=str(c["lcb_sign"]),
        )
        for c in summary["by_strategy_regime"][:n_cells]
    ]
    return ConfidencePanel(
        n_closed=int(ov["n_closed"]),
        win_rate_pct=float(ov["win_rate_pct"]),
        profit_factor=float(ov["profit_factor"]),
        turnover_ratio=float(ov["turnover_ratio"]),
        fee_drag_real_r=float(ov["fee_drag_real_r"]),
        fee_drag_demo_r=float(ov["fee_drag_demo_r"]),
        cells=cells,
    )


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
        # Component A dual curve: demo-actual (== legacy _build_equity_curve)
        # + real-fee-net (fees recomputed at the real OKX schedule). One fills
        # walk produces both; ``equity`` is the demo-actual leg (unchanged).
        dual = _build_dual_equity_curve(
            conn, now_s=now_s, starting_capital=starting_capital,
        )
        bucket_ts = dual.bucket_ts
        equity = dual.equity_demo
        equity_real = dual.equity_real
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
        # Real-fee-net "now" = real-fee realised curve + the SAME uPnL_total.
        # uPnL is gross of fees on BOTH curves (the close fee is unrealised until
        # exit), so the only divergence is the realised fee schedule — the
        # real-fee-net headline equals demo equity + (demo_fee − real_fee) drag.
        equity_now_real = (equity_real[-1] if equity_real else starting_capital)
        equity_with_upnl_real = equity_now_real + upnl_total
        equity_full_real = (
            equity_real + [equity_with_upnl_real]
            if equity_real
            else [equity_with_upnl_real]
        )
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
        # E2 REGIME tab — per-(venue, group) live regime + confidence + evidence.
        # A (venue, symbol)→regime map labels the TRADES tab rows (best-effort:
        # the group_id is the symbol's underlying group, so symbol==group for
        # single-symbol groups like BTC/XAU; falls back to "" when unmatched).
        regime_states = _regime_states(conn)
        regime_by_venue_symbol = {
            (rs.venue, rs.group_id): rs.regime for rs in regime_states
        }
        # TRADES tab shows more rows than the legacy 10 (full-width tab).
        recent_trades = _recent_closed_trades(
            conn, n=40, regime_lookup=regime_by_venue_symbol,
        )
        # E2 EXIT tab — FSM distribution + reason histogram (reuses recent_trades)
        # + G6/G7 gate decision counts.
        exit_surface = _exit_surface(
            conn, now_s=now_s, recent_trades=recent_trades,
        )
        # E2 AI tab — conductor shadow agreement + entry-admission would-suppress.
        ai_shadow = _ai_shadow_panel(conn, now_s=now_s)
        learners = _learner_slots(conn, now_s=now_s)
        edge_validation = _edge_validation(conn, n=8)
        gpt_stats = _gpt_stats(conn, now_s=now_s)
        alerts = _alerts(conn, n=3)
        focus_n, focus_ts = _universe(conn)
        # Stage-2 per-stream rollup. ``positions`` is passed so the per-stream
        # open_n / upnl / exposed decompose the global totals exactly.
        streams = _per_stream_summary(conn, now_s=now_s, positions=positions)
        # Rotation + session-forced-exit telemetry (follow-up #12) — display-only,
        # graceful zero when the telemetry tables are empty/absent.
        rotation = _rotation_telemetry(conn, now_s=now_s)
        # Component A go-live confidence panel (real-fee-net edge evidence).
        confidence = _confidence_panel(conn, starting_equity=starting_capital)
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
            equity_curve_real_fee_net=equity_full_real,
            equity_now_real_fee_net=equity_with_upnl_real,
            real_fee_total=dual.real_fee_total,
            demo_fee_total=dual.demo_fee_total,
            positions=positions,
            strategy_stats=strategy_stats,
            gate_funnel=gate_funnel,
            cell_top=cell_top,
            cell_bottom=cell_bot,
            regime_bars=regime_bars,
            recent_trades=recent_trades,
            learners=learners,
            edge_validation=edge_validation,
            gpt_stats=gpt_stats,
            alerts=alerts,
            streams=streams,
            rotation_count=rotation.rotation_count,
            session_forced_exit_count=rotation.session_forced_exit_count,
            last_rotation=rotation.last_rotation,
            confidence=confidence,
            regime_states=regime_states,
            exit_surface=exit_surface,
            ai_shadow=ai_shadow,
        )
    finally:
        conn.close()
