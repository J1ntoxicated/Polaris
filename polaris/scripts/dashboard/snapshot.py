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

import re
import sqlite3
from pathlib import Path
from typing import Any, Final

from polaris.core.pipeline.agents.confidence import confidence_summary
from polaris.core.probes.gate_kill_value import compute_kill_value_hints
from polaris.core.sizing.constants import (
    demo_starting_equity_alpaca_display,
    demo_starting_equity_capital,
    demo_starting_equity_okx,
    demo_starting_equity_total,
)
from polaris.scripts._virtual_account import virtual_account_enabled
from polaris.scripts.dashboard.snapshot_models import (
    STARTING_CAPITAL,
    AlertRow,
    BenchmarkTier,
    CellRow,
    ClosedTrade,
    ConfidenceCell,
    ConfidencePanel,
    DashboardSnapshot,
    EdgeValidationRow,
    GateDecisionRow,
    GateEvent,
    GateKillValuePanel,
    GateKillValueRow,
    GptStat,
    LearnerSlot,
    PositionRow,
    RegimeBar,
    ReplayBenchmarkPanel,
    StrategyStat,
    StreamSummary,
)
from polaris.scripts.dashboard.snapshot_queries import (
    _attach_sparks,
    _build_dual_equity_curve,
    _cell_mult_lookup,
    _daily_realised_pnl,
    _drawdown_and_sharpe,
    _entry_price_lookup,
    _last_prices,
    _now_s,
    _per_stream_summary,
    _read_positions,
    _recent_gate_events,
    _session_realised_pnl,
    _since_reset_rollup,
    _strategy_descriptions,
    _strategy_since_reset,
    _strategy_stats,
    _ticker_stats,
    virtual_daily_pnl_usd,
    virtual_session_pnl_usd,
    virtual_since_reset,
)
from polaris.scripts.dashboard.snapshot_sections import (
    _ai_shadow_panel,
    _alerts,
    _cell_top_bottom,
    _collect_context_intel,
    _edge_validation,
    _exit_surface,
    _gate_decisions,
    _gpt_stats,
    _learner_slots,
    _recent_closed_trades,
    _regime_bars,
    _regime_states,
    _rotation_telemetry,
    _universe,
)
from polaris.storage.schema_marketdata import marketdata_db_path_for

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
    "GateDecisionRow",
    "GateEvent",
    "GptStat",
    "LearnerSlot",
    "PositionRow",
    "RegimeBar",
    "StreamSummary",
    "StrategyStat",
    "collect_snapshot",
    "read_probe_events",
]

DEFAULT_DB_PATH: Final[Path] = Path("data/polaris.sqlite")


def read_probe_events(
    probe_db_path: Path, *, limit: int = 25
) -> list[dict[str, Any]]:
    """ADR-012 — fail-open read of the most-recent probe readings + decisions.

    Reads the SEPARATE ``data/probes.sqlite`` tuning-log sidecar (NOT the live
    DB) strictly read-only and returns a small list of generic ``probe_events``
    rows for the dashboard (which is separately wired to render them). Empty list
    on ANY error (missing / locked / table-less sidecar) — it must never break
    the snapshot. ``ticker`` / ``venue`` are best-effort parsed from the
    ``position_id`` (``venue:SYMBOL#n``); they fall back to the raw id.
    """
    p = Path(probe_db_path)
    if not p.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        events.extend(_probe_reading_events(conn, limit=limit))
        events.extend(_probe_decision_events(conn, limit=limit))
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    events.sort(key=lambda e: int(e.get("ts", 0)), reverse=True)
    return events[:limit]


_POS_ID_RE: Final = re.compile(
    r"_(okx|capital|alpaca)_(.+)_\d+$", re.IGNORECASE
)


