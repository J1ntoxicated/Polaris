"""Capital velocity tracker (Phase 22.3).

자본 회전 속도 측정 — compound magic의 핵심.

Metrics:
- cash_idle_ratio: cash / (cash + open_position_size) ∈ [0, 1]
  높음 = 자본 놀고 있음 = 신호 부족 진단
- avg_position_duration_min: 평균 hold 시간
- turnover_per_day: 일일 회전 횟수 (target ≥ 5 for compound)
- target_velocity_score: composite (0-100)

Used by dashboard + alert layer. Pure aggregation over portfolio state.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VelocitySnapshot:
    """Portfolio capital velocity at a point in time."""
    cash_idle_ratio: float          # 0..1
    avg_position_age_min: float     # avg minutes held by open contributions
    avg_closed_duration_min: float  # avg minutes for closed contributions today
    turnover_today: int             # count of closed contributions today
    n_open: int
    n_unique_tickers: int
    velocity_score: int             # 0..100 composite
    diagnosis: str                  # human-readable summary


def _day_start_ms(ts_ms: int) -> int:
    return (ts_ms // 86_400_000) * 86_400_000


def compute_velocity(
    portfolio,
    ts_ms: int,
) -> VelocitySnapshot:
    """Snapshot capital velocity.

    cash_idle_ratio: high = capital sitting in cash (signals not firing).
    velocity_score: composite — penalizes idle cash + low turnover.
    """
    cash = portfolio.cash
    total_open_size = sum(
        c.size_usd for pos in portfolio.positions.values()
        for c in pos.contributions if not c.is_closed
    )
    total = cash + total_open_size
    cash_idle_ratio = cash / total if total > 0 else 1.0

    # Open position ages
    open_contribs = [
        c for pos in portfolio.positions.values()
        for c in pos.contributions if not c.is_closed
    ]
    if open_contribs:
        avg_open_age_min = sum(
            (ts_ms - c.open_ts_ms) / 60_000 for c in open_contribs
        ) / len(open_contribs)
    else:
        avg_open_age_min = 0.0

    # Closed today durations
    day_start = _day_start_ms(ts_ms)
    closed_today = [
        c for c in portfolio.closed_contributions()
        if c.close_ts_ms >= day_start
    ]
    if closed_today:
        avg_closed_dur_min = sum(
            (c.close_ts_ms - c.open_ts_ms) / 60_000 for c in closed_today
        ) / len(closed_today)
    else:
        avg_closed_dur_min = 0.0

    n_unique_tickers = len(set(c.ticker for c in open_contribs))

    # Velocity score components
    # 1. Cash idle penalty (lower idle = higher score, max 40)
    idle_score = int((1.0 - min(cash_idle_ratio, 1.0)) * 40)
    # 2. Turnover bonus (more closed today = higher score, max 40 at 10+ trades)
    turnover_score = min(len(closed_today) * 4, 40)
    # 3. Diversification (more unique tickers = higher score, max 20 at 5+)
    diversification_score = min(n_unique_tickers * 4, 20)
    velocity_score = idle_score + turnover_score + diversification_score

    # Diagnosis
    parts = []
    if cash_idle_ratio > 0.7:
        parts.append("HIGH_IDLE")
    if len(closed_today) < 3:
        parts.append("LOW_TURNOVER")
    if avg_open_age_min > 240 and len(open_contribs) > 0:
        parts.append("STALE_HOLDS")
    diagnosis = ",".join(parts) if parts else "OK"

    return VelocitySnapshot(
        cash_idle_ratio=cash_idle_ratio,
        avg_position_age_min=avg_open_age_min,
        avg_closed_duration_min=avg_closed_dur_min,
        turnover_today=len(closed_today),
        n_open=len(open_contribs),
        n_unique_tickers=n_unique_tickers,
        velocity_score=velocity_score,
        diagnosis=diagnosis,
    )
