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
        s = MTAConfluence()
        assert s.min_score == 3

    def test_default_rsi_soft_48(self) -> None:
        s = MTAConfluence()
        assert s.rsi_soft_threshold == 48.0

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
