"""Polaris Intel — REALTIME HYPOs section.

Per-HYPO summary: cash, open count, closed count, win rate, PnL.
Reads from SQL ledger (Phase 17 SSOT).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.dashboard.ansi import (
    B, P_GRN, P_RED, P_YLW, P_CYN, P_WHT, P_GRY, P_DIM, P_MAG,
    POLARIS_BLUE, STAR_4,
    c, pad, rpad, hline,
)


_DB = Path(__file__).resolve().parent.parent.parent.parent / "data" / "polaris.sqlite"


_HYPO_NAMES = {
    "HYPO-007-RT": "RSI 15m Intraday",
    "HYPO-008-RT": "Volume Burst",
    "HYPO-023": "Liquidation Cascade",
    "HYPO-027": "Funding Rate Filter",
    "HYPO-028": "Tick Burst",
    "HYPO-032": "TSMOM 1D",
    "HYPO-036": "Funding Carry",
    "HYPO-040": "Grid Bot",
    "HYPO-NFI-001": "NFI Dipbuy",
}


def _query_hypo_stats() -> list[dict]:
    if not _DB.exists():
        return []
    try:
        conn = sqlite3.connect(_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT hypo_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS opens,
                   SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closes,
                   SUM(CASE WHEN status='closed' AND net_usd>0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN status='closed' THEN COALESCE(net_usd,0) ELSE 0 END) AS pnl
            FROM positions
            WHERE hypo_id LIKE 'HYPO-%'
            GROUP BY hypo_id
            ORDER BY pnl DESC
            """
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def render(W: int, n: int = 12) -> list[str]:
    rows = _query_hypo_stats()
    lines: list[str] = [hline("REALTIME HYPOs — Per-Strategy KPI", W, POLARIS_BLUE)]
    hdr = (
        f"  {c('HYPO', P_DIM):<22} {c('STRATEGY', P_DIM):<22} "
        f"{c('OPEN', P_DIM):>5} {c('CLOSED', P_DIM):>7} "
        f"{c('WIN%', P_DIM):>6} {c('NET $', P_DIM):>10}"
    )
    lines.append(pad(hdr, W))

    if not rows:
        lines.append(pad(c("    (no trades in ledger yet)", P_DIM), W))
    else:
        for r in rows[: n - 2]:
            hid = r["hypo_id"]
            name = _HYPO_NAMES.get(hid, hid)
            opens = r["opens"] or 0
            closes = r["closes"] or 0
            wins = r["wins"] or 0
            pnl = r["pnl"] or 0
            wr = (wins / closes * 100) if closes else 0
            wr_color = P_GRN + B if wr >= 60 else P_YLW if wr >= 40 else P_RED
            pnl_color = P_GRN + B if pnl > 0 else P_RED + B if pnl < 0 else P_DIM
            opens_color = P_WHT + B if opens > 0 else P_DIM
            row = (
                f"  {c(rpad(hid, 22), P_WHT):<22} {c(rpad(name, 22), P_GRY):<22} "
                f"{c(rpad(str(opens), 5), opens_color):>5} {rpad(str(closes), 7):>7} "
                f"{c(rpad(f'{wr:.0f}%', 6), wr_color):>6} "
                f"{c(rpad(f'${pnl:+.2f}', 10), pnl_color):>10}"
            )
            lines.append(pad(row, W))

    while len(lines) < n:
        lines.append(pad("", W))
    return lines[:n]
