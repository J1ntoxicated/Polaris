"""Polaris dashboard v1 — mid-section panel renderers (positions → trades).

Pure render functions for rows 6-53 (live positions, per-strategy stats, gate
funnel, cell-matrix top/bottom, regime heatmap, recent trades). Split out of
``render.py`` to keep each module ≤500 LOC; ``render`` re-exports every panel
function + ``TARGET_WIDTH/HEIGHT`` so existing import paths keep working.
"""

from __future__ import annotations

import time

from polaris.scripts.dashboard.ansi_palette import (
    BOLD,
    DIM,
    HIGHLIGHT,
    MUTED,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    WARNING,
    bar,
    color,
    fmt_age,
    fmt_money,
    hline,
    pad,
    pf_color,
    pnl_color,
    wr_color,
)
from polaris.scripts.dashboard.snapshot import (
    DashboardSnapshot,
    EdgeValidationRow,
    GateDecisionRow,
    PositionRow,
)

TARGET_WIDTH = 220
TARGET_HEIGHT = 55


# ---------------------------------------------------------------------------
# Rows 6-13 — live positions panel
# ---------------------------------------------------------------------------


_POS_HEADER = (
    f"  {'VEN':<5}{'SYMBOL':<14}{'STRAT':<18}{'SIDE':<6}"
    f"{'ENTRY':>10}{'LAST':>10}{'Δ%':>8}{'uPnL$':>10}{'SIZE$':>10}"
    f"{'HELD':>8}{'MULT':>6}"
)


def render_positions_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH, max_rows: int = 6
) -> list[str]:
    out: list[str] = []
    out.append(hline("LIVE POSITIONS (top by uPnL)", width))
    out.append(pad(color(_POS_HEADER, HIGHLIGHT + BOLD), width))
    if not snap.positions:
        empty = pad(f"  {color('(no open positions)', MUTED)}", width)
        out.append(empty)
        for _ in range(max_rows - 1):
            out.append(pad("", width))
        return out
    rows = snap.positions[:max_rows]
    for p in rows:
        out.append(pad(f"  {_pos_row(p)}", width))
    overflow = len(snap.positions) - len(rows)
    if overflow > 0:
        # Replace last row with "+N more"
        out[-1] = pad(
            f"  {color(f'+ {overflow} more positions hidden', MUTED + DIM)}",
            width,
        )
    while len(out) < 2 + max_rows:
        out.append(pad("", width))
    return out


def _pos_row(p: PositionRow) -> str:
    side_c = POSITIVE if p.side.lower() in {"long", "buy"} else NEGATIVE
    upnl_c = pnl_color(p.upnl_usd)
    delta_c = pnl_color(p.delta_pct)
    mult_c = (
        POSITIVE if p.cell_mult > 1.05 else NEGATIVE if p.cell_mult < 0.95 else NEUTRAL
    )
    return (
        f"{p.venue[:4]:<5}{p.symbol[:12]:<14}{p.strategy_id[:16]:<18}"
        f"{color(p.side[:5], side_c):<6}"
        f"{p.entry_price:>10.4f}{p.last_price:>10.4f}"
        f"{color(f'{p.delta_pct:+6.2f}%', delta_c):>8}"
        f"{color(fmt_money(p.upnl_usd, sign=True), upnl_c + BOLD):>10}"
        f"{p.size_usd:>10.2f}{fmt_age(p.held_sec):>8}"
        f"{color(f'{p.cell_mult:>5.2f}x', mult_c):>6}"
    )


# ---------------------------------------------------------------------------
# Rows 14-22 — per-strategy stats panel
# ---------------------------------------------------------------------------


_STRAT_HEADER = (
    f"  {'STRATEGY':<22}{'OPEN':>6}{'CLOSED':>8}{'WR%':>8}"
    f"{'AVGR$10':>8}{'PF':>7}{'PnL$_24h':>12}{'OPEN_NOTNL$':>14}"
)


