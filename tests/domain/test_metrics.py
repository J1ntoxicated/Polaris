"""tests/domain/test_metrics.py — P6 + P7 property-based."""
from __future__ import annotations

import math

import pytest
from hypothesis import given, strategies as st

from src.domain.metrics import (
    DEFAULT_FEE_ROUND_TRIP,
    TradeReturn,
    expectancy,
    fast_fail_gate,
    hit_rate,
    max_drawdown,
    sharpe,
)


class TestTradeReturn:
    def test_long_profit(self) -> None:
        r = TradeReturn(entry_price=100, exit_price=105, direction=1)
        assert r.gross_pct == pytest.approx(0.05)

    def test_short_profit(self) -> None:
        r = TradeReturn(entry_price=100, exit_price=95, direction=-1)
        assert r.gross_pct == pytest.approx(0.05)

    def test_long_loss(self) -> None:
        r = TradeReturn(entry_price=100, exit_price=95, direction=1)
        assert r.gross_pct == pytest.approx(-0.05)

    def test_net_after_fee(self) -> None:
        r = TradeReturn(entry_price=100, exit_price=105, direction=1)
        assert r.net_pct(0.014) == pytest.approx(0.05 - 0.014)

    def test_invalid_direction(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            TradeReturn(entry_price=100, exit_price=105, direction=0)

    def test_invalid_price(self) -> None:
        with pytest.raises(ValueError, match="entry_price"):
            TradeReturn(entry_price=0, exit_price=105, direction=1)


class TestHitRate:
    def test_empty(self) -> None:
        assert hit_rate([]) == 0.0

    def test_all_wins(self) -> None:
        rs = [TradeReturn(100, 105, 1) for _ in range(5)]
        assert hit_rate(rs, 0.0) == 1.0

    def test_all_losses_after_fee(self) -> None:
        # gross +0.005, fee 0.014 → net -0.009 → 0/5 wins
        rs = [TradeReturn(100, 100.5, 1) for _ in range(5)]
        assert hit_rate(rs, 0.014) == 0.0

    def test_mixed(self) -> None:
        rs = [
            TradeReturn(100, 110, 1),  # +10% gross
            TradeReturn(100, 105, 1),  # +5%
            TradeReturn(100, 90, 1),   # -10%
        ]
        assert hit_rate(rs, 0.014) == pytest.approx(2 / 3)


class TestExpectancy:
    def test_empty(self) -> None:
        assert expectancy([]) == 0.0

    def test_simple(self) -> None:
        rs = [TradeReturn(100, 105, 1), TradeReturn(100, 95, 1)]
        # net: 0.05-0.014, -0.05-0.014 → mean = -0.014
        assert expectancy(rs, 0.014) == pytest.approx(-0.014)


class TestSharpe:
    def test_too_few(self) -> None:
        assert sharpe([]) == 0.0
        assert sharpe([TradeReturn(100, 105, 1)]) == 0.0

    def test_zero_std(self) -> None:
        rs = [TradeReturn(100, 105, 1) for _ in range(5)]
        assert sharpe(rs, 0.0) == 0.0  # 모든 net 동일 → std=0


class TestMaxDrawdown:
    def test_empty(self) -> None:
        assert max_drawdown([]) == 0.0

    def test_no_drawdown(self) -> None:
        rs = [TradeReturn(100, 110, 1) for _ in range(3)]
        assert max_drawdown(rs, 0.0) == 0.0

    def test_drawdown_after_peak(self) -> None:
        rs = [
            TradeReturn(100, 120, 1),  # +20%
            TradeReturn(100, 80, 1),   # -20%
        ]
        # equity: 1.2 → 0.96. peak=1.2. dd=(1.2-0.96)/1.2 = 0.2
        assert max_drawdown(rs, 0.0) == pytest.approx(0.2)


class TestFastFailGate:
    def test_pass(self) -> None:
        assert fast_fail_gate(0.02, fee_round_trip=0.014) is True

    def test_fail_equal(self) -> None:
        assert fast_fail_gate(0.014, fee_round_trip=0.014) is False

    def test_fail_below(self) -> None:
        assert fast_fail_gate(0.005, fee_round_trip=0.014) is False


# Property-based (P7)


@st.composite
def valid_trade_return(draw):
    entry = draw(st.floats(min_value=0.01, max_value=1e6, allow_nan=False))
    exit = draw(st.floats(min_value=0.01, max_value=1e6, allow_nan=False))
    direction = draw(st.sampled_from([1, -1]))
    return TradeReturn(entry_price=entry, exit_price=exit, direction=direction)


class TestMetricsInvariants:
    @given(returns=st.lists(valid_trade_return(), min_size=0, max_size=50))
    def test_hit_rate_in_zero_to_one(self, returns) -> None:
        rate = hit_rate(returns)
        assert 0.0 <= rate <= 1.0

    @given(returns=st.lists(valid_trade_return(), min_size=0, max_size=50))
    def test_max_drawdown_non_negative(self, returns) -> None:
        dd = max_drawdown(returns)
        assert dd >= 0.0

    @given(returns=st.lists(valid_trade_return(), min_size=0, max_size=50))
    def test_max_drawdown_finite(self, returns) -> None:
        # short는 이론상 무한 loss 가능 (price → 무한대). long은 100% bounded.
        # 백테스트 경계: dd는 finite + non-negative.
        dd = max_drawdown(returns)
        assert dd >= 0.0
        assert math.isfinite(dd)

    @given(
        tp=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        fee=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    def test_fast_fail_gate_strict_inequality(self, tp, fee) -> None:
        result = fast_fail_gate(tp, fee_round_trip=fee)
        assert result == (tp > fee)
