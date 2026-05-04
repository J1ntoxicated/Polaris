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

from src.data.binance_liquidation_ws import (
    compute_liquidation_pressure,
    stream as binance_liq_stream,
)
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
    set_persister,
    stream_tickers,
)
from src.data.tick_persister import TickPersister
from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.paper import logger as paper_logger
from src.paper.runner import _daily_loss_breached, load_state, save_state
from src.paper.state import PaperBalance, Position
from src.risk.dynamic_sizing import SizingInputs, compute_size
from src.risk.performance_tracker import compute_recent_stats
from src.risk.regime_detector import detect_regime
from src.risk.auto_deprecate import check_deprecate
from src.strategies.binance_lead import BinanceLeadSignal
from src.strategies.breakout_momentum import BreakoutMomentum
from src.strategies.btc_cascade import BTCCascade
from src.strategies.cross_exchange_gap import CrossExchangeGap
from src.strategies.funding_rate_filter import FundingRateFilter
from src.strategies.liquidation_cascade import LiquidationCascade
from src.strategies.mta_confluence import MTAConfluence
from src.strategies.ofi_momentum import OFIMomentum
from src.strategies.orderbook_imbalance import OrderBookImbalance
from src.strategies.rsi_15m_intraday import RSI15mIntraday
from src.strategies.tick_burst import TickBurst
from src.strategies.tick_momentum import TickMomentum
from src.strategies.trade_flow import TradeFlow
from src.strategies.volume_burst import VolumeBurst
from src.strategies.volume_delta_divergence import VolumeDeltaDivergence
from src.strategies.adx_trend_pullback import ADXTrendPullback
from src.strategies.btc_dominance_lag import BTCDominanceLag
from src.strategies.obv_divergence import OBVDivergence
from src.strategies.stoch_rsi import StochRSI
from src.strategies.tsmom import TSMOM
from src.strategies.vpin_toxicity import VPINToxicity
from src.strategies.whale_wall import WhaleWall

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LIVE_FEE_ROUND_TRIP = 0.0014
# Auto-deprecate check interval (seconds) — checked per-HYPO on every tick for the
# fast_fail/loss_cap triggers; frequency trigger checked at DEPRECATE_CHECK_INTERVAL_S cadence
# Phase 2N+: 5min → 1min (faster fail-fast — HYPO-025 lesson: 5min delay = more loss accumulation)
DEPRECATE_CHECK_INTERVAL_S: float = 60.0  # 1 min interval for frequency check (was 300s)
_deprecate_last_check_s: float = 0.0
# Track HYPO started_at timestamps (ms) — populated on first trade or runner start
_hypo_started_at_ms: dict[str, int] = {}
# Funding rate cache — updated by funding rate poll task
_funding_rate_cache: dict[str, float | None] = {}  # symbol -> funding_8h rate
_FUNDING_POLL_INTERVAL_S: float = 60.0
_funding_last_poll_s: float = 0.0
INDICATOR_REFRESH_SEC = 60  # candle indicator 매 60초 refresh
TP_PCT_INTRADAY = 0.006
SL_PCT_INTRADAY = 0.0035
MIN_HOLD_MS = 90_000          # entry 후 90s signal_exit lockout (TP/SL는 활성)
RE_ENTRY_COOLDOWN_MS = 60_000 # close 후 60s 같은 (ticker,strategy) re-entry 차단
MAX_HOLD_MS = 4 * 3600 * 1000 # Phase 2g: 4h 초과 position 자동 청산 (timeframe mismatch SUI 등)
# Fix 1 (Codex Round 4): supervisor restart delay (patchable in tests)
_SUPERVISOR_RESTART_DELAY_S: float = 5.0

