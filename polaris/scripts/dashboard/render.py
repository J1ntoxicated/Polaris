"""Polaris dashboard v1 — pure render functions.

Inputs: ``DashboardSnapshot`` (from snapshot.py).
Outputs: list[str] of exactly TARGET_HEIGHT rows, each visible-padded to
TARGET_WIDTH chars.

Layout (220×55):
    Row  1     : header banner + status
    Row  2     : top metrics bar (equity / exposed / uPnL / daily / DD / Sharpe)
    Row  3     : hline 24H EQUITY
    Row  4     : 200-char sparkline
    Row  5     : min/max/Δ annotation
    Row  6     : hline LIVE POSITIONS
    Row  7     : position header
    Rows 8-13  : 6 position rows (overflow → "+N more" on last)
    Row 14     : hline PER-STRATEGY STATS
    Row 15     : strategy header
    Rows 16-22 : 7 strategy rows
    Row 23     : hline GATE FUNNEL (1h)
    Rows 24-31 : 8 gate rows (G1-G8)
    Row 32     : hline CELL MATRIX TOP 5 (n_eff≥20)
    Row 33     : cell header
    Rows 34-38 : 5 top cells
    Row 39     : hline CELL MATRIX BOTTOM 5
    Rows 40-44 : 5 bot cells
    Row 45     : hline REGIME HEATMAP (4 regimes)
    Rows 46-49 : 4 regime rows (chop / bull / bear / crisis)
    Row 50     : hline RECENT CLOSED TRADES
    Row 51     : trade header
    Rows 52-53 : 2 closed trades
    Row 54     : LEARNERS (3 P0 inline) + GPT (calls/h, $/h)
    Row 55     : ALERTS (last 1) + universe focus
"""

from __future__ import annotations

import time

from polaris.scripts.dashboard.ansi_palette import (
    BOLD,
    DIM,
    HIGHLIGHT,
    HOME,
    INFO,
    MUTED,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    WARNING,
    bar,
    color,
    fmt_age,
    fmt_money,
    fmt_pct,
    hline,
    pad,
    pf_color,
    pnl_color,
    sparkline,
    wr_color,
)
from polaris.scripts.dashboard.snapshot import (
    DashboardSnapshot,
    GateRow,
    PositionRow,
)

TARGET_WIDTH = 220
TARGET_HEIGHT = 55


# ---------------------------------------------------------------------------
# Row 1-2 — header + top metrics bar
# ---------------------------------------------------------------------------


def render_header(snap: DashboardSnapshot, *, width: int = TARGET_WIDTH) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap.ts_now))
    title = color("POLARIS DASHBOARD v1", HIGHLIGHT + BOLD)
    badge = color(" [DEMO·PAPER] ", WARNING + BOLD)
    status = color(f"{ts}", NEUTRAL)
    refresh = color("refresh 5s", MUTED)
    sep = color(" │ ", MUTED)
    line = f"  {title}{badge}{sep}{status}{sep}{refresh}"
    return pad(line, width)


def render_top_bar(snap: DashboardSnapshot, *, width: int = TARGET_WIDTH) -> str:
    eq_c = pnl_color(snap.equity_now - snap.starting_capital)
    upnl_c = pnl_color(snap.upnl_total)
    daily_c = pnl_color(snap.daily_pnl_usd)
    dd_c = NEGATIVE if snap.drawdown_pct > 0.1 else NEUTRAL
    sharpe_c = (
        POSITIVE
        if snap.sharpe_24h >= 1.0
        else WARNING if snap.sharpe_24h >= 0.0 else NEGATIVE
    )
    # Day 9 F12 fix — venue-split base label so the operator can see the
    # OKX SPOT vs Capital CFD demo equity split at a glance.
    base_label = (
        "base $" + fmt_money(snap.starting_capital)
        + f" [OKX ${fmt_money(snap.starting_capital_okx)}"
        + f" / CAP ${fmt_money(snap.starting_capital_capital)}]"
        if snap.starting_capital_okx > 0 and snap.starting_capital_capital > 0
        else "base $" + fmt_money(snap.starting_capital)
    )
    parts = [
        f"  {color('EQUITY', NEUTRAL)} {color('$' + fmt_money(snap.equity_now), eq_c + BOLD)}"
        f" ({color(fmt_money(snap.equity_now - snap.starting_capital, sign=True), eq_c)})"
        f" {color(base_label, MUTED)}",
        f"{color('EXPOSED', NEUTRAL)} {color('$' + fmt_money(snap.exposed_usd), WARNING if snap.exposed_usd > snap.equity_now else NEUTRAL)}",
        f"{color('uPnL', NEUTRAL)} {color('$' + fmt_money(snap.upnl_total, sign=True), upnl_c + BOLD)}",
        f"{color('Daily', NEUTRAL)} {color('$' + fmt_money(snap.daily_pnl_usd, sign=True), daily_c + BOLD)}"
        f" ({snap.daily_trades}t)",
        f"{color('DD', NEUTRAL)} {color('-' + fmt_money(snap.drawdown_pct, decimals=2) + '%', dd_c)}"
        f" (peak ${fmt_money(snap.peak_equity)})",
        f"{color('Sharpe', NEUTRAL)} {color(fmt_money(snap.sharpe_24h, decimals=2), sharpe_c)}",
        f"{color('Open', NEUTRAL)} {color(str(snap.open_positions_n), HIGHLIGHT)}",
        f"{color('Cells', NEUTRAL)} {color(str(snap.active_cells_n), HIGHLIGHT)}",
        f"{color('Focus', NEUTRAL)} {color(str(snap.universe_focus_n), HIGHLIGHT)}",
    ]
    sep = color(" │ ", MUTED)
    line = sep.join(parts)
    return pad(line, width)


