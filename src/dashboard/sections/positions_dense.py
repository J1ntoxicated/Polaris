"""Polaris Ops — POSITIONS panel (dense, width-matched, like legacy).

Per-strategy contribution rows. Column widths sum exactly to W (full-width fill).
Legacy positions_columns.py pattern: ColWidths allocator → ultra/wide/narrow tiers.

User mandate: "컬럼 폭이랑 윈도우 폭이랑 내용 폭이랑 딱딱 맞춰서 다 쓰란".
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from src.dashboard.ansi import (
    B, P_GRN, P_RED, P_YLW, P_CYN, P_WHT, P_GRY, P_DIM, P_MAG, P_BLU, P_ORG,
    POLARIS_BLUE, STAR, STAR_O, STAR_4, STAR_4O, BLOCK, SHADE,
    c, pad, rpad, hline, fmt_age,
)
from src.dashboard.sections.header import _read_live


_STATE_GLYPH = {
    "hot": (STAR, P_GRN + B),
    "warm": (STAR_O, P_YLW),
    "cold": (STAR_4O, P_CYN),
    "losing": (STAR_4, P_RED + B),
    "?": ("·", P_DIM),
}

_HYPO_CODE = {
    "HYPO-007-RT": "007", "HYPO-008-RT": "008", "HYPO-023": "023",
    "HYPO-027": "027", "HYPO-028": "028", "HYPO-032": "032",
    "HYPO-036": "036", "HYPO-040": "040", "HYPO-NFI-001": "NFI",
}


@dataclass
class PosCols:
    """Width allocator — sum + separators MUST equal W."""
    LEAD: int      # 2 spaces lead
    TKR: int
    HYPO: int
    STRAT: int
    ST: int
    ENTRY: int
    LAST: int
    BAR: int
    PCT: int
    UPNL: int
    SIZE: int
    HELD: int
    SLIP: int
    FEE: int
    REG: int
    CONF: int
    STOPD: int
    FWDEV: int
    EXITS: int
    REASON: int  # absorbs remainder

    @property
    def n_cols(self) -> int:
        return 18  # not counting LEAD or REASON for separator math


def _alloc(W: int) -> PosCols:
    """Return ColWidths sized for terminal W. Sum + 18 separators = W.
    REASON column absorbs remainder so content fills full width.
    """
    if W >= 240:  # ultra
        c = PosCols(2, 12, 5, 20, 4, 13, 13, 12, 8, 10, 9, 7, 6, 6, 12, 6, 8, 8, 9, 0)
    elif W >= 200:  # wide (220 default)
        c = PosCols(2, 12, 5, 18, 4, 12, 12, 12, 8, 9, 8, 7, 6, 6, 10, 6, 7, 7, 9, 0)
    elif W >= 180:
        c = PosCols(2, 11, 5, 14, 3, 10, 10, 10, 7, 9, 7, 6, 5, 5, 9, 5, 6, 6, 7, 0)
    else:  # narrow
        c = PosCols(2, 10, 4, 10, 3, 9, 9, 8, 7, 8, 6, 5, 4, 4, 7, 4, 5, 5, 5, 0)
    used = (c.LEAD + c.TKR + c.HYPO + c.STRAT + c.ST + c.ENTRY + c.LAST
            + c.BAR + c.PCT + c.UPNL + c.SIZE + c.HELD + c.SLIP + c.FEE
            + c.REG + c.CONF + c.STOPD + c.FWDEV + c.EXITS + 18)
    c.REASON = max(0, W - used)
    return c


def _pnl_bar(pct: float, width: int) -> str:
    half = width // 2
    if abs(pct) < 0.001:
        return c(" " * (half - 1) + "│" + " " * half, P_DIM)
    clamp = max(-2.0, min(2.0, pct))
    norm = clamp / 2.0
    if norm > 0:
        filled = max(1, int(norm * half))
        return " " * half + c(BLOCK * filled, P_GRN + B) + " " * (half - filled)
    filled = max(1, int(-norm * half))
    return " " * (half - filled) + c(BLOCK * filled, P_RED + B) + " " * half


def _exit_summary(exits) -> str:
    if not exits:
        return "—"
    glyphs = []
    for e in exits:
        n = e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")
        if "take_profit" in n: glyphs.append(c("T", P_GRN))
        elif "stop_loss" in n: glyphs.append(c("S", P_RED))
        elif "trailing" in n: glyphs.append(c("R", P_CYN))
        elif "time_based" in n: glyphs.append(c("M", P_YLW))
        elif "signal_reversal" in n: glyphs.append(c("X", P_MAG))
        elif "partial" in n: glyphs.append(c("P", P_ORG))
    return "".join(glyphs)


def _build_header(cw: PosCols) -> str:
    SP = " "
    return (
        f"{' ' * cw.LEAD}"
        f"{c(rpad('Ticker', cw.TKR), P_GRY)}{SP}"
        f"{c(rpad('Hypo', cw.HYPO), P_GRY)}{SP}"
        f"{c(rpad('Strategy', cw.STRAT), P_GRY)}{SP}"
        f"{c(rpad('St', cw.ST), P_GRY)}{SP}"
        f"{c(rpad('Entry', cw.ENTRY), P_GRY)}{SP}"
        f"{c(rpad('Last', cw.LAST), P_GRY)}{SP}"
        f"{c(rpad('PnL', cw.BAR), P_GRY)}{SP}"
        f"{c(rpad('Δ%', cw.PCT), P_GRY)}{SP}"
        f"{c(rpad('uPnL$', cw.UPNL), P_GRY)}{SP}"
        f"{c(rpad('Size$', cw.SIZE), P_GRY)}{SP}"
        f"{c(rpad('Held', cw.HELD), P_GRY)}{SP}"
        f"{c(rpad('Slip', cw.SLIP), P_GRY)}{SP}"
        f"{c(rpad('Fee', cw.FEE), P_GRY)}{SP}"
        f"{c(rpad('Reg', cw.REG), P_GRY)}{SP}"
        f"{c(rpad('Conf', cw.CONF), P_GRY)}{SP}"
        f"{c(rpad('StopD%', cw.STOPD), P_GRY)}{SP}"
        f"{c(rpad('FwdEV', cw.FWDEV), P_GRY)}{SP}"
        f"{c(rpad('Exits', cw.EXITS), P_GRY)}"
        + (f"{SP}{c(rpad('Signal Reason', max(0, cw.REASON-1)), P_GRY)}" if cw.REASON > 1 else "")
    )


def _build_row(contr: dict, cur_price: float, ticker: str, is_first: bool, cw: PosCols, now_ms: int) -> str:
    SP = " "
    held_s = (now_ms - contr["open_ts_ms"]) / 1000
    held_str = fmt_age(held_s)
    change_pct = contr.get("unrealized_pct", 0) * 100
    unreal = contr.get("unrealized_usd", 0)
    state = contr.get("state", "?")
    glyph, gcolor = _STATE_GLYPH.get(state, _STATE_GLYPH["?"])
    ch_color = P_GRN + B if change_pct > 0 else P_RED + B if change_pct < 0 else P_DIM
    up_color = P_GRN + B if unreal > 0 else P_RED + B if unreal < 0 else P_DIM

    entry = contr.get("entry_price", 0)
    entry_str = f"{entry:.4f}" if entry < 1 else f"{entry:.2f}"
    last_str = f"{cur_price:.4f}" if cur_price < 1 else f"{cur_price:.2f}" if cur_price else "—"
    size_v = contr.get("size_usd", 0)

    ticker_disp = ticker if is_first else "  └─"
    ticker_color = P_WHT + B if is_first else P_DIM
    hypo_id = contr.get("hypo_id", "?")
    hypo_short = _HYPO_CODE.get(hypo_id, hypo_id[:cw.HYPO])
    sname = (contr.get("strategy", "?"))[: cw.STRAT]

    slip = contr.get("entry_slip_bps", 0)
    slip_str = f"{slip:.1f}b" if slip else "—"
    fee = size_v * 0.002
    fee_str = f"${fee:.2f}" if fee else "—"
    regime = (contr.get("regime") or "?")[: cw.REG]
    conf = contr.get("signal_confidence", 0)
    conf_str = f"{conf:.2f}" if conf else "—"
    conf_color = P_GRN if conf >= 0.7 else P_YLW if conf >= 0.5 else P_DIM

    # Stop distance: for SL exit if attached
    stopd_str = "—"
    for e in contr.get("exit_strategies", []):
        n = e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")
        if "stop_loss" in n:
            sl_pct = e.get("pct", 0) if isinstance(e, dict) else getattr(e, "pct", 0)
            if sl_pct:
                stopd_pct = (change_pct / 100 + sl_pct) * 100
                stopd_str = f"{stopd_pct:+.2f}%"
            break

    fwd_ev = contr.get("forward_ev_pct", 0) * 100 if contr.get("forward_ev_pct") else 0
    fwd_color = P_GRN + B if fwd_ev > 0.5 else P_YLW if fwd_ev > 0 else P_RED if fwd_ev < 0 else P_DIM
    fwd_str = f"{fwd_ev:+.2f}%" if fwd_ev else "—"

    exits = contr.get("exit_strategies", [])
    exits_str = _exit_summary(exits)
    reason = (contr.get("signal_reason") or "")

    line = (
        f"{' ' * cw.LEAD}"
        f"{c(rpad(ticker_disp[:cw.TKR], cw.TKR), ticker_color)}{SP}"
        f"{c(rpad(hypo_short, cw.HYPO), P_CYN)}{SP}"
        f"{c(rpad(sname, cw.STRAT), P_GRY)}{SP}"
        f"{c(rpad(glyph, cw.ST), gcolor)}{SP}"
        f"{rpad(entry_str, cw.ENTRY)}{SP}"
        f"{rpad(last_str, cw.LAST)}{SP}"
        f"{_pnl_bar(change_pct, cw.BAR)}{SP}"
        f"{c(rpad(f'{change_pct:+.2f}%', cw.PCT), ch_color)}{SP}"
        f"{c(rpad(f'{unreal:+.2f}', cw.UPNL), up_color)}{SP}"
        f"{rpad(f'{size_v:.0f}', cw.SIZE)}{SP}"
        f"{rpad(held_str, cw.HELD)}{SP}"
        f"{c(rpad(slip_str, cw.SLIP), P_DIM)}{SP}"
        f"{c(rpad(fee_str, cw.FEE), P_DIM)}{SP}"
        f"{c(rpad(regime, cw.REG), P_BLU)}{SP}"
        f"{c(rpad(conf_str, cw.CONF), conf_color)}{SP}"
        f"{c(rpad(stopd_str, cw.STOPD), P_DIM)}{SP}"
        f"{c(rpad(fwd_str, cw.FWDEV), fwd_color)}{SP}"
        f"{rpad(exits_str, cw.EXITS)}"
    )
    if cw.REASON > 1:
        line += f"{SP}{c(rpad(reason[:cw.REASON-1], cw.REASON-1), P_DIM)}"
    return line


def render(W: int, n: int = 18) -> list[str]:
    cw = _alloc(W)
    snap = _read_live()
    groups = snap.get("position_groups", [])

    lines: list[str] = [hline("OPEN POSITIONS (per-strategy slice)", W, POLARIS_BLUE)]
    lines.append(pad(_build_header(cw), W))

    if not groups:
        lines.append(pad(c("    (no open positions — waiting for signal)", P_DIM), W))
        while len(lines) < n:
            lines.append(pad("", W))
        return lines[:n]

    now_ms = int(_dt.datetime.now().timestamp() * 1000)
    groups.sort(key=lambda g: g.get("total_size_usd", 0), reverse=True)

    data_rows = n - 2
    rows_added = 0
    for grp in groups:
        if rows_added >= data_rows:
            break
        ticker = grp["ticker"]
        cur_price = grp.get("current_price", 0)
        for i, contr in enumerate(grp.get("contributions", [])):
            if rows_added >= data_rows:
                break
            row = _build_row(contr, cur_price, ticker, i == 0, cw, now_ms)
            lines.append(pad(row, W))
            rows_added += 1

    while len(lines) < n:
        lines.append(pad("", W))
    return lines[:n]
