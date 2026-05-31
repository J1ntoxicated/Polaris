"""Pure equity-curve accumulator + summary metrics (no I/O).

The replay engine accumulates a single real-fee-net curve: starting equity plus
the cumulative ``net_pnl_usd`` of each trade, stamped at the trade's ``exit_ts``.
A leading ``(start_ts, starting_equity)`` anchor keeps the curve on the same
clock as the baselines. ``demo_actual`` (the punitive 70 bps demo schedule) is
an optional parallel curve for dashboard parity, never the go-live signal.

Metrics are computed from per-trade net-fee returns (net_pnl / equity_before),
so Sharpe / profit-factor / drawdown all reflect REAL fees.
"""

from __future__ import annotations

import math

from polaris.core.replay.models import ReplayTrade

__all__ = [
    "build_equity_curve",
    "summarize_metrics",
]


def build_equity_curve(
    *,
    trades: list[ReplayTrade],
    starting_equity: float,
    start_ts: int,
) -> list[tuple[int, float]]:
    """Anchor + per-trade cumulative net-fee equity points (ordered by exit)."""
    curve: list[tuple[int, float]] = [(int(start_ts), float(starting_equity))]
    equity = float(starting_equity)
    for tr in sorted(trades, key=lambda t: (t.exit_ts, t.entry_ts)):
        equity += tr.net_pnl_usd
        curve.append((int(tr.exit_ts), equity))
    return curve


def _per_trade_returns(trades: list[ReplayTrade], starting_equity: float) -> list[float]:
    """Net-fee return of each trade relative to the equity before it opened."""
    rets: list[float] = []
    equity = float(starting_equity)
    for tr in sorted(trades, key=lambda t: (t.exit_ts, t.entry_ts)):
        base = equity if equity > 0.0 else starting_equity
        rets.append(tr.net_pnl_usd / base if base != 0.0 else 0.0)
        equity += tr.net_pnl_usd
    return rets


def _sharpe(returns: list[float]) -> float:
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std <= 0.0:
        return 0.0
    # Per-trade Sharpe (not annualised); the benchmark layer applies the
    # period scaling against the shared baseline clock. Kept raw here so the
    # ratio is comparable trade-for-trade with the baselines.
    return mean / std * math.sqrt(n)


def _max_drawdown(curve: list[tuple[int, float]]) -> float:
    peak = -math.inf
    max_dd = 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        if peak > 0.0:
            dd = (equity - peak) / peak
            max_dd = min(max_dd, dd)
    return max_dd


def summarize_metrics(
    *,
    trades: list[ReplayTrade],
    curve: list[tuple[int, float]],
    starting_equity: float,
) -> dict[str, float]:
    """Real-fee-net summary: net_pnl, sharpe, max_dd, win_rate, profit_factor,
    turnover, fee_drag_real_r, n."""
    n = len(trades)
    net_pnl = sum(t.net_pnl_usd for t in trades)
    rets = _per_trade_returns(trades, starting_equity)
    wins = [t for t in trades if t.net_pnl_usd > 0.0]
    gross_win = sum(t.net_pnl_usd for t in wins)
    gross_loss = -sum(t.net_pnl_usd for t in trades if t.net_pnl_usd < 0.0)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0.0 else 0.0
    turnover = sum(t.notional for t in trades)
    fee_drag_real_r = sum(t.rt_fee_usd for t in trades)
    return {
        "net_pnl": net_pnl,
        "sharpe": _sharpe(rets),
        "max_dd": _max_drawdown(curve),
        "win_rate": (len(wins) / n) if n else 0.0,
        "profit_factor": profit_factor,
        "turnover": turnover,
        "fee_drag_real_r": fee_drag_real_r,
        "n": float(n),
    }