def render_strategy_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH, max_rows: int = 7
) -> list[str]:
    out: list[str] = []
    out.append(hline("PER-STRATEGY STATS (closed=24h, open/notnl=now)", width))
    out.append(pad(color(_STRAT_HEADER, HIGHLIGHT + BOLD), width))
    if not snap.strategy_stats:
        empty = pad(f"  {color('(no strategy stats yet)', MUTED)}", width)
        out.append(empty)
        for _ in range(max_rows - 1):
            out.append(pad("", width))
        return out
    rows = snap.strategy_stats[:max_rows]
    for s in rows:
        wr_c = wr_color(s.wr_pct)
        pf_c = pf_color(s.pf)
        pnl_c = pnl_color(s.pnl_usd)
        out.append(
            pad(
                f"  {s.strategy_id[:20]:<22}{s.open_n:>6}{s.closed_n:>8}"
                f"{color(f'{s.wr_pct:>5.1f}%', wr_c):>8}"
                f"{s.avg_r:>+8.2f}"
                f"{color(f'{s.pf:>5.2f}', pf_c):>7}"
                f"{color(fmt_money(s.pnl_usd, sign=True), pnl_c + BOLD):>12}"
                f"{fmt_money(s.notional_usd):>14}",
                width,
            )
        )
    while len(out) < 2 + max_rows:
        out.append(pad("", width))
    return out


# ---------------------------------------------------------------------------
# Rows 23-31 — gate funnel panel
# ---------------------------------------------------------------------------


def render_gate_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH
) -> list[str]:
    out: list[str] = []
    out.append(hline("GATE DECISIONS (last 1h, what each gate decided)", width))
    if not snap.gate_decisions:
        out.append(pad(f"  {color('(no gate events yet)', MUTED)}", width))
        for _ in range(7):
            out.append(pad("", width))
        return out
    for g in snap.gate_decisions:
        out.append(pad(f"  {_gate_row(g)}", width))
    return out


def _gate_row(g: GateDecisionRow) -> str:
    return (
        f"{color(f'G{g.gate_id}', HIGHLIGHT + BOLD):<3} "
        f"{color(g.label[:10], NEUTRAL):<11} "
        f"{color(g.headline, POSITIVE)}  "
        f"{color(f'n={g.n}', MUTED)}"
    )


# ---------------------------------------------------------------------------
# Rows 32-44 — cell matrix top + bottom panel
# ---------------------------------------------------------------------------


_CELL_HEADER = (
    f"  {'EXCH':<6}{'STRATEGY':<18}{'TICKER':<14}{'REGIME':<14}"
    f"{'N_EFF':>9}{'SCORE':>10}{'MULT':>8}"
)


def render_cell_top_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH, max_rows: int = 5
) -> list[str]:
    out: list[str] = []
    out.append(hline(f"CELL MATRIX TOP {max_rows} (n_eff≥20)", width))
    out.append(pad(color(_CELL_HEADER, HIGHLIGHT + BOLD), width))
    if not snap.cell_top:
        out.append(pad(f"  {color('(no eligible cells)', MUTED)}", width))
        for _ in range(max_rows - 1):
            out.append(pad("", width))
        return out
    rows = snap.cell_top[:max_rows]
    for c in rows:
        mult_c = POSITIVE if c.mult > 1.05 else NEUTRAL
        score_c = pnl_color(c.score)
        out.append(
            pad(
                f"  {c.exchange[:5]:<6}{c.strategy[:16]:<18}{c.ticker[:12]:<14}"
                f"{c.regime[:12]:<14}"
                f"{c.n_eff:>9.1f}"
                f"{color(f'{c.score:>+8.4f}', score_c + BOLD):>10}"
                f"{color(f'{c.mult:>5.2f}x', mult_c):>8}",
                width,
            )
        )
    overflow = len(snap.cell_top) - len(rows)
    if overflow > 0:
        out[-1] = pad(
            f"  {color(f'+ {overflow} more cells hidden', MUTED + DIM)}", width
        )
    while len(out) < 2 + max_rows:
        out.append(pad("", width))
    return out


