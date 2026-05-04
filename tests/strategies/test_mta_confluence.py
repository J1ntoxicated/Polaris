"""tests/strategies/test_mta_confluence.py — 3-of-4 scoring (Phase 2g Round 2)."""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import SignalAction
from src.strategies.mta_confluence import MTAConfluence


def _candles(n: int, base: float = 100.0, slope: float = 0.0) -> list[Candle]:
    return [
        Candle(
            timestamp_ms=(i + 1) * 60_000,
            open=base * (1 + i * slope),
            high=base * (1 + i * slope) * 1.005,
            low=base * (1 + i * slope) * 0.995,
            close=base * (1 + i * slope),
            volume=100,
        )
        for i in range(n)
    ]


def _build_data(d1_slope=0.001, h4_slope=0.0, h1_slope=0.0, m15_bull=False) -> dict:
    d1 = _candles(60, slope=d1_slope)
    h4 = _candles(60, slope=h4_slope)
    h1 = _candles(20, slope=h1_slope)
    m15 = _candles(4)
    if m15_bull:
        # 마지막 candle bullish: open<close & close>prev_close
        last = m15[-1]
        m15[-1] = Candle(
            timestamp_ms=last.timestamp_ms,
            open=last.close * 0.995, high=last.close * 1.01, low=last.close * 0.99,
            close=last.close * 1.005, volume=100,
        )
    return {"1D": d1, "4H": h4, "1H": h1, "15m": m15}


class TestThresholds:
    def test_default_min_score_3(self) -> None:
        # Phase 2g Round 6 fix: 3→2 (mandatory + 1점이면 진입)
        s = MTAConfluence()
        assert s.min_score == 2

    def test_default_rsi_soft_48(self) -> None:
        # Phase 2g Round 6 fix: 48→52 (BTC RSI 49.6 통과)
        s = MTAConfluence()
        assert s.rsi_soft_threshold == 52.0

    def test_default_target_size_100(self) -> None:
        s = MTAConfluence()
        assert s.target_size_usd == 100.0


class TestInsufficientData:
    def test_short_data_holds(self) -> None:
        s = MTAConfluence()
        sig = s.evaluate_multi_tf({"1D": _candles(10), "4H": _candles(10),
                                   "1H": _candles(5), "15m": _candles(2)})
        assert sig.action == SignalAction.HOLD


class TestExitCondition:
    def test_overbought_rsi_exits(self) -> None:
        # h1 strong uptrend → RSI 100 > 65 → EXIT
        s = MTAConfluence()
        sig = s.evaluate_multi_tf(_build_data(d1_slope=0.001, h1_slope=0.05, m15_bull=True))
        assert sig.action == SignalAction.EXIT


class TestMandatoryGate:
    def test_no_mandatory_no_entry(self) -> None:
        # h1 downward → low RSI → no overbought exit
        # 4H flat → pullback FALSE → mandatory FAIL → HOLD (entry 차단)
        s = MTAConfluence()
        sig = s.evaluate_multi_tf(_build_data(d1_slope=0.001, h4_slope=0.0,
                                               h1_slope=-0.001, m15_bull=True))
        assert sig.action == SignalAction.HOLD
        assert "mandatory=False" in sig.reason


