"""Trading metrics — pure (P6).

All functions deterministic, no I/O. P7 property-based test 적용 영역.

References:
- INSIGHT-002 MTTR-alpha 정의
- INSIGHT-007 OKX SPOT fee 수학 (fee_round_trip 0.0014 default = LV3+ taker 0.07% × 2)
- 60_alpha/_README Promotion Gate 기준
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_FEE_ROUND_TRIP = 0.0014  # INSIGHT-007 OKX SPOT LV3+ taker 0.07% × 2 (latent bug fix 2026-05-04)


@dataclass(frozen=True, slots=True)
class TradeReturn:
    """단일 trade의 gross return (entry → exit). Pure data."""

    entry_price: float
    exit_price: float
    direction: int  # +1 long / -1 short

    def __post_init__(self) -> None:
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}")
        if self.exit_price <= 0:
            raise ValueError(f"exit_price must be > 0, got {self.exit_price}")
        if self.direction not in (1, -1):
            raise ValueError(f"direction must be +1 or -1, got {self.direction}")

    @property
    def gross_pct(self) -> float:
        """Gross return percentage (signed)."""
        return self.direction * (self.exit_price - self.entry_price) / self.entry_price

    def net_pct(self, fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP) -> float:
        """Net return after round-trip fee."""
        return self.gross_pct - fee_round_trip


def hit_rate(returns: list[TradeReturn], fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP) -> float:
    """비율: net > 0 trade 수 / 전체. 비어있으면 0.0."""
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r.net_pct(fee_round_trip) > 0)
    return wins / len(returns)


def expectancy(returns: list[TradeReturn], fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP) -> float:
    """평균 net return per trade. 비어있으면 0.0."""
    if not returns:
        return 0.0
    return sum(r.net_pct(fee_round_trip) for r in returns) / len(returns)


def sharpe(returns: list[TradeReturn], fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP) -> float:
    """Sharpe ratio (risk-free 0). 비어있거나 std=0이면 0.0."""
    if len(returns) < 2:
        return 0.0
    nets = [r.net_pct(fee_round_trip) for r in returns]
    mean = sum(nets) / len(nets)
    var = sum((n - mean) ** 2 for n in nets) / (len(nets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std


def max_drawdown(returns: list[TradeReturn], fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP) -> float:
    """Max drawdown (sequential equity curve). 양수 (예: 0.15 = 15% drawdown). 비어있으면 0.0."""
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r.net_pct(fee_round_trip)
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def fast_fail_gate(expected_tp_pct: float, fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP) -> bool:
    """INSIGHT-007 적용 — expected_TP > fee × 2 통과 검증.

    True = 수학적 생존 가능 (BACKTEST 진입 OK).
    False = fast-fail (HYPOTHESIS archived).
    """
    return expected_tp_pct > fee_round_trip
