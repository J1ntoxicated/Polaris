"""Promotion Gate — pure (P6).

60_alpha workflow gate: HYPOTHESIS BACKTEST → PAPER → ADR.

References:
- principles P4 Validation Boundary (alpha)
- 60_alpha/_README workflow
- INSIGHT-006 frozen_params (m2_promotion_min_n_trades=30, min_expectancy=0.001)
- INSIGHT-007 fast-fail gate
"""
from __future__ import annotations

from dataclasses import dataclass

from src.backtest.result import BacktestResult
from src.domain.metrics import fast_fail_gate

# Promotion thresholds (frozen_params [[INSIGHT-006]] + ADR-010 강화)
MIN_TRADES = 30
MIN_EXPECTANCY = 0.001  # 0.1% per trade after fee
MIN_SHARPE = 0.5  # ADR-010: 백테스트 신뢰도 낮음 (INSIGHT-012) → fast-fail 수준만
MIN_WIN_RATE = 0.52  # ADR-010: random walk null (50%) 약간 위
MAX_DRAWDOWN_LIMIT = 0.10  # 10%


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Promotion gate result — pure data."""

    passed: bool
    fast_fail_passed: bool
    failures: tuple[str, ...]
    summary: dict


def evaluate_fast_fail(expected_tp_pct: float, fee_round_trip: float) -> bool:
    """BACKTEST 24h fast-fail gate (INSIGHT-007 OKX SPOT fee 수학)."""
    return fast_fail_gate(expected_tp_pct, fee_round_trip)


def evaluate_promotion(result: BacktestResult) -> PromotionDecision:
    """BACKTEST 결과 → PAPER 진입 가능 여부.

    ADR-010 (Backtest + Paper parallel) 적용:
    - 백테스트 = fast-fail + sanity check (INSIGHT-012 신뢰도 한계)
    - PAPER가 진짜 알파 검증

    PAPER 진입 조건 (모두 만족):
    1. fast-fail gate (expectancy > fee_round_trip) — INSIGHT-007
    2. n_trades >= MIN_TRADES (30, 통계 최소)
    3. expectancy >= MIN_EXPECTANCY (0.001)
    4. sharpe >= MIN_SHARPE (0.5, ADR-010 강화)
    5. win_rate >= MIN_WIN_RATE (0.52, random walk null 위)
    6. max_drawdown <= MAX_DRAWDOWN_LIMIT (0.10)
    """
    failures: list[str] = []

    fast_fail_ok = evaluate_fast_fail(result.expectancy, result.fee_round_trip)
    if not fast_fail_ok:
        failures.append(
            f"fast-fail: expectancy {result.expectancy:.6f} <= fee {result.fee_round_trip}"
        )

    if result.n_trades < MIN_TRADES:
        failures.append(f"n_trades: {result.n_trades} < {MIN_TRADES}")

    if result.expectancy < MIN_EXPECTANCY:
        failures.append(
            f"expectancy: {result.expectancy:.6f} < {MIN_EXPECTANCY}"
        )

    if result.sharpe < MIN_SHARPE:
        failures.append(f"sharpe: {result.sharpe:.4f} < {MIN_SHARPE}")

    if result.hit_rate < MIN_WIN_RATE:
        failures.append(f"win_rate: {result.hit_rate:.4f} < {MIN_WIN_RATE}")

    if result.max_drawdown > MAX_DRAWDOWN_LIMIT:
        failures.append(
            f"max_drawdown: {result.max_drawdown:.4f} > {MAX_DRAWDOWN_LIMIT}"
        )

    return PromotionDecision(
        passed=len(failures) == 0,
        fast_fail_passed=fast_fail_ok,
        failures=tuple(failures),
        summary=result.summary(),
    )