class TestRelaxedThreshold:
    """Phase 2g Round 6 — Codex B 완전형 threshold 완화 검증."""

    def _build_relaxed_data(self) -> dict:
        """BTC dry-run 재현: 1d↑=True, h4_pull=True, rsi≈50, m15 단일 bullish candle."""
        # 1D uptrend: slope 양수 → SMA20 > SMA50 성립
        d1 = _candles(60, base=100.0, slope=0.001)

        # 4H pullback: SMA50 < close < SMA20 성립하려면
        # close가 중간값 필요. SMA20 약간 위, SMA50 약간 아래
        # slope=0.0 + 마지막 close 조작으로 pullback 구성
        h4 = _candles(60, base=100.0, slope=0.0)
        # SMA20 = 100.0, SMA50 = 100.0 → close도 100.0 → pullback(close<sma20) FAIL
        # pullback 조건: close < sma20 AND close > sma50
        # sma50 < sma20이어야 의미있음. slope 미세 양수 시 sma20 > sma50
        # h4 slope 양수 + 마지막 close를 sma20보다 살짝 아래로 조작
        h4_slope = 0.0005
        h4 = _candles(60, base=95.0, slope=h4_slope)
        last_h4 = h4[-1]
        from statistics import mean as _mean
        sma20 = _mean([c.close for c in h4[-20:]])
        sma50 = _mean([c.close for c in h4[-50:]])
        # close를 sma50 < close < sma20 사이로 설정
        target_close = (sma20 + sma50) / 2
        h4[-1] = Candle(
            timestamp_ms=last_h4.timestamp_ms,
            open=target_close * 1.001,
            high=target_close * 1.005,
            low=target_close * 0.998,
            close=target_close,
            volume=100,
        )

        # 1H: RSI ≈ 50 — neutral (rsi_soft_threshold=52이면 h1_trigger=True)
        # RSI 50 미만 유도: 최근 몇 candle 하락
        h1 = _candles(20, base=100.0, slope=0.0)
        # 마지막 몇 개 약간 하락 → RSI 50 미만 유도
        for i in range(-5, 0):
            c = h1[i]
            h1[i] = Candle(
                timestamp_ms=c.timestamp_ms,
                open=c.open, high=c.high, low=c.low,
                close=c.close * 0.998,  # 약 0.2% 하락
                volume=100,
            )

        # 15m: close > open (단일 bullish candle — Fix B)
        m15 = _candles(4, base=100.0, slope=0.0)
        last = m15[-1]
        m15[-1] = Candle(
            timestamp_ms=last.timestamp_ms,
            open=last.close * 0.997,   # open < close
            high=last.close * 1.003,
            low=last.close * 0.996,
            close=last.close,           # close > open
            volume=100,
        )

        return {"1D": d1, "4H": h4, "1H": h1, "15m": m15}

    def test_relaxed_threshold_enters(self) -> None:
        """RSI 50, 15m 단일 bullish, h4 pullback, 1d up → min_score=2 충족 → ENTER_LONG."""
        s = MTAConfluence(
            target_size_usd=100.0,
            rsi_soft_threshold=52.0,
            min_score=2,
        )
        sig = s.evaluate_multi_tf(self._build_relaxed_data())
        assert sig.action == SignalAction.ENTER_LONG, (
            f"expected ENTER_LONG but got {sig.action}: {sig.reason}"
        )

    def test_relaxed_defaults_enter(self) -> None:
        """기본값(rsi_soft=52, min_score=2)으로 동일 데이터 → ENTER_LONG."""
        s = MTAConfluence()  # 기본값 사용
        sig = s.evaluate_multi_tf(self._build_relaxed_data())
        assert sig.action == SignalAction.ENTER_LONG, (
            f"expected ENTER_LONG with defaults but got {sig.action}: {sig.reason}"
        )

    def test_m15_single_candle_bull_sufficient(self) -> None:
        """Fix B: 15m close > open 단독으로 m15_bullish 충족 (prev_close 비교 불필요)."""
        s = MTAConfluence(min_score=2, rsi_soft_threshold=52.0)
        # m15 마지막 candle: close > open but close < prev.close (Fix B 이전엔 False였던 케이스)
        data = self._build_relaxed_data()
        m15 = data["15m"]
        last = m15[-1]
        prev = m15[-2]
        # close를 prev.close보다 낮게, 하지만 open보다는 높게
        new_close = prev.close * 0.999  # close < prev.close
        new_open = new_close * 0.997    # open < close
        m15[-1] = Candle(
            timestamp_ms=last.timestamp_ms,
            open=new_open,
            high=new_close * 1.002,
            low=new_open * 0.998,
            close=new_close,
            volume=100,
        )
        data["15m"] = m15
        sig = s.evaluate_multi_tf(data)
        # Fix B 적용 후: close>open이면 m15_bullish=True → mandatory 가능
        # (h4_pullback도 True인 케이스여야 하므로 action 확인보다 reason 확인)
        assert "15m_bull=True" in sig.reason, (
            f"Fix B: 15m close>open should set 15m_bull=True, got: {sig.reason}"
        )


