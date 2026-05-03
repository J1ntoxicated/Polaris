"""Promotion Gate — pure (P6).

60_alpha workflow gate: HYPOTHESIS BACKTEST → PAPER → ADR.

References:
- principles P4 Validation Boundary (alpha)
- 60_alpha/_README workflow
- INSIGHT-006 frozen_params
- INSIGHT-007 fast-fail gate
- INSIGHT-015 timeframe-aware (1d viable)
- ADR-010 Backtest + Paper parallel
- ADR-011 Promotion Gate Timeframe-aware (Scalp/Swing/Position)
"""
from __future__ import annotations

from dataclasses import dataclass

from src.backtest.result import BacktestResult
from src.domain.metrics import fast_fail_gate

# ─────── Category thresholds (ADR-011 Timeframe-aware) ───────

# Category 1 — Scalp (1m, 5m, 15m, 1h)
SCALP_MIN_TRADES = 50
SCALP_MIN_SHARPE = 0.8
SCALP_MIN_WIN_RATE = 0.55
SCALP_MAX_DRAWDOWN = 0.10
SCALP_FEE_MULTIPLIER = 1.5  # expectancy > fee × 1.5

# Category 2 — Swing (4h, 12h, 1d)
SWING_MIN_TRADES = 15
SWING_MIN_SHARPE = 0.3
SWING_MIN_WIN_RATE = 0.30
SWING_MAX_DRAWDOWN = 0.50
SWING_FEE_MULTIPLIER = 5.0

# Category 3 — Position (3d+, weekly)
POSITION_MIN_TRADES = 8
POSITION_MIN_SHARPE = 0.2
POSITION_MIN_WIN_RATE = 0.25
POSITION_MAX_DRAWDOWN = 0.70
POSITION_FEE_MULTIPLIER = 10.0

# Default (legacy = scalp 기본)
MIN_TRADES = SCALP_MIN_TRADES
MIN_SHARPE = SCALP_MIN_SHARPE
MIN_WIN_RATE = SCALP_MIN_WIN_RATE
MAX_DRAWDOWN_LIMIT = SCALP_MAX_DRAWDOWN
MIN_EXPECTANCY = 0.001  # legacy compat


def categorize_timeframe(timeframe: str, n_trades: int | None = None) -> str:
    """timeframe → 'scalp' | 'swing' | 'position'.

    ADR-011: 4h는 경계 — n_trades 기반 자동 분류 (>100 → scalp, ≤ → swing).
    1d는 n_trades < SWING_MIN_TRADES (15)이면 position으로 격하 (긴 hold 가설).
    """
    tf = timeframe.lower().strip()
    if tf in {"1m", "3m", "5m", "15m", "30m", "1h"}:
        return "scalp"
    if tf == "4h":
        # 4h 경계: n_trades 많으면 scalp, 적으면 swing (ADR-011)
        if n_trades is not None and n_trades > 100:
            return "scalp"
        return "swing"
    if tf in {"2h", "6h", "8h", "12h"}:
        return "swing"
    if tf == "1d":
        # 1d: trade 빈도 낮으면 position (long-hold 가설)
        if n_trades is not None and n_trades < SWING_MIN_TRADES:
            return "position"
        return "swing"
    return "position"  # 3d, 1w 등


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Promotion gate result — pure data."""

    passed: bool
    fast_fail_passed: bool
    failures: tuple[str, ...]
    summary: dict
    category: str = ""


def evaluate_fast_fail(expected_tp_pct: float, fee_round_trip: float) -> bool:
    """BACKTEST 24h fast-fail gate (INSIGHT-007 OKX SPOT fee 수학)."""
    return fast_fail_gate(expected_tp_pct, fee_round_trip)


def _evaluate_with_thresholds(
    result: BacktestResult,
    category: str,
    min_trades: int,
    min_sharpe: float,
    min_win_rate: float,
    max_dd: float,
    fee_mult: float,
) -> PromotionDecision:
    failures: list[str] = []

    fast_fail_ok = evaluate_fast_fail(result.expectancy, result.fee_round_trip)
    if not fast_fail_ok:
        failures.append(
            f"fast-fail: expectancy {result.expectancy:.6f} <= fee {result.fee_round_trip}"
        )

    if result.n_trades < min_trades:
        failures.append(f"n_trades: {result.n_trades} < {min_trades}")

    fee_threshold = result.fee_round_trip * fee_mult
    if result.expectancy < fee_threshold:
        failures.append(
            f"expectancy: {result.expectancy:.6f} < fee × {fee_mult} ({fee_threshold:.6f})"
        )

    if result.sharpe < min_sharpe:
        failures.append(f"sharpe: {result.sharpe:.4f} < {min_sharpe}")

    if result.hit_rate < min_win_rate:
        failures.append(f"win_rate: {result.hit_rate:.4f} < {min_win_rate}")

    if result.max_drawdown > max_dd:
        failures.append(f"max_drawdown: {result.max_drawdown:.4f} > {max_dd}")

    return PromotionDecision(
        passed=len(failures) == 0,
        fast_fail_passed=fast_fail_ok,
        failures=tuple(failures),
        summary=result.summary(),
        category=category,
    )


def evaluate_promotion_scalp(result: BacktestResult) -> PromotionDecision:
    """Category 1 — Scalp (1m~1h). ADR-011 strict thresholds."""
    return _evaluate_with_thresholds(
        result, "scalp", SCALP_MIN_TRADES, SCALP_MIN_SHARPE, SCALP_MIN_WIN_RATE,
        SCALP_MAX_DRAWDOWN, SCALP_FEE_MULTIPLIER,
    )


def evaluate_promotion_swing(result: BacktestResult) -> PromotionDecision:
    """Category 2 — Swing (4h~1d). ADR-011 trend-aware thresholds."""
    return _evaluate_with_thresholds(
        result, "swing", SWING_MIN_TRADES, SWING_MIN_SHARPE, SWING_MIN_WIN_RATE,
        SWING_MAX_DRAWDOWN, SWING_FEE_MULTIPLIER,
    )


def evaluate_promotion_position(result: BacktestResult) -> PromotionDecision:
    """Category 3 — Position (3d+, weekly). ADR-011 long-hold thresholds."""
    return _evaluate_with_thresholds(
        result, "position", POSITION_MIN_TRADES, POSITION_MIN_SHARPE, POSITION_MIN_WIN_RATE,
        POSITION_MAX_DRAWDOWN, POSITION_FEE_MULTIPLIER,
    )


def evaluate_promotion(
    result: BacktestResult,
    category: str | None = None,
) -> PromotionDecision:
    """Dispatcher — category 자동 분류 또는 명시.

    Args:
        result: BacktestResult.
        category: 'scalp' | 'swing' | 'position' | None (auto from timeframe).
    """
    if category is None:
        category = categorize_timeframe(result.timeframe, n_trades=result.n_trades)
    if category == "scalp":
        return evaluate_promotion_scalp(result)
    if category == "swing":
        return evaluate_promotion_swing(result)
    if category == "position":
        return evaluate_promotion_position(result)
    raise ValueError(f"unknown category: {category!r}")
