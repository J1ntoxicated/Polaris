"""Polaris Intel — HEADER section.

Full-width 4 rows. Title / system status / broker / portfolio summary one-liner.
Polaris terminal aesthetic — ★ glyphs, P_ pastels, hline dividers.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from src.dashboard.ansi import (
    B, R, P_GRN, P_RED, P_YLW, P_CYN, P_WHT, P_GRY, P_DIM,
    POLARIS_BLUE, STAR, STAR_4, STAR_4O,
    c, pad, hline, badge, BG_G, BG_R, BG_BLK, BG_Y,
    fmt_age,
)

LIVE_JSON = Path(__file__).resolve().parent.parent.parent.parent / "data" / "paper" / "portfolio_live.json"


def _read_live() -> dict:
    if not LIVE_JSON.exists():
        return {}
    try:
        return json.loads(LIVE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _live_age_s() -> float:
    if not LIVE_JSON.exists():
        return -1
    return _dt.datetime.now().timestamp() - LIVE_JSON.stat().st_mtime


def render(W: int, tick: int = 0) -> list[str]:
    """4-row header.
    Row 0: title + ★ + datetime
    Row 1: broker / mode / max_size + cash + equity + dd
    Row 2: daily target + velocity + active hypos
    Row 3: hline divider
    """
    snap = _read_live()
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    age_s = _live_age_s()

    # Row 0: title
    star = c(STAR, POLARIS_BLUE + B)
    title = f"{star} {c('POLARIS', P_WHT + B)} {c('북극성', P_GRY)} {c('— Ops Dashboard', P_DIM)}"
    age_lbl = (
        c(" LIVE", P_GRN + B) if age_s < 60 and age_s >= 0
        else c(" STALE", P_YLW) if age_s < 300
        else c(" DEAD", P_RED + B)
    )
    right_part = c(now, P_GRY) + age_lbl
    row0 = pad(title, W - len(now) - 8) + right_part

    # Row 1: broker / cash / equity / dd
    broker_class = snap.get("broker_class", "—")
    broker_mode = snap.get("broker_mode", "")
    broker_color = P_CYN + B if "OKX" in broker_class else P_GRY
    broker_str = c(broker_class, broker_color)
    if broker_mode:
        mode_color = (
            BG_G + B if broker_mode == "DEMO" else BG_R + B
        )
        broker_str += " " + c(f" {broker_mode} ", mode_color)

    cash = snap.get("cash", 0)
    equity = snap.get("equity", 0)
    starting = snap.get("starting_cash", 0) or 1
    hwm = snap.get("hwm", equity)
    dd_pct = snap.get("drawdown_pct", 0) * 100
    realized = snap.get("realized_pnl", 0)

    eq_color = P_GRN + B if equity >= starting else P_RED + B
    real_color = P_GRN + B if realized >= 0 else P_RED + B
    dd_color = P_RED + B if dd_pct > 3 else P_YLW if dd_pct > 1 else P_GRN

    n_open = snap.get("n_open_contributions", 0)
    n_tickers = snap.get("n_unique_tickers", 0)

    row1 = pad(
        f"  {c('BROKER', P_DIM)} {broker_str}   "
        f"{c('EQUITY', P_DIM)} {c(f'${equity:,.2f}', eq_color)} "
        f"{c('cash', P_DIM)} ${cash:,.2f}  "
        f"{c('hwm', P_DIM)} ${hwm:,.2f}  "
        f"{c('dd', P_DIM)} {c(f'{dd_pct:+.2f}%', dd_color)}  "
        f"{c('open', P_DIM)} {c(str(n_open), P_WHT + B)} (×{n_tickers})  "
        f"{c('realized', P_DIM)} {c(f'${realized:+.2f}', real_color)}",
        W,
    )

    # Row 2: daily target + velocity + hypos
    daily = snap.get("daily_target", {})
    velocity = snap.get("velocity", {})
    active_hypos = snap.get("active_hypos", [])

    target_usd = daily.get("target_usd", 0)
    actual = daily.get("actual_today_usd", 0)
    progress = daily.get("progress_ratio", 0) * 100
    on_track = daily.get("on_track", False)
    n_today = daily.get("n_trades_today", 0)
    prog_color = P_GRN + B if on_track else P_YLW if progress > 30 else P_RED

    idle = velocity.get("cash_idle_ratio", 1.0) * 100
    turnover = velocity.get("turnover_today", 0)
    vdiag = velocity.get("diagnosis", "")
    diag_color = P_RED if vdiag and vdiag != "OK" else P_GRN

    row2 = pad(
        f"  {c('DAILY 0.5%', P_DIM)} target=${target_usd:.2f} "
        f"actual={c(f'${actual:+.2f}', P_GRN if actual >= 0 else P_RED)} "
        f"({c(f'{progress:+.1f}%', prog_color)}) trades={n_today}    "
        f"{c('VELOCITY', P_DIM)} idle={idle:.0f}% turnover={turnover} "
        f"{c(vdiag or 'OK', diag_color)}    "
        f"{c('HYPO', P_DIM)} {c(str(len(active_hypos)), P_WHT + B)}={','.join(h.replace('HYPO-', '') for h in active_hypos[:6])}",
        W,
    )

    # Row 3: hline
    row3 = hline("POLARIS OPS", W, POLARIS_BLUE)

    return [row0, row1, row2, row3]