def render_cell_bottom_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH, max_rows: int = 5
) -> list[str]:
    out: list[str] = []
    out.append(hline(f"CELL MATRIX BOTTOM {max_rows} (warning band)", width))
    if not snap.cell_bottom:
        out.append(pad(f"  {color('(empty)', MUTED)}", width))
        for _ in range(max_rows - 1):
            out.append(pad("", width))
        return out
    rows = snap.cell_bottom[:max_rows]
    for c in rows:
        mult_c = NEGATIVE if c.mult < 0.95 else NEUTRAL
        score_c = pnl_color(c.score)
        out.append(
            pad(
                f"  {c.exchange[:5]:<6}{c.strategy[:16]:<18}{c.ticker[:12]:<14}"
                f"{c.regime[:12]:<14}"
                f"{c.n_eff:>9.1f}"
                f"{color(f'{c.score:>+8.4f}', score_c + BOLD):>10}"
                f"{color(f'{c.mult:>5.2f}x', mult_c):>8}",
                width,
            )
        )
    overflow = len(snap.cell_bottom) - len(rows)
    if overflow > 0:
        out[-1] = pad(
            f"  {color(f'+ {overflow} more cells hidden', MUTED + DIM)}", width
        )
    while len(out) < 1 + max_rows:
        out.append(pad("", width))
    return out


# ---------------------------------------------------------------------------
# Rows 45-48 — regime heatmap panel
# ---------------------------------------------------------------------------


def render_regime_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH, max_rows: int = 4
) -> list[str]:
    """Render all 4 regimes (chop / bull_trend / bear_trend / crisis).

    Codex P0 fix: previously hard-capped at 3 rows so a non-zero `crisis`
    regime got silently truncated. Now defaults to ``max_rows=4`` and renders
    every regime in fixed order, even if its count is 0.
    """
    out: list[str] = []
    total = sum(b.count for b in snap.regime_bars) or 1
    out.append(hline("REGIME HEATMAP (active groups)", width))
    if not snap.regime_bars:
        out.append(pad(f"  {color('(no regimes yet)', MUTED)}", width))
        for _ in range(max_rows - 1):
            out.append(pad("", width))
        return out
    bar_w = max(20, width - 50)
    palette = {
        "chop": NEUTRAL,
        "bull_trend": POSITIVE,
        "bear_trend": NEGATIVE,
        "crisis": WARNING + BOLD,
    }
    rendered_rows: list[str] = []
    for b in snap.regime_bars[:max_rows]:
        pct = b.count / total * 100.0
        col = palette.get(b.regime, NEUTRAL)
        bar_str = bar(pct, min(bar_w, 100), fill_color=col)
        rendered_rows.append(
            f"  {color(b.regime[:12], col + BOLD):<14} {bar_str} "
            f"{color(f'{b.count:>3}', col + BOLD)} "
            f"{color(f'({pct:>5.1f}%)', NEUTRAL)}"
        )
    for line in rendered_rows:
        out.append(pad(line, width))
    while len(out) < 1 + max_rows:
        out.append(pad("", width))
    return out


# ---------------------------------------------------------------------------
# Edge-validation panel (Phase 1 — Bayesian posterior, display only)
# ---------------------------------------------------------------------------


_EDGE_HEADER = (
    f"  {'EXCH':<6}{'STRATEGY':<18}{'TICKER':<14}{'REGIME':<12}"
    f"{'COST_ADJ_EXP':>14}{'P(exp>0)':>10}{'N':>6}{'VERDICT':>17}{'COST':>7}"
)


