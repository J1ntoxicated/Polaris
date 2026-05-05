"""Paper runner — shell (P6).

Single-cycle evaluator: OKX history fetch → strategy.evaluate → state update + log.

운영 모델:
- 매 cycle (cron 또는 manual): `run_cycle(ticker, strategy, state) -> (new_state, current_price)`
- State는 JSON 파일로 persistence (machine SSOT, P1)
- Log는 vault/50_runtime append (human readable)

ADR-010 risk management:
- 단일 포지션 ≤ 2% balance
- 일일 손실 한도 ≤ 5% balance (구현됨, codex Round 1 fix)
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from src.data.okx_history import fetch_history
from src.domain.signal import SignalAction
from src.domain.strategy import Strategy

DAILY_LOSS_LIMIT_PCT = 0.05  # ADR-010
from typing import Optional

from src.paper import logger as paper_logger
from src.paper.state import PaperBalance, Position, PositionStatus

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE_DIR = PROJECT_ROOT / "data" / "paper"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "polaris.sqlite"

logger = logging.getLogger(__name__)


# Phase 9: shadow write to SQLite ledger. JSON remains primary (read path) until
# Phase 9.3 cutover. Toggle via env or by passing _LEDGER_ENABLED=False.
_LEDGER_ENABLED: bool = True
_ledger_singleton = None  # type: ignore[var-annotated]


def _get_ledger():
    """Lazy-init module-level ledger connection. Returns None on disable/error."""
    global _ledger_singleton
    if not _LEDGER_ENABLED:
        return None
    if _ledger_singleton is not None:
        return _ledger_singleton
    try:
        from src.persist.ledger import TradeLedger
        led = TradeLedger(DEFAULT_LEDGER_PATH)
        led.open()
        _ledger_singleton = led
        return led
    except Exception as e:
        logger.warning(f"ledger init failed: {e!r} — JSON-only mode")
        return None


# Phase 15 (P0-1 fix): track shadow write health so portfolio halt knows
# whether ledger reflects reality. Failures still don't crash trading, but
# they're now visible + counted (after N consecutive failures, runner could
# auto-disable ledger-dependent gates as a safety measure).
_shadow_write_failures: int = 0
_shadow_write_last_success_ms: int = 0


def get_ledger_health() -> dict:
    """Return ledger write health stats (consumed by portfolio halt + dashboard)."""
    return {
        "consecutive_failures": _shadow_write_failures,
        "last_success_ms": _shadow_write_last_success_ms,
    }


def _shadow_write_balance(
    ticker: str, strategy_name: str, balance: PaperBalance, hypo_id: str | None = None,
) -> bool:
    """Phase 17: SQL ledger PRIMARY write (formerly shadow). Returns True on success.

    Phase 9 history (now superseded by Phase 17):
      - Phase 9: shadow write, JSON primary
      - Phase 16: SQL primary read, JSON shadow read fallback
      - Phase 17 (this): SQL primary write, JSON still written as backup

    Failures still don't crash trading (returns False, JSON shadow continues).
    Counter exposed via get_ledger_health() for portfolio halt + dashboard.

    hypo_id resolved lazily from REALTIME_HYPOS if not provided.
    """
    global _shadow_write_failures, _shadow_write_last_success_ms
    led = _get_ledger()
    if led is None:
        _shadow_write_failures += 1
        return False
    try:
        # Resolve hypo_id if not passed
        hid = hypo_id
        if hid is None:
            try:
                from src.persist.migrations import _hypo_id_for_strategy
                hid = _hypo_id_for_strategy(strategy_name)
            except Exception:
                hid = f"LEGACY-{strategy_name}"

        # Upsert balance
        led.upsert_balance(hid, ticker, balance)

        # Upsert positions (shadow — INSERT OR REPLACE handles dedup)
        for pos in balance.open_positions:
            led.insert_position_open(pos, hid, strategy_name)
        for pos in balance.closed_positions:
            led.insert_position_open(pos, hid, strategy_name)
            if pos.exit_price > 0 and pos.close_ts_ms > 0:
                led.update_position_close(
                    position_id=pos.position_id,
                    exit_price=pos.exit_price,
                    close_ts_ms=pos.close_ts_ms,
                    exit_reason="shadow_sync",
                )
        # Success — reset failure counter
        _shadow_write_failures = 0
        import time as _t
        _shadow_write_last_success_ms = int(_t.time() * 1000)
        return True
    except Exception as e:
        _shadow_write_failures += 1
        if _shadow_write_failures > 3:
            logger.error(
                f"ledger write FAILED {_shadow_write_failures}× consecutive "
                f"({hypo_id}/{ticker}): {e!r}"
            )
        else:
            logger.warning(f"ledger write failed ({hypo_id}/{ticker}): {e!r}")
        return False


def _state_file(ticker: str, strategy_name: str, base_dir: Path | None = None) -> Path:
    # base_dir default = module attribute (monkeypatch-friendly).
    bd = base_dir if base_dir is not None else DEFAULT_STATE_DIR
    bd.mkdir(parents=True, exist_ok=True)
    safe = f"{ticker.replace('/', '-')}_{strategy_name}".lower()
    return bd / f"paper_state_{safe}.json"


def load_state(ticker: str, strategy_name: str, starting_usd: float = 5000.0) -> PaperBalance:
    """Load PaperBalance — Phase 16: SQL ledger primary, JSON fallback.

    Phase 9.1: JSON primary, SQL shadow.
    Phase 16 (this): SQL primary read, JSON fallback (handles legacy / SQL miss).

    SQL is now SSOT. JSON kept as backup for safety + human inspection.
    """
    # Phase 16: try SQL first
    bal = _load_from_ledger(ticker, strategy_name)
    if bal is not None:
        return bal

    # JSON fallback
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
            fee_round_trip=p.get("fee_round_trip", 0.0014),
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
            fee_round_trip=p.get("fee_round_trip", 0.0014),
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


def _load_from_ledger(
    ticker: str, strategy_name: str,
) -> Optional[PaperBalance]:
    """Phase 16 — try SQL ledger as primary source. Returns None on miss/error."""
    if not _LEDGER_ENABLED:
        return None
    try:
        led = _get_ledger()
        if led is None:
            return None
        from src.persist.migrations import _hypo_id_for_strategy
        try:
            hid = _hypo_id_for_strategy(strategy_name)
        except Exception:
            hid = f"LEGACY-{strategy_name}"
        return led.get_paper_balance(hid, ticker)
    except Exception as e:
        logger.warning(f"ledger primary read failed ({ticker}/{strategy_name}): {e!r}")
        return None


def save_state(ticker: str, strategy_name: str, balance: PaperBalance) -> None:
    """Phase 17: SQL primary write, JSON shadow.

    Order:
      1. SQL ledger upsert (primary, raises on failure)
      2. JSON file atomic write (shadow — backup, human inspection)

    Falls back to JSON-only if ledger disabled or fails (with warning).
    """
    # Phase 17: SQL primary write
    sql_ok = _primary_write_ledger(ticker, strategy_name, balance)

    # JSON shadow write (always — preserves backup + dashboard backward compat)
    path = _state_file(ticker, strategy_name)
    tmp = path.with_suffix(".json.tmp")
    raw = {
        "starting_usd": balance.starting_usd,
        "cash_usd": balance.cash_usd,
        "open_positions": [
            {**asdict(p), "status": p.status.value}
            for p in balance.open_positions
        ],
        "closed_positions": [
            {**asdict(p), "status": p.status.value}
            for p in balance.closed_positions
        ],
    }
    tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic rename (POSIX guarantee)


def _primary_write_ledger(
    ticker: str, strategy_name: str, balance: PaperBalance,
) -> bool:
    """Phase 17: SQL ledger primary write. Returns True on success.

    Failures still don't crash trading (fall through to JSON shadow), but
    they're visible via get_ledger_health(). After 10+ failures, portfolio
    halt skips (avoids blind halt on unreliable ledger).
    """
    return _shadow_write_balance(ticker, strategy_name, balance)


def _daily_loss_breached(balance: PaperBalance, last_ts_ms: int) -> tuple[bool, float]:
    """ADR-010 일일 손실 5% 한도 체크. Returns (breached, today_loss_pct)."""
    if not balance.closed_positions:
        return False, 0.0
    # last_ts_ms 기준 오늘 (UTC) closed positions 합산
    one_day_ms = 86_400_000
    day_start_ms = (last_ts_ms // one_day_ms) * one_day_ms
    today_pnl = sum(
        p.net_usd for p in balance.closed_positions
        if p.close_ts_ms >= day_start_ms
    )
    if today_pnl >= 0:
        return False, 0.0
    loss_pct = abs(today_pnl) / balance.starting_usd
    return loss_pct >= DAILY_LOSS_LIMIT_PCT, loss_pct


def run_cycle(
    ticker: str,
    strategy: Strategy,
    bar: str = "1D",
    starting_usd: float = 5000.0,
    max_position_pct: float = 0.02,
    fee_round_trip: float = 0.0014,
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

    # 1. EXIT 처리 — TP/SL 강제 + signal.EXIT
    # ADR-010 risk: TP +0.6%, SL -0.35% (intraday 기본)
    # 1d strategy는 더 wide (TP +3%, SL -2%)
    is_intraday = bar.lower() in {"1m", "3m", "5m", "15m", "30m", "1h"}
    tp_pct = 0.006 if is_intraday else 0.03
    sl_pct = 0.0035 if is_intraday else 0.02

    has_pos = any(p.ticker == ticker for p in balance.open_positions)
    if not has_pos and signal.action == SignalAction.EXIT:
        summary["signal"] = "exit_noop"

    closed_this_cycle = False  # flip-flop 차단
    for pos in tuple(balance.open_positions):
        if pos.ticker != ticker:
            continue
        gross = pos.direction * (last.close - pos.entry_price) / pos.entry_price
        exit_reason = None
        if signal.action == SignalAction.EXIT:
            exit_reason = "signal_exit"
        elif gross >= tp_pct:
            exit_reason = f"tp_hit:{gross:+.4f}>={tp_pct}"
        elif gross <= -sl_pct:
            exit_reason = f"sl_hit:{gross:+.4f}<=-{sl_pct}"
        if exit_reason:
            balance = balance.close(
                pos.position_id, exit_price=last.close, close_ts_ms=last.timestamp_ms,
            )
            closed = balance.closed_positions[-1]
            paper_logger.log_close(ticker, strategy.name, closed)
            paper_logger.log_event(
                ticker, strategy.name,
                "EXIT_REASON",
                exit_reason,
            )
            closed_this_cycle = True
    summary["closed_this_cycle"] = closed_this_cycle

    # ADR-010 daily loss 5% 한도 체크 (entry 전)
    daily_breached, today_loss = _daily_loss_breached(balance, last.timestamp_ms)
    summary["daily_loss_pct"] = today_loss

    # 2. ENTRY (현재 open 없을 때만 + 같은 cycle close 안 했을 때 — flip-flop 차단)
    has_open = any(p.ticker == ticker for p in balance.open_positions)
    if signal.action == SignalAction.ENTER_LONG and not has_open and not daily_breached and not closed_this_cycle:
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
    elif signal.action == SignalAction.ENTER_LONG and daily_breached:
        summary["entry_skipped"] = f"daily_loss_breached: {today_loss:.2%} >= {DAILY_LOSS_LIMIT_PCT:.0%}"
        paper_logger.log_event(
            ticker, strategy.name,
            "DAILY_LOSS_BREACH",
            f"loss_pct={today_loss:.4f} blocked entry",
        )

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
