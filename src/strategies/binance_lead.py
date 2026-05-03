"""Binance Leading Signal — pure (P6).

HYPOTHESIS-014 (Codex DEBATE Round 3, 84% 합의 + INSIGHT-022):

가설: Binance가 BTC 가격발견 주도 venue (Stalder-Cosenza 2025, Alexander-Heck 2024).
0.1-0.5s lead 활용 → OKX entry 직전 Binance trade flow 확인.

Logic (`evaluate_cross(okx_tick, binance_state)`):
- Binance 최근 100 trades taker_buy_ratio > 0.65 (강한 매수 우위)
- AND Binance 최근 가격 변동성 (50 trades) > 8 bps (활발한 시장)
- AND OKX-Binance price gap < 30 bps (정상 spread, abnormal 차이 시 skip)
- → ENTER_LONG (Binance leads → OKX 따라옴 가정)

Exit:
- Binance taker_buy_ratio < 0.40 → EXIT (Binance에서 매도 압력 시작)
- 그 외는 runner TP/SL/min_hold 처리

Hysteresis (Codex Round 4 패턴 반영):
- 0.65 entry / 0.40 exit (deadzone 0.40-0.65) — 충분한 confluence

Risk guards:
- Binance state stale (last trade > 30s old) → HOLD
- Cross-exchange price gap > 0.5% → HOLD (data quality / liquidity 이상)
- min Binance trades 30 (insufficient sample → HOLD)
"""
from __future__ import annotations

import time

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_BUY_RATIO = 0.65
DEFAULT_SELL_RATIO = 0.40
DEFAULT_MIN_TRADES = 30
DEFAULT_MIN_VOLATILITY_BPS = 8.0
DEFAULT_MAX_GAP_BPS = 30.0
DEFAULT_STALE_MS = 30_000
DEFAULT_TARGET_SIZE_USD = 100.0


class BinanceLeadSignal(Strategy):
    """Cross-exchange leading signal — Binance trade flow drives OKX entry.

    NOTE: standard `evaluate(window)` 미사용. `evaluate_cross(okx_tick, binance_state)` 호출.
    """

    name = "binance_lead"
    min_window = 1

    def __init__(
        self,
        buy_ratio: float = DEFAULT_BUY_RATIO,
        sell_ratio: float = DEFAULT_SELL_RATIO,
        min_trades: int = DEFAULT_MIN_TRADES,
        min_volatility_bps: float = DEFAULT_MIN_VOLATILITY_BPS,
        max_gap_bps: float = DEFAULT_MAX_GAP_BPS,
        stale_ms: int = DEFAULT_STALE_MS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.buy_ratio = buy_ratio
        self.sell_ratio = sell_ratio
        self.min_trades = min_trades
        self.min_volatility_bps = min_volatility_bps
        self.max_gap_bps = max_gap_bps
        self.stale_ms = stale_ms
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 0
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason="BinanceLeadSignal requires cross — use evaluate_cross",
        )

    def evaluate_cross(self, okx_tick: dict, binance_state: dict) -> Signal:
        """Cross-exchange evaluation.

        Args:
            okx_tick: OKX tick (last/bid/ask/ts).
            binance_state: dict with keys:
                - taker_buy_ratio: float | None
                - n_trades: int
                - volatility_bps: float | None
                - last_price: float | None
                - last_trade_ts_ms: int | None
        """
        ts = int(okx_tick.get("ts", 0) or 0)
        okx_last = float(okx_tick.get("last", 0) or 0)

        b_ratio = binance_state.get("taker_buy_ratio")
        b_n = binance_state.get("n_trades", 0)
        b_vol = binance_state.get("volatility_bps")
        b_price = binance_state.get("last_price")
        b_ts = binance_state.get("last_trade_ts_ms")

        # Guard 1: insufficient sample
        if b_n < self.min_trades or b_ratio is None or b_price is None:
            return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                          reason=f"insufficient binance n={b_n}")

        # Guard 2: stale (Fix 2, Codex Round 4)
        # Use runner-injected now_ms (local clock) if present — avoids cross-exchange
        # clock skew where OKX ts and Binance ts use different exchange servers.
        # Fallback: ts (OKX exchange clock) — less accurate but safe.
        now_ms = binance_state.get("now_ms") or ts
        if b_ts is None or (now_ms > 0 and now_ms - b_ts > self.stale_ms):
            return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                          reason=f"binance stale {b_ts}")

        # Guard 3: price gap quality
        if okx_last > 0 and b_price > 0:
            gap_bps = abs(okx_last - b_price) / okx_last * 10000
            if gap_bps > self.max_gap_bps:
                return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                              reason=f"abnormal gap {gap_bps:.1f}bps")

        # Exit signal
        if b_ratio <= self.sell_ratio:
            return Signal(
                timestamp_ms=ts, action=SignalAction.EXIT, confidence=0.7,
                reason=f"binance sell aggr {b_ratio:.2f} <= {self.sell_ratio}",
            )

        # Entry: strong buy + active market
        if b_ratio >= self.buy_ratio:
            if b_vol is None or b_vol < self.min_volatility_bps:
                return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                              reason=f"binance vol {b_vol or 0:.1f}bps too low")
            return Signal(
                timestamp_ms=ts, action=SignalAction.ENTER_LONG, confidence=0.8,
                target_size_usd=self.target_size_usd,
                reason=(
                    f"binance lead buy={b_ratio:.2f} vol={b_vol:.1f}bps "
                    f"n={b_n} gap={(okx_last-b_price)/okx_last*10000:+.1f}bps"
                ),
            )

        return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                      reason=f"binance buy {b_ratio:.2f} (deadzone)")
