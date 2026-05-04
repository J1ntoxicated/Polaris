"""Multi-Timeframe Analysis (MTA) Confluence — pure (P6).

HYPOTHESIS-010: high-probability long entry — 3-of-4 timeframe confluence.

Codex Round 1 권고 (Phase 2g Round 2):
- 4/4 hard confluence는 trade-zero risk → 3-of-4 scoring
- 4H pullback + 15m bullish = MANDATORY (price-action core)
- 1H RSI<48 = SOFT (1점)
- 1D uptrend = SOFT (1점) — regime filter
- 4 of 4 weak signals 평균 ≥ 3점 통과
- 금지: 1H RSI<40 hard

Exit:
- 1H RSI > 65 → EXIT
- TP/SL은 runner (TP +0.6% / SL -0.35%)

특이: evaluate(window) 대신 evaluate_multi_tf(tf_data) 사용.
"""
from __future__ import annotations

from statistics import mean

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy
from src.strategies.rsi_mean_reversion import compute_rsi


def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return mean(values[-period:])


class MTAConfluence(Strategy):
    """Multi-Timeframe Confluence — 4 timeframe 모두 정합 시 entry.

    NOTE: standard `evaluate(window)` 대신 `evaluate_multi_tf(tf_data)` 사용.
    cron / runner에서는 multi-tf wrapper 필요.
    """

    name = "mta_confluence"
    min_window = 50  # primary tf (15m) 기준

    def __init__(
        self,
        target_size_usd: float = 100.0,
        rsi_soft_threshold: float = 52.0,  # Phase 2g Round 6 fix: 48→52 (BTC RSI 49.6 통과)
        rsi_overbought: float = 65.0,
        min_score: int = 2,                 # Phase 2g Round 6 fix: 3→2 (mandatory + 1점이면 진입)
    ):
        self.target_size_usd = target_size_usd
        self.rsi_soft_threshold = rsi_soft_threshold
        self.rsi_overbought = rsi_overbought
        self.min_score = min_score

    def evaluate(self, window: list[Candle]) -> Signal:
        """Single-tf fallback — confluence 평가 불가, HOLD 반환."""
        ts = window[-1].timestamp_ms if window else 0
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason="MTA requires multi-tf data — use evaluate_multi_tf",
        )

    def evaluate_multi_tf(self, tf_data: dict[str, list[Candle]]) -> Signal:
        """4 timeframe 평가 → confluence signal."""
        d1 = tf_data.get("1D", [])
        h4 = tf_data.get("4H", [])
        h1 = tf_data.get("1H", [])
        m15 = tf_data.get("15m", [])

        if not (len(d1) >= 50 and len(h4) >= 50 and len(h1) >= 14 and len(m15) >= 2):
            return Signal(
                timestamp_ms=m15[-1].timestamp_ms if m15 else 0,
                action=SignalAction.HOLD, confidence=0.0,
                reason="insufficient multi-tf data",
            )

        d1_closes = [c.close for c in d1]
        h4_closes = [c.close for c in h4]
        h1_closes = [c.close for c in h1]
        ts = m15[-1].timestamp_ms

        # 1D uptrend (regime filter — soft, 1점)
        d1_sma20 = _sma(d1_closes, 20)
        d1_sma50 = _sma(d1_closes, 50)
        d1_uptrend = d1_sma20 > d1_sma50

        # 4H pullback (mandatory — price-action core)
        h4_sma20 = _sma(h4_closes, 20)
        h4_sma50 = _sma(h4_closes, 50)
        h4_close = h4_closes[-1]
        h4_pullback = h4_close < h4_sma20 and h4_close > h4_sma50

        # 1H RSI soft (1점)
        h1_rsi = compute_rsi(h1_closes, 14)
        h1_trigger = h1_rsi < self.rsi_soft_threshold

        # 15m bullish candle (mandatory — entry timing)
        # Phase 2g Round 6 Fix B: close > open 단독 (prev_close 비교 제거 — entry timing 단순화)
        m15_curr = m15[-1]
        m15_bullish = m15_curr.close > m15_curr.open

        # Exit: 1H RSI overbought
        if h1_rsi > self.rsi_overbought:
            return Signal(
                timestamp_ms=ts, action=SignalAction.EXIT, confidence=0.7,
                reason=f"1h RSI {h1_rsi:.1f} > {self.rsi_overbought} overbought",
            )

        # Codex Round 1 권고: 4H pullback + 15m bullish 둘 다 필수, 그 외 score >= min_score
        # Phase 2g Round 7 fix: 1D downtrend 시 effective_min_score 자동 상향
        #   → downtrend에서 mandatory only(score=2) 진입 차단 (INSIGHT-021 flip-flop 방지)
        mandatory_pass = h4_pullback and m15_bullish
        score = int(d1_uptrend) + int(h4_pullback) + int(h1_trigger) + int(m15_bullish)
        effective_min_score = self.min_score if d1_uptrend else self.min_score + 1

        if mandatory_pass and score >= effective_min_score:
            return Signal(
                timestamp_ms=ts, action=SignalAction.ENTER_LONG,
                confidence=0.7 + 0.05 * (score - effective_min_score),
                target_size_usd=self.target_size_usd,
                reason=(
                    f"MTA score={score}/4 (effective_min={effective_min_score}) mandatory=PASS "
                    f"1d↑={d1_uptrend} h4_pull={h4_pullback} 1h_rsi={h1_rsi:.1f} 15m_bull={m15_bullish}"
                ),
            )

        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=(
                f"score={score}/4 (effective_min={effective_min_score}) mandatory={mandatory_pass} "
                f"1d↑={d1_uptrend} h4_pull={h4_pullback} 1h_rsi={h1_rsi:.1f} 15m_bull={m15_bullish}"
            ),
        )
