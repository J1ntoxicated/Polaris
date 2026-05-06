"""Polaris Terminal Dashboard — shell (P6).

Layout (full-screen, ~200 cols × ~60 rows):
- Header (1) — version + Codex Round 4 + INSIGHT-021 + system state
- Stats Summary (1) — 누적 PnL / win rate / signal_exit ratio (Round 4 효과)
- Realtime HYPOs table (HYPO-007~012, REALTIME_HYPOS)
- Daily Cron HYPOs table (HYPO-003/004 ACTIVE_HYPOS)
- Open Positions live (entry/held/cur PnL)
- Live trade events (latest 30, BALANCE filter)

Usage:
    python -m src.dashboard.cli                  # snapshot
    python -m src.dashboard.cli --refresh 5      # auto refresh

References:
- INSIGHT-021 flip-flop fee bleed fix (Round 4)
- ADR-012 realtime architecture
- ADR-013 HARNESS Meta Mode
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import requests
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.paper.cron import ACTIVE_HYPOS
from src.paper.realtime_runner import REALTIME_HYPOS, MIN_HOLD_MS, RE_ENTRY_COOLDOWN_MS
from src.paper.runner import load_state

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VAULT_LOG_DIR = PROJECT_ROOT / "vault" / "50_runtime"
DATA_PAPER_DIR = PROJECT_ROOT / "data" / "paper"
CRON_LOG = DATA_PAPER_DIR / "cron.log"
REALTIME_ERR = DATA_PAPER_DIR / "realtime.err"

# Round 4 fix restart cutoff (epoch ms)
ROUND4_CUTOFF_MS = 1777845919_000

console = Console()

# 톤다운 회색 — 모든 panel/table border 일관 (Jin mandate)
BORDER = "grey42"
# ASCII box (pure ASCII +-| chars — Unicode wide-char 렌더링 차이 완벽 회피)
BOX = box.ASCII

# ── OKX live ticker cache (1s TTL — matches dashboard refresh) ────────────────
_TICKER_CACHE = {"ts": 0.0, "data": {}}
OKX_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"


def fetch_live_tickers() -> dict[str, float]:
    """OKX SPOT tickers — single batch fetch, cached 1s."""
    if time.time() - _TICKER_CACHE["ts"] < 1.0 and _TICKER_CACHE["data"]:
        return _TICKER_CACHE["data"]
    try:
        r = requests.get(OKX_TICKERS_URL, timeout=3)
        rows = r.json().get("data", [])
        _TICKER_CACHE["data"] = {row["instId"]: float(row["last"]) for row in rows}
        _TICKER_CACHE["ts"] = time.time()
    except Exception:
        pass
    return _TICKER_CACHE["data"]


def _strategy_name(hypo: dict) -> str:
    cls = hypo.get("strategy_cls") or hypo.get("strategy")
    return cls(**hypo.get("params", hypo.get("strategy_params", {}))).name


def fmt_price(price: float) -> str:
    """Format price without scientific notation — tier by magnitude.

    Pure function (P6). No I/O, deterministic.

    Tiers:
        >= 1000  : comma-separated, 2 dp  e.g. BTC  79,134.50
        >= 10    : 3 dp                   e.g. SOL  150.123
        >= 0.1   : 4 dp                   e.g. DOGE   0.1083
        >= 0.001 : 5 dp                   e.g. SUI    0.00920
        else     : 8 dp                   e.g. PEPE   0.00000392
    """
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 10:
        return f"{price:.3f}"
    elif price >= 0.1:
        return f"{price:.4f}"
    elif price >= 0.001:
        return f"{price:.5f}"
    else:
        return f"{price:.8f}"


# ───── Header ─────


def render_header() -> Panel:
    now = _dt.datetime.now().isoformat(timespec="seconds")
    runner_pid = _runner_pid()
    runner_status = f"[green]●[/green] PID {runner_pid}" if runner_pid else "[red]✗ down[/red]"
    body = (
        f"[bold cyan]Polaris[/bold cyan] [dim]│[/dim] {now} [dim]│[/dim] "
        f"runner: {runner_status} [dim]│[/dim] "
        f"min_hold {MIN_HOLD_MS//1000}s [dim]│[/dim] cooldown {RE_ENTRY_COOLDOWN_MS//1000}s\n"
        f"[dim]Codex Round 4 (74% ACCEPT WITH CONDITIONS)[/dim] [dim]│[/dim] "
        f"INSIGHT-021 flip-flop fix [dim]│[/dim] "
        f"ADR-013 HARNESS Meta Mode"
    )
    return Panel(body, border_style=BORDER, box=BOX, padding=(0, 1))


def _runner_pid() -> int:
    """Read launchctl status — return PID if active."""
    try:
        import subprocess
        r = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=2
        )
        for line in r.stdout.splitlines():
            if "com.polaris.paper.realtime" in line:
                parts = line.split()
                pid = parts[0]
                if pid.isdigit():
                    return int(pid)
    except Exception:
        pass
    return 0


# ───── Stats summary ─────


def _scan_all_states() -> tuple[list[dict], list[dict]]:
    """Return (all_closed, all_open) across every paper position.

    Phase 17: read from SQL ledger (SSOT). Falls back to JSON file scan
    if ledger unavailable (graceful degradation).
    """
    # Phase 17: SQL primary
    try:
        return _scan_from_ledger()
    except Exception as e:
        # Fall back to JSON scan
        pass

    closed_all, open_all = [], []
    for f in sorted(DATA_PAPER_DIR.glob("paper_state_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = f.stem.replace("paper_state_", "")
        for p in d.get("closed_positions", []):
            p["_strategy_file"] = name
            closed_all.append(p)
        for p in d.get("open_positions", []):
            p["_strategy_file"] = name
            open_all.append(p)
    return closed_all, open_all


def _equity_from_ledger(tickers_map: dict) -> tuple[float, float, float]:
    """Phase 19: total cash + open size + unrealized from SQL ledger (SSOT).

    Returns (total_cash_usd, total_open_size_usd, total_unrealized_usd).
    Raises on ledger error (caller falls back to JSON).
    """
    from pathlib import Path
    from src.persist.ledger import TradeLedger
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "polaris.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    with TradeLedger(db_path) as led:
        total_cash = led.conn.execute(
            "SELECT COALESCE(SUM(cash_usd), 0) AS s FROM balances"
        ).fetchone()["s"]
        opens = led.conn.execute(
            "SELECT ticker, direction, entry_price, entry_size_usd, fee_round_trip "
            "FROM positions WHERE status = 'open'"
        ).fetchall()
    total_open_size = sum(r["entry_size_usd"] for r in opens)
    total_unrealized = 0.0
    for r in opens:
        cur = tickers_map.get(r["ticker"])
        if cur:
            gross = r["direction"] * (cur - r["entry_price"]) / r["entry_price"]
            total_unrealized += (gross - r["fee_round_trip"]) * r["entry_size_usd"]
    return float(total_cash), float(total_open_size), float(total_unrealized)


def _scan_from_ledger() -> tuple[list[dict], list[dict]]:
    """Phase 17: query SQL ledger for all positions (SSOT)."""
    from pathlib import Path
    from src.persist.ledger import TradeLedger
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "polaris.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    closed_all, open_all = [], []
    with TradeLedger(db_path) as led:
        rows = led.conn.execute(
            "SELECT * FROM positions"
        ).fetchall()
    for r in rows:
        # Adapt SQL row to legacy dict format
        p = {
            "position_id": r["position_id"],
            "ticker": r["ticker"],
            "direction": r["direction"],
            "entry_price": r["entry_price"],
            "exit_price": r["exit_price"] or 0.0,
            "size_usd": r["entry_size_usd"],
            "open_ts_ms": r["open_ts_ms"],
            "close_ts_ms": r["close_ts_ms"] or 0,
            "fee_round_trip": r["fee_round_trip"],
            "_strategy_file": (
                r["ticker"].lower() + "_" + r["strategy_name"]
            ),
            "status": r["status"],
        }
        if r["status"] == "open":
            open_all.append(p)
        else:
            closed_all.append(p)
    return closed_all, open_all


def _read_portfolio_live() -> dict:
    """Phase 24 — read live portfolio JSON snapshot from runner."""
    p = DATA_PAPER_DIR / "portfolio_live.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def render_portfolio_summary() -> Panel:
    """Phase 24 — Polaris real trading bot architecture summary.

    Replaces legacy stats_summary. Shows:
    - Cash / Equity / Drawdown / HWM (single account, Phase 20)
    - Daily 0.5% target progress (Phase 22)
    - Capital velocity (Phase 22.3)
    - Broker status (Phase 14.2)
    - PM orchestrator activity (Phase 23.5)
    """
    snap = _read_portfolio_live()
    if not snap:
        return Panel("[dim]Waiting for runner snapshot…[/dim]",
                     title="Portfolio (Phase 20-23)",
                     border_style=BORDER, box=BOX, padding=(0, 1))

    cash = snap.get("cash", 0)
    equity = snap.get("equity", 0)
    starting = snap.get("starting_cash", 5000)
    hwm = snap.get("hwm", equity)
    dd = snap.get("drawdown_pct", 0) * 100
    realized = snap.get("realized_pnl", 0)
    n_open = snap.get("n_open_contributions", 0)
    n_tickers = snap.get("n_unique_tickers", 0)
    broker_class = snap.get("broker_class", "—")
    broker_mode = snap.get("broker_mode", "")

    daily = snap.get("daily_target", {})
    target = daily.get("target_usd", 0)
    actual = daily.get("actual_today_usd", 0)
    prog = daily.get("progress_ratio", 0) * 100
    n_today = daily.get("n_trades_today", 0)
    on_track = daily.get("on_track", False)

    velocity = snap.get("velocity", {})
    idle = velocity.get("cash_idle_ratio", 0) * 100
    turnover = velocity.get("turnover_today", 0)
    vscore = velocity.get("score", 0)
    vdiag = velocity.get("diagnosis", "")

    pm = snap.get("pm_stats", {})

    # Color helpers
    eq_color = "green" if equity >= starting else "red"
    real_color = "green" if realized >= 0 else "red"
    prog_color = "green" if on_track else ("yellow" if prog > 30 else "red")
    dd_color = "red" if dd > 3 else ("yellow" if dd > 1 else "green")
    broker_color = "magenta" if "OKX" in broker_class else "cyan"
    broker_label = (
        f"[{broker_color}]{broker_class}[/{broker_color}]"
        + (f" ({broker_mode})" if broker_mode else "")
    )

    line1 = (
        f"[bold]EQUITY[/bold] [{eq_color}]${equity:,.2f}[/{eq_color}] "
        f"cash=${cash:,.2f} hwm=${hwm:,.2f} "
        f"dd=[{dd_color}]{dd:.2f}%[/{dd_color}]  "
        f"open=[bold]{n_open}[/bold] (×{n_tickers} ticker)  "
        f"realized=[{real_color}]${realized:+.2f}[/{real_color}]  "
        f"broker={broker_label}"
    )
    line2 = (
        f"[bold]DAILY 0.5%[/bold] target=${target:.2f} actual=${actual:+.2f} "
        f"([{prog_color}]{prog:+.1f}%[/{prog_color}]) trades={n_today}  "
        f"[bold]VELOCITY[/bold] idle={idle:.0f}% turnover={turnover} "
        f"score={vscore} {vdiag}"
    )
    pm_line = ""
    if pm:
        pm_line = (
            f"\n[bold]PM CYCLE[/bold] eval={pm.get('n_evaluated', 0)} "
            f"hold={pm.get('n_holds', 0)} close={pm.get('n_closes', 0)} "
            f"rotate={pm.get('n_rotates', 0)} add={pm.get('n_adds', 0)}"
        )
        opps = pm.get("top_opportunities", [])
        if opps:
            top3 = ", ".join(
                f"{o['ticker']}/{o['strategy']}({o['expected_return_pct']*100:+.2f}%)"
                for o in opps[:3]
            )
            pm_line += f"  top: {top3}"

    body = line1 + "\n" + line2 + pm_line
    return Panel(body, title="Polaris Real Trading Bot (Phase 20-23)",
                 border_style=BORDER, box=BOX, padding=(0, 1))


def render_position_groups() -> Table:
    """Phase 24 — position grouping by ticker (AggregatedPosition + contributions).

    Each row = 1 contribution slice. Ticker grouped, strategy attribution visible.
    State (HOT/WARM/COLD/LOSING) from PositionEvaluator.
    """
    table = Table(
        title="Position Groups (per-ticker contributions, with HOT/WARM/COLD state)",
        show_header=True, header_style="bold yellow",
        expand=True, title_justify="left",
        border_style=BORDER, box=BOX,
    )
    table.add_column("Ticker", justify="left", no_wrap=True, max_width=12)
    table.add_column("Strategy", justify="left", no_wrap=True, max_width=18)
    table.add_column("State", justify="center", no_wrap=True, max_width=7)
    table.add_column("Entry $", justify="right", no_wrap=True, max_width=12)
    table.add_column("Last $", justify="right", no_wrap=True, max_width=12)
    table.add_column("Δ %", justify="right", no_wrap=True, max_width=8)
    table.add_column("uPnL $", justify="right", no_wrap=True, max_width=8)
    table.add_column("Size $", justify="right", no_wrap=True, max_width=7)
    table.add_column("Held", justify="right", no_wrap=True, max_width=7)

    snap = _read_portfolio_live()
    groups = snap.get("position_groups", [])
    if not groups:
        table.add_row("—", "—", "—", "—", "—", "—", "—", "—", "—")
        return table

    now_ms = int(_dt.datetime.now().timestamp() * 1000)
    state_styles = {
        "hot": "bold green", "warm": "yellow",
        "cold": "cyan", "losing": "bold red", "?": "dim",
    }

    # Sort groups by total_size desc
    groups.sort(key=lambda g: g.get("total_size_usd", 0), reverse=True)

    for grp in groups:
        ticker = grp["ticker"]
        cur_price = grp.get("current_price", 0)
        for i, c in enumerate(grp.get("contributions", [])):
            held_s = (now_ms - c["open_ts_ms"]) / 1000
            held_str = f"{held_s:.0f}s" if held_s < 3600 else f"{held_s/3600:.1f}h"
            change_pct = c.get("unrealized_pct", 0) * 100
            unrealized = c.get("unrealized_usd", 0)
            state = c.get("state", "?")
            state_style = state_styles.get(state, "dim")
            ch_style = "green" if change_pct > 0 else ("red" if change_pct < 0 else "dim")
            upnl_style = "green" if unrealized >= 0 else "red"

            # Show ticker only on first contribution of the group
            ticker_disp = ticker if i == 0 else f"  └─"
            table.add_row(
                ticker_disp,
                c["strategy"][:18],
                Text(state.upper(), style=state_style),
                fmt_price(c["entry_price"]),
                fmt_price(cur_price) if cur_price else "—",
                Text(f"{change_pct:+.2f}%", style=ch_style),
                Text(f"{unrealized:+.2f}", style=upnl_style),
                f"{c['size_usd']:.0f}",
                held_str,
            )
    return table


def render_stats_summary() -> Panel:
    closed, open_pos = _scan_all_states()

    def _net_usd(p: dict) -> float:
        gross = p["direction"] * (p["exit_price"] - p["entry_price"]) / p["entry_price"]
        return (gross - p["fee_round_trip"]) * p["size_usd"]

    pre = [p for p in closed if p["open_ts_ms"] < ROUND4_CUTOFF_MS]
    post = [p for p in closed if p["open_ts_ms"] >= ROUND4_CUTOFF_MS]

    def _stats(pp: list[dict]) -> dict:
        if not pp:
            return {"n": 0, "sum": 0.0, "wins": 0, "tp": 0, "sl": 0, "sig": 0, "avg_held_s": 0}
        wins = sum(1 for p in pp if _net_usd(p) > 0)
        tp = sum(1 for p in pp if (p["exit_price"] - p["entry_price"]) / p["entry_price"] >= 0.006)
        sl = sum(1 for p in pp if (p["exit_price"] - p["entry_price"]) / p["entry_price"] <= -0.0035)
        return {
            "n": len(pp), "sum": sum(_net_usd(p) for p in pp),
            "wins": wins, "tp": tp, "sl": sl, "sig": len(pp) - tp - sl,
            "avg_held_s": sum((p["close_ts_ms"] - p["open_ts_ms"]) / 1000 for p in pp) / len(pp),
        }

    pre_s = _stats(pre)
    post_s = _stats(post)

    def _row(label: str, s: dict) -> str:
        if s["n"] == 0:
            return f"[bold]{label}[/bold] (0 trades)"
        pnl_color = "green" if s["sum"] >= 0 else "red"
        wr = 100 * s["wins"] / s["n"]
        wr_color = "green" if wr >= 50 else ("yellow" if wr >= 30 else "red")
        return (
            f"[bold]{label}[/bold] "
            f"n={s['n']:3d} "
            f"PnL=[{pnl_color}]${s['sum']:+8.2f}[/{pnl_color}] "
            f"avg=${s['sum']/s['n']:+5.2f} "
            f"win=[{wr_color}]{wr:4.0f}%[/{wr_color}] "
            f"(tp={s['tp']:2d}/sl={s['sl']:2d}/sig={s['sig']:3d}) "
            f"avg_held={s['avg_held_s']:5.0f}s"
        )

    # Aggregate equity (cash + open size) — 트레이더가 봐야 할 핵심
    tickers_map = fetch_live_tickers()
    total_cash = 0.0
    total_open_size = 0.0
    total_unrealized = 0.0
    # Phase 19: total equity from SQL ledger (SSOT). Falls back to JSON scan
    # if ledger unavailable (graceful degradation).
    try:
        total_cash, total_open_size, total_unrealized = _equity_from_ledger(tickers_map)
    except Exception:
        for f in DATA_PAPER_DIR.glob("paper_state_*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            total_cash += d.get("cash_usd", 0.0)
            for p in d.get("open_positions", []):
                total_open_size += p["size_usd"]
                cur = tickers_map.get(p["ticker"])
                if cur:
                    gross = p["direction"] * (cur - p["entry_price"]) / p["entry_price"]
                    total_unrealized += (gross - p["fee_round_trip"]) * p["size_usd"]
    total_realized = sum(_net_usd(p) for p in closed)
    total_equity = total_cash + total_open_size + total_unrealized
    realized_color = "green" if total_realized >= 0 else "red"
    unreal_color = "green" if total_unrealized >= 0 else "red"

    body = (
        f"[bold]TOTAL[/bold] equity=${total_equity:,.0f} "
        f"cash=${total_cash:,.0f} "
        f"open_size=${total_open_size:,.0f} ({len(open_pos)} pos) "
        f"realized=[{realized_color}]${total_realized:+8.2f}[/{realized_color}] "
        f"unrealized=[{unreal_color}]${total_unrealized:+6.2f}[/{unreal_color}]\n"
        f"{_row('PRE  Round 4', pre_s)}\n"
        f"{_row('POST Round 4', post_s)}"
    )
    title = "Account & Realized PnL (PRE = pre-Round4 / POST = post-Round4)"
    return Panel(body, title=title, border_style=BORDER, box=BOX, padding=(0, 1))


# ───── Realtime HYPOs (HYPO-007~012) ─────


def render_realtime_hypos() -> Table:
    table = Table(
        title="Realtime HYPOs (WebSocket tick-driven)",
        show_header=True, header_style="bold magenta",
        expand=True, title_justify="left",
        border_style=BORDER, box=BOX,
    )
    table.add_column("HYPO", justify="left", no_wrap=True, max_width=14)
    table.add_column("Strategy", justify="left", no_wrap=True, max_width=22)
    table.add_column("TF", justify="left", no_wrap=True, max_width=5)
    table.add_column("Tickers", justify="left", no_wrap=True, max_width=22, overflow="ellipsis")
    table.add_column("Open", justify="right", no_wrap=True)
    table.add_column("Closed", justify="right", no_wrap=True)
    table.add_column("Cash $", justify="right", no_wrap=True)
    table.add_column("PnL $", justify="right", no_wrap=True)
    table.add_column("Win%", justify="right", no_wrap=True)

    for hypo in REALTIME_HYPOS:
        sname = _strategy_name(hypo)
        n_open = n_closed = 0
        cash_sum = pnl_sum = 0.0
        wins = 0
        for ticker in hypo["tickers"]:
            try:
                bal = load_state(ticker, sname, starting_usd=hypo["starting_usd"])
            except Exception:
                continue
            n_open += bal.n_open
            n_closed += bal.n_closed
            cash_sum += bal.cash_usd
            pnl_sum += bal.realized_pnl_usd
            for p in bal.closed_positions:
                gross = p.direction * (p.exit_price - p.entry_price) / p.entry_price
                if (gross - p.fee_round_trip) > 0:
                    wins += 1
        wr = (100 * wins / n_closed) if n_closed > 0 else 0.0
        wr_style = "green" if wr >= 50 else ("yellow" if wr >= 30 else ("red" if n_closed > 0 else "dim"))
        pnl_style = "green" if pnl_sum >= 0 else "red"
        # Tickers: count + 첫 2개 (cell wrap 방지)
        first_two = ",".join(t.split("-")[0] for t in hypo['tickers'][:2])
        ticker_str = f"{len(hypo['tickers'])}({first_two})"
        table.add_row(
            hypo["hypo_id"], sname, hypo.get("primary_tf", "?"), ticker_str,
            str(n_open), str(n_closed),
            f"{cash_sum:,.0f}",
            Text(f"{pnl_sum:+,.2f}", style=pnl_style),
            Text(f"{wr:.0f}", style=wr_style) if n_closed else Text("—", style="dim"),
        )
    return table


# ───── Daily Cron HYPOs (HYPO-003/004) ─────


def render_cron_hypos() -> Table:
    table = Table(
        title="Daily Cron HYPOs (1d candle, 01:00 UTC)",
        show_header=True, header_style="bold cyan",
        expand=True, title_justify="left",
        border_style=BORDER, box=BOX,
    )
    table.add_column("HYPO", justify="left", no_wrap=True, max_width=14)
    table.add_column("Ticker", justify="left", no_wrap=True, max_width=14)
    table.add_column("Strategy", justify="left", no_wrap=True, max_width=22)
    table.add_column("Cash $", justify="right", no_wrap=True)
    table.add_column("Open", justify="right", no_wrap=True)
    table.add_column("Closed", justify="right", no_wrap=True)
    table.add_column("PnL $", justify="right", no_wrap=True)

    for hypo in ACTIVE_HYPOS:
        sname = _strategy_name(hypo)
        for ticker in hypo["tickers"]:
            try:
                bal = load_state(ticker, sname, starting_usd=hypo["starting_usd"])
                pnl = bal.realized_pnl_usd
                pnl_style = "green" if pnl >= 0 else ("red" if pnl < 0 else "dim")
                table.add_row(
                    hypo["hypo_id"], ticker, sname,
                    f"{bal.cash_usd:,.0f}",
                    str(bal.n_open), str(bal.n_closed),
                    Text(f"{pnl:+,.2f}", style=pnl_style),
                )
            except Exception as e:
                table.add_row(hypo["hypo_id"], ticker, sname, "—", "—", "—", f"err: {e}"[:30])
    return table


# ───── Open Positions (live) ─────


def render_open_positions() -> Table:
    table = Table(
        title="Open Positions (live tick price)",
        show_header=True, header_style="bold yellow",
        expand=True, title_justify="left",
        border_style=BORDER, box=BOX,
    )
    table.add_column("Ticker", justify="left", no_wrap=True, max_width=14)
    table.add_column("Strategy", justify="left", no_wrap=True, max_width=22)
    table.add_column("Entry $", justify="right", no_wrap=True, max_width=14)
    table.add_column("Last $", justify="right", no_wrap=True, max_width=14)
    table.add_column("Δ %", justify="right", no_wrap=True, max_width=8)
    table.add_column("uPnL $", justify="right", no_wrap=True, max_width=8)
    table.add_column("Size $", justify="right", no_wrap=True, max_width=8)
    table.add_column("Held", justify="right", no_wrap=True, max_width=7)

    _, open_pos = _scan_all_states()
    if not open_pos:
        table.add_row("—", "—", "—", "—", "—", "—", "—", "—")
        return table

    tickers_map = fetch_live_tickers()
    now_ms = int(_dt.datetime.now().timestamp() * 1000)
    open_pos.sort(key=lambda p: p["open_ts_ms"], reverse=True)

    for p in open_pos:
        held_s = (now_ms - p["open_ts_ms"]) / 1000
        held_str = f"{held_s:.0f}s" if held_s < 3600 else f"{held_s/3600:.1f}h"
        held_style = "yellow" if held_s > 1800 else "white"
        # Strategy name from file: paper_state_{ticker}_{sname}.json — strip ticker prefix
        # _strategy_file = "btc-usdt_trade_flow" → split first "_usdt_" → "trade_flow"
        sf = p["_strategy_file"]
        sname = sf.split("_usdt_", 1)[1] if "_usdt_" in sf else sf.split("_", 1)[1] if "_" in sf else sf

        cur_price = tickers_map.get(p["ticker"])
        if cur_price:
            change_pct = (cur_price - p["entry_price"]) / p["entry_price"] * 100
            unrealized_usd = (change_pct / 100 - p["fee_round_trip"]) * p["size_usd"]
            # Color: green if past TP (+0.6%), red if past SL (-0.35%), yellow else
            if change_pct >= 0.6:
                ch_style = "bold green"
            elif change_pct <= -0.35:
                ch_style = "bold red"
            elif change_pct > 0:
                ch_style = "green"
            else:
                ch_style = "red"
            cur_str = fmt_price(cur_price)
            ch_str = f"{change_pct:+.2f}%"
            upnl_style = "green" if unrealized_usd >= 0 else "red"
            upnl_str = f"{unrealized_usd:+.2f}"
        else:
            cur_str = "—"
            ch_str = "—"
            ch_style = "dim"
            upnl_str = "—"
            upnl_style = "dim"

        table.add_row(
            p["ticker"], sname,
            fmt_price(p["entry_price"]),
            cur_str,
            Text(ch_str, style=ch_style),
            Text(upnl_str, style=upnl_style),
            f"{p['size_usd']:.0f}",
            Text(held_str, style=held_style),
        )
    return table


# ───── Live trade events ─────


def render_live_logs(max_lines: int = 25, hide_balance: bool = True) -> Panel:
    # Dynamic truncation: terminal width - timestamp(8) - src(14) - panel borders + safety
    try:
        msg_max = max(40, console.size.width - 50)
    except Exception:
        msg_max = 100
    events: list[tuple[str, str, str]] = []

    if VAULT_LOG_DIR.exists():
        for log_file in VAULT_LOG_DIR.glob("paper_log_*.md"):
            label = log_file.name.replace("paper_log_", "").replace(".md", "")
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines[-100:]:
                if line.startswith("| 20"):
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) >= 3:
                        if hide_balance and parts[1] == "BALANCE":
                            continue
                        events.append((parts[0], label[:28], f"[{parts[1]}] {parts[2]}"))

    # Realtime err log — Phase 24: capture all new event types
    if REALTIME_ERR.exists():
        try:
            tail = REALTIME_ERR.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
        except OSError:
            tail = []
        # Phase 24 event tags to surface in dashboard
        watch_tags = (
            "OPEN", "CLOSE", "POS-EXIT", "PM-CYCLE", "PM-CLOSE", "PM-ROTATE",
            "PM-ADD", "PM-ROTATE-FAIL", "PORTFOLIO-HALT", "REGIME-BLOCK",
            "SPREAD-SKIP", "LIQ-SKIP", "BROKER", "PORTFOLIO", "PM-ORCH",
            "POLICY-UPDATE",
        )
        tag_pattern = "|".join(re.escape(t) for t in watch_tags)
        evt_re = re.compile(
            rf"^(\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}})[,.]?\d* "
            rf"\[(\w+)\] \[({tag_pattern})\]\s+(.+)$"
        )
        # Aggregate noisy SPREAD-SKIP / REGIME-BLOCK into hourly counts (don't spam)
        skip_counts: dict[str, int] = {}
        for line in tail:
            m = evt_re.match(line)
            if not m:
                continue
            tag = m.group(3)
            ts = m.group(1)
            if tag in ("SPREAD-SKIP", "REGIME-BLOCK"):
                skip_counts[tag] = skip_counts.get(tag, 0) + 1
                continue
            events.append(
                (ts.replace(" ", "T"), "runner", f"[{tag}] {m.group(4)}")
            )
        if skip_counts:
            events.append((
                _dt.datetime.now().isoformat(timespec="seconds"),
                "aggregator",
                "[SKIPS] " + ", ".join(f"{k}={v}" for k, v in skip_counts.items()),
            ))

    events.sort(key=lambda x: x[0], reverse=True)
    latest = events[:max_lines]

    if not latest:
        body = "[dim](no trade events yet)[/dim]"
    else:
        lines = []
        for ts, src, msg in latest:
            if "[OPEN]" in msg:
                color = "yellow bold"
            elif "[CLOSE]" in msg and "net=+" in msg:
                color = "green bold"
            elif "[CLOSE]" in msg and "net=-" in msg:
                color = "red bold"
            elif "[PM-ROTATE]" in msg:
                color = "cyan bold"
            elif "[PM-ADD]" in msg:
                color = "blue bold"
            elif "[PM-CLOSE]" in msg:
                color = "magenta"
            elif "[PORTFOLIO-HALT]" in msg or "BREACH" in msg:
                color = "magenta bold"
            elif "[BROKER]" in msg or "[PORTFOLIO]" in msg or "[PM-ORCH]" in msg:
                color = "cyan"
            elif "[POLICY-UPDATE]" in msg:
                color = "yellow"
            else:
                color = "white"
            lines.append(f"[dim]{ts[-8:]}[/dim] [{color}]{src:14s}[/{color}] {msg[:msg_max]}")
        body = "\n".join(lines)
    title = f"Trade Events (latest {max_lines}, BALANCE hidden)"
    return Panel(body, title=title, border_style=BORDER, box=BOX, padding=(0, 1))


# ───── Compose ─────


def _dynamic_log_lines() -> int:
    """Trade events line count = remaining terminal rows after fixed sections.

    Fixed sections (estimated):
      Header        4
      Stats         4
      Realtime  ~13 (6 hypos + header + footer + padding)
      Cron      ~10 (5 cron rows + header + footer + padding)
      Open      ~14 (~10 positions + header + footer)
      Trade hdr  3
      Padding    4
    Sum ≈ 52 → events fits in (rows - 52).
    """
    try:
        rows = console.size.height
    except Exception:
        rows = 50
    return max(5, rows - 52)


def render_dashboard() -> Group:
    """Single-frame composite — no clear, no flicker."""
    # Debug: write console size + actual rendered line widths
    try:
        debug_file = DATA_PAPER_DIR / "dashboard_size.log"
        if not debug_file.exists() or time.time() - debug_file.stat().st_mtime > 30:
            import io
            from rich.console import Console as _C
            buf = io.StringIO()
            test_c = _C(width=console.size.width, file=buf, force_terminal=False, no_color=True)
            test_c.print(Group(
                render_header(), render_portfolio_summary(),
                render_position_groups(),
                render_realtime_hypos(), render_cron_hypos(),
                render_stats_summary(), render_live_logs(max_lines=14),
            ))
            out = buf.getvalue().split("\n")
            widths = [len(ln) for ln in out]
            over = [(i, w) for i, w in enumerate(widths) if w > console.size.width]
            with debug_file.open("w") as f:
                f.write(f"console.size = {console.size.width}w × {console.size.height}h @ {_dt.datetime.now().isoformat(timespec='seconds')}\n")
                f.write(f"rendered lines: {len(out)}, max width: {max(widths)}\n")
                f.write(f"overflow lines (>{console.size.width}): {len(over)}\n")
                for i, w in over[:10]:
                    f.write(f"  L{i}: width={w} content='{out[i][:120]}...'\n")
    except Exception as e:
        try:
            debug_file.write_text(f"DEBUG ERROR: {e}\n")
        except Exception:
            pass
    return Group(
        render_header(),
        render_portfolio_summary(),       # Phase 24 — new top-of-screen panel
        render_position_groups(),          # Phase 24 — contribution grouping with state
        render_realtime_hypos(),
        render_cron_hypos(),
        render_stats_summary(),            # Legacy stats (lifetime PnL)
        render_live_logs(max_lines=_dynamic_log_lines()),
    )


def main() -> None:
    global console  # 한글/emoji wide-char 안전마진 console 교체용
    ap = argparse.ArgumentParser(description="Polaris terminal dashboard (live)")
    ap.add_argument("--refresh", type=int, default=0,
                    help="auto refresh interval seconds (0 = single shot)")
    args = ap.parse_args()

    if args.refresh <= 0:
        console.print(render_dashboard())
        return

    # Rich Live — single-frame replacement, no clear/flicker
    # Width = terminal cols - 10 (큰 안전 margin: 한글/scrollbar/font kerning 회피)
    # Override: POLARIS_DASH_WIDTH=N
    import os as _os
    explicit = _os.environ.get("POLARIS_DASH_WIDTH")
    if explicit and explicit.isdigit():
        safe_width = int(explicit)
    else:
        safe_width = max(80, console.size.width - 10)
    console = Console(width=safe_width)  # height auto-detect from TTY

    refresh_hz = max(1, int(round(1.0 / args.refresh))) if args.refresh > 0 else 1
    try:
        with Live(
            render_dashboard(),
            console=console,
            refresh_per_second=refresh_hz,
            screen=True,         # alt-screen buffer (clean exit, no scrollback pollution)
            transient=False,
        ) as live:
            while True:
                time.sleep(args.refresh)
                live.update(render_dashboard())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Exiting...[/bold yellow]")


if __name__ == "__main__":
    main()
