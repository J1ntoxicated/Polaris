"""Polaris Terminal Dashboard — shell (P6).

Single-shot rendering of:
- Active HYPO summary (per ticker × strategy: balance, equity, positions, PnL)
- BACKTEST results (recent backtest cache or live re-run)
- Signal history (last N from vault paper logs)
- Daily summary

Usage:
    python -m src.dashboard.cli                  # snapshot
    python -m src.dashboard.cli --refresh 60     # auto-refresh every 60s

References:
- INSIGHT-002 MTTR-alpha
- ADR-010 Backtest + Paper parallel
- vault/60_alpha/_README workflow
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import time
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.paper.cron import ACTIVE_HYPOS
from src.paper.runner import load_state

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VAULT_LOG_DIR = PROJECT_ROOT / "vault" / "50_runtime"
DATA_PAPER_DIR = PROJECT_ROOT / "data" / "paper"

console = Console()


def _safe_strategy_name(hypo: dict) -> str:
    s = hypo["strategy"](**hypo["strategy_params"])
    return s.name


def render_active_hypos() -> Table:
    """Active HYPO 5 cycle status."""
    table = Table(title="🎯 Active HYPOs (Paper Status)", show_header=True, header_style="bold cyan")
    table.add_column("HYPO", justify="left")
    table.add_column("Ticker", justify="left")
    table.add_column("Strategy", justify="left")
    table.add_column("Cash $", justify="right")
    table.add_column("Equity $", justify="right")
    table.add_column("Open", justify="right")
    table.add_column("Closed", justify="right")
    table.add_column("Realized PnL $", justify="right")

    for hypo in ACTIVE_HYPOS:
        strategy_name = _safe_strategy_name(hypo)
        for ticker in hypo["tickers"]:
            try:
                bal = load_state(ticker, strategy_name, starting_usd=hypo["starting_usd"])
                cash = bal.cash_usd
                # equity 계산 — current price unknown, 마지막 paper log에서 read
                eq = bal.cash_usd + sum(p.size_usd for p in bal.open_positions)  # 단순화
                pnl = bal.realized_pnl_usd
                pnl_color = "green" if pnl >= 0 else "red"
                table.add_row(
                    hypo["hypo_id"],
                    ticker,
                    strategy_name,
                    f"{cash:,.0f}",
                    f"{eq:,.0f}",
                    str(bal.n_open),
                    str(bal.n_closed),
                    Text(f"{pnl:+,.2f}", style=pnl_color),
                )
            except Exception as e:
                table.add_row(
                    hypo["hypo_id"], ticker, strategy_name,
                    "—", "—", "—", "—", f"err: {e}",
                )
    return table


def render_recent_signals(n_lines: int = 10) -> Table:
    """Vault paper log 마지막 N events."""
    table = Table(title=f"📡 Recent Signals (last {n_lines} per log)", show_header=True, header_style="bold magenta")
    table.add_column("Log file", style="dim")
    table.add_column("Last event", justify="left")

    if not VAULT_LOG_DIR.exists():
        table.add_row("—", "no log dir")
        return table

    log_files = sorted(VAULT_LOG_DIR.glob("paper_log_*.md"))
    if not log_files:
        table.add_row("—", "no paper logs yet")
        return table

    for f in log_files:
        text = f.read_text(encoding="utf-8")
        # Extract event lines (after the table header)
        lines = [ln for ln in text.splitlines() if ln.startswith("| 20")]
        if not lines:
            table.add_row(f.name, "(no events)")
            continue
        last_n = lines[-min(n_lines, len(lines)):]
        # 가장 최근 1줄만 short display, 추가는 별도 panel에
        short = last_n[-1] if last_n else ""
        # Strip table syntax
        short = re.sub(r"^\|\s*", "", short).rstrip("|").strip()
        table.add_row(f.name.replace("paper_log_", "").replace(".md", ""), short[:120])
    return table


def render_alpha_index() -> Panel:
    """Vault alpha_index 상태 카운트."""
    idx_path = PROJECT_ROOT / "vault" / "60_alpha" / "_alpha_index.md"
    if not idx_path.exists():
        return Panel("alpha_index missing", title="🔬 Alpha Index", border_style="red")
    text = idx_path.read_text(encoding="utf-8")
    # Extract count rows
    counts = []
    for line in text.splitlines():
        m = re.match(r"\|\s*(Active|Graduated|Archived|\*\*Total\*\*)\s*\|\s*(\d+)\s*\|", line)
        if m:
            counts.append(f"{m.group(1)}: {m.group(2)}")
    body = "\n".join(counts) if counts else "(no count)"
    return Panel(body, title="🔬 Alpha Index", border_style="cyan")


def render_polaris_summary() -> Panel:
    """Polaris 운영 모델 요약."""
    body = (
        "[bold]7 영속 원칙[/bold]: P1 Authority · P2 Lifecycle · P3 Write Path · P4 Validation · P5 Alpha-first KPI · P6 Pure Core · P7 Property test\n"
        "[bold]4 모드[/bold]: DEV · ALPHA · FORENSIC · DEBATE\n"
        "[bold]4 agent[/bold]: vault-curator · code-implementer · forensic-investigator · codex-debate-partner\n"
        "[bold]Cron[/bold]: 매일 01:00 UTC × 5 cycle (BTC/ETH/SOL × HYPO-003/004)\n"
        "[bold]Codex review[/bold]: Round 1 78% → Round 2 88% (잔여 gap 5: stop/dedup/partial/short/sizing)"
    )
    return Panel(body, title="🌟 Polaris 운영 모델", border_style="green")


def render_dashboard() -> None:
    now = _dt.datetime.now().isoformat(timespec="seconds")
    console.rule(f"[bold]Polaris Dashboard — {now}[/bold]")
    console.print(render_polaris_summary())
    console.print(render_active_hypos())
    console.print(render_alpha_index())
    console.print(render_recent_signals())


def main() -> None:
    ap = argparse.ArgumentParser(description="Polaris terminal dashboard")
    ap.add_argument("--refresh", type=int, default=0,
                    help="auto refresh interval seconds (0 = single shot)")
    args = ap.parse_args()

    if args.refresh <= 0:
        render_dashboard()
        return

    while True:
        try:
            console.clear()
            render_dashboard()
            console.print(f"\n[dim]Auto refresh every {args.refresh}s — Ctrl+C to exit[/dim]")
            time.sleep(args.refresh)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Exiting...[/bold yellow]")
            break


if __name__ == "__main__":
    main()