def _venue_ticker(position_id: str) -> tuple[str, str]:
    """Best-effort (venue, ticker) from a position id — raw id on no match.

    Live position ids are ``pos_<hash>_<venue>_<SYMBOL>_<ts>`` (e.g.
    ``pos_3397a5959a0044a0_capital_US500_1782200852`` →  ``capital`` / ``US500``;
    ``pos_tick_flow_pressu_okx_LTC-USDT_1782201222`` → ``okx`` / ``LTC-USDT``).
    The symbol may itself contain ``_`` (``OIL_CRUDE``), so anchor on the known
    venue token and the trailing numeric timestamp. Also tolerates the legacy
    ``venue:SYMBOL#n`` form. Falls back to the raw id when nothing matches."""
    pid = str(position_id)
    m = _POS_ID_RE.search(pid)
    if m:
        return m.group(1).lower(), m.group(2)
    if ":" in pid:
        venue, rest = pid.split(":", 1)
        return venue, rest.split("#", 1)[0]
    return "", pid


def _probe_reading_events(
    conn: sqlite3.Connection, *, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ts, gate_id, position_id, probe_id, kind, lean, confidence "
        "FROM probe_readings ORDER BY ts DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for ts, gate_id, position_id, probe_id, kind, lean, conf in rows:
        venue, ticker = _venue_ticker(position_id)
        out.append(
            {
                "name": str(probe_id),
                "ticker": ticker,
                "venue": venue,
                "kind": str(kind),
                "lean": float(lean),
                "confidence": float(conf),
                "reading": f"lean={float(lean):+.2f} conf={float(conf):.2f}",
                "action": None,
                "ts": int(ts),
                "gate_id": int(gate_id),
            }
        )
    return out


def _probe_decision_events(
    conn: sqlite3.Connection, *, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ts, position_id, mode, composite_lean, action "
        "FROM probe_decisions ORDER BY ts DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for ts, position_id, mode, composite_lean, action in rows:
        venue, ticker = _venue_ticker(position_id)
        out.append(
            {
                "name": "engine",
                "ticker": ticker,
                "venue": venue,
                "kind": str(mode),
                "lean": float(composite_lean),
                "confidence": 0.0,
                "reading": f"composite={float(composite_lean):+.2f}",
                "action": str(action),
                "ts": int(ts),
                "gate_id": 6,
            }
        )
    return out


def _starting_capital() -> float:
    """Resolved live each call so env-overrides (POLARIS_DEMO_STARTING_EQUITY_*) win."""
    return demo_starting_equity_total()


def _mode_banner(virtual_on: bool) -> str:
    """One-line plain-English mode label (Jin 2026-07-07).

    Distinguishes the fresh $100k-per-exchange VIRTUAL measurement from the
    legacy real-venue equity reconciliation (the old $157k-style number) so
    the two are never confused on the board. Display-only.
    """
    if virtual_on:
        return "VIRTUAL PAPER ACCOUNT — venue reconciliation OFF — seed $100k x 3"
    return "DEMO/PAPER — real-venue reconciliation ON"


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
        replay=_replay_panel(summary.get("replay", {"present": False})),
    )


def _replay_panel(blk: dict[str, Any]) -> ReplayBenchmarkPanel:
    """Map the confidence ``replay`` block -> the dashboard ReplayBenchmarkPanel.

    Display-only — ``present=False`` when no offline run has been persisted."""
    if not blk.get("present"):
        return ReplayBenchmarkPanel()
    tiers = [
        BenchmarkTier(
            tier=str(t.get("tier", "")),
            baseline=str(t.get("baseline", "")),
            sharpe_spread=float(t.get("sharpe_spread", 0.0)),
            passed=bool(t.get("passed", False)),
            note=str(t.get("note", "")),
        )
        for t in blk.get("tiers", [])
    ]
    spreads = {str(k): float(v) for k, v in dict(blk.get("sharpe_spreads", {})).items()}
    return ReplayBenchmarkPanel(
        present=True,
        run_id=str(blk.get("run_id", "")),
        n=int(blk.get("n", 0)),
        net_pnl_real_fee=float(blk.get("net_pnl_real_fee", 0.0)),
        sharpe=float(blk.get("sharpe", 0.0)),
        max_dd=float(blk.get("max_dd", 0.0)),
        win_rate=float(blk.get("win_rate", 0.0)),
        profit_factor=float(blk.get("profit_factor", 0.0)),
        turnover=float(blk.get("turnover", 0.0)),
        fee_drag_real_r=float(blk.get("fee_drag_real_r", 0.0)),
        psr=float(blk.get("psr", 0.0)),
        deflated_sharpe=float(blk.get("deflated_sharpe", 0.0)),
        net_pnl_r_lcb=float(blk.get("net_pnl_r_lcb", 0.0)),
        net_pnl_r_ucb=float(blk.get("net_pnl_r_ucb", 0.0)),
        is_oos_spread=float(blk.get("is_oos_spread", 0.0)),
        verdict=str(blk.get("verdict", "")),
        sharpe_spreads=spreads,
        tiers=tiers,
    )


def _gate_kill_value_panel(
    conn: sqlite3.Connection, *, md_conn: sqlite3.Connection | None = None,
) -> GateKillValuePanel:
    """Build the EDGE-tab gate-kill-value panel (07-08 BUILD, /debate evidence).

    Read-only rollup of ``gate_kill_value.compute_kill_value_hints`` — see that
    module's docstring for the full mandate. ``present=False`` (graceful zero)
    when no ``(gate_id, cohort)`` group clears the stratified sample floor.

    storage-split (round 3 MED fix): ``gate_kill_counterfactuals`` is
    marketdata-domain — written/resolved on the marketdata conn
    (``record_pipeline_cohort`` / ``sweep_forward_marks``) post-split.
    ``md_conn=None`` falls back to ``conn``, byte-identical.
    """
    hints = compute_kill_value_hints(md_conn if md_conn is not None else conn)
    if not hints:
        return GateKillValuePanel()
    rows = [
        GateKillValueRow(
            gate_id=h.gate_id, cohort=h.cohort,
            n_killed=h.n_killed, n_passed=h.n_passed,
            mean_killed_fwd_r=h.mean_killed_fwd_r,
            mean_passed_fwd_r=h.mean_passed_fwd_r,
            separation=h.separation, anti_edge=h.anti_edge,
        )
        for h in hints
    ]
    return GateKillValuePanel(present=True, rows=rows)


# ---------------------------------------------------------------------------
# Top-level collector
# ---------------------------------------------------------------------------


def collect_snapshot(db_path: Path = DEFAULT_DB_PATH) -> DashboardSnapshot:
    """Single-pass DB read → DashboardSnapshot. Returns zero-snapshot on missing DB."""
    starting_capital = _starting_capital()
    starting_okx = demo_starting_equity_okx()
    starting_capital_cap = demo_starting_equity_capital()
    starting_capital_alp = demo_starting_equity_alpaca_display()
    virtual_on = virtual_account_enabled()
    mode_banner = _mode_banner(virtual_on)
    if not db_path.exists():
        return DashboardSnapshot(
            ts_now=_now_s(),
            starting_capital=starting_capital,
            starting_capital_okx=starting_okx,
            starting_capital_capital=starting_capital_cap,
            starting_capital_alpaca=starting_capital_alp,
            equity_now=starting_capital,
            peak_equity=starting_capital,
            virtual_account_enabled=virtual_on,
            mode_banner=mode_banner,
        )

    now_s = _now_s()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = None
    # Storage-split (2026-07-14) — a SECOND read-only conn against the sibling
    # marketdata DB (bars/quote_ticks/watchlist_focus/altdata_snapshot/every
    # *_shadow table). Every reader below that touches a marketdata-domain
    # table takes an optional ``md_conn`` kwarg falling back to ``conn`` when
    # None (byte-identical for legacy single-conn tests) — so a missing
    # marketdata file degrades gracefully instead of crashing the snapshot.
    md_path = marketdata_db_path_for(db_path)
    md_conn = (
        sqlite3.connect(f"file:{md_path}?mode=ro", uri=True)
        if md_path.exists() else None
    )
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
        session_pnl, session_n = _session_realised_pnl(conn, now_s=now_s)
        last_prices = _last_prices(conn, md_conn=md_conn)
        entry_lookup = _entry_price_lookup(conn)
        cell_mult = _cell_mult_lookup(conn)
        regime_bars, regime_lookup = _regime_bars(conn, now_s=now_s)
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=last_prices,
            entry_lookup=entry_lookup,
            cell_mult=cell_mult,
            regime_lookup=regime_lookup,
            md_conn=md_conn,
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
        ticker_stats = _ticker_stats(conn, now_s=now_s)
        # Measurement-reset baseline (Jin 2026-06-23) — forward edge over trades
        # OPENED at/after the latest measurement_resets.reset_ts. None when no
        # reset stamped → the board falls back to all-time. Display-only.
        since_reset = _since_reset_rollup(conn)
        strategy_since_reset = _strategy_since_reset(conn)
        cell_top, cell_bot, eligible_n, total_cells_n = _cell_top_bottom(
            conn, cell_mult=cell_mult, n=5,
        )
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
            conn, n=100, regime_lookup=regime_by_venue_symbol, md_conn=md_conn,
        )
        # Symbol sparkline (Jin 2026-06-25) — embed the recent-close mini history
        # on each positions / ticker / recent-trade row in ONE bars query (no
        # per-row fetch). Display-only; empty spark when the bars cache has nothing
        # for a symbol (graceful).
        _attach_sparks(conn, positions, ticker_stats, recent_trades, md_conn=md_conn)
        # E2 EXIT tab — FSM distribution + reason histogram (reuses recent_trades)
        # + G6/G7 gate decision counts.
        exit_surface = _exit_surface(
            conn, now_s=now_s, recent_trades=recent_trades,
        )
        # E2 AI tab — conductor shadow agreement + entry-admission would-suppress.
        ai_shadow = _ai_shadow_panel(conn, now_s=now_s)
        # Live gate-event feed (newest first) + per-strategy one-line descriptions.
        # recent_gate_events reads gate_events; strategy_descriptions is file-based
        # (vault notes, no DB). Both display-only, graceful empty.
        recent_gate_events = _recent_gate_events(conn)
        strategy_descriptions = _strategy_descriptions()
        learners = _learner_slots(conn, now_s=now_s)
        edge_validation = _edge_validation(conn, n=8)
        gpt_stats = _gpt_stats(conn, now_s=now_s)
        alerts = _alerts(conn, n=3)
        focus_n, focus_ts = _universe(conn, md_conn=md_conn)
        # Per-gate DECISION summary (replaces the pass/kill funnel). Built AFTER
        # _universe so G1's row can show the live focus count + derived
        # liquidity-floor exclusion. Read-only; never feeds trading.
        gate_decisions = _gate_decisions(
            conn,
            now_s=now_s,
            universe_focus_n=focus_n,
            universe_refresh=focus_ts,
        )
        # Stage-2 per-stream rollup. ``positions`` is passed so the per-stream
        # open_n / upnl / exposed decompose the global totals exactly. Mark
        # freshness (Alpaca RTH / Capital FX-weekend) lives PER-LANE only on
        # each StreamSummary.marks_label/marks_age_sec — no global rollup here
        # (a prior global upnl_marks_label/upnl_marks_age_sec that copied ONLY
        # the Alpaca lane's label onto the 3-venue upnl_total was misleading —
        # removed, Jin 2026-07-08 dashboard-live-net fix).
        streams = _per_stream_summary(
            conn, now_s=now_s, positions=positions, md_conn=md_conn,
        )
        # VIRTUAL-ledger main-board aggregates (Jin 2026-07-08 fix) — the
        # since_reset/daily/session equivalents scoped to the fresh VIRTUAL
        # ledger (per-venue anchor, aggregated), so the main board never mixes
        # the legacy real-roundtrip fills in with the fresh virtual sim ones.
        # Always computed (cheap; mirrors the existing per-stream virtual calc
        # in ``_per_stream_summary``, which also runs unconditionally) — the
        # frontend decides what to show based on ``virtual_account_enabled``.
        virtual_daily_pnl, virtual_daily_n = virtual_daily_pnl_usd(conn, now_s=now_s)
        virtual_session_pnl, virtual_session_n = virtual_session_pnl_usd(
            conn, now_s=now_s,
        )
        virtual_reset = virtual_since_reset(conn)
        # Rotation + session-forced-exit telemetry (follow-up #12) — display-only,
        # graceful zero when the telemetry tables are empty/absent.
        rotation = _rotation_telemetry(conn, now_s=now_s)
        # Component A go-live confidence panel (real-fee-net edge evidence).
        confidence = _confidence_panel(conn, starting_equity=starting_capital)
        # G3/G4 gate-kill counterfactual value panel (07-08 BUILD) — /debate
        # evidence only, never feeds a live gate threshold.
        gate_kill_value = _gate_kill_value_panel(conn, md_conn=md_conn)
        # ADR-012 — observe-mode probe events from the SEPARATE probes.sqlite
        # sidecar (fail-open: empty list on a missing / locked sidecar). Read-only
        # connective tissue for the dashboard; never feeds sizing/gating/exit.
        probe_events = read_probe_events(db_path.parent / "probes.sqlite")
        # CONTEXT/INTEL tab — every alt-data context input the bot weighs, the
        # latest row per source from the read-only altdata_snapshot audit table.
        # "The bot's eyes" surfaced for the operator. Never feeds trading.
        context_intel = _collect_context_intel(conn, now_s=now_s, md_conn=md_conn)
        return DashboardSnapshot(
            ts_now=now_s,
            starting_capital=starting_capital,
            starting_capital_okx=starting_okx,
            starting_capital_capital=starting_capital_cap,
            starting_capital_alpaca=starting_capital_alp,
            equity_now=equity_with_upnl,
            exposed_usd=exposed_usd,
            upnl_total=upnl_total,
            daily_pnl_usd=daily_pnl,
            daily_trades=daily_n,
            session_pnl_usd=session_pnl,
            session_trades=session_n,
            drawdown_pct=dd_pct,
            peak_equity=peak,
            sharpe_24h=sharpe,
            open_positions_n=len(positions),
            active_cells_n=eligible_n,
            total_cells_n=total_cells_n,
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
            ticker_stats=ticker_stats,
            gate_decisions=gate_decisions,
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
            reconciled_realized_usd=rotation.reconciled_realized_usd,
            reconciled_loss_usd=rotation.reconciled_loss_usd,
            reconciled_loss_n=rotation.reconciled_loss_n,
            asset_class_fallback_n=rotation.asset_class_fallback_n,
            last_rotation=rotation.last_rotation,
            confidence=confidence,
            gate_kill_value=gate_kill_value,
            regime_states=regime_states,
            exit_surface=exit_surface,
            ai_shadow=ai_shadow,
            recent_gate_events=recent_gate_events,
            strategy_descriptions=strategy_descriptions,
            probe_events=probe_events,
            since_reset=since_reset,
            strategy_since_reset=strategy_since_reset,
            context_intel=context_intel,
            virtual_account_enabled=virtual_on,
            mode_banner=mode_banner,
            virtual_daily_pnl_usd=virtual_daily_pnl,
            virtual_daily_trades=virtual_daily_n,
            virtual_session_pnl_usd=virtual_session_pnl,
            virtual_session_trades=virtual_session_n,
            virtual_since_reset=virtual_reset,
        )
    finally:
        conn.close()
        if md_conn is not None:
            md_conn.close()