def _edge_row(e: EdgeValidationRow) -> str:
    exp_c = pnl_color(e.cost_adj_exp)
    if e.verdict == "validated-alpha":
        v_c = POSITIVE + BOLD
    elif e.verdict == "anti-edge":
        v_c = NEGATIVE + BOLD
    else:
        v_c = MUTED
    p_c = POSITIVE if e.p_pos >= 0.85 else NEGATIVE if e.p_pos <= 0.15 else NEUTRAL
    cost_tag = color("est", WARNING) if e.est_cost else color("real", MUTED)
    return (
        f"{e.exchange[:5]:<6}{e.strategy[:16]:<18}{e.ticker[:12]:<14}"
        f"{e.regime[:10]:<12}"
        f"{color(f'{e.cost_adj_exp:>+12.3f}', exp_c):>14}"
        f"{color(f'{e.p_pos:>8.3f}', p_c + BOLD):>10}"
        f"{e.n_samples:>6}"
        f"{color(f'{e.verdict[:15]:>15}', v_c):>17}"
        f"{cost_tag:>7}"
    )


def render_edge_validation_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH, max_rows: int = 6
) -> list[str]:
    """Render the cost-adjusted expectancy posterior table (measure-only).

    Strictly display: ``cost_adj_exp`` (posterior mean μ_n), ``P(exp>0)``
    confidence, sample count, verdict label, and a Capital ``est cost`` flag.
    Never feeds sizing.
    """
    out: list[str] = []
    out.append(hline("EDGE VALIDATION (cost-adj expectancy posterior — display only)", width))
    out.append(pad(color(_EDGE_HEADER, HIGHLIGHT + BOLD), width))
    if not snap.edge_validation:
        out.append(pad(f"  {color('(no posterior samples yet)', MUTED)}", width))
        for _ in range(max_rows - 1):
            out.append(pad("", width))
        return out
    for e in snap.edge_validation[:max_rows]:
        out.append(pad(f"  {_edge_row(e)}", width))
    while len(out) < 2 + max_rows:
        out.append(pad("", width))
    return out


# ---------------------------------------------------------------------------
# Rows 49-53 — recent closed trades panel
# ---------------------------------------------------------------------------


_TRADE_HEADER = (
    f"  {'TIME':<10}{'VEN':<5}{'SYMBOL':<14}{'STRAT':<18}{'CLOSE':<6}"
    f"{'ENTRY':>10}{'EXIT':>10}{'NET_PnL$':>12}{'R$10':>7}{'HELD':>7}{'REASON':>8}"
)


def render_trades_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH, max_rows: int = 2
) -> list[str]:
    out: list[str] = []
    out.append(hline("RECENT CLOSED TRADES (last)", width))
    out.append(pad(color(_TRADE_HEADER, HIGHLIGHT + BOLD), width))
    if not snap.recent_trades:
        out.append(pad(f"  {color('(no closed trades yet)', MUTED)}", width))
        for _ in range(max_rows - 1):
            out.append(pad("", width))
        return out
    rows = snap.recent_trades[:max_rows]
    for t in rows:
        pnl_c = pnl_color(t.pnl_usd)
        side_c = NEGATIVE if t.side_close.lower() in {"sell", "short"} else POSITIVE
        ts_str = time.strftime("%H:%M:%S", time.localtime(t.ts_close))
        reason_c = (
            POSITIVE if t.exit_reason == "TP"
            else NEGATIVE if t.exit_reason == "SL"
            else WARNING if t.exit_reason == "TIME"
            else NEUTRAL
        )
        out.append(
            pad(
                f"  {ts_str:<10}{t.venue[:4]:<5}{t.symbol[:12]:<14}"
                f"{t.strategy_id[:16]:<18}"
                f"{color(t.side_close[:5], side_c):<6}"
                f"{t.entry_price:>10.4f}{t.exit_price:>10.4f}"
                f"{color(fmt_money(t.pnl_usd, sign=True), pnl_c + BOLD):>12}"
                f"{color(f'{t.r_units:>+5.2f}', pnl_c):>7}"
                f"{fmt_age(t.held_sec):>7}"
                f"{color(t.exit_reason[:7], reason_c):>8}",
                width,
            )
        )
    overflow = len(snap.recent_trades) - len(rows)
    if overflow > 0:
        out[-1] = pad(
            f"  {color(f'+ {overflow} more trades hidden', MUTED + DIM)}", width
        )
    while len(out) < 2 + max_rows:
        out.append(pad("", width))
    return out
