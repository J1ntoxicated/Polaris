"""Sector/dual-momentum rotation context SHADOW (frontgate-scan item #8, G1).

DEMO/PAPER virtual capital only. behavior-0: ROUTING/CONTEXT recording only —
no directional weight, no filter, no size cut, no new GPT call. Stage 1
(this module): the 11 SPDR sector ETFs' relative-momentum ranking, recorded
ONLY on the monthly rebalance-boundary bar (mirrors
``index_dual_momentum_rotation._is_rebalance_bar`` — the first 1D bar of a
new UTC calendar month; sector momentum has no reason to recompute every
5-10min L0 cycle). Stage 2 (per-stock candidate forward-return spread) needs
a ticker→sector mapping source decision (a /debate item, no calendar
deadline) and is NOT built here.

Pure functions only — the DB-query glue (``bars`` fetch, look-ahead boundary,
prior-cycle delta lookup) lives in ``polaris/scripts/_production_layers.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from polaris.core.indicators.production import compute_momentum
from polaris.core.universe._momentum_shadow import BarRow
from polaris.core.universe._ranking import _pop_z

# The 11 SPDR sector ETFs (Alpaca venue) — the standard GICS-sector proxy set.
SECTOR_ETF_SYMBOLS: Final[frozenset[str]] = frozenset(
    {
        "XLK",  # Technology
        "XLF",  # Financials
        "XLE",  # Energy
        "XLV",  # Health Care
        "XLI",  # Industrials
        "XLY",  # Consumer Discretionary
        "XLP",  # Consumer Staples
        "XLU",  # Utilities
        "XLB",  # Materials
        "XLRE",  # Real Estate
        "XLC",  # Communication Services
    }
)

# 120 trading days (~6 months) — the SAME ROC lookback convention already used
# by ``index_dual_momentum_rotation.ROC_LOOKBACK`` (a proven multi-month
# relative-momentum measure, not a new arbitrary constant).
SECTOR_ROC_LOOKBACK_DAYS: Final[int] = 120


def is_rebalance_boundary(deduped_bars: Sequence[BarRow]) -> bool:
    """True iff the LAST lagged bar is the first 1D bar of a new UTC month.

    Mirrors ``index_dual_momentum_rotation._is_rebalance_bar``: compares the
    last two deduped daily bars' (year, month) via integer epoch-day math (no
    datetime dependency, consistent with ``_momentum_shadow.dedup_daily_bars``
    — epoch is UTC-aligned). Fewer than 2 bars → False (nothing to compare;
    never fires on a cold/short history).
    """
    if len(deduped_bars) < 2:
        return False
    last_day = int(deduped_bars[-1][0]) // 86_400
    prev_day = int(deduped_bars[-2][0]) // 86_400
    # A UTC calendar month boundary crossed between the two days iff the
    # 30-day-max-spread month-start count differs — cheaply detected via the
    # (days-since-epoch // ~30) bucket would be inexact across month lengths,
    # so compare the actual (year, month) derived from days-since-epoch using
    # the proleptic Gregorian civil-from-days algorithm (no datetime import).
    return _civil_month(last_day) != _civil_month(prev_day)


def _civil_month(days_since_epoch: int) -> tuple[int, int]:
    """(year, month) for a UTC day index (days since 1970-01-01), no datetime.

    Howard Hinnant's civil_from_days algorithm (proleptic Gregorian, exact for
    any int day count) — avoids a datetime/timezone dependency for a pure
    epoch-day → calendar-month mapping.
    """
    z = days_since_epoch + 719_468
    era = z // 146_097 if z >= 0 else (z - 146_096) // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    m = mp + 3 if mp < 10 else mp - 9
    y = y + 1 if m <= 2 else y
    return y, m


def sector_momentum_raw(bars: Sequence[BarRow]) -> float:
    """ROC over ``SECTOR_ROC_LOOKBACK_DAYS`` from LAGGED+DEDUPED daily bars.

    ``bars`` must already be look-ahead-filtered + deduped (caller contract,
    same as ``_momentum_shadow.momentum_composite_raw``). Insufficient history
    falls back to 0.0 via ``compute_momentum``'s own short-window guard.
    """
    closes = [float(r[1]) for r in bars]
    return compute_momentum(closes, n=SECTOR_ROC_LOOKBACK_DAYS)


def rank_sector_etfs(
    bars_by_symbol: Mapping[str, Sequence[BarRow]],
) -> list[tuple[str, float, int]]:
    """(symbol, momentum_z, rank) sorted rank 1..N, rank 1 = strongest ROC.

    Single cross-sectional population (all 11 ETFs are one peer group) —
    population z-score via the same ``_pop_z`` helper ``_ranking.py`` uses.
    Symbols with insufficient history still enter the population at their
    0.0-fallback raw ROC (a small, fixed 11-name set — unlike the broad
    universe in item #3, there is no "sparse vs sufficient" split to guard).
    """
    symbols = sorted(bars_by_symbol)  # deterministic order (ties break stably)
    raw = [sector_momentum_raw(bars_by_symbol[s]) for s in symbols]
    z = _pop_z(raw)
    order = sorted(range(len(symbols)), key=lambda i: z[i], reverse=True)
    rank_by_idx = {idx: rank for rank, idx in enumerate(order, start=1)}
    return [(symbols[i], z[i], rank_by_idx[i]) for i in range(len(symbols))]


__all__ = [
    "SECTOR_ETF_SYMBOLS",
    "SECTOR_ROC_LOOKBACK_DAYS",
    "is_rebalance_boundary",
    "rank_sector_etfs",
    "sector_momentum_raw",
]