class TestRound7DowntrendGuard:
    """Phase 2g Round 7 — 1D downtrend 시 effective_min_score 자동 상향 검증.

    1D downtrend + mandatory만(score=2) → effective_min=3 → HOLD (flip-flop 차단).
    1D uptrend + mandatory만(score=2) → effective_min=2 → ENTER (기존 동작 유지).
    1D downtrend + 추가 1점(score=3) → effective_min=3 → ENTER (고품질 신호 허용).
    """

    def _build_pullback_data(
        self,
        d1_uptrend: bool,
        h1_rsi_low: bool,
    ) -> dict:
        """h4_pullback=True, m15_bull=True 고정, 1D방향과 1H RSI만 제어."""
        from statistics import mean as _mean

        # 1D: uptrend/downtrend 제어
        if d1_uptrend:
            d1 = _candles(60, base=100.0, slope=0.001)   # SMA20 > SMA50
        else:
            d1 = _candles(60, base=100.0, slope=-0.001)  # SMA20 < SMA50

        # 4H pullback: SMA20 > close > SMA50 성립
        h4 = _candles(60, base=95.0, slope=0.0005)
        sma20 = _mean([c.close for c in h4[-20:]])
        sma50 = _mean([c.close for c in h4[-50:]])
        target_close = (sma20 + sma50) / 2
        last_h4 = h4[-1]
        h4[-1] = Candle(
            timestamp_ms=last_h4.timestamp_ms,
            open=target_close * 1.001,
            high=target_close * 1.005,
            low=target_close * 0.998,
            close=target_close,
            volume=100,
        )

        # 1H RSI: low=True → RSI < 52 (h1_trigger=True, 1점), False → RSI > 52 (0점)
        if h1_rsi_low:
            # 최근 하락 → RSI < 52 (h1_trigger=True, 1점 획득)
            h1 = _candles(20, base=100.0, slope=0.0)
            for i in range(-5, 0):
                c = h1[i]
                h1[i] = Candle(
                    timestamp_ms=c.timestamp_ms,
                    open=c.open, high=c.high, low=c.low,
                    close=c.close * 0.998, volume=100,
                )
        else:
            # RSI ~57 대역 (52 < RSI < 65 → h1_trigger=False, overbought 미달)
            # 3 candle 중 2 up(+0.3%) + 1 down(-0.5%) → RSI 57 근방
            h1 = _candles(20, base=100.0, slope=0.0)
            base_close = 100.0
            new_closes: list[float] = []
            for i in range(20):
                if i % 3 == 2:
                    base_close *= 0.995  # 1 down
                else:
                    base_close *= 1.003  # 2 up
                new_closes.append(base_close)
            for i in range(20):
                c = h1[i]
                cl = new_closes[i]
                h1[i] = Candle(
                    timestamp_ms=c.timestamp_ms,
                    open=cl * 0.9995,
                    high=cl * 1.002,
                    low=cl * 0.998,
                    close=cl,
                    volume=100,
                )

        # 15m: close > open (bullish — mandatory)
        m15 = _candles(4, base=100.0, slope=0.0)
        last = m15[-1]
        m15[-1] = Candle(
            timestamp_ms=last.timestamp_ms,
            open=last.close * 0.997,
            high=last.close * 1.003,
            low=last.close * 0.996,
            close=last.close,
            volume=100,
        )

        return {"1D": d1, "4H": h4, "1H": h1, "15m": m15}

    def test_downtrend_blocks_mandatory_only(self) -> None:
        """1D downtrend + mandatory(4H+15m)만 = score=2, effective_min=3 → HOLD.

        flip-flop fee bleed 핵심 차단 케이스 (INSIGHT-021).
        """
        s = MTAConfluence(min_score=2, rsi_soft_threshold=52.0)
        data = self._build_pullback_data(d1_uptrend=False, h1_rsi_low=False)
        sig = s.evaluate_multi_tf(data)
        assert sig.action == SignalAction.HOLD, (
            f"Round 7: 1D downtrend + mandatory only should HOLD, got {sig.action}: {sig.reason}"
        )
        assert "effective_min=3" in sig.reason, (
            f"Round 7: reason must expose effective_min=3, got: {sig.reason}"
        )

    def test_uptrend_allows_mandatory_only(self) -> None:
        """1D uptrend + mandatory(4H+15m)만 = score=2, effective_min=2 → ENTER_LONG.

        기존 동작(min_score=2) uptrend 환경에서 유지.
        """
        s = MTAConfluence(min_score=2, rsi_soft_threshold=52.0)
        data = self._build_pullback_data(d1_uptrend=True, h1_rsi_low=False)
        sig = s.evaluate_multi_tf(data)
        assert sig.action == SignalAction.ENTER_LONG, (
            f"Round 7: 1D uptrend + mandatory only should ENTER_LONG, got {sig.action}: {sig.reason}"
        )
        assert "effective_min=2" in sig.reason, (
            f"Round 7: reason must expose effective_min=2, got: {sig.reason}"
        )

    def test_downtrend_allows_score_3(self) -> None:
        """1D downtrend + mandatory + 1H RSI low = score=3, effective_min=3 → ENTER_LONG.

        downtrend여도 3점(score=effective_min) 충족 시 진입 허용.
        """
        s = MTAConfluence(min_score=2, rsi_soft_threshold=52.0)
        data = self._build_pullback_data(d1_uptrend=False, h1_rsi_low=True)
        sig = s.evaluate_multi_tf(data)
        assert sig.action == SignalAction.ENTER_LONG, (
            f"Round 7: 1D downtrend + score=3 should ENTER_LONG, got {sig.action}: {sig.reason}"
        )
        assert "effective_min=3" in sig.reason, (
            f"Round 7: reason must expose effective_min=3, got: {sig.reason}"
        )
