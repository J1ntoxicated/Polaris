"""Realtime tick-driven runner — shell (P6).

Long-running process:
- WebSocket OKX SPOT tick subscribe (top N viable ticker)
- Indicator는 candle 기반 (refresh 매 1분)
- Signal 평가는 tick 마다 (indicator + 현재 tick price)
- Entry/exit 즉시 (tick price, no candle wait)
- TP/SL hit 즉시 close (tick price)

Setup:
    cp scripts/com.polaris.paper.realtime.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.polaris.paper.realtime.plist
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
from typing import Callable

from src.data.multi_tf import fetch_multi_tf
from src.data.okx_ws import (
    compute_book_imbalance,
    compute_taker_buy_ratio,
    get_last_price,
    get_recent_trades,
    stream_tickers,
)
from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.paper import logger as paper_logger
from src.paper.runner import _daily_loss_breached, load_state, save_state
from src.paper.state import PaperBalance, Position
from src.strategies.breakout_momentum import BreakoutMomentum
from src.strategies.mta_confluence import MTAConfluence
from src.strategies.orderbook_imbalance import OrderBookImbalance
from src.strategies.rsi_15m_intraday import RSI15mIntraday
from src.strategies.tick_momentum import TickMomentum
from src.strategies.trade_flow import TradeFlow
from src.strategies.volume_burst import VolumeBurst

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LIVE_FEE_ROUND_TRIP = 0.0014
INDICATOR_REFRESH_SEC = 60  # candle indicator 매 60초 refresh
TP_PCT_INTRADAY = 0.006
SL_PCT_INTRADAY = 0.0035
MIN_HOLD_MS = 90_000          # entry 후 90s signal_exit lockout (TP/SL는 활성)
RE_ENTRY_COOLDOWN_MS = 60_000 # close 후 60s 같은 (ticker,strategy) re-entry 차단
MAX_HOLD_MS = 4 * 3600 * 1000 # Phase 2g: 4h 초과 position 자동 청산 (timeframe mismatch SUI 등)

# Realtime active HYPOs — 모든 viable ticker
REALTIME_HYPOS = [
    {
        "hypo_id": "HYPO-007-RT",
        "strategy_cls": RSI15mIntraday,
        "params": {},
        "primary_tf": "15m",
        "tickers": ["BTC-USDT", "DOGE-USDT", "PEPE-USDT", "SUI-USDT", "ADA-USDT", "TRUMP-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
    },
    {
        "hypo_id": "HYPO-008-RT",
        "strategy_cls": VolumeBurst,
        "params": {},
        "primary_tf": "1H",
        "tickers": ["ORDI-USDT", "DOGE-USDT", "SOL-USDT", "PEPE-USDT", "TRUMP-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.05,
    },
    {
        "hypo_id": "HYPO-009-RT",
        "strategy_cls": BreakoutMomentum,
        "params": {"lookback": 10},
        "primary_tf": "1H",
        "tickers": ["DOGE-USDT", "PEPE-USDT", "ORDI-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
    },
    # HYPO-010 TickMomentum — candle 무관, tick payload 직접 평가
    {
        "hypo_id": "HYPO-010-TICK",
        "strategy_cls": TickMomentum,
        "params": {},
        "primary_tf": "tick",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "PEPE-USDT", "SUI-USDT",
                    "ORDI-USDT", "TRUMP-USDT", "ADA-USDT", "XRP-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
    },
    # HYPO-011 OrderBook Imbalance (OKX books5)
    {
        "hypo_id": "HYPO-011-BOOK",
        "strategy_cls": OrderBookImbalance,
        "params": {},
        "primary_tf": "book",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "PEPE-USDT", "SUI-USDT",
                    "ORDI-USDT", "TRUMP-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
    },
    # HYPO-012 Trade Flow (taker buy/sell ratio)
    {
        "hypo_id": "HYPO-012-FLOW",
        "strategy_cls": TradeFlow,
        "params": {},
        "primary_tf": "flow",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "PEPE-USDT", "SUI-USDT",
                    "ORDI-USDT", "TRUMP-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
    },
]

# Tick-cached indicators per (hypo_id, ticker)
_indicator_cache: dict[tuple, tuple[float, list[Candle]]] = {}

# Last close timestamp per (ticker, strategy_name) — strategy-level cooldown
_last_close_ms: dict[tuple[str, str], int] = {}
# Last close timestamp per ticker (any strategy) — account-level cooldown
# (Codex Round 4 gap fix: 다른 strategy의 즉시 재진입 차단으로 fee bleed 누적 방지)
_last_close_ms_ticker: dict[str, int] = {}


def _refresh_candles(ticker: str, tf: str) -> list[Candle]:
    """Refresh candles for ticker × tf. Cache 60s per primary_tf."""
    key = (ticker, tf)
    cached = _indicator_cache.get(key)
    if cached and time.time() - cached[0] < INDICATOR_REFRESH_SEC:
        return cached[1]
    data = fetch_multi_tf(ticker, timeframes=(tf,))
    candles = data.get(tf, [])
    _indicator_cache[key] = (time.time(), candles)
    return candles


def _eval_and_act(hypo: dict, ticker: str, tick_price: float, tick_ts_ms: int, full_tick: dict | None = None) -> None:
    """매 tick 호출 — strategy 평가 + 즉시 entry/exit.

    primary_tf == 'tick' 인 strategy는 candle 무관 — tick payload 직접 사용.
    """
    strategy = hypo["strategy_cls"](**hypo["params"])
    sname = strategy.name

    # 1. Strategy evaluate — tick / book / flow / candle
    primary = hypo.get("primary_tf")
    if primary == "tick" and hasattr(strategy, "evaluate_tick"):
        if full_tick is None:
            return
        signal = strategy.evaluate_tick(full_tick)
    elif primary == "book" and hasattr(strategy, "evaluate_book"):
        imb = compute_book_imbalance(ticker)
        if imb is None or full_tick is None:
            return
        signal = strategy.evaluate_book(full_tick, imb)
    elif primary == "flow" and hasattr(strategy, "evaluate_flow"):
        ratio = compute_taker_buy_ratio(ticker, window=100)
        n_trades = len(get_recent_trades(ticker, 100))
        if ratio is None or full_tick is None:
            return
        signal = strategy.evaluate_flow(full_tick, ratio, n_trades)
    else:
        candles = _refresh_candles(ticker, hypo["primary_tf"])
        if len(candles) < strategy.min_window:
            return
        signal = strategy.evaluate(candles)

    balance = load_state(ticker, sname, starting_usd=hypo["starting_usd"])

    # 1. Open positions — TP/SL/exit-signal 체크 (TICK PRICE 기반!)
    # Codex Round 4 fix: min hold time — entry 후 MIN_HOLD_MS 동안 signal_exit 차단 (flip-flop fee bleed 방지)
    closed_this_tick = False
    for pos in tuple(balance.open_positions):
        if pos.ticker != ticker:
            continue
        gross = pos.direction * (tick_price - pos.entry_price) / pos.entry_price
        held_ms = max(0, tick_ts_ms - pos.open_ts_ms)
        exit_reason = None
        if gross >= TP_PCT_INTRADAY:
            exit_reason = f"tp_hit:{gross:+.4f}"
        elif gross <= -SL_PCT_INTRADAY:
            exit_reason = f"sl_hit:{gross:+.4f}"
        elif held_ms >= MAX_HOLD_MS:
            exit_reason = f"max_hold:{held_ms//1000}s"  # Phase 2g: timeframe mismatch 강제 청산
        elif signal.action == SignalAction.EXIT and held_ms >= MIN_HOLD_MS:
            exit_reason = "signal_exit"
        if exit_reason:
            balance = balance.close(pos.position_id, exit_price=tick_price, close_ts_ms=tick_ts_ms)
            closed = balance.closed_positions[-1]
            paper_logger.log_close(ticker, sname, closed)
            paper_logger.log_event(ticker, sname, "EXIT_REASON", exit_reason)
            logger.info(f"[CLOSE] {hypo['hypo_id']} {ticker} @{tick_price} reason={exit_reason} net={closed.net_usd:+.2f} held={held_ms/1000:.0f}s")
            closed_this_tick = True
            _last_close_ms[(ticker, sname)] = tick_ts_ms
            _last_close_ms_ticker[ticker] = tick_ts_ms
            save_state(ticker, sname, balance)

    # 2. Daily loss + entry
    # Codex Round 4 fix: re-entry cooldown — close 후 RE_ENTRY_COOLDOWN_MS 동안 같은 (ticker,strategy) 재진입 차단
    daily_breached, _ = _daily_loss_breached(balance, tick_ts_ms)
    has_open = any(p.ticker == ticker for p in balance.open_positions)
    last_close = _last_close_ms.get((ticker, sname), 0)
    last_close_ticker = _last_close_ms_ticker.get(ticker, 0)
    in_cooldown = (
        (last_close > 0 and (tick_ts_ms - last_close) < RE_ENTRY_COOLDOWN_MS)
        or (last_close_ticker > 0 and (tick_ts_ms - last_close_ticker) < RE_ENTRY_COOLDOWN_MS)
    )
    if (signal.action == SignalAction.ENTER_LONG
            and not has_open and not daily_breached and not closed_this_tick and not in_cooldown):
        size_cap = balance.equity_usd({ticker: tick_price}) * hypo["max_position_pct"]
        size = min(signal.target_size_usd, size_cap, balance.cash_usd)
        if size > 0:
            pos = Position(
                position_id=f"{ticker}-{tick_ts_ms}",
                ticker=ticker, direction=1,
                entry_price=tick_price,  # TICK PRICE, not candle close!
                size_usd=size,
                open_ts_ms=tick_ts_ms,
                fee_round_trip=LIVE_FEE_ROUND_TRIP,
            )
            balance = balance.open(pos)
            paper_logger.log_open(ticker, sname, pos)
            logger.info(f"[OPEN] {hypo['hypo_id']} {ticker} @{tick_price} size=${size:.2f}")
            save_state(ticker, sname, balance)


# ───── Tick callback ─────


def make_tick_handler() -> Callable[[str, dict], None]:
    """Closure — tick 받으면 모든 매칭 HYPO 평가."""
    def handler(inst_id: str, tick: dict) -> None:
        tick_price = float(tick.get("last", 0) or 0)
        tick_ts = int(tick.get("ts", 0) or 0)
        if tick_price <= 0:
            return
        for hypo in REALTIME_HYPOS:
            if inst_id not in hypo["tickers"]:
                continue
            try:
                _eval_and_act(hypo, inst_id, tick_price, tick_ts, full_tick=tick)
            except Exception as e:
                logger.error(f"eval err {hypo['hypo_id']} {inst_id}: {e}")
    return handler


def main() -> None:
    # 모든 unique ticker 합치기
    all_tickers = set()
    for h in REALTIME_HYPOS:
        all_tickers.update(h["tickers"])
    tickers = sorted(all_tickers)
    logger.info(f"=== Polaris Realtime Runner — {_dt.datetime.now().isoformat(timespec='seconds')} ===")
    logger.info(f"Subscribing {len(tickers)} tickers: {tickers}")
    handler = make_tick_handler()
    asyncio.run(stream_tickers(tickers=tickers, on_tick=handler))


if __name__ == "__main__":
    main()
