"""Pure benchmark baselines — buy&hold, naive TSMOM, naive Bollinger (no I/O).

Spec: ``.claude/plans/p1_replay_benchmark_harness_2026-05-31.md`` §3 Relative.
Every baseline runs on the **same bars**, the **same** ``real_fee_usd`` schedule,
and the **same clock** (anchor = first bar ts, points stamped at bar ts) as the
replay engine — so the 3-tier gate compares like-for-like. The bot must beat
ALL of these on real-fee-net Sharpe to pass tier 1.

Conventions (kept naive on purpose — these are reference strategies, not the
bot): fills are at the bar **close** (the baselines are evaluation references,
not look-ahead-audited execution); each position change pays a round-trip
``real_fee_usd`` on the turned-over notional. Equity compounds the per-bar
return of the held position.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from polaris.core.data.schema import Bar
from polaris.core.economics.fees import real_fee_usd

__all__ = [
    "BaselineCurve",
    "bollinger_baseline",
    "buy_and_hold_baseline",
    "tsmom_baseline",
]


@dataclass(frozen=True, slots=True)
class BaselineCurve:
    """A baseline's equity curve + per-bar returns on the shared clock.

    ``equity_curve`` = leading ``(start_ts, starting_equity)`` anchor + one point
    per bar (``(ts, equity)``), the same shape as
    ``ReplayResult.equity_curve_real_fee_net``. ``returns`` = the per-bar net-fee
    returns the Sharpe is computed from. ``total_fee_usd`` = the real fees this
    baseline paid (its fee drag, for parity with the bot's ``fee_drag_real_r``).
    """

    name: str
    equity_curve: tuple[tuple[int, float], ...]
    returns: tuple[float, ...]
    total_fee_usd: float


def _empty(name: str, starting_equity: float) -> BaselineCurve:
    return BaselineCurve(
        name=name,
        equity_curve=((0, float(starting_equity)),),
        returns=(),
        total_fee_usd=0.0,
    )


def buy_and_hold_baseline(bars: list[Bar], *, starting_equity: float) -> BaselineCurve:
    """Enter at the first bar's open, hold to the last close, ONE round trip.

    Net final equity = starting + (close_last/open_first − 1)·notional − round-trip
    real fee. On a monotone series the net return == close/open − 1 − fee-fraction
    (TDD #4). The equity curve marks-to-market each bar's close (pre-exit-fee), so
    the curve tracks the held position; the terminal exit fee is applied at the
    last point.
    """
    if not bars:
        return _empty("buy_hold", starting_equity)
    venue = bars[0].venue
    entry = float(bars[0].open)
    if entry <= 0.0:
        return _empty("buy_hold", starting_equity)
    notional = float(starting_equity)
    qty = notional / entry
    entry_fee = real_fee_usd(venue, notional)
    start_ts = int(bars[0].ts)
    curve: list[tuple[int, float]] = [(start_ts, float(starting_equity))]
    returns: list[float] = []
    prev_equity = float(starting_equity)
    last_idx = len(bars) - 1
    for i, b in enumerate(bars):
        mark = float(b.close)
        gross = (mark - entry) * qty
        fees = entry_fee
        if i == last_idx:  # apply the exit (close) leg on the terminal bar
            fees += real_fee_usd(venue, abs(mark * qty))
        equity = float(starting_equity) + gross - fees
        curve.append((int(b.ts), equity))
        returns.append((equity - prev_equity) / prev_equity if prev_equity != 0.0 else 0.0)
        prev_equity = equity
    total_fee = entry_fee + real_fee_usd(venue, abs(float(bars[last_idx].close) * qty))
    return BaselineCurve(
        name="buy_hold",
        equity_curve=tuple(curve),
        returns=tuple(returns),
        total_fee_usd=total_fee,
    )


def _position_walk(
    bars: list[Bar], *, starting_equity: float, signals: list[int], name: str
) -> BaselineCurve:
    """Walk a long/flat/short position series, compounding per-bar returns net of
    a round-trip real fee charged whenever the target position changes.

    ``signals[i]`` is the position held INTO bar ``i+1`` (decided on bar ``i``'s
    close, so no look-ahead): +1 long, 0 flat, −1 short. The per-bar return is
    ``pos·(close[i+1]/close[i] − 1)``; a position change at ``i`` pays a fee on
    the turned-over notional (current equity) for each leg that flips.
    """
    venue = bars[0].venue
    equity = float(starting_equity)
    start_ts = int(bars[0].ts)
    curve: list[tuple[int, float]] = [(start_ts, equity)]
    returns: list[float] = []
    total_fee = 0.0
    prev_pos = 0
    for i in range(len(bars) - 1):
        target = signals[i]
        # Position change pays |Δpos| legs of round-trip fee on the notional.
        if target != prev_pos:
            legs = abs(target - prev_pos)
            fee = legs * real_fee_usd(venue, equity)
            total_fee += fee
            equity -= fee
        prev_pos = target
        c0 = float(bars[i].close)
        c1 = float(bars[i + 1].close)
        bar_ret = (c1 / c0 - 1.0) if c0 > 0.0 else 0.0
        net_equity = equity * (1.0 + target * bar_ret)
        returns.append((net_equity - equity) / equity if equity != 0.0 else 0.0)
        equity = net_equity
        curve.append((int(bars[i + 1].ts), equity))
    # Close any residual position on the final bar (exit leg).
    if prev_pos != 0:
        fee = abs(prev_pos) * real_fee_usd(venue, equity)
        total_fee += fee
        equity -= fee
        curve[-1] = (curve[-1][0], equity)
    return BaselineCurve(
        name=name, equity_curve=tuple(curve), returns=tuple(returns), total_fee_usd=total_fee
    )


def tsmom_baseline(
    bars: list[Bar], *, starting_equity: float, lookback: int = 20
) -> BaselineCurve:
    """Naive time-series-momentum: long when the ``lookback``-bar return is > 0,
    short when < 0, flat at exactly 0. Each flip turns the book over (2 legs of
    real fee). Decided on bar close (no look-ahead) for the next bar."""
    if len(bars) < 2:
        return _empty("tsmom", starting_equity)
    closes = [float(b.close) for b in bars]
    signals: list[int] = []
    for i in range(len(bars) - 1):
        if i < lookback:
            signals.append(0)  # warmup: flat
            continue
        past = closes[i - lookback]
        mom = (closes[i] - past)
        signals.append(1 if mom > 0.0 else (-1 if mom < 0.0 else 0))
    return _position_walk(bars, starting_equity=starting_equity, signals=signals, name="tsmom")


def bollinger_baseline(
    bars: list[Bar], *, starting_equity: float, window: int = 20, k: float = 2.0
) -> BaselineCurve:
    """Naive Bollinger mean-reversion: long when close pierces the lower band
    (``mean − k·std``), flat once back at/above the mid (``mean``). Long/flat only
    (no short). Decided on bar close for the next bar (no look-ahead)."""
    if len(bars) < 2:
        return _empty("bollinger", starting_equity)
    closes = [float(b.close) for b in bars]
    signals: list[int] = []
    pos = 0
    for i in range(len(bars) - 1):
        if i < window:
            signals.append(0)
            continue
        win = closes[i - window + 1 : i + 1]
        mean = sum(win) / len(win)
        var = sum((c - mean) ** 2 for c in win) / len(win)
        std = math.sqrt(var)
        lower = mean - k * std
        c = closes[i]
        if pos == 0 and c < lower:
            pos = 1  # enter long on lower-band pierce
        elif pos == 1 and c >= mean:
            pos = 0  # exit at the mid
        signals.append(pos)
    return _position_walk(bars, starting_equity=starting_equity, signals=signals, name="bollinger")