# Realtime active HYPOs — Phase 2N+ (HYPO-025 cut 2026-05-04)
# Deprecated tick strategies: HYPO-010/013/014/016/017 — Jin 판단 2026-05-04
# HYPO-025 — DEPRECATED Phase 2N+ (n=6 win 33%, -$3.76, auto-trigger met)
# Remaining: HYPO-007-RT + HYPO-008-RT + HYPO-023/024/027/028/032/033/034
REALTIME_HYPOS = [
    {
        "hypo_id": "HYPO-007-RT",
        "strategy_cls": RSI15mIntraday,
        "params": {},
        "primary_tf": "15m",
        "tickers": ["BTC-USDT", "DOGE-USDT", "PEPE-USDT", "SUI-USDT", "ADA-USDT", "TRUMP-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    {
        "hypo_id": "HYPO-008-RT",
        "strategy_cls": VolumeBurst,
        "params": {},
        "primary_tf": "1H",
        "tickers": ["ORDI-USDT", "DOGE-USDT", "SOL-USDT", "PEPE-USDT", "TRUMP-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    {
        # HYPO-023: Binance Perp Liquidation Cascade Mean Reversion (Phase 2k 2026-05-04)
        # Binance PERP forceOrder → data source only. OKX SPOT → execution.
        # Target: short 청산 dominant ($1M+) + OKX price panic drop (0.4%) → ENTER_LONG
        # Expected edge: 0.5-2% mean-revert vs 0.28% fee round-trip = 양수 EV
        # Paper forward only (historical forceOrder 미제공)
        "hypo_id": "HYPO-023",
        "strategy_cls": LiquidationCascade,
        "params": {},
        "primary_tf": "liquidation",
        # Major liquid pairs with active Binance perp liquidation
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
        # Binance perp symbols for liquidation WS (derived at runtime)
        "_binance_perp_syms": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"],
    },
    # ── Phase 2L: 5 신규 HYPOs (fail-fast paradigm, 2026-05-04) ──────────────
    {
        # HYPO-024: Cross-Exchange Price Gap
        # Binance price directly leads OKX by 0.1-0.5s via raw price gap (not flow ratio).
        # Source: binance_ws @bookTicker (get_book_ticker) already active.
        "hypo_id": "HYPO-024",
        "strategy_cls": CrossExchangeGap,
        "params": {},
        "primary_tf": "gap",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    # HYPO-025 VolumeDeltaDivergence — DEPRECATED Phase 2N+ (2026-05-04)
    # n=6, win 33% < 40% fast_fail threshold, avg_size $687, lifetime -$3.76.
    # Auto-trigger met (n>=5, win<40%). Dynamic sizing gave large size to weak strategy → loss acceleration.
    # Manual cut to immediately stop bleeding (trigger was already met, 5min check delay accrued more loss).
    # {
    #     "hypo_id": "HYPO-025",
    #     "strategy_cls": VolumeDeltaDivergence,
    #     "params": {},
    #     "primary_tf": "delta",
    #     "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "PEPE-USDT"],
    #     "starting_usd": 5000.0,
    # }
    # HYPO-026 DEPRECATED 2026-05-04: n=7, 0 wins, -$1.31.
    # whale_wall pattern明백히 비유효 — n=10 auto-trigger 미달이지만 수동 cut.
    # {
    #     "hypo_id": "HYPO-026",
    #     "strategy_cls": WhaleWall,
    #     ...
    # }
    {
        # HYPO-027: Funding Rate Filter (HYPO-015 부활, size modifier role)
        # Binance Futures funding_8h <= -0.05% → boost; >= +0.10% → block.
        # Runs as independent HYPO here: funding_boost = ENTER_LONG signal on squeeze.
        # Shell fetches funding rate via REST every 60s (see _poll_funding_rate).
        "hypo_id": "HYPO-027",
        "strategy_cls": FundingRateFilter,
        "params": {},
        "primary_tf": "funding",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
        "_binance_futures_syms": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    {
        # HYPO-028: Tick Burst Follow
        # 5s price spike +0.3% → same-direction entry, 60s hold.
        # Fee 0.28% < 0.30% burst → marginal positive EV if continuation follows.
        # Source: OKX tickers WS (5s price history maintained in _price_history_5s).
        "hypo_id": "HYPO-028",
        "strategy_cls": TickBurst,
        "params": {},
        "primary_tf": "burst",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    # ── Phase 2L+: 3 신규 HYPOs (candle-based 1H, 2026-05-04) ────────────────
    # ── Phase 2M: 3 Academic-grade HYPOs (2026-05-04) ────────────────────────
    {
        # HYPO-032: Time-Series Momentum (TSMOM)
        # Basis: Moskowitz, Ooi, Pedersen (2012) JFE 104(2) — 58 futures, Sharpe 1.0+, 25yr persistent.
        # Hypothesis: 1d/7d/30d return continuation (ratio >= 60% positive → ENTER_LONG).
        # Source: 1D candles (daily bar close comparison — no indicator computation).
        # Auto-deprecate: n=5 / -$5 (Phase 2M strict gate).
        "hypo_id": "HYPO-032",
        "strategy_cls": TSMOM,
        "params": {},
        "primary_tf": "1D",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT", "ADA-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    {
        # HYPO-033: VPIN Toxicity (simplified)
        # Basis: Easley, Lopez de Prado, O'Hara (2012) RFS — informed trading detection.
        # Hypothesis: VPIN > 0.7 over last 50 volume buckets → informed buy flow → momentum.
        # Source: OKX trades WS (get_recent_trades already active).
        # Shell builds volume buckets from OKX trades; VPIN pure function evaluates.
        # Auto-deprecate: n=5 / -$5 (Phase 2M strict gate).
        "hypo_id": "HYPO-033",
        "strategy_cls": VPINToxicity,
        "params": {},
        "primary_tf": "vpin",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    {
        # HYPO-034: BTC Dominance Lead-Lag
        # Basis: Stalder & Cosenza (2025) + Liu et al. (2022) — BTC leads alt returns by 30s-3min.
        # Hypothesis: BTC 5min delta > +0.5% + alt lags BTC → alt will follow → ENTER_LONG.
        # Source: OKX tickers WS (price_history_60s already active from HYPO-017 infra).
        # Uses _get_cascade_state() BTC 5min delta + tick alt delta.
        # Auto-deprecate: n=5 / -$5 (Phase 2M strict gate).
        "hypo_id": "HYPO-034",
        "strategy_cls": BTCDominanceLag,
        "params": {},
        "primary_tf": "btclag",
        "tickers": ["ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT", "ADA-USDT"],
        "starting_usd": 5000.0,
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
    },
    # ── DEPRECATED strategies (preserved as comments for audit trail) ──────────
    # HYPO-025 VolumeDeltaDivergence — DEPRECATED Phase 2N+ (2026-05-04)
    # n=6, win 33%, avg_size $687, -$3.76. Auto fast_fail trigger met (n>=5, win<40%).
    # Dynamic sizing gave $687 to a 33% win-rate strategy → loss acceleration confirmed.
    # HYPO-029 StochRSI — DEPRECATED Phase 2M (Jin mandate 2026-05-04)
    # 학술 근거 부족: basic indicator, no academic paper backing. Cut before deploy.
    # HYPO-030 ADXTrendPullback — DEPRECATED Phase 2M (Jin mandate 2026-05-04)
    # 학술 근거 부족: basic indicator combo, no peer-reviewed basis. Cut before deploy.
    # HYPO-031 OBVDivergence — DEPRECATED Phase 2M (Jin mandate 2026-05-04)
    # 학술 근거 부족: basic indicator, no peer-reviewed paper. Cut before deploy.
    # HYPO-009 BreakoutMomentum — DEPRECATED Round 9 (Codex 92% 합의 2026-05-04)
    # n=16, win 44%, TP 7 / SL 9, -$2.47. EV -1.33%/trade. TP<SL asymmetry unfixable.
    # HYPO-010 TickMomentum — DEPRECATED Round 15 (Jin 판단 2026-05-04)
    # n=95, win 43%, -$14.98. 변질 진행 — tick-driven scalp alpha deterioration confirmed.
    # Round 14 수정 (size $200, TRUMP 제거, regime cluster guard) 후에도 EV 음수 지속.
    # HYPO-011 OrderBookImbalance — DEPRECATED Round 8 (Codex 95% 합의 2026-05-04)
    # n=336, TP 0회, signal_exit 99.7%, -$77.93 lifetime.
    # HYPO-012 TradeFlow — DEPRECATED Round 8 (Codex 95% 합의 2026-05-04)
    # n=450, TP 9.8%, EV -0.22%/trade, -$151.77 lifetime.
    # HYPO-013 MTAConfluence — DEPRECATED Round 15 (Jin 판단 2026-05-04)
    # n=1, 100% win, +$0.46 — sample 부족, 빈도 0. 60분 실측에서 1 trade 기록.
    # HYPO-014 BinanceLeadSignal — DEPRECATED Round 15 (Jin 판단 2026-05-04)
    # n=1, 0% win, -$0.20 — vol threshold 미달 지속, cross-exchange lead 미확인.
    # HYPO-016 OFIMomentum — DEPRECATED Round 15 (Jin 판단 2026-05-04)
    # n=37, win 24%, -$3.92 — 사전 trigger (사후 momentum 추종 실패). Round 13 n=10 TP=0 조건 달성.
    # HYPO-017 BTCCascade — DEPRECATED Round 15 (Jin 판단 2026-05-04)
    # n=0, 60분 trigger 0 — BTC 1min +0.30% + ETH +0.10% 동시 조건 빈도 부족.
]


# Binance ticker subset for cross-exchange — derived from REALTIME_HYPOS primary_tf="cross" or "gap"
def _binance_subscribe_tickers() -> list[str]:
    syms = set()
    for h in REALTIME_HYPOS:
        if h.get("primary_tf") in ("cross", "gap"):
            syms.update(h["tickers"])
    return sorted(syms)


# Binance perp liquidation symbols — derived from REALTIME_HYPOS primary_tf="liquidation"
def _binance_liq_subscribe_symbols() -> list[str]:
    """HYPO-023: liquidation WS 구독 symbol list.

    Returns Binance perp format (e.g. "BTCUSDT") from hypo._binance_perp_syms.
    """
    syms: set[str] = set()
    for h in REALTIME_HYPOS:
        if h.get("primary_tf") == "liquidation":
            syms.update(h.get("_binance_perp_syms", []))
    return sorted(syms)

# Tick-cached indicators per (hypo_id, ticker)
_indicator_cache: dict[tuple, tuple[float, list[Candle]]] = {}

# BTC 1D candle cache for regime detection (refresh every 60s)
_btc_1d_cache: tuple[float, list] = (0.0, [])
_BTC_1D_REFRESH_SEC = 60.0

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

# HYPO-028 Tick Burst: 5s rolling price history per ticker
# deque[(ts_ms, price)] — maxlen 150: 30Hz × 5s = 150 max entries
_price_history_5s: dict[str, deque] = {}

# HYPO-010 regime cluster guard (Round 14 — forensic INSIGHT-029).
# 5분 sliding window: 3+ 다른 ticker에서 SL hit → 10분 pause (regime change 자동 감지).
_hypo010_sl_window: deque = deque(maxlen=20)  # (ts_ms, ticker)
_hypo010_pause_until_ms: int = 0


def _check_hypo010_regime_cluster(ticker: str, exit_reason: str, tick_ts_ms: int) -> None:
    """HYPO-010 SL hit 시 cross-ticker cluster 추적.

    5분 sliding window 내 3+ ticker에서 SL hit 발생 시 HYPO-010 신규 entry를 10분 pause.
    regime change cluster 자동 감지 (forensic INSIGHT-029 — multi-ticker 동조 하락).

    Args:
        ticker: SL이 발생한 ticker (e.g. "BTC-USDT").
        exit_reason: close exit reason 문자열 (e.g. "sl_hit:-0.0036").
        tick_ts_ms: 현재 tick timestamp ms.

    Shell function — modifies module-level _hypo010_sl_window, _hypo010_pause_until_ms.
    """
    global _hypo010_pause_until_ms
    if not exit_reason.startswith("sl_hit"):
        return
    _hypo010_sl_window.append((tick_ts_ms, ticker))
    # 5분 초과 항목 trim
    cutoff = tick_ts_ms - 300_000
    while _hypo010_sl_window and _hypo010_sl_window[0][0] < cutoff:
        _hypo010_sl_window.popleft()
    distinct_tickers = {t for _, t in _hypo010_sl_window}
    if len(distinct_tickers) >= 3:
        _hypo010_pause_until_ms = tick_ts_ms + 600_000  # 10분 pause
        logger.warning(
            f"[REGIME-CLUSTER] HYPO-010 paused 10min "
            f"(5min window: {len(_hypo010_sl_window)} SLs across {len(distinct_tickers)} tickers)"
        )


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


def _update_price_history_5s(ticker: str, ts_ms: int, price: float) -> None:
    """매 tick 호출 — 5s rolling window 유지 (HYPO-028 tick burst 계산용).

    Args:
        ticker: e.g. "BTC-USDT".
        ts_ms: tick timestamp in milliseconds.
        price: current price.

    Shell function — modifies module-level state.
    """
    buf = _price_history_5s.setdefault(ticker, deque(maxlen=150))
    buf.append((ts_ms, price))
    # Trim entries older than 6s (5s window + 1s margin)
    while len(buf) > 1 and ts_ms - buf[0][0] > 6_000:
        buf.popleft()


def _get_price_5s_ago(ticker: str, ts_ms: int) -> Optional[float]:
    """Get price ~5s ago for tick burst calculation.

    Returns oldest price in 5s window if window has >= 3s of data, else None.

    Pure logic — reads module-level _price_history_5s (shell boundary).
    """
    buf = _price_history_5s.get(ticker)
    if not buf or len(buf) < 2:
        return None
    oldest_ts, oldest_price = buf[0]
    if ts_ms - oldest_ts < 3_000:
        # Less than 3s of data — not enough
        return None
    return oldest_price


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


def _get_btc_1d_candles() -> list:
    """Return BTC 1D candles for regime detection. Cache 60s (module-level).

    Shell function — touches _btc_1d_cache (module state).
    """
    global _btc_1d_cache
    ts, candles = _btc_1d_cache
    if time.time() - ts < _BTC_1D_REFRESH_SEC:
        return candles
    data = fetch_multi_tf("BTC-USDT", timeframes=("1D",))
    candles = data.get("1D", [])
    _btc_1d_cache = (time.time(), candles)
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
    elif primary == "liquidation" and hasattr(strategy, "evaluate_cascade"):
        # HYPO-023: Binance Perp Liquidation Cascade Mean Reversion (Phase 2k)
        # Binance perp forceOrder → liquidation pressure (data source)
        # OKX SPOT price → panic-drop detection (execution)
        if full_tick is None:
            return
        binance_sym = ticker.replace("-", "")  # "BTC-USDT" → "BTCUSDT"
        liq_pressure = compute_liquidation_pressure(binance_sym, lookback_ms=60_000)
        # 60s price history via existing _price_history_60s (HYPO-017 reuse)
        buf = _price_history_60s.get(ticker)
        price_60s_ago = None
        if buf and len(buf) >= 2:
            oldest_ts, oldest_price = buf[0]
            if tick_ts_ms - oldest_ts >= 50_000:  # at least 50s of history
                price_60s_ago = oldest_price
        signal = strategy.evaluate_cascade(full_tick, liq_pressure, price_60s_ago=price_60s_ago)
        # Warm-up + HOLD reason logging (rate-limited 5min/ticker)
        if signal.action == SignalAction.HOLD:
            if not hasattr(_eval_and_act, "_liq_hold_last"):
                _eval_and_act._liq_hold_last = {}
            last_liq_log = _eval_and_act._liq_hold_last.get(ticker, 0)
            if tick_ts_ms - last_liq_log > 300_000:  # 5분
                pressure_str = (
                    f"${liq_pressure['total_usd']:,.0f} imb={liq_pressure['imbalance']:+.3f}"
                    if liq_pressure else "no_data"
                )
                logger.info(
                    f"[LIQ-CASCADE-HOLD] {ticker} pressure={pressure_str} {signal.reason}"
                )
                _eval_and_act._liq_hold_last[ticker] = tick_ts_ms
        elif signal.action == SignalAction.ENTER_LONG:
            pressure_str = (
                f"${liq_pressure['total_usd']:,.0f} imb={liq_pressure['imbalance']:+.3f}"
                if liq_pressure else "?"
            )
            logger.info(
                f"[LIQ-CASCADE] {ticker} ENTRY SIGNAL pressure={pressure_str} {signal.reason}"
            )
    elif primary == "gap" and hasattr(strategy, "evaluate_cross"):
        # HYPO-024: Cross-Exchange Price Gap — Binance @bookTicker best bid vs OKX last
        if full_tick is None:
            return
        binance_sym = ticker.replace("-", "")  # "BTC-USDT" → "BTCUSDT"
        from src.data.binance_ws import get_book_ticker
        b_book = get_book_ticker(binance_sym)
        if b_book is None:
            if not hasattr(_eval_and_act, "_gap_nofeed_last"):
                _eval_and_act._gap_nofeed_last = {}
            last_warn = _eval_and_act._gap_nofeed_last.get(ticker, 0)
            if tick_ts_ms - last_warn > 300_000:  # 5분
                logger.warning(f"[GAP-NOFEED] {ticker} Binance bookTicker 미공급")
                _eval_and_act._gap_nofeed_last[ticker] = tick_ts_ms
            return
        binance_price_state = {
            "price": b_book.get("bid", 0.0),  # best bid = Binance SPOT best buy price
            "ts_ms": b_book.get("ts_ms", 0),
            "now_ms": int(time.time() * 1000),
        }
        signal = strategy.evaluate_cross(full_tick, binance_price_state)
    elif primary == "delta" and hasattr(strategy, "evaluate_delta"):
        # HYPO-025: Volume Delta Divergence — OKX trades WS cumulative delta
        if full_tick is None:
            return
        trades = get_recent_trades(ticker, 200)
        signal = strategy.evaluate_delta(full_tick, trades)
    elif primary == "wall" and hasattr(strategy, "evaluate_book"):
        # HYPO-026: Whale Wall — OKX books5 large bid detection
        if full_tick is None:
            return
        from src.data.okx_ws import get_book
        book = get_book(ticker)
        if book is None:
            return
        signal = strategy.evaluate_book(full_tick, book)
    elif primary == "funding" and hasattr(strategy, "compute_multiplier"):
        # HYPO-027: Funding Rate Filter — standalone ENTER signal on squeeze
        # Funding rate fetched by shell poll; pure compute_multiplier determines action
        binance_sym = ticker.replace("-", "")
        funding = _funding_rate_cache.get(binance_sym)
        multiplier = strategy.compute_multiplier(funding)
        ts_val = int((full_tick or {}).get("ts", 0) or 0) or 1
        if multiplier == 0.0:
            # Longs blocked
            signal = Signal(
                timestamp_ms=ts_val,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"funding_block rate={funding}",
            )
        elif multiplier > 1.0:
            # Short squeeze → enter
            signal = Signal(
                timestamp_ms=ts_val,
                action=SignalAction.ENTER_LONG,
                confidence=0.70,
                target_size_usd=200.0,
                reason=f"funding_squeeze rate={funding} boost=x{multiplier:.2f}",
            )
        else:
            signal = Signal(
                timestamp_ms=ts_val,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"funding_neutral rate={funding}",
            )
    elif primary == "burst" and hasattr(strategy, "evaluate_tick"):
        # HYPO-028: Tick Burst — 5s price spike follow
        if full_tick is None:
            return
        price_5s_ago = _get_price_5s_ago(ticker, tick_ts_ms)
        signal = strategy.evaluate_tick(full_tick, price_5s_ago)
    elif primary == "vpin" and hasattr(strategy, "evaluate_vpin"):
        # HYPO-033: VPIN Toxicity — OKX trades WS volume bucket construction
        # Shell builds per-tick synthetic volume buckets from OKX recent trades.
        # Bucket = {buy_vol: sum of buy_qty, sell_vol: sum of sell_qty} per 50-trade window.
        if full_tick is None:
            return
        trades = get_recent_trades(ticker, 200)
        # Build rolling 50-trade buckets (each bucket = 10 trades aggregated)
        # get_recent_trades returns list[tuple]: (ts_ms, side, size, price)
        # Must use tuple unpacking — NOT dict .get() — fixes HYPO-033 runtime error.
        buckets: list[dict] = []
        bucket_size = 10
        for i in range(0, len(trades), bucket_size):
            chunk = trades[i:i + bucket_size]
            if not chunk:
                continue
            buy_vol = sum(
                size * price
                for _ts_ms, side, size, price in chunk
                if side == "buy"
            )
            sell_vol = sum(
                size * price
                for _ts_ms, side, size, price in chunk
                if side == "sell"
            )
            buckets.append({"buy_vol": buy_vol, "sell_vol": sell_vol})
        signal = strategy.evaluate_vpin(full_tick, buckets)
        # HOLD reason logging (rate-limited 5min/ticker)
        if signal.action == SignalAction.HOLD:
            if not hasattr(_eval_and_act, "_vpin_hold_last"):
                _eval_and_act._vpin_hold_last = {}
            last_vpin_log = _eval_and_act._vpin_hold_last.get(ticker, 0)
            if tick_ts_ms - last_vpin_log > 300_000:
                logger.info(f"[VPIN-HOLD] {ticker} {signal.reason}")
                _eval_and_act._vpin_hold_last[ticker] = tick_ts_ms
    elif primary == "btclag" and hasattr(strategy, "evaluate_lag"):
        # HYPO-034: BTC Dominance Lead-Lag — BTC 5min delta leads alt
        # Reuses _price_history_60s infrastructure from HYPO-017 (already maintained per tick).
        if full_tick is None:
            return
        # Build BTC 5min state from 60s rolling price history
        # Use oldest entry in 5min window as "price_5min_ago" approximation
        buf_btc = _price_history_60s.get("BTC-USDT")
        if buf_btc is None or len(buf_btc) < 2:
            # BTC warmup — log rate-limited
            if not hasattr(_eval_and_act, "_btclag_warmup_last"):
                _eval_and_act._btclag_warmup_last = {}
            last_warm = _eval_and_act._btclag_warmup_last.get(ticker, 0)
            if tick_ts_ms - last_warm > 60_000:
                logger.info(f"[BTCLAG-WARMUP] {ticker} BTC price history not ready")
                _eval_and_act._btclag_warmup_last[ticker] = tick_ts_ms
            return
        btc_price_now = buf_btc[-1][1]
        btc_price_5min_ago = buf_btc[0][1]  # oldest entry in 60s window ≈ up to 65s ago
        btc_ts_ms = buf_btc[-1][0]
        btc_state_for_lag = {
            "price_now": btc_price_now,
            "price_5min_ago": btc_price_5min_ago,
            "ts_ms": btc_ts_ms,
        }
        # Alt 5min delta from alt's own price history
        buf_alt = _price_history_60s.get(ticker)
        alt_5min_delta_pct = 0.0
        if buf_alt and len(buf_alt) >= 2:
            alt_old = buf_alt[0][1]
            alt_now = buf_alt[-1][1]
            alt_5min_delta_pct = (alt_now - alt_old) / alt_old if alt_old > 0 else 0.0
        # 24h delta from tick (OKX tickers WS provides open24h)
        alt_24h_open = float(full_tick.get("open24h", 0) or 0)
        alt_last = float(full_tick.get("last", 0) or 0)
        alt_24h_delta_pct = (alt_last - alt_24h_open) / alt_24h_open if alt_24h_open > 0 else 0.0
        alt_context = {
            "alt_5min_delta_pct": alt_5min_delta_pct,
            "alt_24h_delta_pct": alt_24h_delta_pct,
        }
        signal = strategy.evaluate_lag(full_tick, btc_state_for_lag, alt_context, now_ms=tick_ts_ms)
        if signal.action == SignalAction.HOLD:
            if not hasattr(_eval_and_act, "_btclag_hold_last"):
                _eval_and_act._btclag_hold_last = {}
            last_lag_log = _eval_and_act._btclag_hold_last.get(ticker, 0)
            if tick_ts_ms - last_lag_log > 300_000:
                logger.info(f"[BTCLAG-HOLD] {ticker} {signal.reason}")
                _eval_and_act._btclag_hold_last[ticker] = tick_ts_ms
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
            # Round 14: HYPO-010 regime cluster guard — SL hit 시 cross-ticker 추적
            if hypo["hypo_id"] == "HYPO-010-TICK" and sname == "tick_momentum":
                _check_hypo010_regime_cluster(ticker, exit_reason, tick_ts_ms)

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
    # Round 14: HYPO-010 regime cluster pause guard (INSIGHT-029 — 5min 3+ SL → 10min entry block)
    in_regime_pause = (
        hypo["hypo_id"] == "HYPO-010-TICK"
        and _hypo010_pause_until_ms > 0
        and tick_ts_ms < _hypo010_pause_until_ms
    )
    if (signal.action == SignalAction.ENTER_LONG
            and not has_open and not daily_breached and not closed_this_tick
            and not in_cooldown and not in_regime_pause):
        # ── Phase 2j: Dynamic sizing (Kelly + confidence + regime + drawdown) ──
        # 1. Recent performance stats (HYPO별 last 20 closed trades)
        perf_stats = compute_recent_stats(list(balance.closed_positions), lookback=20)

        # 2. Regime — BTC 1D candles (cached 60s)
        btc_1d = _get_btc_1d_candles()
        regime = detect_regime(btc_1d)

        # 3. Current drawdown (vs HYPO starting capital)
        equity = balance.equity_usd({ticker: tick_price})
        starting = hypo.get("starting_usd", balance.starting_usd)
        dd = max(0.0, (starting - equity) / starting)

        # 4. Compute dynamic size (pure)
        # Phase 2N+: pass n_trades for cold start cap (< 20 → max $300)
        n_closed = len(balance.closed_positions)
        sizing = compute_size(SizingInputs(
            cash_usd=balance.cash_usd,
            signal_confidence=signal.confidence,
            recent_win_rate=perf_stats["win_rate"],
            recent_avg_win_pct=perf_stats["avg_win_pct"],
            recent_avg_loss_pct=perf_stats["avg_loss_pct"],
            regime=regime,
            drawdown_pct=dd,
        ), n_trades=n_closed)
        # Phase 2N+: hard_cap replaces per-HYPO max_position_pct (ADR-015 정합).
        # max_position_pct (0.04 → $200 cap) was silently overriding dynamic sizing.
        # Dynamic sizing (compute_size) already caps at MAX_FRACTION=0.20 internally.
        # Shell adds one absolute safety bound: equity × 0.30 ($5000 → $1500 max).
        hard_cap = equity * 0.30
        size = min(sizing.size_usd, hard_cap, balance.cash_usd)

        logger.info(
            f"[DYN-SIZE] {hypo['hypo_id']} {ticker} size=${size:.0f} "
            f"regime={regime} {sizing.reason}"
        )

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


def _get_all_closed_for_hypo(hypo: dict) -> list:
    """Collect all closed positions across all tickers for a HYPO.

    Shell function — calls load_state for each ticker.
    """
    strategy = hypo["strategy_cls"](**hypo["params"])
    sname = strategy.name
    closed: list = []
    for ticker in hypo["tickers"]:
        try:
            bal = load_state(ticker, sname, starting_usd=hypo["starting_usd"])
            closed.extend(list(bal.closed_positions))
        except Exception:
            pass
    return closed


def _check_hypo_deprecate_inline(hypo: dict, tick_ts_ms: int) -> None:
    """Inline fast_fail + loss_cap check after each eval. Removes from REALTIME_HYPOS if triggered.

    Shell function — mutates REALTIME_HYPOS list.
    """
    hid = hypo["hypo_id"]
    started = _hypo_started_at_ms.get(hid, tick_ts_ms)
    closed = _get_all_closed_for_hypo(hypo)
    reason = check_deprecate(hid, closed, started, now_ms=tick_ts_ms)
    if reason is not None:
        if hypo in REALTIME_HYPOS:
            REALTIME_HYPOS.remove(hypo)
            logger.warning(f"[DEPRECATE] {hid} removed — {reason}")


def _run_auto_deprecate_check(tick_ts_ms: int) -> None:
    """5min cadence: frequency trigger check across all active HYPOs.

    Shell function — mutates REALTIME_HYPOS list.
    """
    for hypo in list(REALTIME_HYPOS):
        hid = hypo["hypo_id"]
        started = _hypo_started_at_ms.get(hid, tick_ts_ms)
        closed = _get_all_closed_for_hypo(hypo)
        reason = check_deprecate(hid, closed, started, now_ms=tick_ts_ms)
        if reason is not None:
            if hypo in REALTIME_HYPOS:
                REALTIME_HYPOS.remove(hypo)
                logger.warning(f"[DEPRECATE] {hid} removed — {reason}")


def make_tick_handler() -> Callable[[str, dict], None]:
    """Closure — tick 받으면 모든 매칭 HYPO 평가."""
    def handler(inst_id: str, tick: dict) -> None:
        tick_price = float(tick.get("last", 0) or 0)
        tick_ts = int(tick.get("ts", 0) or 0)
        if tick_price <= 0:
            return
        # HYPO-017: 1min price history 업데이트 — BTC/ETH cascade source tickers
        # HYPO-028: 5s price history 업데이트 — burst detection
        # 모든 ticker tick에 대해 호출
        if tick_ts > 0:
            _update_price_history(inst_id, tick_ts, tick_price)
            _update_price_history_5s(inst_id, tick_ts, tick_price)
        # Auto-deprecate: frequency trigger check every 5min (fast_fail/loss_cap inline)
        global _deprecate_last_check_s
        now_s = time.time()
        if now_s - _deprecate_last_check_s >= DEPRECATE_CHECK_INTERVAL_S:
            _deprecate_last_check_s = now_s
            _run_auto_deprecate_check(tick_ts)

        for hypo in list(REALTIME_HYPOS):
            if inst_id not in hypo["tickers"]:
                continue
            try:
                _eval_and_act(hypo, inst_id, tick_price, tick_ts, full_tick=tick)
                # Inline fast_fail + loss_cap check after each eval (real-time cut)
                _check_hypo_deprecate_inline(hypo, tick_ts)
            except Exception as e:
                logger.error(f"eval err {hypo['hypo_id']} {inst_id}: {e}")
    return handler


async def _run_okx_and_binance(
    tickers: list[str],
    binance_tickers: list[str],
    binance_liq_symbols: list[str] | None = None,
) -> None:
    """OKX + Binance SPOT WS + Binance Perp Liquidation WS 동시 실행.

    Phase 2k 추가: HYPO-023 Liquidation Cascade — binance_liq_symbols 구독.

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
            logger.info(f"Binance SPOT WS subscribe: {binance_tickers}")
            tasks.add(asyncio.create_task(binance_stream(symbols=binance_tickers, on_event=None)))
        if binance_liq_symbols:
            logger.info(f"Binance perp liquidation WS subscribe: {binance_liq_symbols}")
            tasks.add(asyncio.create_task(binance_liq_stream(symbols=binance_liq_symbols)))
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
    # Initialize started_at timestamps for all HYPOs (deprecate frequency trigger reference)
    now_ms = int(time.time() * 1000)
    for h in REALTIME_HYPOS:
        _hypo_started_at_ms.setdefault(h["hypo_id"], now_ms)

    # Fix 4 (Phase 2N): Tick persistence — OKX trades WS → SQLite
    # Persister init before WS connect so first trades are captured.
    try:
        _tick_persister = TickPersister(db_path="data/tick_history.sqlite")
        set_persister(_tick_persister)
        logger.info("[TICK-PERSIST] TickPersister started: data/tick_history.sqlite")
    except Exception as e:
        logger.error(f"[TICK-PERSIST] TickPersister init failed (non-fatal): {e}")
        _tick_persister = None

    all_tickers = set()
    for h in REALTIME_HYPOS:
        all_tickers.update(h["tickers"])
    tickers = sorted(all_tickers)
    binance_tickers = _binance_subscribe_tickers()
    binance_liq_symbols = _binance_liq_subscribe_symbols()
    logger.info(f"=== Polaris Realtime Runner — {_dt.datetime.now().isoformat(timespec='seconds')} ===")
    logger.info(f"OKX subscribe {len(tickers)} tickers: {tickers}")
    logger.info(f"Active HYPOs: {[h['hypo_id'] for h in REALTIME_HYPOS]}")
    if binance_tickers:
        logger.info(f"Binance SPOT subscribe {len(binance_tickers)} tickers: {binance_tickers}")
    if binance_liq_symbols:
        logger.info(
            f"Binance perp liquidation subscribe {len(binance_liq_symbols)} symbols: {binance_liq_symbols}"
        )
    asyncio.run(_run_okx_and_binance(tickers, binance_tickers, binance_liq_symbols))


if __name__ == "__main__":
    main()
