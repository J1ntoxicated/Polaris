"""§0d — Equity liquidity floor, VALUE-based (no ticker hardcode).

Pure module (no I/O, no DB, no network) — the value-based counterpart to
``_okx_liquid_universe``'s liquid-set role for the Alpaca equity sleeve. Rather
than a static symbol roster, this is a $ THRESHOLD on the bars themselves: a
symbol clears the gate iff its trailing median daily dollar volume clears the
floor. A T3 universe expansion is therefore a pure env change
(``POLARIS_EQ_LIQ_MIN_DAILY_USD`` lowered), never a code change / ticker
hardcode. flow_not_block: a positive-match gate — it only decides whether THIS
symbol's own history clears the floor, it never blocks any other symbol.
"""

from __future__ import annotations

import os
import statistics
from typing import Final

from polaris.strategies.base import BarView

EQ_LIQ_MIN_DAILY_USD_ENV: Final[str] = "POLARIS_EQ_LIQ_MIN_DAILY_USD"
# Active-universe vol_24h_usd p84 (== the T1+T2 liquidity cut) 2026-07-10
# snapshot — re-derive the percentile and update this env/default together
# when the universe composition shifts materially.
EQ_LIQ_MIN_DAILY_USD_DEFAULT: Final[float] = 30_000_000.0
EQ_LIQ_LOOKBACK_DAYS: Final[int] = 20


def _bar_dollar_volume(bar: BarView) -> float:
    if bar.notional_usd > 0.0:
        return bar.notional_usd
    return bar.close * bar.volume


def median_daily_dollar_volume(
    bars: list[BarView], bars_per_day: int
) -> float | None:
    """Median per-bar $ volume over the trailing lookback window, scaled to daily.

    ``bars_per_day`` bars = 1 session (1 for 1D strategies, 26 for a 15m RTH
    strategy — 6.5h / 15m). The window is the trailing
    ``EQ_LIQ_LOOKBACK_DAYS * bars_per_day`` bars; per-bar $ volume is
    ``bar.notional_usd`` when positive, else ``close * volume``. The window's
    median per-bar $ volume is scaled by ``bars_per_day`` back to a daily
    figure. Needs at least one full session (``bars_per_day`` bars) to return
    a value — fewer degrades to ``None`` (degrade-never-crash; the caller
    treats ``None`` as "not liquid enough", never a crash).
    """
    if bars_per_day <= 0:
        return None
    window = bars[-(EQ_LIQ_LOOKBACK_DAYS * bars_per_day):]
    if len(window) < bars_per_day:
        return None
    per_bar = [_bar_dollar_volume(b) for b in window]
    return statistics.median(per_bar) * bars_per_day


def _resolve_min_usd(min_usd: float | None) -> float:
    if min_usd is not None:
        return min_usd
    raw = os.environ.get(EQ_LIQ_MIN_DAILY_USD_ENV)
    if raw is None or raw == "":
        return EQ_LIQ_MIN_DAILY_USD_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return EQ_LIQ_MIN_DAILY_USD_DEFAULT


def equity_liquidity_ok(
    bars: list[BarView], bars_per_day: int, min_usd: float | None = None
) -> bool:
    """True iff the symbol's trailing median daily $ volume clears the floor.

    ``min_usd`` resolves arg -> ``POLARIS_EQ_LIQ_MIN_DAILY_USD`` env -> the
    $30M default. A T1+T2 VALUE gate — no ticker hardcode; a T3 universe widen
    is an env-only change (lower the floor). Insufficient bars (``None``
    median) -> not liquid enough (fails closed on doubt for a brand-new
    symbol, never a crash).
    """
    median = median_daily_dollar_volume(bars, bars_per_day)
    if median is None:
        return False
    return median >= _resolve_min_usd(min_usd)


__all__ = [
    "EQ_LIQ_LOOKBACK_DAYS",
    "EQ_LIQ_MIN_DAILY_USD_DEFAULT",
    "EQ_LIQ_MIN_DAILY_USD_ENV",
    "equity_liquidity_ok",
    "median_daily_dollar_volume",
]
