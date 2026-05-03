"""Polaris Terminal Dashboard — shell (P6).

Layout (content-fit, ~180 cols × ~50 rows):
- Header (compact 1 panel)
- Active HYPOs table (5+ rows expand)
- Alpha Index (1 line)
- Live log stream (real-time tail of all paper_log + cron.log)

Usage:
    python -m src.dashboard.cli                  # snapshot
    python -m src.dashboard.cli --refresh 10     # auto refresh

References:
- INSIGHT-002 MTTR-alpha
- ADR-010 Backtest + Paper parallel
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.paper.cron import ACTIVE_HYPOS
from src.paper.runner import load_state

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VAULT_LOG_DIR = PROJECT_ROOT / "vault" / "50_runtime"
DATA_PAPER_DIR = PROJECT_ROOT / "data" / "paper"
CRON_LOG = DATA_PAPER_DIR / "cron.log"

console = Console()


def _strategy_name(hypo: dict) -> str:
    return hypo["strategy"](**hypo["strategy_params"]).name


def render_header() -> Panel:
    now = _dt.datetime.now().isoformat(timespec="seconds")
    body = (
        f"[bold cyan]Polaris[/bold cyan] [dim]│[/dim] {now} [dim]│[/dim] "
        f"[green]●[/green] paper cron daily 01:00 UTC [dim]│[/dim] "
        f"[green]●[/green] dashboard live\n"
        f"[dim]7 원칙[/dim] P1 Authority/P2 Lifecycle/P3 Write/P4 Validation/P5 Alpha-first/P6 Pure/P7 Property "
        f"[dim]│[/dim] [dim]Codex Round 2[/dim] 88%"
    )
    return Panel(body, border_style="green", padding=(0, 1))


def render_active_hypos() -> Table:
    table = Table(
        title="🎯 Active HYPOs (Paper Status)",
        show_header=True,
        header_style="bold cyan",
        expand=True,
        title_justify="left",
    )
    table.add_column("HYPO", justify="left", no_wrap=True)
    table.add_column("Ticker", justify="left", no_wrap=True)
    table.add_column("Strategy", justify="left", no_wrap=True)
    table.add_column("Cash $", justify="right")
    table.add_column("Equity $", justify="right")
    table.add_column("Open", justify="right")
    table.add_column("Closed", justify="right")
    table.add_column("Realized PnL $", justify="right")
    table.add_column("Last Event", justify="left", overflow="fold")

    for hypo in ACTIVE_HYPOS:
        sname = _strategy_name(hypo)
        for ticker in hypo["tickers"]:
            try:
                bal = load_state(ticker, sname, starting_usd=hypo["starting_usd"])
                cash = bal.cash_usd
                eq = bal.cash_usd + sum(p.size_usd for p in bal.open_positions)
                pnl = bal.realized_pnl_usd
                pnl_color = "green" if pnl >= 0 else "red"

                safe_t = ticker.replace("-", "_").lower()
                log_file = VAULT_LOG_DIR / f"paper_log_{safe_t}_{sname}.md"
                last_event = "—"
                if log_file.exists():
                    lines = [
                        ln for ln in log_file.read_text(encoding="utf-8").splitlines()
                        if ln.startswith("| 20")
                    ]
                    if lines:
                        last = lines[-1]
                        parts = [p.strip() for p in last.split("|")[1:-1]]
                        if len(parts) >= 3:
                            last_event = f"{parts[1]}: {parts[2][:60]}"

                table.add_row(
                    hypo["hypo_id"],
                    ticker,
                    sname,
                    f"{cash:,.0f}",
                    f"{eq:,.0f}",
                    str(bal.n_open),
                    str(bal.n_closed),
                    Text(f"{pnl:+,.2f}", style=pnl_color),
                    last_event,
                )
            except Exception as e:
                table.add_row(
                    hypo["hypo_id"], ticker, sname,
                    "—", "—", "—", "—", "—", f"err: {e}"[:60],
                )
    return table


def render_alpha_index() -> Panel:
    idx_path = PROJECT_ROOT / "vault" / "60_alpha" / "_alpha_index.md"
    if not idx_path.exists():
        return Panel("alpha_index missing", title="🔬 Alpha", border_style="red")
    text = idx_path.read_text(encoding="utf-8")
    counts = []
    for line in text.splitlines():
        m = re.match(r"\|\s*(Active|Graduated|Archived|\*\*Total\*\*)\s*\|\s*(\d+)\s*\|", line)
        if m:
            label = m.group(1).replace("*", "")
            counts.append(f"[bold]{label}[/bold]: {m.group(2)}")
    body = " · ".join(counts) if counts else "(no count)"
    return Panel(body, title="🔬 Alpha Index", border_style="cyan", padding=(0, 1))


def _tail_lines(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def render_live_logs(max_lines: int = 25) -> Panel:
    """모든 vault paper_log + cron.log의 latest events 합친 live stream."""
    events: list[tuple[str, str, str]] = []

    if VAULT_LOG_DIR.exists():
        for log_file in VAULT_LOG_DIR.glob("paper_log_*.md"):
            label = log_file.name.replace("paper_log_", "").replace(".md", "")
            for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("| 20"):
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) >= 3:
                        events.append((parts[0], label[:28], f"[{parts[1]}] {parts[2]}"))

    if CRON_LOG.exists():
        for line in _tail_lines(CRON_LOG, 50):
            m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]?\d* \[(\w+)\] (.+)$", line)
            if m:
                events.append((m.group(1).replace(" ", "T"), "cron", f"[{m.group(2)}] {m.group(3)}"))

    events.sort(key=lambda x: x[0], reverse=True)
    latest = events[:max_lines]

    if not latest:
        body = "[dim](no events yet — first cycle 대기 중)[/dim]"
    else:
        lines = []
        for ts, src, msg in latest:
            color = (
                "yellow" if "OPEN" in msg
                else "green" if "CLOSE" in msg
                else "magenta" if "BREACH" in msg
                else "blue" if "BALANCE" in msg
                else "white"
            )
            lines.append(f"[dim]{ts[-8:]}[/dim] [{color}]{src:28s}[/{color}] {msg[:130]}")
        body = "\n".join(lines)
    return Panel(body, title=f"📡 Live Log Stream (latest {max_lines})", border_style="magenta", padding=(0, 1))


def render_dashboard() -> None:
    console.print(render_header())
    console.print(render_active_hypos())
    console.print(render_alpha_index())
    console.print(render_live_logs(max_lines=25))


def main() -> None:
    ap = argparse.ArgumentParser(description="Polaris terminal dashboard (live)")
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
            console.print(f"\n[dim]Refresh every {args.refresh}s — Ctrl+C to exit[/dim]")
            time.sleep(args.refresh)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Exiting...[/bold yellow]")
            break


if __name__ == "__main__":
    main()
