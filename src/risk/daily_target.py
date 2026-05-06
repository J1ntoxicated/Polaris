"""Daily 0.5% compound target tracker (Phase 22.2).

User vision: "하루 0.5% 수익이 목표 — 컴파운드 매직"
    0.5% × 252 trading days compounded = ~250% / year
    0.5% × 365 days compounded = ~518% / year

Tracker measures:
- Today's realized PnL (closed contributions today)
- Today's unrealized PnL (open contributions MTM)
- Target = starting_cash × 0.005 (default)
- Progress = (realized + unrealized) / target
- Time-of-day pressure (if past 50% of day with <30% progress → behind)

Pure-ish — reads portfolio state, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass


DEFAULT_DAILY_TARGET_PCT: float = 0.005  # 0.5%


@dataclass(frozen=True)
class DailyProgress:
    """Snapshot of today's compound progress."""
    target_usd: float
    realized_today_usd: float
    unrealized_usd: float
    actual_today_usd: float        # realized + unrealized
    progress_ratio: float           # actual / target (1.0 = on track)
    n_trades_today: int
    on_track: bool                  # progress >= 1.0
    behind: bool                    # progress < 0.3 past 50% of day
    summary: str


def _day_start_ms(ts_ms: int) -> int:
    """UTC day boundary."""
    return (ts_ms // 86_400_000) * 86_400_000


def compute_daily_progress(
    portfolio,
    current_prices: dict[str, float],
    ts_ms: int,
    target_pct: float = DEFAULT_DAILY_TARGET_PCT,
) -> DailyProgress:
    """Snapshot today's PnL vs daily target.

    portfolio: PortfolioManager instance (uses cash, closed_contributions, positions)
    current_prices: {ticker: latest price} for unrealized MTM
    ts_ms: current timestamp
    target_pct: daily target (default 0.5%)
    """
    target_usd = portfolio.starting_cash * target_pct
    day_start = _day_start_ms(ts_ms)

    # Realized today
    realized_today = sum(
        c.realized_net_usd for c in portfolio.closed_contributions()
        if c.close_ts_ms >= day_start
    )
    n_trades_today = sum(
        1 for c in portfolio.closed_contributions()
        if c.close_ts_ms >= day_start
    )

    # Unrealized (open contributions)
    unrealized = 0.0
    for ticker, pos in portfolio.positions.items():
        price = current_prices.get(ticker, 0.0)
        if price <= 0:
            continue
        for c in pos.contributions:
            if c.is_closed:
                continue
            unrealized += c.unrealized_usd(price) - (c.size_usd * c.fee_round_trip)

    actual = realized_today + unrealized
    progress = actual / target_usd if target_usd > 0 else 0.0

    # Time-of-day pressure
    day_progress = (ts_ms - day_start) / 86_400_000  # 0..1
    behind = (day_progress > 0.5) and (progress < 0.3)
    on_track = progress >= 1.0

    summary = (
        f"target=${target_usd:.2f} actual=${actual:+.2f} "
        f"({progress*100:+.1f}%) realized=${realized_today:+.2f} "
        f"unreal=${unrealized:+.2f} n={n_trades_today} day={day_progress*100:.0f}%"
    )

    return DailyProgress(
        target_usd=target_usd,
        realized_today_usd=realized_today,
        unrealized_usd=unrealized,
        actual_today_usd=actual,
        progress_ratio=progress,
        n_trades_today=n_trades_today,
        on_track=on_track,
        behind=behind,
        summary=summary,
    )
