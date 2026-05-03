"""Paper runner — shell (P6).

Single-cycle evaluator: OKX history fetch → strategy.evaluate → state update + log.

운영 모델:
- 매 cycle (cron 또는 manual): `run_cycle(ticker, strategy, state) -> (new_state, current_price)`
- State는 JSON 파일로 persistence (machine SSOT, P1)
- Log는 vault/50_runtime append (human readable)

ADR-010 risk management:
- 단일 포지션 ≤ 2% balance
- 일일 손실 한도 ≤ 5% balance (TODO: 추후 구현)
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from src.data.okx_history import fetch_history
from src.domain.signal import SignalAction
from src.domain.strategy import Strategy
from src.paper import logger as paper_logger
from src.paper.state import PaperBalance, Position, PositionStatus

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE_DIR = PROJECT_ROOT / "data" / "paper"

logger = logging.getLogger(__name__)


def _state_file(ticker: str, strategy_name: str, base_dir: Path | None = None) -> Path:
    # base_dir default = module attribute (monkeypatch-friendly).
    bd = base_dir if base_dir is not None else DEFAULT_STATE_DIR
    bd.mkdir(parents=True, exist_ok=True)
    safe = f"{ticker.replace('/', '-')}_{strategy_name}".lower()
    return bd / f"paper_state_{safe}.json"


def load_state(ticker: str, strategy_name: str, starting_usd: float = 5000.0) -> PaperBalance:
    """JSON에서 state 로드. 없으면 초기 balance."""
    path = _state_file(ticker, strategy_name)
    if not path.exists():
        return PaperBalance(starting_usd=starting_usd, cash_usd=starting_usd)
    raw = json.loads(path.read_text(encoding="utf-8"))
    open_positions = tuple(
        Position(
            position_id=p["position_id"],
            ticker=p["ticker"],
            direction=p["direction"],
            entry_price=p["entry_price"],
            size_usd=p["size_usd"],
            open_ts_ms=p["open_ts_ms"],
            close_ts_ms=p.get("close_ts_ms", 0),
            exit_price=p.get("exit_price", 0.0),
            fee_round_trip=p.get("fee_round_trip", 0.014),
            status=PositionStatus(p.get("status", "open")),
        )
        for p in raw.get("open_positions", [])
    )
    closed_positions = tuple(
        Position(
            position_id=p["position_id"],
            ticker=p["ticker"],
            direction=p["direction"],
            entry_price=p["entry_price"],
            size_usd=p["size_usd"],
            open_ts_ms=p["open_ts_ms"],
            close_ts_ms=p.get("close_ts_ms", 0),
            exit_price=p.get("exit_price", 0.0),
            fee_round_trip=p.get("fee_round_trip", 0.014),
            status=PositionStatus(p.get("status", "closed")),
        )
        for p in raw.get("closed_positions", [])
    )
    return PaperBalance(
        starting_usd=raw["starting_usd"],
        cash_usd=raw["cash_usd"],
        open_positions=open_positions,
        closed_positions=closed_positions,
    )


def save_state(ticker: str, strategy_name: str, balance: PaperBalance) -> None:
    """JSON 저장 (machine state, P1 — vault X)."""
    path = _state_file(ticker, strategy_name)
    raw = {
        "starting_usd": balance.starting_usd,
        "cash_usd": balance.cash_usd,
        "open_positions": [
            {
                **asdict(p),
                "status": p.status.value,
            }
            for p in balance.open_positions
        ],
        "closed_positions": [
            {
                **asdict(p),
                "status": p.status.value,
            }
            for p in balance.closed_positions
        ],
    }
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def run_cycle(
    ticker: str,
    strategy: Strategy,
    bar: str = "1D",
    starting_usd: float = 5000.0,
    max_position_pct: float = 0.02,
    fee_round_trip: float = 0.014,
) -> dict:
    """Single paper cycle.

    1. State 로드
    2. OKX history fetch (strategy.min_window + buffer)
    3. strategy.evaluate(window) → Signal
    4. Open position 있으면 EXIT 검토
    5. 신규 ENTER 신호이면 size 계산 후 open
    6. State 저장 + log

    Returns: dict with summary.
    """
    balance = load_state(ticker, strategy.name, starting_usd=starting_usd)

    # OKX history fetch — strategy min_window + 5 buffer
    n = max(strategy.min_window + 5, 100)
    candles = fetch_history(inst_id=ticker, bar=bar, limit=n)
    if not candles:
        return {"status": "error", "reason": "no candles fetched"}

    last = candles[-1]
    signal = strategy.evaluate(candles)
    summary = {
        "ticker": ticker,
        "strategy": strategy.name,
        "bar": bar,
        "candle_ts_ms": last.timestamp_ms,
        "current_price": last.close,
        "signal": signal.action.value,
        "signal_reason": signal.reason,
        "n_open_pre": balance.n_open,
    }

    # 1. EXIT 처리 (open position 있을 때만)
    for pos in tuple(balance.open_positions):
        if pos.ticker != ticker:
            continue
        should_exit = signal.action == SignalAction.EXIT
        if should_exit:
            balance = balance.close(
                pos.position_id, exit_price=last.close, close_ts_ms=last.timestamp_ms,
            )
            closed = balance.closed_positions[-1]
            paper_logger.log_close(ticker, strategy.name, closed)

    # 2. ENTRY (현재 open 없을 때만, 단순 — 1 ticker 1 position)
    has_open = any(p.ticker == ticker for p in balance.open_positions)
    if signal.action == SignalAction.ENTER_LONG and not has_open:
        size_cap = balance.equity_usd({ticker: last.close}) * max_position_pct
        size = min(signal.target_size_usd, size_cap, balance.cash_usd)
        if size <= 0:
            summary["entry_skipped"] = "size <= 0 or no cash"
        else:
            pos = Position(
                position_id=f"{ticker}-{last.timestamp_ms}",
                ticker=ticker,
                direction=1,
                entry_price=last.close,
                size_usd=size,
                open_ts_ms=last.timestamp_ms,
                fee_round_trip=fee_round_trip,
            )
            balance = balance.open(pos)
            paper_logger.log_open(ticker, strategy.name, pos)

    # 3. Balance log + save
    paper_logger.log_balance(ticker, strategy.name, balance, last.close)
    save_state(ticker, strategy.name, balance)

    summary.update({
        "n_open_post": balance.n_open,
        "n_closed": balance.n_closed,
        "cash_usd": balance.cash_usd,
        "equity_usd": balance.equity_usd({ticker: last.close}),
        "realized_pnl_usd": balance.realized_pnl_usd,
    })
    return summary


def main() -> None:
    """Manual CLI entry: python -m src.paper.runner"""
    import argparse
    from src.strategies.sma_crossover import SMACrossover

    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="BTC-USDT")
    ap.add_argument("--bar", default="1D")
    ap.add_argument("--strategy", default="sma_crossover")
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    ap.add_argument("--starting-usd", type=float, default=5000.0)
    ap.add_argument("--max-position-pct", type=float, default=0.02)
    args = ap.parse_args()

    if args.strategy == "sma_crossover":
        strategy = SMACrossover(fast=args.fast, slow=args.slow)
    else:
        raise ValueError(f"unknown strategy: {args.strategy}")

    summary = run_cycle(
        ticker=args.ticker, strategy=strategy, bar=args.bar,
        starting_usd=args.starting_usd, max_position_pct=args.max_position_pct,
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
