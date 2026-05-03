"""Paper trading logger — shell (P6).

Append-only log to vault/50_runtime/paper_log_<ticker>.md.

P1 Authority: vault/50_runtime은 harness append-only (P3 Write Path).
이 모듈은 paper runner가 호출 — vault에 trade event 기록 (machine 결과 → human readable).
machine state 자체는 src/paper/state.py + state.json (별도).
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from src.paper.state import PaperBalance, Position

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "vault" / "50_runtime"


def _ensure_log_file(ticker: str, strategy_name: str) -> Path:
    """Log 파일 경로 + frontmatter 초기화 (없을 시)."""
    safe_ticker = ticker.replace("/", "-").replace("-", "_").lower()
    safe_strategy = strategy_name.replace(" ", "_").lower()
    path = LOG_DIR / f"paper_log_{safe_ticker}_{safe_strategy}.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"entity_type: paper_log\n"
            f"entity_id: paper_log_{safe_ticker}_{safe_strategy}\n"
            f"auto: true\n"
            f"last_modified: {_dt.date.today().isoformat()}\n"
            f"expires: never\n"
            f"editable: false\n"
            f'back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]\n'
            f"mode: alpha\n"
            f"reviewed_by: none\n"
            f"tags: [meta, paper, alpha, polaris, mode/alpha]\n"
            f"strategy: {strategy_name}\n"
            f"ticker: {ticker}\n"
            f"---\n\n"
            f"# Paper Log — {ticker} {strategy_name}\n\n"
            f"Append-only. ADR-010 paper 운영 기록.\n\n"
            f"## Events\n\n"
            f"| timestamp | event | detail |\n"
            f"|---|---|---|\n",
            encoding="utf-8",
        )
    return path


def log_event(ticker: str, strategy_name: str, event: str, detail: str) -> None:
    """Append single event to paper log."""
    path = _ensure_log_file(ticker, strategy_name)
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"| {ts} | {event} | {detail} |\n")


def log_open(ticker: str, strategy_name: str, position: Position) -> None:
    log_event(
        ticker, strategy_name,
        "OPEN",
        f"id={position.position_id} dir={position.direction} entry={position.entry_price:.2f} size=${position.size_usd:.0f}",
    )


def log_close(ticker: str, strategy_name: str, position: Position) -> None:
    log_event(
        ticker, strategy_name,
        "CLOSE",
        f"id={position.position_id} entry={position.entry_price:.2f} exit={position.exit_price:.2f} "
        f"net={position.net_pct*100:+.2f}% net_usd={position.net_usd:+.2f}",
    )


def log_balance(ticker: str, strategy_name: str, balance: PaperBalance, current_price: float) -> None:
    """BALANCE 이벤트 — 직전 entry와 다를 때만 append (M4 메타 작업 한도)."""
    eq = balance.equity_usd({ticker: current_price})
    pnl = balance.realized_pnl_usd
    new_msg = (
        f"cash=${balance.cash_usd:.2f} equity=${eq:.2f} realized_pnl=${pnl:+.2f} "
        f"open={balance.n_open} closed={balance.n_closed}"
    )
    # 직전 BALANCE와 동일하면 skip (noise 차단)
    path = _ensure_log_file(ticker, strategy_name)
    last_balance = ""
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if "| BALANCE |" in line:
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 3:
                last_balance = parts[2]
            break
    if last_balance == new_msg:
        return  # no change → skip
    log_event(ticker, strategy_name, "BALANCE", new_msg)
