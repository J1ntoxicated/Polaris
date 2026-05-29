"""Polaris Dashboard v2 — Jin-readable (한국어, 큰 글자, 단순화).

Jin 2026-05-08 mandate: "뭔지 모르겠다 / 어케 보라는건지" → v1 (10 panels, 220×55,
trader-grade dense) 대신 한국어 큰 글자 핵심 6 섹션으로 재설계.

Layout (140 cols × 40 rows):
- 헤더 (1 row): 타이틀 + 시각 + DEMO 배지
- 핵심 지표 (5 rows): 오늘 PnL / 총 자산 / DD / Sharpe / 24h 거래수 (BIG)
- 시스템 상태 (5 rows): 정상/이상 / AI 호출 / 거래소 / fault / 활성 strategy
- TOP/BOTTOM 종목 (12 rows, 2-column): 잘됨 5 + 안됨 5
- 활성 포지션 (6 rows): 현재 열려있는 포지션 (overflow → "+N more")
- 최근 트레이드 (6 rows): 마지막 5건 청산
- 푸터 (1 row): refresh + 기타

색상: 초록=좋음, 빨강=나쁨, 노랑=주의, 회색=중립.
거래소 venue 분할 (OKX/Capital) 표시.

Read-only against ``data/polaris.sqlite``. Reuses ``polaris.scripts.dashboard.snapshot``.

Run: ``python3 -m polaris.scripts.dashboard_v2`` (Ctrl-C 종료).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from polaris.scripts.dashboard.ansi_palette import (
    BLOCK,
    BOLD,
    DIM,
    HIDE_CURSOR,
    HOME,
    INFO,
    MUTED,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    RESET,
    SHOW_CURSOR,
    WARNING,
    bar,
    color,
    sparkline,
)
from polaris.scripts.dashboard.snapshot import (
    DEFAULT_DB_PATH,
    DashboardSnapshot,
    collect_snapshot,
)

DEFAULT_REFRESH_SEC: Final[float] = 5.0
WIDTH: Final[int] = 140
HEIGHT: Final[int] = 40


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _fmt_money(v: float, *, signed: bool = False) -> str:
    sign = "+" if (signed and v >= 0) else ""
    abs_v = abs(v)
    if abs_v >= 1_000_000:
        return f"{sign}${v/1_000_000:.2f}M" if v >= 0 else f"-${abs_v/1_000_000:.2f}M"
    if abs_v >= 1_000:
        return f"{sign}${v/1_000:.2f}K" if v >= 0 else f"-${abs_v/1_000:.2f}K"
    return f"{sign}${v:,.2f}" if v >= 0 else f"-${abs_v:,.2f}"


def _fmt_pct(v: float, *, signed: bool = True) -> str:
    sign = "+" if (signed and v >= 0) else ""
    return f"{sign}{v:.2f}%"


def _color_pnl(v: float) -> str:
    if v > 0:
        return POSITIVE
    if v < 0:
        return NEGATIVE
    return NEUTRAL


def _ago(sec: float) -> str:
    if sec < 60:
        return f"{int(sec)}s"
    if sec < 3600:
        return f"{int(sec/60)}m"
    if sec < 86400:
        return f"{sec/3600:.1f}h"
    return f"{sec/86400:.1f}d"


def _pad(line: str, width: int = WIDTH) -> str:
    """Pad to width counting only visible chars (strip ANSI for length)."""
    import re
    visible = re.sub(r"\x1b\[[0-9;]*m", "", line)
    pad = max(0, width - len(visible))
    return line + " " * pad


def _hr(label: str = "", width: int = WIDTH) -> str:
    if label:
        head = f"━━━ {label} "
        return color(head + "━" * max(0, width - len(head)), MUTED)
    return color("━" * width, MUTED)


def _heat_tile(score: float) -> str:
    """2-char colored block whose hue encodes cell score (green=alpha, red=anti)."""
    if score >= 0.30:
        c = POSITIVE + BOLD
    elif score >= 0.05:
        c = POSITIVE
    elif score > -0.05:
        c = NEUTRAL
    elif score > -0.30:
        c = NEGATIVE
    else:
        c = NEGATIVE + BOLD
    return color(BLOCK * 2, c)


# ---------------------------------------------------------------------------
# Dynamic visualization (Task D — sparkline + gauges + heat tiles)
# ---------------------------------------------------------------------------


def render_equity_spark(snap: DashboardSnapshot) -> str:
    """1행 — 24h 자산 곡선 스파크라인 + Δ 주석 (데이터 없으면 graceful)."""
    label = color("  📈  24h 자산", NEUTRAL + BOLD)
    curve = snap.equity_curve
    if not curve:
        return _pad(f"{label}   {color('(데이터 수집 중…)', MUTED + DIM)}")
    spark = color(sparkline(curve, 70), INFO)
    delta = curve[-1] - curve[0]
    pct = (delta / curve[0] * 100.0) if curve[0] else 0.0
    lo, hi = min(curve), max(curve)
    ann = (
        color(f"  {_fmt_money(delta, signed=True)} ({_fmt_pct(pct)})", _color_pnl(delta) + BOLD)
        + color(f"   lo {_fmt_money(lo)} · hi {_fmt_money(hi)}", MUTED)
    )
    return _pad(f"{label}  {spark}{ann}")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def render_header(snap: DashboardSnapshot) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap.ts_now))
    title = color("  POLARIS", INFO + BOLD)
    badge = color(" [DEMO·PAPER] ", WARNING + BOLD)
    when = color(ts, NEUTRAL)
    sep = color(" │ ", MUTED)
    return _pad(f"{title}{badge}{sep}{when}{sep}{color('refresh 5s', MUTED)}")


def render_core_metrics(snap: DashboardSnapshot) -> list[str]:
    """5 rows — 오늘 PnL / 총 자산 / DD / Sharpe / 24h 거래수 (BIG)."""
    pnl = snap.daily_pnl_usd
    eq = snap.equity_now
    eq_delta = eq - snap.starting_capital
    dd = snap.drawdown_pct
    sharpe = snap.sharpe_24h
    trades = snap.daily_trades

    pnl_color_v = _color_pnl(pnl)
    eq_color_v = _color_pnl(eq_delta)
    dd_color_v = NEGATIVE if dd > 5 else (WARNING if dd > 1 else NEUTRAL)
    sharpe_color_v = POSITIVE if sharpe >= 1 else (WARNING if sharpe >= 0 else NEGATIVE)

    # BIG numbers — column 1 = label (15 wide), col 2 = value (25 wide), col 3 = sub (rest)
    label_w = 18

    rows = []
    rows.append(_pad(
        color("  💰  오늘 PnL".ljust(label_w), NEUTRAL + BOLD)
        + color(f"{_fmt_money(pnl, signed=True):>20}", pnl_color_v + BOLD)
        + color(f"   ({trades:,} 거래 · 24h)", MUTED)
    ))
    rows.append(_pad(
        color("  💼  총 자산".ljust(label_w), NEUTRAL + BOLD)
        + color(f"{_fmt_money(eq):>20}", eq_color_v + BOLD)
        + color(f"   시작 ${snap.starting_capital:,.0f}  ({_fmt_money(eq_delta, signed=True)})", MUTED)
    ))
    # DD gauge scaled to the -35% deep-drawdown checkpoint (full bar = 35%).
    dd_gauge = bar(min(dd / 35.0 * 100.0, 100.0), 14, fill_color=dd_color_v)
    rows.append(_pad(
        color("  📉  Drawdown".ljust(label_w), NEUTRAL + BOLD)
        + color(f"{_fmt_pct(-dd, signed=False):>20}", dd_color_v + BOLD)
        + f"  {dd_gauge}"
        + color(f"  peak ${snap.peak_equity:,.0f}", MUTED)
    ))
    rows.append(_pad(
        color("  🎯  Sharpe (24h)".ljust(label_w), NEUTRAL + BOLD)
        + color(f"{sharpe:>20.2f}", sharpe_color_v + BOLD)
        + color(f"   {'좋음' if sharpe >= 1 else '보통' if sharpe >= 0 else '주의'}", MUTED)
    ))
    exp_ratio = (snap.exposed_usd / eq * 100.0) if eq else 0.0
    exp_gauge = bar(
        min(exp_ratio, 100.0), 14,
        fill_color=WARNING if exp_ratio > 80 else NEUTRAL,
    )
    rows.append(_pad(
        color("  💱  활성 노출".ljust(label_w), NEUTRAL + BOLD)
        + color(f"{_fmt_money(snap.exposed_usd):>20}", NEUTRAL + BOLD)
        + f"  {exp_gauge}"
        + color(f"  {exp_ratio:.0f}% · open {snap.open_positions_n} · uPnL {_fmt_money(snap.upnl_total, signed=True)}", MUTED)
    ))
    return rows


def render_system_status(snap: DashboardSnapshot) -> list[str]:
    """5 rows — 시스템 상태 / AI 호출 / 거래소 / fault / 활성."""
    # System health: any alerts in last hour?
    err_alerts = sum(1 for a in snap.alerts if a.level in ("ERROR", "CRITICAL"))
    health_ok = err_alerts == 0

    # AI calls (sum over last 1h from gpt_stats)
    total_calls_h = sum(g.calls_per_h for g in snap.gpt_stats)
    total_cost_h = sum(g.cost_per_h_usd for g in snap.gpt_stats)
    total_cost_24h = sum(g.cost_24h_proj_usd for g in snap.gpt_stats)

    # Strategies HALTed (no halts table → assume 0 for now)
    n_strategies_active = len({s.strategy_id for s in snap.strategy_stats if s.open_n + s.closed_n > 0})

    # Cells / Universe focus
    cells_n = snap.active_cells_n
    universe_n = snap.universe_focus_n

    label_w = 18
    rows = []
    rows.append(_pad(
        color("  ⚙️  시스템 상태".ljust(label_w), NEUTRAL + BOLD)
        + (color("  ✅ 정상", POSITIVE + BOLD) if health_ok else color(f"  ⚠️  ERROR x{err_alerts}", NEGATIVE + BOLD))
        + color(f"     refresh {snap.universe_last_refresh}", MUTED)
    ))
    # AI budget gauge — 24h projected cost against a $50/day soft reference.
    ai_gauge = bar(
        min(total_cost_24h / 50.0 * 100.0, 100.0), 12,
        fill_color=WARNING if total_cost_24h > 40 else INFO,
    )
    rows.append(_pad(
        color("  🤖  AI 호출 (1h)".ljust(label_w), NEUTRAL + BOLD)
        + color(f"  {total_calls_h:>5,} calls", INFO + BOLD)
        + f"  {ai_gauge}"
        + color(f"  ${total_cost_h:.3f}/h → 24h ${total_cost_24h:,.2f} 예상", MUTED)
    ))
    rows.append(_pad(
        color("  📡  거래소".ljust(label_w), NEUTRAL + BOLD)
        + color(f"  OKX {snap.starting_capital_okx/1000:.0f}K · CAP {snap.starting_capital_capital/1000:.0f}K", NEUTRAL + BOLD)
        + color(f"     focus {universe_n} symbols · cells {cells_n}", MUTED)
    ))
    # Portfolio win-rate gauge (closed-trade weighted across strategies).
    closed_total = sum(s.closed_n for s in snap.strategy_stats)
    wr = (
        sum(s.wr_pct * s.closed_n for s in snap.strategy_stats) / closed_total
        if closed_total else 0.0
    )
    wr_gauge = bar(wr, 12, fill_color=POSITIVE if wr >= 50 else NEGATIVE)
    rows.append(_pad(
        color("  🚦  Strategies".ljust(label_w), NEUTRAL + BOLD)
        + color(f"  {n_strategies_active}/7 active", INFO + BOLD)
        + f"  {wr_gauge}"
        + color(f"  WR {wr:.0f}% · 0 HALTed · 0 faults", MUTED)
    ))
    # 4 regimes summary
    regime_str = " ".join(f"{r.regime}:{r.count}" for r in snap.regime_bars)
    rows.append(_pad(
        color("  🌊  Regime".ljust(label_w), NEUTRAL + BOLD)
        + color(f"  {regime_str or 'n/a'}", NEUTRAL)
    ))
    return rows


def render_top_bottom(snap: DashboardSnapshot) -> list[str]:
    """12 rows — TOP 5 / BOTTOM 5 cells (2-column side-by-side)."""
    top = snap.cell_top[:5]
    bot = snap.cell_bottom[:5]

    rows = [_pad(
        color("  🏆 TOP 5 잘됨", POSITIVE + BOLD).ljust(70 + 13)
        + color("  💀 BOTTOM 5 안됨", NEGATIVE + BOLD)
    )]
    rows.append(_pad(
        color(f"  {'ticker/strat/regime':<40} {'score':>7} {'n':>6}", MUTED)
        + color(f"   {'ticker/strat/regime':<40} {'score':>7} {'n':>6}", MUTED)
    ))

    for i in range(5):
        l_part = ""
        r_part = ""
        if i < len(top):
            t = top[i]
            l_label = f"{t.ticker}/{t.strategy}/{t.regime}"[:40]
            l_part = (
                f"  {_heat_tile(t.score)} "
                + color(f"{l_label:<40}", NEUTRAL)
                + color(f" {t.score:>+7.3f}", POSITIVE)
                + color(f" {int(t.n_eff):>6}", MUTED)
            )
        else:
            l_part = " " * (2 + 3 + 40 + 1 + 7 + 1 + 6)
        if i < len(bot):
            b = bot[i]
            r_label = f"{b.ticker}/{b.strategy}/{b.regime}"[:40]
            r_part = (
                f"   {_heat_tile(b.score)} "
                + color(f"{r_label:<40}", NEUTRAL)
                + color(f" {b.score:>+7.3f}", NEGATIVE)
                + color(f" {int(b.n_eff):>6}", MUTED)
            )
        rows.append(_pad(l_part + r_part))
    return rows


def render_open_positions(snap: DashboardSnapshot) -> list[str]:
    """6 rows — active positions (overflow → "+N more")."""
    pos = snap.positions[:5]
    rows = [_pad(
        color(f"  {'venue':<8} {'symbol':<14} {'strat':<18} {'side':<5} {'size':>10} {'uPnL':>12} {'Δ%':>7} {'held':>6}", MUTED)
    )]
    for p in pos:
        c = _color_pnl(p.upnl_usd)
        # row_count>1 = lifecycle-drift / scale-in indicator surfaced via [×N]
        # tag in WARNING color (B-P0-1, 2026-05-10).
        sym_label = f"{p.symbol}[×{p.row_count}]" if p.row_count > 1 else p.symbol
        sym_color = WARNING if p.row_count > 1 else NEUTRAL
        rows.append(_pad(
            color(f"  {p.venue:<8} ", NEUTRAL)
            + color(f"{sym_label:<14}", sym_color)
            + color(f" {p.strategy_id:<18} {p.side:<5} {_fmt_money(p.size_usd):>10}", NEUTRAL)
            + color(f" {_fmt_money(p.upnl_usd, signed=True):>12}", c)
            + color(f" {_fmt_pct(p.delta_pct):>7}", c)
            + color(f" {_ago(p.held_sec):>6}", MUTED)
        ))
    while len(rows) < 6:
        if len(rows) == 1 and not pos:
            rows.append(_pad(color("  (열린 포지션 없음 — 모두 청산됨)", MUTED + DIM)))
        else:
            rows.append(_pad(""))
    if len(snap.positions) > 5:
        rows[-1] = _pad(color(f"  +{len(snap.positions)-5} more positions...", MUTED))
    return rows


def render_recent_trades(snap: DashboardSnapshot) -> list[str]:
    """6 rows — 최근 5건 청산 트레이드."""
    trades = snap.recent_trades[:5]
    rows = [_pad(
        color(f"  {'시각':<10} {'venue':<8} {'symbol':<14} {'strat':<18} {'side':<5} {'PnL':>12} {'R':>6} {'reason':<10}", MUTED)
    )]
    now = snap.ts_now
    for t in trades:
        ago = _ago(now - t.ts_close)
        c = _color_pnl(t.pnl_usd)
        rows.append(_pad(
            color(f"  {ago:<10} {t.venue:<8} {t.symbol:<14} {t.strategy_id:<18} {t.side_close:<5}", NEUTRAL)
            + color(f" {_fmt_money(t.pnl_usd, signed=True):>12}", c)
            + color(f" {t.r_units:>+6.2f}", c)
            + color(f" {t.exit_reason[:10]:<10}", MUTED)
        ))
    while len(rows) < 6:
        if len(rows) == 1 and not trades:
            rows.append(_pad(color("  (최근 청산 트레이드 없음)", MUTED + DIM)))
        else:
            rows.append(_pad(""))
    return rows


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------


def render_dashboard_v2(snap: DashboardSnapshot) -> list[str]:
    rows: list[str] = []
    rows.append(render_header(snap))
    rows.append(render_equity_spark(snap))
    rows.append(_hr("핵심 지표"))
    rows.extend(render_core_metrics(snap))
    rows.append(_pad(""))
    rows.append(_hr("시스템 상태"))
    rows.extend(render_system_status(snap))
    rows.append(_pad(""))
    rows.append(_hr("종목 랭킹  (cell matrix)"))
    rows.extend(render_top_bottom(snap))
    rows.append(_pad(""))
    rows.append(_hr("활성 포지션"))
    rows.extend(render_open_positions(snap))
    rows.append(_pad(""))
    rows.append(_hr("최근 트레이드 (5건)"))
    rows.extend(render_recent_trades(snap))
    # Pad to HEIGHT
    while len(rows) < HEIGHT:
        rows.append(_pad(""))
    return rows[:HEIGHT]


def _emit_frame(rows: list[str]) -> None:
    sys.stdout.write(HOME)
    for r in rows:
        sys.stdout.write(r)
        sys.stdout.write("\n")
    sys.stdout.flush()


def run(db_path: Path, refresh_sec: float) -> int:
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.write("\x1b[2J")  # clear screen once
    sys.stdout.flush()
    try:
        while True:
            snap = collect_snapshot(db_path)
            rows = render_dashboard_v2(snap)
            _emit_frame(rows)
            time.sleep(refresh_sec)
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET + "\n")
        sys.stdout.flush()
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dashboard_v2")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--refresh", type=float, default=DEFAULT_REFRESH_SEC)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run(args.db, args.refresh)


if __name__ == "__main__":
    sys.exit(main())
