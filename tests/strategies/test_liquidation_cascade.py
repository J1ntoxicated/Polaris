"""tests/strategies/test_liquidation_cascade.py — Liquidation Cascade TDD (HYPO-023).

TDD: 실패 테스트 먼저 작성 → 구현 후 통과.

Spec (HYPOTHESIS-023):
- liq_pressure.total_usd >= 1_000_000 (큰 cascade 발생)
- liq_pressure.imbalance < -0.6 (short 청산 dominant = 강제 매수 압력 종료 → over-extension)
  → SPOT-only: short 청산 dominant + price drop = 과매도 → mean-revert LONG
- tick.last drop > 0.4% (60s 기준 panic 신호)
- → ENTER_LONG

Exit:
- pressure 완화 (total < $300k) → EXIT
- imbalance 정상 화 → EXIT (no longer distorted)

HOLD:
- total_usd 미달
- long 청산 dominant (imbalance > -0.6: SPOT-only에서 short 진입 불가 → skip)
- panic drop 미충족
- liq_pressure None
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.signal import SignalAction
from src.strategies.liquidation_cascade import (
    DEFAULT_IMBALANCE_THRESHOLD,
    DEFAULT_MIN_TOTAL_USD,
    DEFAULT_PRICE_DROP_THRESHOLD,
    DEFAULT_TARGET_SIZE_USD,
    LiquidationCascade,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

NOW_MS = 1_000_000_000


def _tick(last: float = 64_500.0, ts: int = NOW_MS) -> dict:
    return {"last": last, "ts": ts}


def _pressure(
    total_usd: float = 2_000_000.0,
    long_liq_usd: float = 200_000.0,
    short_liq_usd: float = 1_800_000.0,
    count: int = 5,
) -> dict:
    total = long_liq_usd + short_liq_usd
    imbalance = (long_liq_usd - short_liq_usd) / total if total > 0 else 0.0
    return {
        "total_usd": total_usd,
        "long_liq_usd": long_liq_usd,
        "short_liq_usd": short_liq_usd,
        "imbalance": imbalance,
        "count": count,
    }


# ── Default constants ─────────────────────────────────────────────────────────


class TestDefaultConstants:
    def test_min_total_usd(self) -> None:
        assert DEFAULT_MIN_TOTAL_USD == pytest.approx(1_000_000.0)

    def test_imbalance_threshold(self) -> None:
        """임계값 < 0 (short 청산 dominant = imbalance < threshold)."""
        assert DEFAULT_IMBALANCE_THRESHOLD == pytest.approx(-0.6)

    def test_price_drop_threshold(self) -> None:
        assert DEFAULT_PRICE_DROP_THRESHOLD == pytest.approx(0.004)

    def test_target_size_usd(self) -> None:
        assert DEFAULT_TARGET_SIZE_USD == pytest.approx(200.0)


# ── Core entry logic ──────────────────────────────────────────────────────────


class TestEntryConditions:
    def test_short_liq_dominant_with_panic_drop_enters(self) -> None:
        """Happy path: short 청산 dominant ($1.8M/$2M) + 1% drop → ENTER_LONG."""
        strat = LiquidationCascade()
        # short 청산 dominant: imbalance < -0.6
        p = _pressure(total_usd=2_000_000.0, long_liq_usd=200_000.0, short_liq_usd=1_800_000.0)
        assert p["imbalance"] < -0.6
        tick = _tick(last=64_500.0)
        price_60s_ago = 65_200.0  # 1.07% drop → above panic threshold
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price_60s_ago)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == pytest.approx(DEFAULT_TARGET_SIZE_USD)
        assert sig.confidence > 0.0

    def test_long_liq_dominant_holds(self) -> None:
        """long 청산 dominant (imbalance > -0.6) → SPOT-only에서 진입 불가 → HOLD."""
        strat = LiquidationCascade()
        # long 청산 dominant: short_liq 적음 → imbalance > 0
        p = _pressure(total_usd=2_000_000.0, long_liq_usd=1_800_000.0, short_liq_usd=200_000.0)
        assert p["imbalance"] > -0.6
        tick = _tick(last=64_500.0)
        price_60s_ago = 65_500.0  # big drop
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price_60s_ago)
        assert sig.action == SignalAction.HOLD

    def test_below_min_total_usd_holds(self) -> None:
        """total_usd $800k < $1M threshold → HOLD."""
        strat = LiquidationCascade()
        p = _pressure(total_usd=800_000.0, long_liq_usd=80_000.0, short_liq_usd=720_000.0)
        tick = _tick(last=64_500.0)
        price_60s_ago = 65_500.0
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price_60s_ago)
        assert sig.action == SignalAction.HOLD

    def test_no_panic_drop_holds(self) -> None:
        """short 청산 dominant but price flat (no panic) → HOLD."""
        strat = LiquidationCascade()
        p = _pressure(total_usd=2_000_000.0, long_liq_usd=200_000.0, short_liq_usd=1_800_000.0)
        tick = _tick(last=65_000.0)
        price_60s_ago = 65_100.0  # only 0.15% drop < 0.4% threshold → HOLD
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price_60s_ago)
        assert sig.action == SignalAction.HOLD

    def test_no_price_60s_ago_still_enters(self) -> None:
        """price_60s_ago=None → price guard 스킵, pressure 조건만으로 ENTER_LONG.

        Note: price 정보 없을 때 더 보수적으로 처리해야 하지만 spec은
        price_60s_ago=None → drop 조건 무시하고 진입 허용.
        """
        strat = LiquidationCascade()
        p = _pressure(total_usd=2_000_000.0, long_liq_usd=200_000.0, short_liq_usd=1_800_000.0)
        tick = _tick(last=64_500.0)
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=None)
        assert sig.action == SignalAction.ENTER_LONG

    def test_none_pressure_holds(self) -> None:
        """liq_pressure=None → HOLD."""
        strat = LiquidationCascade()
        tick = _tick(last=64_500.0)
        sig = strat.evaluate_cascade(tick, None, price_60s_ago=65_000.0)
        assert sig.action == SignalAction.HOLD

    def test_exactly_at_total_threshold_enters(self) -> None:
        """total_usd == $1M (경계) → ENTER_LONG."""
        strat = LiquidationCascade()
        p = _pressure(total_usd=1_000_000.0, long_liq_usd=100_000.0, short_liq_usd=900_000.0)
        assert p["imbalance"] < -0.6  # -0.8 — dominant
        tick = _tick(last=64_500.0)
        price_60s_ago = 65_200.0  # 1.07% drop
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price_60s_ago)
        assert sig.action == SignalAction.ENTER_LONG

    def test_clearly_above_panic_threshold_enters(self) -> None:
        """drop 0.5% > 0.4% threshold → ENTER_LONG (clearly above threshold)."""
        strat = LiquidationCascade()
        p = _pressure(total_usd=2_000_000.0, long_liq_usd=200_000.0, short_liq_usd=1_800_000.0)
        tick_price = 64_500.0
        # drop = 0.5% — clearly above 0.4% threshold
        price_60s_ago = tick_price / (1.0 - 0.005)
        tick = _tick(last=tick_price)
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price_60s_ago)
        assert sig.action == SignalAction.ENTER_LONG


# ── Exit logic ────────────────────────────────────────────────────────────────


class TestExitLogic:
    def test_pressure_recovered_exits(self) -> None:
        """total_usd < $300k → pressure 완화 → EXIT."""
        strat = LiquidationCascade()
        p = _pressure(total_usd=200_000.0, long_liq_usd=20_000.0, short_liq_usd=180_000.0)
        tick = _tick(last=64_500.0)
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=64_800.0)
        assert sig.action == SignalAction.EXIT

    def test_none_pressure_when_in_position_exits(self) -> None:
        """liq_pressure=None (liquidations stopped) → EXIT (청산 폭풍 종료)."""
        strat = LiquidationCascade()
        tick = _tick(last=64_500.0)
        # evaluate_cascade with None → HOLD (no position context — spec HOLD for entry)
        # EXIT는 runner level에서 처리 (strategy는 HOLD 반환)
        sig = strat.evaluate_cascade(tick, None, price_60s_ago=64_800.0)
        assert sig.action == SignalAction.HOLD  # no pressure info = HOLD, not EXIT


# ── evaluate() stub ───────────────────────────────────────────────────────────


class TestEvaluateStub:
    def test_evaluate_stub_returns_hold(self) -> None:
        """Standard evaluate(window) → HOLD (candle 기반 불가)."""
        from src.domain.candle import Candle
        strat = LiquidationCascade()
        c = Candle(timestamp_ms=1, open=1, high=1, low=1, close=1, volume=1)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD

    def test_strategy_name(self) -> None:
        assert LiquidationCascade.name == "liquidation_cascade"

    def test_min_window(self) -> None:
        assert LiquidationCascade.min_window == 1


# ── Custom params ─────────────────────────────────────────────────────────────


class TestCustomParams:
    def test_custom_min_total_usd(self) -> None:
        """낮은 임계값: $500k → 더 민감한 entry."""
        strat = LiquidationCascade(min_total_usd=500_000.0)
        p = _pressure(total_usd=700_000.0, long_liq_usd=70_000.0, short_liq_usd=630_000.0)
        tick = _tick(last=64_500.0)
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=65_200.0)
        assert sig.action == SignalAction.ENTER_LONG

    def test_custom_target_size_usd(self) -> None:
        strat = LiquidationCascade(target_size_usd=500.0)
        p = _pressure(total_usd=2_000_000.0, long_liq_usd=200_000.0, short_liq_usd=1_800_000.0)
        tick = _tick(last=64_500.0)
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=65_200.0)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == pytest.approx(500.0)


# ── Property-based ────────────────────────────────────────────────────────────


class TestProperties:
    @given(
        total_usd=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
        imbalance=st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        price=st.floats(min_value=0.001, max_value=1e6, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_no_crash_on_any_input(
        self, total_usd: float, imbalance: float, price: float
    ) -> None:
        """임의 입력에 crash 없음."""
        strat = LiquidationCascade()
        long_usd = total_usd * max(0.0, (1 + imbalance) / 2)
        short_usd = total_usd * max(0.0, (1 - imbalance) / 2)
        p = {
            "total_usd": total_usd,
            "long_liq_usd": long_usd,
            "short_liq_usd": short_usd,
            "imbalance": imbalance,
            "count": 1,
        }
        tick = {"last": price, "ts": NOW_MS}
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price * 1.01)
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.EXIT, SignalAction.HOLD)

    @given(
        total_usd=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
        imbalance=st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        price=st.floats(min_value=0.001, max_value=1e6, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_enter_long_has_positive_size(
        self, total_usd: float, imbalance: float, price: float
    ) -> None:
        """ENTER_LONG signal의 target_size_usd > 0 (Signal invariant)."""
        strat = LiquidationCascade()
        long_usd = total_usd * max(0.0, (1 + imbalance) / 2)
        short_usd = total_usd * max(0.0, (1 - imbalance) / 2)
        p = {
            "total_usd": total_usd,
            "long_liq_usd": long_usd,
            "short_liq_usd": short_usd,
            "imbalance": imbalance,
            "count": 1,
        }
        tick = {"last": price, "ts": NOW_MS}
        sig = strat.evaluate_cascade(tick, p, price_60s_ago=price * 1.01)
        if sig.action == SignalAction.ENTER_LONG:
            assert sig.target_size_usd > 0.0
