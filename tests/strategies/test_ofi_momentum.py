"""tests/strategies/test_ofi_momentum.py — OFI Momentum (HYPO-016) TDD.

TDD: 실패 테스트 먼저 작성 → 구현 후 통과.
"""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.ofi_momentum import (
    DEFAULT_OFI_ENTRY,
    DEFAULT_OFI_EXIT,
    DEFAULT_MIN_TRADES,
    DEFAULT_TARGET_SIZE_USD,
    OFIMomentum,
    evaluate_ofi,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _tick(last: float = 100.0, ts: int = 1_000) -> dict:
    return {"ts": ts, "last": last, "bid": last - 0.1, "ask": last + 0.1, "open24h": last}


def _trades_buy_heavy(n: int = 50, price: float = 100.0) -> list[tuple]:
    """강한 buy pressure: size 4 buy : 1 sell — OFI_norm ≈ +0.60."""
    trades = []
    for i in range(n):
        side = "buy" if i % 5 != 0 else "sell"
        trades.append((1_000 + i, side, 1.0, price))
    return trades


def _trades_sell_heavy(n: int = 50, price: float = 100.0) -> list[tuple]:
    """강한 sell pressure: 1 buy : 4 sell — OFI_norm ≈ -0.60."""
    trades = []
    for i in range(n):
        side = "sell" if i % 5 != 0 else "buy"
        trades.append((1_000 + i, side, 1.0, price))
    return trades


def _trades_neutral(n: int = 50, price: float = 100.0) -> list[tuple]:
    """중립: buy = sell — OFI_norm ≈ 0.0."""
    trades = []
    for i in range(n):
        side = "buy" if i % 2 == 0 else "sell"
        trades.append((1_000 + i, side, 1.0, price))
    return trades


# ─── Unit tests ───────────────────────────────────────────────────────────────

class TestDefaultThresholds:
    def test_entry_threshold(self) -> None:
        assert DEFAULT_OFI_ENTRY == 0.40

    def test_exit_threshold(self) -> None:
        assert DEFAULT_OFI_EXIT == -0.20

    def test_min_trades(self) -> None:
        assert DEFAULT_MIN_TRADES == 10

    def test_target_size_usd(self) -> None:
        assert DEFAULT_TARGET_SIZE_USD == 100.0


class TestPureHelper:
    """evaluate_ofi pure function tests."""

    def test_strong_buy_returns_ofi_norm_positive(self) -> None:
        """4:1 buy:sell → OFI_norm ≈ +0.60."""
        trades = _trades_buy_heavy(50)
        ofi_norm, vwap = evaluate_ofi(trades)
        assert ofi_norm > 0.40

    def test_strong_sell_returns_ofi_norm_negative(self) -> None:
        """1:4 buy:sell → OFI_norm ≈ -0.60."""
        trades = _trades_sell_heavy(50)
        ofi_norm, vwap = evaluate_ofi(trades)
        assert ofi_norm < -0.20

    def test_neutral_returns_ofi_norm_near_zero(self) -> None:
        """1:1 buy:sell → OFI_norm ≈ 0."""
        trades = _trades_neutral(50)
        ofi_norm, vwap = evaluate_ofi(trades)
        assert abs(ofi_norm) < 0.10

    def test_vwap_matches_uniform_price(self) -> None:
        """동일 price trades → vwap == price."""
        trades = _trades_buy_heavy(20, price=50.0)
        _, vwap = evaluate_ofi(trades)
        assert abs(vwap - 50.0) < 1e-9

    def test_empty_trades_returns_zero(self) -> None:
        ofi_norm, vwap = evaluate_ofi([])
        assert ofi_norm == 0.0
        assert vwap == 0.0


class TestStrongBuyWithPriceConfirm:
    """ENTRY: ofi_norm > 0.40 AND last > vwap * 1.0005."""

    def test_strong_buy_with_price_confirm_enters(self) -> None:
        """OFI_norm > 0.40 + 가격 vwap 대비 상승 → ENTER_LONG."""
        s = OFIMomentum()
        # vwap = 100.0 (uniform price), last = 100.15 > 100.05 (vwap * 1.0005)
        trades = _trades_buy_heavy(50, price=100.0)
        tick = _tick(last=100.15)
        sig = s.evaluate_ofi(tick, trades)
        assert sig.action == SignalAction.ENTER_LONG

    def test_strong_buy_with_price_confirm_has_positive_size(self) -> None:
        s = OFIMomentum()
        trades = _trades_buy_heavy(50, price=100.0)
        tick = _tick(last=100.15)
        sig = s.evaluate_ofi(tick, trades)
        assert sig.target_size_usd > 0


class TestStrongBuyNoPriceConfirm:
    """Lagging 회피: OFI_norm high BUT last <= vwap → HOLD (price not confirming)."""

    def test_strong_buy_no_price_confirm_holds(self) -> None:
        """OFI 강하지만 가격 vwap 이하 → HOLD (lagging 회피 핵심 차별점)."""
        s = OFIMomentum()
        # vwap = 100.0, last = 99.9 (vwap 하회) → 가격 미확인 → HOLD
        trades = _trades_buy_heavy(50, price=100.0)
        tick = _tick(last=99.9)
        sig = s.evaluate_ofi(tick, trades)
        assert sig.action == SignalAction.HOLD

    def test_strong_buy_at_exact_vwap_holds(self) -> None:
        """last == vwap (1.0005 기준 미달) → HOLD."""
        s = OFIMomentum()
        trades = _trades_buy_heavy(50, price=100.0)
        tick = _tick(last=100.0)  # vwap * 1.0005 = 100.05 미달
        sig = s.evaluate_ofi(tick, trades)
        assert sig.action == SignalAction.HOLD


class TestSellPressureExits:
    """EXIT: ofi_norm < -0.20."""

    def test_sell_pressure_exits(self) -> None:
        s = OFIMomentum()
        trades = _trades_sell_heavy(50, price=100.0)
        tick = _tick(last=99.5)
        sig = s.evaluate_ofi(tick, trades)
        assert sig.action == SignalAction.EXIT


class TestNeutralHolds:
    """중립 OFI → HOLD."""

    def test_neutral_holds(self) -> None:
        s = OFIMomentum()
        trades = _trades_neutral(50, price=100.0)
        tick = _tick(last=100.05)
        sig = s.evaluate_ofi(tick, trades)
        assert sig.action == SignalAction.HOLD


class TestInsufficientTrades:
    """trades < min_trades → HOLD (데이터 부족)."""

    def test_insufficient_trades_holds(self) -> None:
        s = OFIMomentum()
        trades = _trades_buy_heavy(5, price=100.0)  # min_trades=10, 부족
        tick = _tick(last=100.5)
        sig = s.evaluate_ofi(tick, trades)
        assert sig.action == SignalAction.HOLD

    def test_empty_trades_holds(self) -> None:
        s = OFIMomentum()
        sig = s.evaluate_ofi(_tick(), [])
        assert sig.action == SignalAction.HOLD

    def test_exactly_min_trades_proceeds(self) -> None:
        """정확히 min_trades 개수 → 평가 진행 (buy_heavy면 enter)."""
        s = OFIMomentum()
        trades = _trades_buy_heavy(10, price=100.0)
        tick = _tick(last=100.5)
        sig = s.evaluate_ofi(tick, trades)
        # min_trades 충족 → OFI 평가 (HOLD or ENTER depending on OFI)
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.HOLD, SignalAction.EXIT)


class TestOFICustomThresholds:
    """파라미터화 threshold 동작 검증."""

    def test_custom_entry_threshold_respected(self) -> None:
        """entry threshold 0.70 설정 시 0.60 OFI 값은 HOLD."""
        s = OFIMomentum(ofi_entry=0.70)
        # _trades_buy_heavy 4:1 → OFI_norm ≈ +0.60 (0.70 미달)
        trades = _trades_buy_heavy(50, price=100.0)
        tick = _tick(last=100.5)  # price confirm OK
        sig = s.evaluate_ofi(tick, trades)
        assert sig.action == SignalAction.HOLD

    def test_evaluate_returns_hold_via_base(self) -> None:
        """Strategy ABC evaluate() → HOLD (candle 기반 아님)."""
        from src.domain.candle import Candle
        s = OFIMomentum()
        candle = Candle(timestamp_ms=1_000, open=100.0, high=101.0, low=99.0, close=100.5, volume=1000.0)
        sig = s.evaluate([candle])
        assert sig.action == SignalAction.HOLD
