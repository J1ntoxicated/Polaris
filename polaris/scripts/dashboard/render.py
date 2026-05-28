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
    color,
    fmt_money,
    fmt_pct,
    hline,
    pad,
    pnl_color,
    sparkline,
)
from polaris.scripts.dashboard.render_panels import (
    TARGET_HEIGHT,
    TARGET_WIDTH,
    render_cell_bottom_panel,
    render_cell_top_panel,
    render_gate_panel,
    render_positions_panel,
    render_regime_panel,
    render_strategy_panel,
    render_trades_panel,
)
from polaris.scripts.dashboard.snapshot import DashboardSnapshot

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
