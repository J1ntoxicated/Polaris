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
from collections import deque
from typing import Callable, Optional

from src.data.binance_ws import (
    compute_recent_volatility_bps as binance_volatility_bps,
    compute_taker_buy_ratio as binance_taker_buy_ratio,
    get_last_trade as binance_get_last_trade,
    get_recent_trades as binance_get_recent_trades,
    stream as binance_stream,
)
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
from src.strategies.binance_lead import BinanceLeadSignal
from src.strategies.breakout_momentum import BreakoutMomentum
from src.strategies.btc_cascade import BTCCascade
from src.strategies.mta_confluence import MTAConfluence
from src.strategies.ofi_momentum import OFIMomentum
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
# Fix 1 (Codex Round 4): supervisor restart delay (patchable in tests)
_SUPERVISOR_RESTART_DELAY_S: float = 5.0

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
    # HYPO-009 BreakoutMomentum — DEPRECATED Round 9 (Codex 92% 합의 2026-05-04)
    # n=16, win 44%, TP 7 / SL 9, -$2.47 total. EV -1.33%/trade (paper fee 0.0014 — was 0.014 typo).
    # TP<SL asymmetry: structural negative EV — unfixable by parameter tuning.
    # Strategy file (breakout_momentum.py) preserved for learning archive.
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
    # HYPO-011 OrderBook Imbalance — DEPRECATED Round 8 (Codex 95% 합의 2026-05-04)
    # n=336, TP 0회, signal_exit 99.7%, -$77.93 lifetime. 전략 파일 archive 보존.
    # HYPO-012 Trade Flow — DEPRECATED Round 8 (Codex 95% 합의 2026-05-04)
    # n=450, TP 9.8%, EV -0.22%/trade, -$151.77 lifetime. 전략 파일 archive 보존.
    # HYPO-013 MTA Confluence (Phase 2g Round 6 — threshold 완화, Codex 78% 합의 B 완전형)
    {
        "hypo_id": "HYPO-013-MTA",
        "strategy_cls": MTAConfluence,
        "params": {"target_size_usd": 100.0, "rsi_soft_threshold": 52.0, "min_score": 2},
        "primary_tf": "mta",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "ORDI-USDT", "SUI-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    },
    # HYPO-014 Binance Lead Signal (Phase 2g Round 3 — cross-exchange leading)
    {
        "hypo_id": "HYPO-014-BLEAD",
        "strategy_cls": BinanceLeadSignal,
        "params": {"target_size_usd": 100.0},
        "primary_tf": "cross",
        # Binance-OKX 매칭되는 ticker만 (BTC/ETH/SOL/DOGE — Binance major pair)
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    },
    # HYPO-016 OFI Momentum (Phase 2g Round 11 — Codex 72% 합의)
    # HYPO-012 TradeFlow 실패 후속: count ratio → signed volume + vwap confirmation
    # OFI = Chordia et al. 2021 order flow momentum alpha
    {
        "hypo_id": "HYPO-016-OFI",
        "strategy_cls": OFIMomentum,
        "params": {"target_size_usd": 100.0},
        "primary_tf": "ofi",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "PEPE-USDT", "ORDI-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    },
    # HYPO-017 BTC-Led Alt Cascade (Phase 2g Round 11 — Codex Round 11 + INSIGHT-027 forensic)
    # HYPO-010 orthogonality: 11.1% raw overlap, causal cascade ~0% → orthogonal
    # BTCCascade: BTC 1min +0.30% AND ETH +0.10% → alt follow lag entry
    # alt_24h < +0.50% guard: HYPO-010 territory exclusion (orthogonality hard guard)
    {
        "hypo_id": "HYPO-017-CASCADE",
        "strategy_cls": BTCCascade,
        "params": {"target_size_usd": 100.0},
        "primary_tf": "cascade",
        # Alt 5종 — BTC/ETH는 cascade signal source (not traded)
        "tickers": ["DOGE-USDT", "SOL-USDT", "ORDI-USDT", "PEPE-USDT", "TRUMP-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    },
]


# Binance ticker subset for cross-exchange — derived from REALTIME_HYPOS primary_tf="cross"
def _binance_subscribe_tickers() -> list[str]:
    syms = set()
    for h in REALTIME_HYPOS:
        if h.get("primary_tf") == "cross":
            syms.update(h["tickers"])
    return sorted(syms)

# Tick-cached indicators per (hypo_id, ticker)
_indicator_cache: dict[tuple, tuple[float, list[Candle]]] = {}

# Last close timestamp per (ticker, strategy_name) — strategy-level cooldown
_last_close_ms: dict[tuple[str, str], int] = {}
# Last close timestamp per ticker (any strategy) — account-level cooldown
# (Codex Round 4 gap fix: 다른 strategy의 즉시 재진입 차단으로 fee bleed 누적 방지)
_last_close_ms_ticker: dict[str, int] = {}

# HYPO-017 BTC Cascade: 1min rolling price history per ticker (BTC/ETH source tickers)
# deque[(ts_ms, price)] — maxlen 600: 3Hz × 60s = 180 + 3× spike safety margin
# (Codex Round 12 F1: maxlen 200 → 600; spike 10tps × 60s = 600 entries, deque
#  auto-truncate was triggering before ts-trim → stale price_1min_ago)
_price_history_60s: dict[str, deque] = {}


def _update_price_history(ticker: str, ts_ms: int, price: float) -> None:
    """매 tick 호출 — 60s rolling window 유지 (HYPO-017 cascade 1min delta 계산용).

    Args:
        ticker: e.g. "BTC-USDT" or "ETH-USDT".
        ts_ms: tick timestamp in milliseconds.
        price: current price.

    Shell function — modifies module-level state.
    """
    buf = _price_history_60s.setdefault(ticker, deque(maxlen=600))
    buf.append((ts_ms, price))
    # Trim entries older than 65s (preserve a 60s window with 5s margin)
    while len(buf) > 1 and ts_ms - buf[0][0] > 65_000:
        buf.popleft()


def _get_cascade_state(ticker: str, current_price: float, current_ts_ms: int) -> Optional[dict]:
    """Build cascade state dict for BTC/ETH from price history.

    Returns dict with price_now, price_1min_ago, ts_ms — or None if insufficient data.
    Requires at least 50s of price history (50_000ms oldest entry).

    Pure logic — reads module-level _price_history_60s (shell boundary).
    """
    buf = _price_history_60s.get(ticker)
    if not buf or len(buf) < 2:
        return None
    oldest_ts, oldest_price = buf[0]
    if current_ts_ms - oldest_ts < 50_000:
        # Less than 50s of data — not enough for reliable 1min delta
        return None
    # Codex Round 12 F2: use the actual last BTC/ETH tick ts, not the alt-ticker
    # tick ts. If BTC/ETH has not ticked for 29s and an alt tick arrives now,
    # current_ts_ms would report 0s stale — defeating the 30s stale guard in
    # BTCCascade.evaluate_cascade. buf[-1][0] is the real last source tick time.
    last_source_ts = buf[-1][0]
    return {
        "price_now": current_price,
        "price_1min_ago": oldest_price,
        "ts_ms": last_source_ts,
    }


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
    elif primary == "ofi" and hasattr(strategy, "evaluate_ofi"):
        # HYPO-016: signed volume OFI + vwap price confirmation
        trades_recent = get_recent_trades(ticker, 50)
        if full_tick is None:
            return
        signal = strategy.evaluate_ofi(full_tick, trades_recent)
    elif primary == "cascade" and hasattr(strategy, "evaluate_cascade"):
        # HYPO-017: BTC-led alt cascade — 1min BTC/ETH delta → alt follow lag
        if full_tick is None:
            return
        btc_state = _get_cascade_state("BTC-USDT", get_last_price("BTC-USDT") or 0.0, tick_ts_ms)
        eth_state = _get_cascade_state("ETH-USDT", get_last_price("ETH-USDT") or 0.0, tick_ts_ms)
        if btc_state is None or eth_state is None:
            # Insufficient price history — log warmup progress (rate-limited 1min/ticker)
            # Codex Round 12 Q7: distinguish cold-start warmup from persistent data gap
            if not hasattr(_eval_and_act, "_cascade_warmup_last"):
                _eval_and_act._cascade_warmup_last = {}
            last_warmup = _eval_and_act._cascade_warmup_last.get(ticker, 0)
            if tick_ts_ms - last_warmup > 60_000:
                logger.info(
                    f"[CASCADE-WARMUP] {ticker} btc_ready={btc_state is not None}"
                    f" eth_ready={eth_state is not None}"
                )
                _eval_and_act._cascade_warmup_last[ticker] = tick_ts_ms
            return
        signal = strategy.evaluate_cascade(full_tick, btc_state, eth_state, now_ms=tick_ts_ms)
    elif primary == "cross" and hasattr(strategy, "evaluate_cross"):
        # Phase 2g Round 3: Binance cross-exchange leading signal (HYPO-014)
        # OKX-style "BTC-USDT" → Binance "BTCUSDT" conversion
        binance_sym = ticker.replace("-", "")
        b_ratio = binance_taker_buy_ratio(binance_sym, window=100)
        b_vol = binance_volatility_bps(binance_sym, window=50)
        b_last = binance_get_last_trade(binance_sym)  # (ts_ms, side, size, price)
        if full_tick is None or b_last is None:
            # Round 10: 5분에 1번 WARN — Binance state 미공급 추적 (WS 문제 vs threshold 미충족 구분)
            if not hasattr(_eval_and_act, "_blead_nofeed_last"):
                _eval_and_act._blead_nofeed_last = {}
            last_warn = _eval_and_act._blead_nofeed_last.get(ticker, 0)
            if tick_ts_ms - last_warn > 300_000:  # 5분
                logger.warning(f"[BLEAD-NOFEED] {ticker} b_last={b_last} (Binance trades 미공급)")
                _eval_and_act._blead_nofeed_last[ticker] = tick_ts_ms
            return
        binance_state = {
            "taker_buy_ratio": b_ratio,
            "n_trades": len(binance_get_recent_trades(binance_sym, 100)),
            "volatility_bps": b_vol,
            "last_price": b_last[3],
            "last_trade_ts_ms": b_last[0],
            # Fix 2 (Codex Round 4): local clock avoids cross-exchange clock skew.
            # OKX ts and Binance ts use different exchange servers → pure local time.
            "now_ms": int(time.time() * 1000),
        }
        signal = strategy.evaluate_cross(full_tick, binance_state)
        # Round 10: HOLD reason 분포 추적 (rate-limited 1분/ticker)
        if signal.action == SignalAction.HOLD:
            if not hasattr(_eval_and_act, "_blead_hold_last"):
                _eval_and_act._blead_hold_last = {}
            last_hold = _eval_and_act._blead_hold_last.get(ticker, 0)
            if tick_ts_ms - last_hold > 60_000:  # 1분
                logger.info(f"[BLEAD-HOLD] {ticker} {signal.reason}")
                _eval_and_act._blead_hold_last[ticker] = tick_ts_ms
    elif primary == "mta" and hasattr(strategy, "evaluate_multi_tf"):
        # Phase 2g Round 2: MTA confluence — 4 TF (1D/4H/1H/15m) candle fetch
        tf_data = fetch_multi_tf(ticker, timeframes=("1D", "4H", "1H", "15m"))
        if not all(tf_data.get(tf) for tf in ("1D", "4H", "1H", "15m")):
            return
        # Codex Round 2 fix (Q5 IMMEDIATE): stale data guard — 각 TF 마지막 candle
        # 이 자체 주기의 1.5x 이상 오래되면 skip (가격 발견 stale)
        TF_MAX_STALE_MS = {"15m": 30*60_000, "1H": 90*60_000, "4H": 6*3600_000, "1D": 36*3600_000}
        stale = False
        for tf in ("15m", "1H", "4H", "1D"):
            last_ts = tf_data[tf][-1].timestamp_ms
            if tick_ts_ms - last_ts > TF_MAX_STALE_MS[tf]:
                stale = True
                break
        if stale:
            return
        signal = strategy.evaluate_multi_tf(tf_data)
        # Round 10: HOLD reason 분포 추적 (24h 후 too strict vs wrong logic 판단)
        # Rate-limit 5분/ticker (BLEAD-HOLD/NOFEED 패턴 — log spam 방지)
        if signal.action == SignalAction.HOLD:
            if not hasattr(_eval_and_act, "_mta_hold_last"):
                _eval_and_act._mta_hold_last = {}
            last_log = _eval_and_act._mta_hold_last.get(ticker, 0)
            if tick_ts_ms - last_log > 300_000:  # 5분
                logger.info(f"[MTA-HOLD] {ticker} {signal.reason}")
                _eval_and_act._mta_hold_last[ticker] = tick_ts_ms
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
        # HYPO-017: 1min price history 업데이트 — BTC/ETH cascade source tickers
        # 모든 ticker tick에 대해 호출 (BTC-USDT, ETH-USDT 포함)
        if tick_ts > 0:
            _update_price_history(inst_id, tick_ts, tick_price)
        for hypo in REALTIME_HYPOS:
            if inst_id not in hypo["tickers"]:
                continue
            try:
                _eval_and_act(hypo, inst_id, tick_price, tick_ts, full_tick=tick)
            except Exception as e:
                logger.error(f"eval err {hypo['hypo_id']} {inst_id}: {e}")
    return handler


async def _run_okx_and_binance(tickers: list[str], binance_tickers: list[str]) -> None:
    """OKX + Binance WS 동시 실행 (Phase 2g Round 3 cross-exchange).

    Fix 1 (Codex Round 4): supervisor loop — either task crashing no longer kills the other.
    Uses asyncio.wait(FIRST_COMPLETED) so that when Binance crashes (24h auto-disconnect),
    OKX task is cancelled, and the entire pair restarts after _SUPERVISOR_RESTART_DELAY_S.
    `_SUPERVISOR_RESTART_DELAY_S` is module-level to allow test patching.
    """
    handler = make_tick_handler()
    while True:  # supervisor — one side crash → cancel all + restart
        tasks: set[asyncio.Task] = {
            asyncio.create_task(stream_tickers(tickers=tickers, on_tick=handler))
        }
        if binance_tickers:
            logger.info(f"Binance WS subscribe: {binance_tickers}")
            tasks.add(asyncio.create_task(binance_stream(symbols=binance_tickers, on_event=None)))
        try:
            # Wait until any task finishes (normal return or exception)
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            # Fix 4 (Codex Round 5): await actual cancellation before next iteration.
            # t.cancel() only schedules; gather blocks until CancelledError is delivered.
            # Without this, the old task can overlap with new tasks (duplicate WS subscribe).
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                exc = t.exception() if not t.cancelled() else None
                if exc is not None:
                    logger.error(
                        f"WS task crashed: {exc!r} — supervisor restart in {_SUPERVISOR_RESTART_DELAY_S}s"
                    )
                else:
                    logger.warning(
                        f"WS task completed normally — supervisor restart in {_SUPERVISOR_RESTART_DELAY_S}s"
                    )
            await asyncio.sleep(_SUPERVISOR_RESTART_DELAY_S)
        except asyncio.CancelledError:
            # Propagate clean shutdown (test teardown / process kill)
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise
        except Exception as e:
            logger.error(f"supervisor outer error: {e!r} — restart in {_SUPERVISOR_RESTART_DELAY_S}s")
            await asyncio.sleep(_SUPERVISOR_RESTART_DELAY_S)


def main() -> None:
    all_tickers = set()
    for h in REALTIME_HYPOS:
        all_tickers.update(h["tickers"])
    tickers = sorted(all_tickers)
    binance_tickers = _binance_subscribe_tickers()
    logger.info(f"=== Polaris Realtime Runner — {_dt.datetime.now().isoformat(timespec='seconds')} ===")
    logger.info(f"OKX subscribe {len(tickers)} tickers: {tickers}")
    if binance_tickers:
        logger.info(f"Binance subscribe {len(binance_tickers)} tickers: {binance_tickers}")
    asyncio.run(_run_okx_and_binance(tickers, binance_tickers))


if __name__ == "__main__":
    main()
