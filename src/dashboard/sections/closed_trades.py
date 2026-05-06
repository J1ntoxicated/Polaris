"""Polaris Ops — CLOSED TRADES section (recent N from SQL ledger).

Per legacy operations.py pattern: closed positions list with PnL.
Reads from data/polaris.sqlite positions table where status='closed'.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from src.dashboard.ansi import (
    B, P_GRN, P_RED, P_YLW, P_CYN, P_WHT, P_GRY, P_DIM, P_BLU,
    POLARIS_BLUE,
    c, pad, rpad, hline, fmt_age,
)


_DB = Path(__file__).resolve().parent.parent.parent.parent / "data" / "polaris.sqlite"

_HYPO_CODE = {
    "HYPO-007-RT": "007", "HYPO-008-RT": "008", "HYPO-023": "023",
    "HYPO-027": "027", "HYPO-028": "028", "HYPO-032": "032",
    "HYPO-036": "036", "HYPO-040": "040", "HYPO-NFI-001": "NFI",
}


def _query(limit: int = 30) -> list[dict]:
    if not _DB.exists():
        return []
    try:
        conn = sqlite3.connect(_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT hypo_id, strategy_name, ticker, entry_price, exit_price,
                   entry_size_usd, open_ts_ms, close_ts_ms, gross_usd, fee_usd,
                   net_usd, exit_reason
            FROM positions
            WHERE status='closed'
            ORDER BY close_ts_ms DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def render(W: int, n: int = 16) -> list[str]:
    rows = _query(limit=n - 2)
    lines: list[str] = [hline("CLOSED TRADES (recent)", W, POLARIS_BLUE)]
    SP = " "
    hdr = (
        f"  {c('Ago', P_GRY):<6}{SP}"
        f"{c('Ticker', P_GRY):<10}{SP}"
        f"{c('Hypo', P_GRY):<5}{SP}"
        f"{c('Strategy', P_GRY):<14}{SP}"
        f"{c('Entry', P_GRY):>10}{SP}"
        f"{c('Exit', P_GRY):>10}{SP}"
        f"{c('Δ%', P_GRY):>7}{SP}"
        f"{c('Net$', P_GRY):>9}{SP}"
        f"{c('Size$', P_GRY):>7}{SP}"
        f"{c('Hold', P_GRY):>6}{SP}"
        f"{c('Reason', P_GRY):<30}"
    )
    lines.append(pad(hdr, W))

    if not rows:
        lines.append(pad(c("    (no closed trades yet)", P_DIM), W))
        while len(lines) < n:
            lines.append(pad("", W))
        return lines[:n]

    now_ms = int(time.time() * 1000)
    for r in rows:
        close_ts = r.get("close_ts_ms") or 0
        ago_s = (now_ms - close_ts) / 1000 if close_ts else 0
        ago_str = fmt_age(ago_s)

        ep = r.get("entry_price") or 0
        xp = r.get("exit_price") or 0
        pct = ((xp - ep) / ep * 100) if ep > 0 else 0
        net = r.get("net_usd") or 0
        size_v = r.get("entry_size_usd") or 0
        hold_ms = (close_ts - (r.get("open_ts_ms") or 0)) if close_ts else 0
        hold_str = fmt_age(hold_ms / 1000)

        ep_str = f"{ep:.4f}" if ep < 1 else f"{ep:.2f}"
        xp_str = f"{xp:.4f}" if xp < 1 else f"{xp:.2f}"

        net_color = P_GRN + B if net > 0 else P_RED + B if net < 0 else P_DIM
        pct_color = P_GRN + B if pct > 0 else P_RED + B if pct < 0 else P_DIM

        ticker = r.get("ticker", "?")
        hypo = _HYPO_CODE.get(r.get("hypo_id", ""), (r.get("hypo_id") or "?")[:5])
        sname = (r.get("strategy_name") or "?")[:14]
        reason = (r.get("exit_reason") or "")[:30]

        # Color reason by type
        rcolor = P_DIM
        if "tp" in reason.lower() or "take" in reason.lower():
            rcolor = P_GRN
        elif "sl" in reason.lower() or "stop" in reason.lower() or "loss" in reason.lower():
            rcolor = P_RED
        elif "max_hold" in reason or "time" in reason.lower():
            rcolor = P_YLW
        elif "rotate" in reason or "cold" in reason:
            rcolor = P_CYN

        row = (
            f"  {rpad(ago_str, 6)}{SP}"
            f"{c(rpad(ticker, 10), P_WHT):<10}{SP}"
            f"{c(rpad(hypo, 5), P_CYN):<5}{SP}"
            f"{c(rpad(sname, 14), P_GRY):<14}{SP}"
            f"{rpad(ep_str, 10)}{SP}"
            f"{rpad(xp_str, 10)}{SP}"
            f"{c(rpad(f'{pct:+.2f}%', 7), pct_color)}{SP}"
            f"{c(rpad(f'{net:+.2f}', 9), net_color)}{SP}"
            f"{rpad(f'{size_v:.0f}', 7)}{SP}"
            f"{rpad(hold_str, 6)}{SP}"
            f"{c(rpad(reason, 30), rcolor)}"
        )
        lines.append(pad(row, W))

    while len(lines) < n:
        lines.append(pad("", W))
    return lines[:n]