# ---------------------------------------------------------------------------
# Row 3-5 — equity sparkline panel
# ---------------------------------------------------------------------------


def render_equity_panel(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH
) -> list[str]:
    out: list[str] = []
    label = "24H EQUITY CURVE"
    out.append(hline(label, width))
    spark_w = width - 4
    if not snap.equity_curve:
        out.append(pad(f"  {color('(no equity history yet)', MUTED)}", width))
        out.append(pad("", width))
        return out
    spark = sparkline(snap.equity_curve, spark_w)
    out.append(pad(f"  {color(spark, INFO)}", width))
    lo = min(snap.equity_curve)
    hi = max(snap.equity_curve)
    delta = snap.equity_curve[-1] - snap.equity_curve[0]
    pct = (delta / snap.equity_curve[0] * 100.0) if snap.equity_curve[0] else 0.0
    delta_c = pnl_color(delta)
    annotation = (
        f"  {color('min', NEUTRAL)} ${fmt_money(lo)}   "
        f"{color('max', NEUTRAL)} ${fmt_money(hi)}   "
        f"{color('range', NEUTRAL)} ${fmt_money(hi - lo)}   "
        f"{color('24h Δ', NEUTRAL)} "
        f"{color(fmt_money(delta, sign=True) + ' (' + fmt_pct(pct) + ')', delta_c + BOLD)}"
    )
    out.append(pad(annotation, width))
    return out


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
    out.append(hline("GATE FUNNEL (last 1h, pass% bar)", width))
    if not snap.gate_funnel:
        out.append(pad(f"  {color('(no gate events yet)', MUTED)}", width))
        for _ in range(7):
            out.append(pad("", width))
        return out
    bar_w = max(20, width - 80)
    for g in snap.gate_funnel:
        out.append(pad(f"  {_gate_row(g, bar_w)}", width))
    return out


