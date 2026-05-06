"""Polaris Intel — PM ACTIVITY section.

Portfolio Policy Manager orchestrator activity:
- Last cycle counts (eval/hold/close/rotate/add)
- Top 5 opportunities ranked by expected_return × confidence

Phase 23 user vision: 자본 회전 visibility.
"""
from __future__ import annotations

from src.dashboard.ansi import (
    B, P_GRN, P_RED, P_YLW, P_CYN, P_WHT, P_GRY, P_DIM, P_MAG,
    POLARIS_BLUE, STAR_4,
    c, pad, rpad, hline,
)
from src.dashboard.sections.header import _read_live


def render(W: int, n: int = 9) -> list[str]:
    """PM activity panel."""
    snap = _read_live()
    pm = snap.get("pm_stats", {})

    lines: list[str] = [hline("PM ORCHESTRATOR — ACTIVE CAPITAL ALLOCATION", W, POLARIS_BLUE)]

    # Cycle counters
    n_eval = pm.get("n_evaluated", 0)
    n_hold = pm.get("n_holds", 0)
    n_close = pm.get("n_closes", 0)
    n_rotate = pm.get("n_rotates", 0)
    n_add = pm.get("n_adds", 0)

    cycle_row = (
        f"  {c('CYCLE', P_DIM)} "
        f"eval={c(str(n_eval), P_WHT + B)} "
        f"hold={c(str(n_hold), P_GRY)} "
        f"close={c(str(n_close), P_YLW)} "
        f"rotate={c(str(n_rotate), P_CYN + B)} "
        f"add={c(str(n_add), P_GRN + B)}"
    )
    lines.append(pad(cycle_row, W))

    # Opportunities
    opps = pm.get("top_opportunities", [])
    if opps:
        opp_hdr = (
            f"  {c('TOP OPPORTUNITIES', P_DIM)} "
            f"({c(str(len(opps)), P_WHT + B)} ranked)"
        )
        lines.append(pad(opp_hdr, W))
        col_hdr = (
            f"    {c('TICKER', P_DIM):<14} {c('STRATEGY', P_DIM):<22} "
            f"{c('CONF', P_DIM):>7} {c('EV%', P_DIM):>9}"
        )
        lines.append(pad(col_hdr, W))
        for o in opps[:5]:
            er = o.get("expected_return_pct", 0) * 100
            er_color = P_GRN + B if er > 0.5 else P_YLW if er > 0 else P_RED
            conf = o.get("confidence", 0)
            conf_color = P_GRN if conf >= 0.7 else P_YLW if conf >= 0.5 else P_RED
            row = (
                f"    {c(STAR_4, P_CYN)} {rpad(o['ticker'], 12):<14} "
                f"{rpad(o['strategy'], 22):<22} "
                f"{c(rpad(f'{conf:.2f}', 7), conf_color)} "
                f"{c(rpad(f'{er:+.3f}%', 9), er_color)}"
            )
            lines.append(pad(row, W))
    else:
        lines.append(pad(c("    (no opportunities scanned yet)", P_DIM), W))

    while len(lines) < n:
        lines.append(pad("", W))
    return lines[:n]
