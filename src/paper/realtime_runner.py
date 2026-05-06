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
# Load .env (ANTHROPIC_API_KEY etc) at startup — launchd doesn't auto-load
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except ImportError:
    pass
import time
from collections import deque
from typing import Callable, Optional

from src.paper.exit_profiles import get_exit_profile
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
    get_book,
    get_last_price,
    get_recent_trades,
    get_tick,
    set_persister,
    stream_tickers,
)
from src.paper.slippage_model import (
    compute_fill_price,
    compute_liquidity_cap,
    compute_spread_bps,
    should_skip_entry_spread,
)
from src.paper.dispatchers import (
    DispatchContext,
    get_dispatcher,
    register_dispatcher,
)
from src.exec.broker import (
    Broker,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.exec.paper_broker import PaperBroker
from src.exec.okx_broker import OKXBroker, _live_armed as _okx_live_armed
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
from src.strategies.ai_advisor import AIAdvisor
from src.strategies.grid_bot import GridBot
from src.strategies.nfi_dipbuy import NFIDipBuy
from src.strategies.funding_carry import FundingCarry
from src.data.binance_funding import fetch_funding_rates_bulk

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Phase 5 Codex fix: OKX paper Lv1 fee = 0.1%/side = 0.2% round-trip.
# Previous 0.0014 assumed Lv3+ (0.07%/side) — 30% underestimate.
# Override via env LIVE_FEE_ROUND_TRIP=0.002 for live or test environments.
import os as _os
LIVE_FEE_ROUND_TRIP = float(_os.environ.get("LIVE_FEE_ROUND_TRIP", "0.002"))
# Auto-deprecate check interval (seconds) — checked per-HYPO on every tick for the
# fast_fail/loss_cap triggers; frequency trigger checked at DEPRECATE_CHECK_INTERVAL_S cadence
# Phase 2N+: 5min → 1min (faster fail-fast — HYPO-025 lesson: 5min delay = more loss accumulation)
DEPRECATE_CHECK_INTERVAL_S: float = 60.0  # 1 min interval for frequency check (was 300s)
_deprecate_last_check_s: float = 0.0
# Track HYPO started_at timestamps (ms) — populated on first trade or runner start
_hypo_started_at_ms: dict[str, int] = {}
# Funding rate cache — updated by funding rate poll task
# Phase 20.5: track each (ticker, strategy_name) latest signal action — used
# by SignalReversal exit strategies inside PositionManager.check_exits.
_strategy_last_action: dict[tuple[str, str], str] = {}

_funding_rate_cache: dict[str, float | None] = {}  # symbol -> funding_8h rate
_FUNDING_POLL_INTERVAL_S: float = 60.0


async def _poll_funding_rates(symbols: list[str]) -> None:
    """Background task — poll Binance Futures funding every 60s, populate cache.

    Phase 8 (2026-05-05) fix: cache was read-only (HYPO-027 + AI silently saw
    funding=0.0 forever). Polling now writes the cache so downstream funding-
    aware strategies (HYPO-027 FundingFilter, HYPO-036 FundingCarry, AI advisor)
    actually receive live data.

    Codex round-1 fix: fetch_funding_rates_bulk is sync (requests.get × N).
    Run in default executor so the event loop is not blocked while WS streams
    + tick handlers continue. Without this, 3 symbols × 8s timeout = up to
    24s of event-loop stall per poll cycle.

    Shell function — performs HTTP I/O via thread executor.
    """
    loop = asyncio.get_running_loop()
    while True:
        try:
            rates = await loop.run_in_executor(
                None, fetch_funding_rates_bulk, symbols, False
            )
            for sym, rate in rates.items():
                if rate is not None:
                    _funding_rate_cache[sym] = rate
            non_null = sum(1 for v in _funding_rate_cache.values() if v is not None)
            logger.info(
                f"[FUNDING-POLL] populated {non_null}/{len(symbols)} symbols "
                f"sample={dict(list(_funding_rate_cache.items())[:3])}"
            )
        except Exception as e:
            logger.error(f"[FUNDING-POLL] fetch error: {e!r}")
        await asyncio.sleep(_FUNDING_POLL_INTERVAL_S)
_funding_last_poll_s: float = 0.0
INDICATOR_REFRESH_SEC = 60  # candle indicator 매 60초 refresh
TP_PCT_INTRADAY = 0.006
SL_PCT_INTRADAY = 0.0035
MIN_HOLD_MS = 90_000          # entry 후 90s signal_exit lockout (TP/SL는 활성)
RE_ENTRY_COOLDOWN_MS = 60_000 # close 후 60s 같은 (ticker,strategy) re-entry 차단
MAX_HOLD_MS = 4 * 3600 * 1000 # Phase 2g: 4h 초과 position 자동 청산 (timeframe mismatch SUI 등)
# Fix 1 (Codex Round 4): supervisor restart delay (patchable in tests)
_SUPERVISOR_RESTART_DELAY_S: float = 5.0

# Phase 5: 30-ticker universe (NFI X7 standard 40-80 pair pool, Polaris subset).
# Original 15 + 15 new: BNB, ATOM, NEAR, UNI, AAVE, LDO, ICP, FIL, ARB, OP, SHIB, INJ, SEI, TIA, JTO
# OKX SPOT format: "XXX-USDT"
_UNIVERSE_30 = [
    # Original 15 (Phase 4)
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "PEPE-USDT",
    "SUI-USDT", "ADA-USDT", "TRUMP-USDT", "XRP-USDT", "AVAX-USDT",
    "LINK-USDT", "POL-USDT", "ATOM-USDT", "NEAR-USDT", "ORDI-USDT",
    # Phase 5 additions (NFI style — liquid mid-caps)
    "BNB-USDT", "UNI-USDT", "AAVE-USDT", "LDO-USDT", "ICP-USDT",
    "FIL-USDT", "ARB-USDT", "OP-USDT", "SHIB-USDT", "INJ-USDT",
    "SEI-USDT", "TIA-USDT", "JTO-USDT", "BLUR-USDT", "WLD-USDT",
]

# Realtime active HYPOs — Phase 5 (Codex deep review 2026-05-04)
# Phase 2N+: HYPO-025 cut (n=6 win 33%, -$3.76, auto-trigger met)
# Phase 5 new: HYPO-NFI-001 (NFI X7 dip-buy), 30-ticker universe
# Remaining: HYPO-007-RT + HYPO-008-RT + HYPO-023/027/028/032 + HYPO-NFI-001
REALTIME_HYPOS = [
    {
        # HYPO-007-RT: RSI 15m intraday — Phase 5: expanded to 30 tickers
        # NFI standard 40-80 tickers; 30 → signal frequency ~2x vs 15.
        "hypo_id": "HYPO-007-RT",
        "strategy_cls": RSI15mIntraday,
        "params": {},
        "primary_tf": "15m",
        "tickers": _UNIVERSE_30,
        "starting_usd": 50000.0,
        "exit_profile": "scalp",  # TP 0.6%, SL 0.35%, max 4h
    },
    {
        # HYPO-008-RT: Volume Burst 1H — Phase 5: expanded to 30 tickers
        "hypo_id": "HYPO-008-RT",
        "strategy_cls": VolumeBurst,
        "params": {},
        "primary_tf": "1H",
        "tickers": _UNIVERSE_30,
        "starting_usd": 50000.0,
        "exit_profile": "swing",  # TP 5%, SL 2%, max 7d
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
        "starting_usd": 50000.0,  # Phase 2Q: 10x capital
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
        # Binance perp symbols for liquidation WS (derived at runtime)
        "_binance_perp_syms": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"],
        "exit_profile": "liquidation",  # Phase 2P: event-driven mean revert — TP 1.5%, SL 0.7%, max 30min
    },
    # ── Phase 2L: 5 신규 HYPOs (fail-fast paradigm, 2026-05-04) ──────────────
    # HYPO-024 CrossExchangeGap — DEPRECATED (auto fast_fail n=11 win 36% < 40%, 2026-05-04)
    # Phase 4에서 실수로 다시 active 됐다 confirmed (2026-05-05). 영구 cut.
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
        "starting_usd": 50000.0,  # Phase 2Q: 10x capital
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
        "exit_profile": "swing",  # Phase 2P: funding 8h cycle → swing — TP 5%, SL 2%, max 7d
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
        "starting_usd": 50000.0,  # Phase 2Q: 10x capital
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
        "exit_profile": "scalp",  # Phase 2P: 5s burst, 60s expected hold — TP 0.6%, SL 0.35%, max 4h
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
        "starting_usd": 50000.0,  # Phase 2Q: 10x capital
        # max_position_pct removed (Phase 2N+) — dynamic sizing handles cap via ADR-015
        "exit_profile": "position",  # Phase 2P: 30d momentum — TP 12%, SL 4%, max 30d (aligns with Moskowitz 2012 expectancy)
    },
    # HYPO-033 VPIN Toxicity — DEPRECATED (auto loss_cap -$5.21 < -$5, 2026-05-04)
    # Phase 4에서 실수로 다시 active 됐다 confirmed (2026-05-05). 영구 cut.
    # HYPO-034 BTCDominanceLag — DEPRECATED 2026-05-04 (manual cut)
    # n=3, win 0%, 3 SL consecutive, -$7.09. Pattern확실, manual cut (auto-trigger n=5 미도달).
    # Basis: Stalder & Cosenza (2025) — BTC leads alt by 30s-3min.
    # Observed: BTC spike rebound immediately corrected before alt entry → alt enters into reversal.
    # {
    #     "hypo_id": "HYPO-034",
    #     "strategy_cls": BTCDominanceLag,
    #     "params": {},
    #     "primary_tf": "btclag",
    #     "tickers": ["ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT", "ADA-USDT"],
    #     "starting_usd": 50000.0,
    #     "exit_profile": "scalp",
    # }
    # ── Phase 4: Grid Bot + expanded universe (INSIGHT-035, 2026-05-04) ─────────
    {
        # HYPO-040: Grid Bot — sideways market recurring PnL (BingX 287K users validated).
        # ATR compression (<1%) + lower 30% of 24h range → 5-level grid BUY.
        # SPOT-only: BUY at lower boundary, TP +0.8% per level, exit on range breakout.
        # 15 tickers (same as HYPO-007/008 universe).
        # primary_tf = "grid" → evaluate_grid(tick, atr_pct, high_24h, low_24h)
        "hypo_id": "HYPO-040",
        "strategy_cls": GridBot,
        "params": {},
        "primary_tf": "grid",
        "tickers": _UNIVERSE_30,  # Phase 5: 15 → 30 tickers
        "starting_usd": 50000.0,
        "exit_profile": "scalp",  # TP 0.6% (grid default +0.8% handled per-level), max 4h
    },
    # ── Phase 5: NFI X7 Dip-Buy (HYPO-NFI-001) ────────────────────────────────
    {
        # HYPO-036: Funding Carry — Liu & Yu (2024) ~70% hit rate empirical.
        # Standalone entry on extreme negative funding (shorts squeezed → SPOT rally).
        # Distinct from HYPO-027 (size modifier). primary_tf="carry" → evaluate_funding.
        # Default entry threshold -0.05% per 8h, exit on funding recovery (>= 0%) or 12h max.
        "hypo_id": "HYPO-036",
        "strategy_cls": FundingCarry,
        "params": {},
        "primary_tf": "carry",
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
        "_binance_futures_syms": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "starting_usd": 50000.0,
        "exit_profile": "swing",  # 12h hold target — swing (TP 5%, SL 2%, max 7d aligns)
    },
    {
        # HYPO-NFI-001: NFI X7 dip-buy — multi-TF oversold confluence
        # Basis: NostalgiaForInfinity X7 (strat.ninja validated, 88-92% win rate backtests).
        # Multi-TF: RSI_3 5m/15m < 5 + RSI_14 1h < 30 + AROON_4h < 80 + BB lower.
        # Exit: RSI_14 1h > 84 OR price > BB upper.
        # primary_tf = "nfi" → evaluate_multi_tf({5m, 15m, 1H, 4H})
        "hypo_id": "HYPO-NFI-001",
        "strategy_cls": NFIDipBuy,
        "params": {},
        "primary_tf": "nfi",
        "tickers": _UNIVERSE_30,  # NFI standard = 40-80 pairs; Polaris 30-ticker subset
        "starting_usd": 50000.0,
        "exit_profile": "swing",  # NFI exit: RSI_14>84 + BB upper → swing-duration hold TP 5%, SL 2%, max 7d
    },
    # ── DEPRECATED strategies (preserved as comments for audit trail) ──────────
    # HYPO-AI-001 Claude AI Advisor — DEPRECATED Phase 4 (2026-05-05)
    # n=2, 0% win, -$5.91, auto loss_cap trigger 적중 (-$5.91 < -$5)
    # Phase 4 retry fix 후 재등장 했지만 새 entry 1개도 동일 패턴 (조건 trigger 의심)
    # 재차 cut. AI advisor 자체 가치 X retail SPOT 환경 (sample n=2 SL hit 100%).
    # 재시도 시 Codex DEBATE 의무 (다른 spec / threshold / model).

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

# Phase 5 Codex fix #1: Strategy singleton cache — instantiate once per (hypo_id, ticker).
# Previously: every tick created new Strategy(**params) — 90 calls/tick × 15 tickers = CPU waste.
# Key: (hypo_id, ticker) → Strategy instance (reused across ticks).
# Populated lazily on first eval, cleared only on runner restart.
_strategy_instances: dict[tuple[str, str], object] = {}

# Phase 5 Codex fix #1 (balance cache): load_state disk I/O per tick is expensive.
# Cache balance in-memory per (ticker, strategy_name); flush only on entry/exit state change.
# Key: (ticker, strategy_name) → PaperBalance
# Invariant: cache is always in sync after save_state calls (cache updated = file written).
_balance_cache: dict[tuple[str, str], "PaperBalance"] = {}

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


def _get_strategy(hypo: dict, ticker: str) -> object:
    """Return cached Strategy instance for (hypo_id, ticker). Create once, reuse forever.

    Phase 5 Codex fix: strategy objects are stateless (pure) — no need to re-instantiate per tick.
    Singleton per (hypo_id, ticker) eliminates ~90 __init__ calls/tick × 15 tickers.

    Shell function — reads/writes module-level _strategy_instances.
    """
    key = (hypo["hypo_id"], ticker)
    if key not in _strategy_instances:
        _strategy_instances[key] = hypo["strategy_cls"](**hypo["params"])
    return _strategy_instances[key]


def _load_balance(ticker: str, strategy_name: str, starting_usd: float) -> "PaperBalance":
    """Load balance from in-memory cache or disk.

    Phase 5 Codex fix: cache eliminates per-tick load_state disk I/O.
    Cache miss → disk read + populate cache. Cache hit → return cached.

    Shell function — reads/writes module-level _balance_cache.
    """
    key = (ticker, strategy_name)
    if key not in _balance_cache:
        _balance_cache[key] = load_state(ticker, strategy_name, starting_usd=starting_usd)
    return _balance_cache[key]


# Phase 15 (P1-3 fix): broker singleton — entry/exit go through this contract.
# Defaults to PaperBroker (current paper engine simulation). Live transition
# means swapping to OKXBroker via configuration. realtime_runner stays unchanged.
_broker_singleton: Broker | None = None


# Phase 20.5: PortfolioManager + PositionManager singletons —
# replaces 270 PaperBalance state files with single account portfolio.
_portfolio_singleton = None  # type: ignore[var-annotated]
_position_manager_singleton = None  # type: ignore[var-annotated]
PORTFOLIO_STARTING_USD: float = float(_os.environ.get("POLARIS_PORTFOLIO_USD", "5000"))


def get_portfolio():
    """Lazy PortfolioManager singleton — single account, multi-strategy."""
    global _portfolio_singleton
    if _portfolio_singleton is None:
        from src.risk.portfolio_manager import PortfolioConfig, PortfolioManager
        cfg = PortfolioConfig(
            max_per_ticker_usd=float(_os.environ.get("POLARIS_MAX_PER_TICKER_USD", "1500")),
        )
        _portfolio_singleton = PortfolioManager(
            starting_cash_usd=PORTFOLIO_STARTING_USD,
            config=cfg,
        )
        logger.info(
            f"[PORTFOLIO] initialized cash=${PORTFOLIO_STARTING_USD:.0f} "
            f"per_ticker_cap=${cfg.max_per_ticker_usd:.0f}"
        )
    return _portfolio_singleton


def get_position_manager():
    """Lazy PositionManager singleton — wraps portfolio for exit monitoring."""
    global _position_manager_singleton
    if _position_manager_singleton is None:
        from src.risk.position_manager import PositionManager
        _position_manager_singleton = PositionManager(get_portfolio())
        logger.info("[POSITION-MGR] initialized")
    return _position_manager_singleton


# Phase 23.5: PortfolioPolicyManager singleton (orchestrator).
_portfolio_policy_manager_singleton = None  # type: ignore[var-annotated]


def get_pm_orchestrator():
    """Lazy PortfolioPolicyManager singleton — runs every 30s by default."""
    global _portfolio_policy_manager_singleton
    if _portfolio_policy_manager_singleton is None:
        from src.risk.portfolio_policy_manager import PortfolioPolicyManager
        from src.risk.opportunity_scanner import OpportunityScanner
        from src.risk.reallocation_decider import ReallocationDecider
        from src.risk.position_evaluator import PositionEvaluator
        cycle_s = float(_os.environ.get("POLARIS_PM_CYCLE_S", "30"))
        switching_pct = float(_os.environ.get("POLARIS_SWITCH_COST_PCT", "0.007"))
        _portfolio_policy_manager_singleton = PortfolioPolicyManager(
            portfolio=get_portfolio(),
            scanner=OpportunityScanner(scan_interval_s=cycle_s, top_n=10),
            decider=ReallocationDecider(switching_cost_pct=switching_pct),
            evaluator=PositionEvaluator(),
            cycle_interval_s=cycle_s,
        )
        logger.info(
            f"[PM-ORCH] initialized cycle={cycle_s}s switching_cost={switching_pct*100:.2f}%"
        )
    return _portfolio_policy_manager_singleton


def reset_portfolio_singletons() -> None:
    """Test helper — drop singletons."""
    global _portfolio_singleton, _position_manager_singleton
    global _portfolio_policy_manager_singleton
    _portfolio_singleton = None
    _position_manager_singleton = None
    _portfolio_policy_manager_singleton = None


def get_broker() -> Broker:
    """Lazy broker singleton — auto-selects based on env.

    Phase 14.2:
      POLARIS_LIVE_MODE=1 + OKX keys present → OKXBroker (demo by default)
      otherwise → PaperBroker (slippage_model simulation)
    """
    global _broker_singleton
    if _broker_singleton is None:
        if _okx_live_armed():
            # Phase 14.2 — live OKX (defaults to demo via x-simulated-trading)
            max_live_size = float(_os.environ.get("POLARIS_LIVE_MAX_USD", "500"))
            _broker_singleton = OKXBroker(max_size_usd=max_live_size)
            demo_str = "DEMO" if _os.environ.get("POLARIS_OKX_DEMO", "1") == "1" else "REAL-MONEY"
            logger.warning(
                f"[BROKER] OKXBroker armed mode={demo_str} max_size=${max_live_size:.0f}"
            )
        else:
            _broker_singleton = PaperBroker(fee_round_trip=LIVE_FEE_ROUND_TRIP)
            logger.info("[BROKER] PaperBroker (paper trading, no live exchange)")
    return _broker_singleton


def set_broker(broker: Broker) -> None:
    """Override broker singleton (used by tests / live promotion)."""
    global _broker_singleton
    _broker_singleton = broker


# Phase 13: cached portfolio halt check — refresh every 60s
_portfolio_halt_cache: tuple[int, bool] = (0, False)
_PORTFOLIO_HALT_REFRESH_MS: int = 60_000
_PORTFOLIO_SNAPSHOT_INTERVAL_MS: int = 300_000  # 5min
_last_portfolio_snapshot_ms: int = 0
PORTFOLIO_MAX_DRAWDOWN_PCT: float = 0.05  # ADR-010 daily 5% applied at portfolio level

# Phase 15 (P0-2 fix): last seen tick price per ticker — used by portfolio halt
# to MTM open positions. Without this, snapshot uses entry_price → blind to
# intraday open-loss drawdown.
_last_tick_price_per_ticker: dict[str, float] = {}


def _record_tick_price(ticker: str, price: float) -> None:
    """Record latest tick price for portfolio MTM. Shell."""
    if price > 0:
        _last_tick_price_per_ticker[ticker] = price


def _get_current_prices_snapshot() -> dict[str, float]:
    """Return shallow copy of last-seen prices (for portfolio MTM)."""
    return dict(_last_tick_price_per_ticker)


def _maybe_snapshot_portfolio(tick_ts_ms: int) -> None:
    """Phase 20.5 — snapshot now uses PortfolioManager state directly.

    Writes to SQL ledger every 5min for dashboard / audit.
    """
    global _last_portfolio_snapshot_ms
    if tick_ts_ms - _last_portfolio_snapshot_ms < _PORTFOLIO_SNAPSHOT_INTERVAL_MS:
        return
    try:
        portfolio = get_portfolio()
        prices = _get_current_prices_snapshot()
        equity = portfolio.equity(prices)
        global _portfolio_hwm_usd
        _portfolio_hwm_usd = max(_portfolio_hwm_usd, equity)
        drawdown = (
            (_portfolio_hwm_usd - equity) / _portfolio_hwm_usd
            if _portfolio_hwm_usd > 0 else 0.0
        )
        # Optional ledger persistence (not authoritative for halt)
        try:
            from src.paper.runner import _get_ledger
            led = _get_ledger()
            if led is not None:
                led.insert_portfolio_snapshot(
                    ts_ms=tick_ts_ms,
                    total_equity_usd=equity,
                    total_open=portfolio.n_open_contributions,
                    total_realized=portfolio.realized_pnl_usd(),
                    drawdown_pct=drawdown,
                    active_hypos=[h["hypo_id"] for h in REALTIME_HYPOS],
                )
        except Exception:
            pass
        _last_portfolio_snapshot_ms = tick_ts_ms
        logger.info(
            f"[PORTFOLIO-SNAP] equity=${equity:.0f} cash=${portfolio.cash:.0f} "
            f"open={portfolio.n_open_contributions} dd={drawdown*100:.2f}% "
            f"hwm=${_portfolio_hwm_usd:.0f} realized=${portfolio.realized_pnl_usd():+.2f}"
        )
    except Exception as e:
        logger.warning(f"portfolio snapshot write failed: {e!r}")


def _check_portfolio_halt(tick_ts_ms: int) -> bool:
    """Phase 20.5 — drawdown halt from PortfolioManager (single account state).

    Uses PortfolioManager's own equity vs starting_cash — no longer reads
    SQL ledger aggregation (which was per-strategy balances summing to
    inflated virtual capital). True portfolio drawdown.

    Cached 60s.
    """
    global _portfolio_halt_cache
    last_ts, last_halt = _portfolio_halt_cache
    if tick_ts_ms - last_ts < _PORTFOLIO_HALT_REFRESH_MS:
        return last_halt
    try:
        portfolio = get_portfolio()
        prices = _get_current_prices_snapshot()
        equity = portfolio.equity(prices)
        # HWM tracking — kept in module-level cache (not ledger snapshots)
        global _portfolio_hwm_usd
        _portfolio_hwm_usd = max(_portfolio_hwm_usd, equity)
        drawdown = (
            (_portfolio_hwm_usd - equity) / _portfolio_hwm_usd
            if _portfolio_hwm_usd > 0 else 0.0
        )
        halt = drawdown >= PORTFOLIO_MAX_DRAWDOWN_PCT
        _portfolio_halt_cache = (tick_ts_ms, halt)
        return halt
    except Exception as e:
        logger.warning(f"portfolio halt check failed: {e!r}")
        _portfolio_halt_cache = (tick_ts_ms, False)
        return False


# Module-level HWM (in-memory, resets with runner restart)
_portfolio_hwm_usd: float = 0.0


def _sync_legacy_state(
    ticker: str, strategy_name: str, hypo: dict, portfolio,
) -> None:
    """Phase 20.5 backward-compat — build per-(ticker, strategy) PaperBalance
    from portfolio state and persist via legacy save_state.

    Maintains JSON + SQL ledger compat for dashboard, daily_paper_runner,
    and existing tests. Does NOT change portfolio truth — pure mirror.
    """
    try:
        from src.paper.state import PaperBalance, Position as _P, PositionStatus as _PS
        pos_aggr = portfolio.get_position(ticker)
        opens = []
        if pos_aggr is not None:
            for c in pos_aggr.contributions:
                if c.is_closed or c.strategy_name != strategy_name:
                    continue
                opens.append(_P(
                    position_id=c.contribution_id, ticker=ticker, direction=1,
                    entry_price=c.entry_price, size_usd=c.size_usd,
                    open_ts_ms=c.open_ts_ms, fee_round_trip=c.fee_round_trip,
                ))
        # Closed contributions for this (ticker, strategy)
        closes = []
        for c in portfolio.closed_contributions():
            if c.ticker != ticker or c.strategy_name != strategy_name:
                continue
            if c.exit_price <= 0:
                continue
            closes.append(_P(
                position_id=c.contribution_id, ticker=ticker, direction=1,
                entry_price=c.entry_price, size_usd=c.size_usd,
                open_ts_ms=c.open_ts_ms, close_ts_ms=c.close_ts_ms,
                exit_price=c.exit_price, fee_round_trip=c.fee_round_trip,
                status=_PS.CLOSED,
            ))
        bal = PaperBalance(
            starting_usd=hypo.get("starting_usd", 5000.0),
            cash_usd=portfolio.cash,  # shared cash — informational
            open_positions=tuple(opens),
            closed_positions=tuple(closes),
        )
        _save_balance(ticker, strategy_name, bal)
    except Exception as e:
        logger.warning(f"legacy sync failed ({ticker}/{strategy_name}): {e!r}")


def _portfolio_daily_loss_breached(portfolio, tick_ts_ms: int) -> bool:
    """Phase 20.5 — daily loss breach at portfolio level.

    Returns True if today's realized PnL across all contributions <= -5% of
    starting cash (ADR-010 daily loss limit).
    """
    if not portfolio.closed_contributions():
        return False
    one_day_ms = 86_400_000
    day_start_ms = (tick_ts_ms // one_day_ms) * one_day_ms
    today_pnl = sum(
        c.realized_net_usd for c in portfolio.closed_contributions()
        if c.close_ts_ms >= day_start_ms
    )
    if today_pnl >= 0:
        return False
    loss_pct = abs(today_pnl) / portfolio.starting_cash
    return loss_pct >= 0.05


def _check_portfolio_exits(tick_ts_ms: int) -> None:
    """Phase 20.5 — runs PositionManager.check_exits for all open contributions.

    Called once per tick from tick handler (after _eval_and_act runs for all
    HYPOs). Real-time exit monitoring decoupled from entry strategy.
    """
    portfolio = get_portfolio()
    if portfolio.n_open_contributions == 0:
        return
    pm = get_position_manager()
    prices = _get_current_prices_snapshot()
    # Phase 21: build MarketContext per ticker for adaptive policies.
    btc_1d = _get_btc_1d_candles()
    regime_now = detect_regime(btc_1d) if btc_1d else "flat"
    btc_trend = "unknown"
    if len(btc_1d) >= 2:
        btc_trend = "up" if btc_1d[-1].close > btc_1d[-2].close else "down"
    from src.risk.position_policy import MarketContext
    market_ctxs = {}
    for ticker, price in prices.items():
        binance_sym = ticker.replace("-", "")
        funding = _funding_rate_cache.get(binance_sym) or 0.0
        market_ctxs[ticker] = MarketContext(
            ticker=ticker, price=price, ts_ms=tick_ts_ms,
            regime=regime_now, btc_trend=btc_trend, funding_8h=funding,
        )
    # Phase 20.7 (Codex P1 fix): pass per-(ticker, strategy) actions.
    events = pm.check_exits(
        prices, ts_ms=tick_ts_ms,
        last_signal_actions=dict(_strategy_last_action),
        market_contexts=market_ctxs,
    )
    for ev in events:
        # Phase 20.7 — broker SELL was already placed in PositionManager._execute_exit.
        # Here we just update cooldown maps + audit log + legacy state sync.
        _last_close_ms[(ev.ticker, ev.strategy_name)] = tick_ts_ms
        _last_close_ms_ticker[ev.ticker] = tick_ts_ms
        logger.info(
            f"[CLOSE] {ev.strategy_name} {ev.ticker} @{ev.exit_price:.6f} "
            f"reason={ev.exit_reason} net=${ev.realized_net_usd:+.2f} frac={ev.fraction:.2f}"
        )
        # Sync legacy state (JSON + SQL) for dashboard / tests.
        # Try to find owning hypo via strategy_name; if not in REALTIME_HYPOS
        # (test hypo, deprecated, etc.) sync with default fallback.
        synced = False
        for h in REALTIME_HYPOS:
            try:
                strat = _get_strategy(h, ev.ticker)
                if strat.name == ev.strategy_name:
                    _sync_legacy_state(ev.ticker, ev.strategy_name, h, portfolio)
                    synced = True
                    break
            except Exception:
                continue
        if not synced:
            # Default starting_usd 5000 — preserves test compat
            _sync_legacy_state(
                ev.ticker, ev.strategy_name,
                {"starting_usd": 5000.0}, portfolio,
            )


def _save_balance(ticker: str, strategy_name: str, balance: "PaperBalance") -> None:
    """Save balance to disk and update in-memory cache atomically.

    Phase 5 Codex fix: cache kept in sync — always write both cache + disk together.
    Called only on entry/exit state changes (not every tick).

    Shell function — reads/writes module-level _balance_cache.
    """
    _balance_cache[(ticker, strategy_name)] = balance
    save_state(ticker, strategy_name, balance)


def _eval_and_act(hypo: dict, ticker: str, tick_price: float, tick_ts_ms: int, full_tick: dict | None = None) -> None:
    """매 tick 호출 — strategy 평가 + 즉시 entry/exit.

    primary_tf == 'tick' 인 strategy는 candle 무관 — tick payload 직접 사용.

    Phase 10 (registry pattern): primary_tf with registered dispatcher takes
    precedence; otherwise legacy if/elif chain runs (for un-migrated branches).
    """
    strategy = _get_strategy(hypo, ticker)
    sname = strategy.name

    # 1. Strategy evaluate — Phase 18: registry-only dispatch.
    # Legacy if/elif chain removed; all 19 dispatchers registered at module load.
    primary = hypo.get("primary_tf")
    dispatcher = get_dispatcher(primary)
    if dispatcher is not None:
        _book_pre = get_book(ticker) or {}
        _tick_pre = full_tick if full_tick is not None else (get_tick(ticker) or {})
        _bid_pre = float((_tick_pre or {}).get("bid", 0) or 0)
        _ask_pre = float((_tick_pre or {}).get("ask", 0) or 0)
        ctx = DispatchContext(
            strategy=strategy, hypo=hypo, ticker=ticker,
            tick_ts_ms=tick_ts_ms, tick_price=tick_price,
            full_tick=full_tick, book=_book_pre, bid=_bid_pre, ask=_ask_pre,
        )
        signal = dispatcher(ctx)
        if signal is None:
            return
    elif primary in (None, ""):
        # No primary_tf set → fallback to candle-based evaluate (default path).
        candles = _refresh_candles(ticker, "1H")
        if len(candles) < strategy.min_window:
            return
        signal = strategy.evaluate(candles)
    else:
        # primary_tf set but no dispatcher registered → fallback to candle eval
        # using primary_tf as candle timeframe. Backward-compat path.
        candles = _refresh_candles(ticker, primary)
        if len(candles) < strategy.min_window:
            return
        signal = strategy.evaluate(candles)

    # Phase 20.5: Portfolio-based — exit handling via PositionManager.
    # Run exits at START of eval so subsequent entry decisions see updated
    # portfolio state (e.g. TP closes a slot, freeing cash for new entry).
    # Track strategy's last action for SignalReversal exit strategies.
    _strategy_last_action[(ticker, sname)] = signal.action.name  # "EXIT", "ENTER_LONG", "HOLD"
    _record_tick_price(ticker, tick_price)
    try:
        _check_portfolio_exits(tick_ts_ms)
    except Exception as e:
        logger.warning(f"in-eval exits err: {e!r}")

    # Phase 6 (slippage fix): fetch book + L1 quote once per tick.
    _book = get_book(ticker) or {}
    _tick_full = full_tick if full_tick is not None else (get_tick(ticker) or {})
    _bid = float((_tick_full or {}).get("bid", 0) or 0)
    _ask = float((_tick_full or {}).get("ask", 0) or 0)

    # Portfolio-level state (replaces per-strategy PaperBalance)
    portfolio = get_portfolio()
    pos_aggr = portfolio.get_position(ticker)

    # has_open = does this STRATEGY already have an open contribution on this ticker?
    has_open = False
    if pos_aggr is not None:
        has_open = any(
            (not c.is_closed) and c.strategy_name == sname
            for c in pos_aggr.contributions
        )

    # daily_breached at portfolio level (today's realized PnL < -5% of starting cash)
    daily_breached = _portfolio_daily_loss_breached(portfolio, tick_ts_ms)

    # Cooldown via _last_close_ms (preserved from old path; updated by PositionManager exit hook)
    last_close = _last_close_ms.get((ticker, sname), 0)
    last_close_ticker = _last_close_ms_ticker.get(ticker, 0)
    in_cooldown = (
        (last_close > 0 and (tick_ts_ms - last_close) < RE_ENTRY_COOLDOWN_MS)
        or (last_close_ticker > 0 and (tick_ts_ms - last_close_ticker) < RE_ENTRY_COOLDOWN_MS)
    )

    # closed_this_tick now handled by PositionManager run; no longer per-strategy.
    closed_this_tick = False
    # Round 14: HYPO-010 regime cluster pause guard (INSIGHT-029 — 5min 3+ SL → 10min entry block)
    in_regime_pause = (
        hypo["hypo_id"] == "HYPO-010-TICK"
        and _hypo010_pause_until_ms > 0
        and tick_ts_ms < _hypo010_pause_until_ms
    )
    # Phase 11: regime activation matrix — block strategy in mismatched regime.
    # Computes BTC 1D regime (cached 60s) and looks up REGIME_ACTIVATION.
    # Only applied to ENTER_LONG paths (exits always allowed).
    regime_blocked = False
    if signal.action == SignalAction.ENTER_LONG:
        from src.risk.regime_activation import block_reason as _regime_block_reason
        _btc_1d_for_regime = _get_btc_1d_candles()
        _curr_regime = detect_regime(_btc_1d_for_regime) if _btc_1d_for_regime else "flat"
        _block_msg = _regime_block_reason(sname, _curr_regime)
        if _block_msg:
            regime_blocked = True
            # Rate-limit log (5min/strategy) — avoids spam
            if not hasattr(_eval_and_act, "_regime_block_last"):
                _eval_and_act._regime_block_last = {}
            last_log = _eval_and_act._regime_block_last.get((sname, _curr_regime), 0)
            if tick_ts_ms - last_log > 300_000:
                logger.info(f"[REGIME-BLOCK] {hypo['hypo_id']} {ticker} {_block_msg}")
                _eval_and_act._regime_block_last[(sname, _curr_regime)] = tick_ts_ms

    # Phase 6 + Codex round-1: spread filter — skip entries when bid-ask too
    # wide (structural slippage). compute_spread_bps returns inf for invalid
    # quotes (zero/crossed) so we always skip in that case rather than
    # silently fall through to fill_price fallback.
    spread_too_wide = False
    if signal.action == SignalAction.ENTER_LONG:
        _spread_bps = compute_spread_bps(_bid, _ask)
        if should_skip_entry_spread(_spread_bps):
            spread_too_wide = True
            _spread_repr = "inf" if _spread_bps == float("inf") else f"{_spread_bps:.1f}bps"
            logger.info(
                f"[SPREAD-SKIP] {hypo['hypo_id']} {ticker} spread={_spread_repr} > 5bps"
            )

    # Phase 12.2 — pre-compute liq_cap so it joins entry gates (no longer
    # a post-gate check). _liq_skip = book missing/empty.
    _liq_cap_pre = 0.0
    _liq_skip_pre = False
    if signal.action == SignalAction.ENTER_LONG:
        _liq_cap_pre = compute_liquidity_cap("buy", _book, max_book_fraction=0.10)
        if _liq_cap_pre <= 0:
            _liq_skip_pre = True
            logger.info(
                f"[LIQ-SKIP] {hypo['hypo_id']} {ticker} no_book — skip entry"
            )

    # Phase 13: portfolio-level halt — global drawdown cap (5% default).
    # Cached snapshot 60s to avoid per-tick SQL aggregation.
    _portfolio_halt = False
    if signal.action == SignalAction.ENTER_LONG:
        _portfolio_halt = _check_portfolio_halt(tick_ts_ms)
        if _portfolio_halt:
            if not hasattr(_eval_and_act, "_pf_halt_log_last"):
                _eval_and_act._pf_halt_log_last = 0
            if tick_ts_ms - _eval_and_act._pf_halt_log_last > 60_000:
                logger.warning(
                    f"[PORTFOLIO-HALT] global drawdown >= cap — all entries blocked"
                )
                _eval_and_act._pf_halt_log_last = tick_ts_ms

    # Phase 12.2 — composite entry gate (replaces the long boolean chain).
    # All blockers consolidated into a single GateVerdict. Pure decision.
    from src.paper.entry_gates import evaluate_entry_gates as _eval_gates
    _gate = _eval_gates(
        has_open=has_open,
        daily_breached=daily_breached,
        closed_this_tick=closed_this_tick,
        in_cooldown=in_cooldown,
        in_regime_pause=in_regime_pause,
        spread_too_wide=spread_too_wide,
        regime_blocked=regime_blocked,
        liq_skip=_liq_skip_pre,
        portfolio_halt=_portfolio_halt,
    )

    if signal.action == SignalAction.ENTER_LONG and _gate.allow:
        # ── Phase 20.5: portfolio-based entry ──
        # Recent performance stats — derived from PORTFOLIO closed contributions
        # for THIS strategy (cross-ticker). More accurate than per-(ticker,strategy).
        strategy_closes = [
            c for c in portfolio.closed_contributions()
            if c.strategy_name == sname
        ]
        # Convert Contribution → pseudo-Position-like for compute_recent_stats
        from src.paper.state import Position as _Pos, PositionStatus as _PS
        pseudo_closed = [
            _Pos(
                position_id=c.contribution_id, ticker=c.ticker, direction=c.direction,
                entry_price=c.entry_price, size_usd=c.size_usd, open_ts_ms=c.open_ts_ms,
                close_ts_ms=c.close_ts_ms, exit_price=c.exit_price,
                fee_round_trip=c.fee_round_trip, status=_PS.CLOSED,
            ) for c in strategy_closes if c.exit_price > 0
        ]
        perf_stats = compute_recent_stats(pseudo_closed, lookback=20)

        # Regime — BTC 1D candles (cached 60s)
        btc_1d = _get_btc_1d_candles()
        regime = detect_regime(btc_1d)

        # Drawdown vs starting cash (portfolio-level)
        equity = portfolio.equity({ticker: tick_price})
        dd = max(0.0, (portfolio.starting_cash - equity) / portfolio.starting_cash)

        # Dynamic size (pure)
        n_closed = len(strategy_closes)
        sizing = compute_size(SizingInputs(
            cash_usd=portfolio.cash,
            signal_confidence=signal.confidence,
            recent_win_rate=perf_stats["win_rate"],
            recent_avg_win_pct=perf_stats["avg_win_pct"],
            recent_avg_loss_pct=perf_stats["avg_loss_pct"],
            regime=regime,
            drawdown_pct=dd,
        ), n_trades=n_closed)
        hard_cap = equity * 0.30
        _liq_cap = _liq_cap_pre
        size = min(sizing.size_usd, hard_cap, portfolio.cash, _liq_cap if _liq_cap > 0 else float("inf"))

        logger.info(
            f"[DYN-SIZE] {hypo['hypo_id']} {ticker} size=${size:.0f} "
            f"liq_cap=${_liq_cap:.0f} regime={regime} {sizing.reason}"
        )

        if size > 0:
            # Place broker order (PaperBroker or OKXBroker)
            _result = get_broker().place_order(OrderRequest(
                side=OrderSide.BUY,
                ticker=ticker,
                size_usd=size,
                order_type=OrderType.MARKET,
                client_order_id=f"{sname[:8]}{tick_ts_ms}",
            ))
            if _result.status != OrderStatus.FILLED:
                logger.warning(
                    f"[ENTRY-REJECTED] {hypo['hypo_id']} {ticker} "
                    f"status={_result.status.value} {_result.error_msg}"
                )
            else:
                # Build exit_strategies for this contribution from hypo profile
                exit_profile = hypo.get("exit_profile", "scalp")
                from src.exec.exit_strategies import build_default_exits
                exit_strats = build_default_exits(exit_profile)
                # Add SignalReversal as supplementary exit (entry strategy still has voice)
                from src.exec.exit_strategies import SignalReversal
                exit_strats = exit_strats + (SignalReversal(sname),)

                contrib = portfolio.process_entry(
                    ticker=ticker,
                    strategy_name=sname,
                    hypo_id=hypo["hypo_id"],
                    size_usd=size,
                    fill_price=_result.avg_fill_price,
                    ts_ms=tick_ts_ms,
                    exit_strategies=exit_strats,
                    fee_round_trip=LIVE_FEE_ROUND_TRIP,
                    signal_confidence=signal.confidence,
                    signal_reason=signal.reason,
                    regime=regime,
                )
                if contrib is not None:
                    logger.info(
                        f"[OPEN] {hypo['hypo_id']} {ticker} @{_result.avg_fill_price:.6f} "
                        f"(last={tick_price:.6f}, slip={_result.slippage_bps:.1f}bps) "
                        f"size=${size:.2f} cid={contrib.contribution_id[:16]} "
                        f"broker={get_broker().__class__.__name__}"
                    )
                    # Phase 21: assign default adaptive policy on first entry
                    # for this ticker. Subsequent entries (merge) get the same
                    # policy which detects merge and unifies exits.
                    pmgr = get_position_manager()
                    if ticker not in pmgr._policies:
                        from src.risk.adaptive_policies import build_default_composite
                        pmgr.assign_policy(ticker, build_default_composite())
                    # Phase 20.5 backward-compat: sync to JSON + SQL.
                    _sync_legacy_state(ticker, sname, hypo, portfolio)


# ───── Tick callback ─────


def _get_all_closed_for_hypo(hypo: dict) -> list:
    """Collect all closed positions across all tickers for a HYPO.

    Shell function — uses balance cache (falls back to disk on cache miss).
    Strategy singleton used to get sname without re-instantiating.
    """
    # Get sname via singleton (first ticker as key — name is strategy-global)
    first_ticker = hypo["tickers"][0] if hypo["tickers"] else None
    if first_ticker is None:
        return []
    strategy = _get_strategy(hypo, first_ticker)
    sname = strategy.name
    closed: list = []
    for ticker in hypo["tickers"]:
        try:
            bal = _load_balance(ticker, sname, hypo["starting_usd"])
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
        # Phase 15 (P0-2 fix): record latest price for portfolio MTM
        _record_tick_price(inst_id, tick_price)
        # Phase 15 round-2 fix: snapshot write from tick handler (independent
        # of signal action) — ensures HWM rolls forward even in low-signal periods.
        _maybe_snapshot_portfolio(tick_ts)
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

        # Phase 20.5: portfolio-level exit monitoring — runs per tick
        # AFTER all per-(ticker, hypo) signal evaluations complete.
        try:
            _check_portfolio_exits(tick_ts)
        except Exception as e:
            logger.error(f"portfolio exits err: {e!r}")

        # Phase 23.5: PM orchestrator — periodic (default 30s) active
        # capital reallocation. Throttled internally; cheap when not running.
        try:
            _run_pm_cycle(tick_ts)
        except Exception as e:
            logger.error(f"pm cycle err: {e!r}")
    return handler


def _run_pm_cycle(tick_ts_ms: int) -> None:
    """Phase 23.5 — PortfolioPolicyManager cycle.

    Builds candidate signals (all REALTIME_HYPOS × tickers), invokes
    PM.cycle which:
      1. Scans for opportunities (cached, periodic)
      2. Evaluates each open contribution (HOT/WARM/COLD/LOSING)
      3. Decides per-contribution action (HOLD/CLOSE/ROTATE/ADD)
      4. Executes atomically (close-then-open guarded)

    Cycle is throttled by PortfolioPolicyManager.should_run.
    """
    pm = get_pm_orchestrator()
    if not pm.should_run(tick_ts_ms):
        return
    portfolio = get_portfolio()
    if portfolio.n_open_contributions == 0:
        # No positions to manage; still runs scan to populate cache.
        pass

    # Build candidate signals — all (ticker, hypo) combos active
    candidates: list[tuple[str, str, str]] = []
    for h in REALTIME_HYPOS:
        for t in h.get("tickers", []):
            try:
                strat = _get_strategy(h, t)
                candidates.append((t, strat.name, h["hypo_id"]))
            except Exception:
                continue

    # Signal eval function — invokes existing dispatchers, builds Opportunity
    def _signal_eval_fn(ticker: str, strategy_name: str, hypo_id: str):
        # Locate hypo
        hypo = next((h for h in REALTIME_HYPOS if h["hypo_id"] == hypo_id), None)
        if hypo is None:
            return None
        try:
            strategy = _get_strategy(hypo, ticker)
            primary = hypo.get("primary_tf")
            dispatcher = get_dispatcher(primary)
            if dispatcher is None:
                return None
            tick_full = get_tick(ticker) or {}
            tick_price = float(tick_full.get("last", 0) or 0)
            if tick_price <= 0:
                return None
            book = get_book(ticker) or {}
            bid = float(tick_full.get("bid", 0) or 0)
            ask = float(tick_full.get("ask", 0) or 0)
            ctx = DispatchContext(
                strategy=strategy, hypo=hypo, ticker=ticker,
                tick_ts_ms=tick_ts_ms, tick_price=tick_price,
                full_tick=tick_full, book=book, bid=bid, ask=ask,
            )
            sig = dispatcher(ctx)
            if sig is None or sig.action != SignalAction.ENTER_LONG:
                return None
            from src.risk.opportunity_scanner import Opportunity
            # Historical EV proxy: confidence × 0.01 (1% nominal)
            er = sig.confidence * 0.01
            return Opportunity(
                ticker=ticker, strategy_name=strategy_name, hypo_id=hypo_id,
                signal_confidence=sig.confidence,
                historical_ev_pct=0.01,
                expected_return_pct=er,
                signal_reason=sig.reason or "",
                ts_ms=tick_ts_ms,
            )
        except Exception:
            return None

    def _recent_prices_fn(ticker: str) -> list[float]:
        buf = _price_history_60s.get(ticker)
        if not buf:
            return []
        return [p for _ts, p in buf][-30:]

    prices = _get_current_prices_snapshot()
    pm.cycle(
        ts_ms=tick_ts_ms,
        current_prices=prices,
        candidate_signals=candidates,
        signal_eval_fn=_signal_eval_fn,
        recent_prices_fn=_recent_prices_fn,
    )


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
        # Phase 8: funding poll task — populate _funding_rate_cache for HYPO-027/036/AI.
        # Symbols derived from REALTIME_HYPOS entries with `_binance_futures_syms`.
        funding_syms_set: set[str] = set()
        for h in REALTIME_HYPOS:
            for s in h.get("_binance_futures_syms", []):
                funding_syms_set.add(s)
        if funding_syms_set:
            funding_syms = sorted(funding_syms_set)
            logger.info(f"Funding poll subscribe: {funding_syms}")
            tasks.add(asyncio.create_task(_poll_funding_rates(funding_syms)))
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


# ───── Phase 10: Registered dispatchers (registry pattern) ─────
#
# Migrated dispatchers — replace if/elif chain in `_eval_and_act`.
# Each function takes a DispatchContext and returns Signal | None.
# (None = data not ready, runner aborts early. SignalAction.HOLD = real HOLD.)


# --- Phase 18: All 19 dispatchers registered (was if/elif chain in _eval_and_act) ---


def _rate_limit(fn, key, ts_ms: int, interval_ms: int) -> bool:
    """Rate-limit helper — returns True if ok to log (and updates timestamp)."""
    cache_attr = "_rl_cache"
    if not hasattr(fn, cache_attr):
        setattr(fn, cache_attr, {})
    cache = getattr(fn, cache_attr)
    last = cache.get(key, 0)
    if ts_ms - last >= interval_ms:
        cache[key] = ts_ms
        return True
    return False


def _dispatch_candle_factory(timeframe: str):
    """Phase 19: factory for candle-based dispatchers (1m/5m/15m/1H/4H/1D).
    Each registered with timeframe as primary_tf so no candle fallback needed.
    """
    def _dispatch_candle(ctx: DispatchContext) -> Signal | None:
        candles = _refresh_candles(ctx.ticker, timeframe)
        if len(candles) < ctx.strategy.min_window:
            return None
        return ctx.strategy.evaluate(candles)
    _dispatch_candle.__name__ = f"_dispatch_candle_{timeframe}"
    return _dispatch_candle


# Register candle dispatchers for all common timeframes used in REALTIME_HYPOS
for _tf in ("1m", "5m", "15m", "1H", "4H", "1D"):
    register_dispatcher(_tf)(_dispatch_candle_factory(_tf))


@register_dispatcher("tick")
def _dispatch_tick(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    return ctx.strategy.evaluate_tick(ctx.full_tick)


@register_dispatcher("book")
def _dispatch_book(ctx: DispatchContext) -> Signal | None:
    imb = compute_book_imbalance(ctx.ticker)
    if imb is None or ctx.full_tick is None:
        return None
    return ctx.strategy.evaluate_book(ctx.full_tick, imb)


@register_dispatcher("flow")
def _dispatch_flow(ctx: DispatchContext) -> Signal | None:
    ratio = compute_taker_buy_ratio(ctx.ticker, window=100)
    n_trades = len(get_recent_trades(ctx.ticker, 100))
    if ratio is None or ctx.full_tick is None:
        return None
    return ctx.strategy.evaluate_flow(ctx.full_tick, ratio, n_trades)


@register_dispatcher("ofi")
def _dispatch_ofi(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    trades_recent = get_recent_trades(ctx.ticker, 50)
    return ctx.strategy.evaluate_ofi(ctx.full_tick, trades_recent)


@register_dispatcher("cascade")
def _dispatch_cascade(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    btc_state = _get_cascade_state("BTC-USDT", get_last_price("BTC-USDT") or 0.0, ctx.tick_ts_ms)
    eth_state = _get_cascade_state("ETH-USDT", get_last_price("ETH-USDT") or 0.0, ctx.tick_ts_ms)
    if btc_state is None or eth_state is None:
        if _rate_limit(_dispatch_cascade, ctx.ticker, ctx.tick_ts_ms, 60_000):
            logger.info(
                f"[CASCADE-WARMUP] {ctx.ticker} btc_ready={btc_state is not None}"
                f" eth_ready={eth_state is not None}"
            )
        return None
    return ctx.strategy.evaluate_cascade(ctx.full_tick, btc_state, eth_state, now_ms=ctx.tick_ts_ms)


@register_dispatcher("cross")
def _dispatch_cross(ctx: DispatchContext) -> Signal | None:
    binance_sym = ctx.ticker.replace("-", "")
    b_ratio = binance_taker_buy_ratio(binance_sym, window=100)
    b_vol = binance_volatility_bps(binance_sym, window=50)
    b_last = binance_get_last_trade(binance_sym)
    if ctx.full_tick is None or b_last is None:
        if _rate_limit(_dispatch_cross, ctx.ticker, ctx.tick_ts_ms, 300_000):
            logger.warning(f"[BLEAD-NOFEED] {ctx.ticker} b_last={b_last} (Binance trades 미공급)")
        return None
    binance_state = {
        "taker_buy_ratio": b_ratio,
        "n_trades": len(binance_get_recent_trades(binance_sym, 100)),
        "volatility_bps": b_vol,
        "last_price": b_last[3],
        "last_trade_ts_ms": b_last[0],
        "now_ms": int(time.time() * 1000),
    }
    sig = ctx.strategy.evaluate_cross(ctx.full_tick, binance_state)
    if sig.action == SignalAction.HOLD:
        if _rate_limit(_dispatch_cross, f"hold-{ctx.ticker}", ctx.tick_ts_ms, 60_000):
            logger.info(f"[BLEAD-HOLD] {ctx.ticker} {sig.reason}")
    return sig


@register_dispatcher("ai")
def _dispatch_ai(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    last_price = float(ctx.full_tick.get("last", 0) or 0)
    open_24h = float(ctx.full_tick.get("open24h", last_price) or last_price)
    change_24h = (last_price - open_24h) / open_24h * 100.0 if open_24h > 0 else 0.0
    imb = compute_book_imbalance(ctx.ticker)
    bid_depth_usd = ask_depth_usd = 0.0
    if imb is not None:
        vol24h = float(ctx.full_tick.get("vol24h", 0) or 0) * last_price
        mid = vol24h / 2.0
        bid_depth_usd = mid * (1 + imb)
        ask_depth_usd = mid * (1 - imb)
    taker_buy_ratio = compute_taker_buy_ratio(ctx.ticker, window=100) or 0.5
    candles_1h = _refresh_candles(ctx.ticker, "1H")
    rsi_1h_val = "N/A"
    trend_4h_val = trend_1d_val = "unknown"
    if len(candles_1h) >= 14:
        closes = [c.close for c in candles_1h[-15:]]
        gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            rsi_1h_val = round(100 - 100 / (1 + rs), 1)
        else:
            rsi_1h_val = 100.0
    btc_1d = _get_btc_1d_candles()
    if len(btc_1d) >= 2:
        last_close = btc_1d[-1].close
        prev_close = btc_1d[-2].close
        trend_1d_val = "up" if last_close > prev_close else "down"
        trend_4h_val = trend_1d_val
    regime_val = detect_regime(btc_1d) if btc_1d else "unknown"
    binance_sym = ctx.ticker.replace("-", "")
    funding_8h = _funding_rate_cache.get(binance_sym, 0.0) or 0.0
    trades = get_recent_trades(ctx.ticker, 100)
    vpin_val = 0.0
    if trades:
        buy_vol = sum(size * price for _, side, size, price in trades if side == "buy")
        total_vol = sum(size * price for _, side, size, price in trades)
        vpin_val = abs(buy_vol / total_vol - 0.5) * 2 if total_vol > 0 else 0.0
    market_state = {
        "ticker": ctx.ticker, "last": last_price, "change_24h": change_24h,
        "rsi_1h": rsi_1h_val, "trend_4h": trend_4h_val, "trend_1d": trend_1d_val,
        "bid_depth_usd": bid_depth_usd, "ask_depth_usd": ask_depth_usd,
        "taker_buy_ratio": taker_buy_ratio, "vpin": vpin_val,
        "funding_8h": funding_8h, "regime": regime_val,
    }
    sig = ctx.strategy.evaluate_ai(market_state)
    if sig.action == SignalAction.HOLD and "rate_limit" not in sig.reason:
        if _rate_limit(_dispatch_ai, ctx.ticker, ctx.tick_ts_ms, 300_000):
            logger.info(f"[AI-HOLD] {ctx.ticker} {sig.reason}")
    return sig


@register_dispatcher("mta")
def _dispatch_mta(ctx: DispatchContext) -> Signal | None:
    tf_data = fetch_multi_tf(ctx.ticker, timeframes=("1D", "4H", "1H", "15m"))
    if not all(tf_data.get(tf) for tf in ("1D", "4H", "1H", "15m")):
        return None
    TF_MAX_STALE_MS = {"15m": 30*60_000, "1H": 90*60_000, "4H": 6*3600_000, "1D": 36*3600_000}
    for tf in ("15m", "1H", "4H", "1D"):
        if ctx.tick_ts_ms - tf_data[tf][-1].timestamp_ms > TF_MAX_STALE_MS[tf]:
            return None
    sig = ctx.strategy.evaluate_multi_tf(tf_data)
    if sig.action == SignalAction.HOLD:
        if _rate_limit(_dispatch_mta, ctx.ticker, ctx.tick_ts_ms, 300_000):
            logger.info(f"[MTA-HOLD] {ctx.ticker} {sig.reason}")
    return sig


@register_dispatcher("nfi")
def _dispatch_nfi(ctx: DispatchContext) -> Signal | None:
    tf_data = fetch_multi_tf(ctx.ticker, timeframes=("5m", "15m", "1H", "4H"))
    if not tf_data.get("1H") or not tf_data.get("4H"):
        return None
    TF_MAX_STALE_NFI = {"5m": 15*60_000, "15m": 30*60_000, "1H": 90*60_000, "4H": 6*3_600_000}
    for tf, max_ms in TF_MAX_STALE_NFI.items():
        if tf_data.get(tf) and ctx.tick_ts_ms - tf_data[tf][-1].timestamp_ms > max_ms:
            return None
    sig = ctx.strategy.evaluate_multi_tf(tf_data)
    if sig.action == SignalAction.HOLD:
        if _rate_limit(_dispatch_nfi, ctx.ticker, ctx.tick_ts_ms, 300_000):
            logger.info(f"[NFI-DIP-HOLD] {ctx.ticker} {sig.reason}")
    elif sig.action == SignalAction.ENTER_LONG:
        logger.info(f"[NFI-DIP] {ctx.ticker} ENTRY confidence={sig.confidence:.2f} {sig.reason}")
    return sig


@register_dispatcher("liquidation")
def _dispatch_liquidation(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    binance_sym = ctx.ticker.replace("-", "")
    liq_pressure = compute_liquidation_pressure(binance_sym, lookback_ms=300_000)
    buf = _price_history_60s.get(ctx.ticker)
    price_60s_ago = None
    if buf and len(buf) >= 2:
        oldest_ts, oldest_price = buf[0]
        if ctx.tick_ts_ms - oldest_ts >= 50_000:
            price_60s_ago = oldest_price
    sig = ctx.strategy.evaluate_cascade(ctx.full_tick, liq_pressure, price_60s_ago=price_60s_ago)
    if sig.action == SignalAction.HOLD:
        if _rate_limit(_dispatch_liquidation, ctx.ticker, ctx.tick_ts_ms, 300_000):
            pressure_str = (
                f"${liq_pressure['total_usd']:,.0f} imb={liq_pressure['imbalance']:+.3f}"
                if liq_pressure else "no_data"
            )
            logger.info(f"[LIQ-CASCADE-HOLD] {ctx.ticker} pressure={pressure_str} {sig.reason}")
    elif sig.action == SignalAction.ENTER_LONG:
        pressure_str = (
            f"${liq_pressure['total_usd']:,.0f} imb={liq_pressure['imbalance']:+.3f}"
            if liq_pressure else "?"
        )
        logger.info(f"[LIQ-CASCADE] {ctx.ticker} ENTRY SIGNAL pressure={pressure_str} {sig.reason}")
    return sig


@register_dispatcher("gap")
def _dispatch_gap(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    binance_sym = ctx.ticker.replace("-", "")
    from src.data.binance_ws import get_book_ticker
    b_book = get_book_ticker(binance_sym)
    if b_book is None:
        if _rate_limit(_dispatch_gap, ctx.ticker, ctx.tick_ts_ms, 300_000):
            logger.warning(f"[GAP-NOFEED] {ctx.ticker} Binance bookTicker 미공급")
        return None
    binance_price_state = {
        "price": b_book.get("bid", 0.0),
        "ts_ms": b_book.get("ts_ms", 0),
        "now_ms": int(time.time() * 1000),
    }
    return ctx.strategy.evaluate_cross(ctx.full_tick, binance_price_state)


@register_dispatcher("delta")
def _dispatch_delta(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    trades = get_recent_trades(ctx.ticker, 200)
    return ctx.strategy.evaluate_delta(ctx.full_tick, trades)


@register_dispatcher("wall")
def _dispatch_wall(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    book = get_book(ctx.ticker)
    if book is None:
        return None
    return ctx.strategy.evaluate_book(ctx.full_tick, book)


@register_dispatcher("funding")
def _dispatch_funding(ctx: DispatchContext) -> Signal | None:
    binance_sym = ctx.ticker.replace("-", "")
    funding = _funding_rate_cache.get(binance_sym)
    multiplier = ctx.strategy.compute_multiplier(funding)
    ts_val = int((ctx.full_tick or {}).get("ts", 0) or 0) or 1
    if multiplier == 0.0:
        return Signal(timestamp_ms=ts_val, action=SignalAction.HOLD, confidence=0.0,
                      reason=f"funding_block rate={funding}")
    elif multiplier > 1.0:
        return Signal(timestamp_ms=ts_val, action=SignalAction.ENTER_LONG, confidence=0.70,
                      target_size_usd=200.0,
                      reason=f"funding_squeeze rate={funding} boost=x{multiplier:.2f}")
    return Signal(timestamp_ms=ts_val, action=SignalAction.HOLD, confidence=0.0,
                  reason=f"funding_neutral rate={funding}")


@register_dispatcher("grid")
def _dispatch_grid(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    candles_1h = _refresh_candles(ctx.ticker, "1H")
    atr_pct: Optional[float] = None
    if len(candles_1h) >= 14:
        last_price_val = float(ctx.full_tick.get("last", 0) or 0)
        if last_price_val > 0:
            trs = []
            for i in range(1, min(15, len(candles_1h))):
                c = candles_1h[-i]
                prev_c = candles_1h[-i - 1] if i < len(candles_1h) - 1 else c
                tr = max(c.high - c.low, abs(c.high - prev_c.close), abs(c.low - prev_c.close))
                trs.append(tr)
            atr_val = sum(trs) / len(trs) if trs else 0.0
            atr_pct = atr_val / last_price_val
    high_24h_val = float(ctx.full_tick.get("high24h", 0) or 0) or None
    low_24h_val = float(ctx.full_tick.get("low24h", 0) or 0) or None
    has_open_grid = any(
        p.ticker == ctx.ticker
        for p in _load_balance(ctx.ticker, ctx.strategy.name, ctx.hypo["starting_usd"]).open_positions
    )
    sig = ctx.strategy.evaluate_grid(ctx.full_tick, atr_pct, high_24h_val, low_24h_val, is_active=has_open_grid)
    if sig.action == SignalAction.HOLD:
        if _rate_limit(_dispatch_grid, ctx.ticker, ctx.tick_ts_ms, 600_000):
            logger.info(f"[GRID-HOLD] {ctx.ticker} atr={atr_pct and f'{atr_pct*100:.2f}%' or 'N/A'} {sig.reason}")
    return sig


@register_dispatcher("burst")
def _dispatch_burst(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    price_5s_ago = _get_price_5s_ago(ctx.ticker, ctx.tick_ts_ms)
    return ctx.strategy.evaluate_tick(ctx.full_tick, price_5s_ago)


@register_dispatcher("vpin")
def _dispatch_vpin(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    trades = get_recent_trades(ctx.ticker, 200)
    buckets: list[dict] = []
    bucket_size = 10
    for i in range(0, len(trades), bucket_size):
        chunk = trades[i:i + bucket_size]
        if not chunk:
            continue
        buy_vol = sum(size * price for _ts_ms, side, size, price in chunk if side == "buy")
        sell_vol = sum(size * price for _ts_ms, side, size, price in chunk if side == "sell")
        buckets.append({"buy_vol": buy_vol, "sell_vol": sell_vol})
    sig = ctx.strategy.evaluate_vpin(ctx.full_tick, buckets)
    if sig.action == SignalAction.HOLD:
        if _rate_limit(_dispatch_vpin, ctx.ticker, ctx.tick_ts_ms, 300_000):
            logger.info(f"[VPIN-HOLD] {ctx.ticker} {sig.reason}")
    return sig


@register_dispatcher("btclag")
def _dispatch_btclag(ctx: DispatchContext) -> Signal | None:
    if ctx.full_tick is None:
        return None
    buf_btc = _price_history_60s.get("BTC-USDT")
    if buf_btc is None or len(buf_btc) < 2:
        if _rate_limit(_dispatch_btclag, ctx.ticker, ctx.tick_ts_ms, 60_000):
            logger.info(f"[BTCLAG-WARMUP] {ctx.ticker} BTC price history not ready")
        return None
    btc_price_now = buf_btc[-1][1]
    btc_price_5min_ago = buf_btc[0][1]
    btc_ts_ms = buf_btc[-1][0]
    btc_state_for_lag = {
        "price_now": btc_price_now,
        "price_5min_ago": btc_price_5min_ago,
        "ts_ms": btc_ts_ms,
    }
    buf_alt = _price_history_60s.get(ctx.ticker)
    alt_5min_delta_pct = 0.0
    if buf_alt and len(buf_alt) >= 2:
        alt_old = buf_alt[0][1]
        alt_now = buf_alt[-1][1]
        alt_5min_delta_pct = (alt_now - alt_old) / alt_old if alt_old > 0 else 0.0
    alt_24h_open = float(ctx.full_tick.get("open24h", 0) or 0)
    alt_last = float(ctx.full_tick.get("last", 0) or 0)
    alt_24h_delta_pct = (alt_last - alt_24h_open) / alt_24h_open if alt_24h_open > 0 else 0.0
    alt_context = {"alt_5min_delta_pct": alt_5min_delta_pct, "alt_24h_delta_pct": alt_24h_delta_pct}
    sig = ctx.strategy.evaluate_lag(ctx.full_tick, btc_state_for_lag, alt_context, now_ms=ctx.tick_ts_ms)
    if sig.action == SignalAction.HOLD:
        if _rate_limit(_dispatch_btclag, f"hold-{ctx.ticker}", ctx.tick_ts_ms, 300_000):
            logger.info(f"[BTCLAG-HOLD] {ctx.ticker} {sig.reason}")
    return sig


@register_dispatcher("carry")
def _dispatch_carry(ctx: DispatchContext) -> Signal | None:
    """HYPO-036 Funding Carry — funding-triggered SPOT entry (Liu & Yu 2024)."""
    binance_sym = ctx.ticker.replace("-", "")
    funding = _funding_rate_cache.get(binance_sym)
    bal = _load_balance(ctx.ticker, ctx.strategy.name, ctx.hypo["starting_usd"])
    open_pos = next(
        (p for p in bal.open_positions if p.ticker == ctx.ticker), None,
    )
    in_position = open_pos is not None
    age_h = (
        (ctx.tick_ts_ms - open_pos.open_ts_ms) / 3_600_000.0 if open_pos else 0.0
    )
    sig = ctx.strategy.evaluate_funding(
        funding_8h=funding, ts_ms=ctx.tick_ts_ms,
        position_age_hours=age_h, in_position=in_position,
    )
    # Rate-limited HOLD log (5min/ticker)
    if sig.action == SignalAction.HOLD:
        if not hasattr(_dispatch_carry, "_hold_last"):
            _dispatch_carry._hold_last = {}  # type: ignore[attr-defined]
        last_log = _dispatch_carry._hold_last.get(ctx.ticker, 0)  # type: ignore[attr-defined]
        if ctx.tick_ts_ms - last_log > 300_000:
            logger.info(
                f"[CARRY-HOLD] {ctx.ticker} funding={funding} {sig.reason}"
            )
            _dispatch_carry._hold_last[ctx.ticker] = ctx.tick_ts_ms  # type: ignore[attr-defined]
    elif sig.action == SignalAction.ENTER_LONG:
        logger.info(
            f"[CARRY-ENTER] {ctx.ticker} funding={funding} "
            f"conf={sig.confidence:.2f} {sig.reason}"
        )
    return sig


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