def _gate_row(g: GateRow, bar_w: int) -> str:
    pass_c = (
        POSITIVE if g.pass_rate >= 60 else WARNING if g.pass_rate >= 30 else NEGATIVE
    )
    label_c = HIGHLIGHT
    bar_str = bar(g.pass_rate, bar_w, fill_color=pass_c)
    return (
        f"{color(f'G{g.gate_id}', label_c + BOLD):<3} "
        f"{color(g.label[:10], NEUTRAL):<11} "
        f"{bar_str} "
        f"{color(f'{g.pass_rate:>5.1f}%', pass_c + BOLD)}  "
        f"{color('PASS', POSITIVE)} {g.pass_n:>5}  "
        f"{color('KILL', NEGATIVE)} {g.kill_n:>4}  "
        f"{color('total', MUTED)} {g.total:>6}"
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
    for c in snap.cell_top[:max_rows]:
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
    for c in snap.cell_bottom[:max_rows]:
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
    for t in snap.recent_trades[:max_rows]:
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
    while len(out) < 2 + max_rows:
        out.append(pad("", width))
    return out


# ---------------------------------------------------------------------------
# Row 54 — learner state + GPT cost (combined)
# ---------------------------------------------------------------------------


def render_learner_gpt_row(
    snap: DashboardSnapshot, *, width: int = TARGET_WIDTH
) -> str:
    parts: list[str] = []
    parts.append(color("LEARNERS:", HIGHLIGHT + BOLD))
    if not snap.learners:
        parts.append(color("(none)", MUTED))
    else:
        for ls in snap.learners:
            arrow = (
                color("↑", POSITIVE)
                if ls.delta_1h > 0.001
                else color("↓", NEGATIVE) if ls.delta_1h < -0.001 else color("→", NEUTRAL)
            )
            parts.append(
                f"{color(ls.learner_id, NEUTRAL)}={color(f'{ls.value:.2f}', HIGHLIGHT + BOLD)}"
                f"{arrow}{color(f'{ls.delta_1h:+.2f}', pnl_color(ls.delta_1h))}"
                f"{color('(' + ls.key[:18] + ')', MUTED)}"
                f"{color(f'n={ls.n_eff:.0f}', MUTED)}"
            )
    parts.append(color(" │ ", MUTED))
    parts.append(color("GPT:", HIGHLIGHT + BOLD))
    if not snap.gpt_stats:
        parts.append(color("(idle)", MUTED))
    else:
        total_cph = sum(g.cost_per_h_usd for g in snap.gpt_stats)
        for g in snap.gpt_stats:
            parts.append(
                f"{color(g.model[:14], NEUTRAL)} "
                f"{color(f'{g.calls_per_h}c/h', HIGHLIGHT)} "
                f"{color('$' + fmt_money(g.cost_per_h_usd, decimals=4) + '/h', INFO)}"
            )
        parts.append(
            color(f"24h≈${fmt_money(total_cph * 24.0, decimals=2)}", INFO + BOLD)
        )
    line = "  " + " ".join(parts)
    return pad(line, width)


# ---------------------------------------------------------------------------
# Row 55 — alert log + universe focus tail
# ---------------------------------------------------------------------------


def render_alert_row(snap: DashboardSnapshot, *, width: int = TARGET_WIDTH) -> str:
    if snap.alerts:
        a = snap.alerts[0]
        ts = time.strftime("%H:%M:%S", time.localtime(a.ts))
        lvl_c = NEGATIVE if a.level == "ERROR" else WARNING
        head = (
            f"  {color('ALERT', lvl_c + BOLD)} "
            f"{color(ts, MUTED)} "
            f"{color(a.level, lvl_c)} "
            f"{color(a.module[:12], NEUTRAL)} "
            f"{a.msg[:90]}"
        )
    else:
        head = f"  {color('ALERT', MUTED)} {color('(no incidents)', MUTED + DIM)}"
    tail = (
        f"{color('Universe:', NEUTRAL)} "
        f"{color(str(snap.universe_focus_n), HIGHLIGHT)} focus "
        f"{color('refresh', MUTED)} {snap.universe_last_refresh}"
    )
    sep = color(" │ ", MUTED)
    line = head + sep + tail
    return pad(line, width)


# ---------------------------------------------------------------------------
# Top-level renderer
# ---------------------------------------------------------------------------


def render_dashboard(
    snap: DashboardSnapshot,
    *,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
) -> list[str]:
    """Compose all panels into exactly `height` rows of `width` chars."""
    rows: list[str] = []
    rows.append(render_header(snap, width=width))           # row 1
    rows.append(render_top_bar(snap, width=width))           # row 2
    rows.extend(render_equity_panel(snap, width=width))      # rows 3-5
    rows.extend(render_positions_panel(snap, width=width))   # rows 6-13 (8)
    rows.extend(render_strategy_panel(snap, width=width))    # rows 14-22 (9)
    rows.extend(render_gate_panel(snap, width=width))        # rows 23-31 (9)
    rows.extend(render_cell_top_panel(snap, width=width))    # rows 32-38 (7)
    rows.extend(render_cell_bottom_panel(snap, width=width)) # rows 39-44 (6)
    rows.extend(render_regime_panel(snap, width=width))      # rows 45-48 (4)
    rows.extend(render_trades_panel(snap, width=width))      # rows 49-53 (5)
    rows.append(render_learner_gpt_row(snap, width=width))   # row 54
    rows.append(render_alert_row(snap, width=width))         # row 55
    # Truncate or pad to fixed height
    if len(rows) > height:
        rows = rows[:height]
    while len(rows) < height:
        rows.append(pad("", width))
    return rows


def render_full_screen(
    snap: DashboardSnapshot,
    *,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
) -> str:
    """Compose dashboard rows + cursor home + clear-down — flicker-free 5s refresh."""
    rows = render_dashboard(snap, width=width, height=height)
    return HOME + "\n".join(rows)


__all__ = [
    "TARGET_HEIGHT",
    "TARGET_WIDTH",
    "render_alert_row",
    "render_cell_bottom_panel",
    "render_cell_top_panel",
    "render_dashboard",
    "render_equity_panel",
    "render_full_screen",
    "render_gate_panel",
    "render_header",
    "render_learner_gpt_row",
    "render_positions_panel",
    "render_regime_panel",
    "render_strategy_panel",
    "render_top_bar",
    "render_trades_panel",
]
