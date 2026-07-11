"""XS-momentum + 52wk-high SHADOW composite (frontgate-scan item #3, behavior-0).

DEMO/PAPER virtual capital only. This wave is a pure measurement SHADOW: the
composite computed here feeds ``universe.momentum_z`` / ``rank_score_shadow``
(spread-measurement columns) via the ``rank_active_universe(momentum_z=...)``
seam — it never enters the live ranking weight (``RANK_SCORE_W_MOM`` is pinned
at 0.0 in ``schema.py``). flow_not_block: no filter, no size cut, no new GPT
call.

Pure functions only (no I/O) — the DB-query glue (``bars`` fetch + look-ahead
boundary) lives in ``polaris/scripts/_production_layers.py`` alongside its
sibling rank-axis producers (``compute_signal_density_7d`` /
``read_cell_scores_by_instrument``).

Design (verifier-adjusted spec, frontgate-scan item #3):
- ``dedup_daily_bars`` collapses a multi-write-per-day 1D bar history
  (OKX ~1.85x/day, Capital ~1.58x/day dual-cadence writes) to ONE row per UTC
  calendar day (the day's LAST row by ts), so a 252-row window means 252
  actual calendar days, not ~half that.
- ``momentum_composite_raw`` combines (a) a 252-trading-day (~52wk) momentum
  return via ``compute_momentum`` and (b) 52wk-high proximity via
  ``compute_donchian`` — the SAME 252-bar convention already used by
  ``equity_xsect_52w_momentum`` / ``index_52w_high_momentum``. Both reuse the
  existing production indicator functions (no reimplementation).
- ``momentum_z_composite`` z-normalizes each raw component PER (venue,
  asset_class) group (cross-sectional — "XS") via the same population
  z-score helper ``_ranking.py`` already uses, then averages the two
  z-components into ONE composite term. An instrument with insufficient
  history (< 253 deduped days) is excluded from the z-population and
  explicitly forced to 0.0 (neutral) — "결측 히스토리 = 중립 z(0)" — rather
  than letting a fallback-neutral raw value skew against a population that
  does have real history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from polaris.core.indicators.production import compute_donchian, compute_momentum
from polaris.core.universe._ranking import _grouped_pop_z
from polaris.strategies.base import BarView

# 252 trading days ≈ 52 weeks — the SAME "52-week" convention already used by
# ``equity_xsect_52w_momentum.PRIOR_HIGH_LOOKBACK`` /
# ``index_52w_high_momentum.HIGH_LOOKBACK`` (both 252). One window governs
# both the momentum-return leg and the Donchian 52wk-high leg.
WINDOW_DAYS_52W = 252

# BarRow = (ts, close, high, low) — the minimal per-day tuple this module
# consumes (caller fetches these 4 columns from ``bars``).
BarRow = tuple[int, float, float, float]


def dedup_daily_bars(rows: Sequence[BarRow]) -> list[BarRow]:
    """Collapse ``rows`` to one row per UTC calendar day (the day's MAX-ts row).

    ``rows`` need not be pre-sorted. The UTC calendar day is ``ts // 86400``
    (epoch is UTC-aligned, so integer floor-division gives the exact day
    boundary with no datetime/timezone dependency). Returns rows sorted
    ascending by ts. Empty input → empty output.
    """
    by_day: dict[int, BarRow] = {}
    for row in rows:
        ts = int(row[0])
        day = ts // 86_400
        prev = by_day.get(day)
        if prev is None or ts > int(prev[0]):
            by_day[day] = row
    return [by_day[day] for day in sorted(by_day)]


def momentum_composite_raw(bars: Sequence[BarRow]) -> tuple[float, float]:
    """(momentum_raw, donchian_52w_proximity_raw) from LAGGED+DEDUPED daily bars.

    ``bars`` must already be look-ahead-filtered (no same-UTC-day-as-now row)
    and deduped (``dedup_daily_bars``), ascending by ts — the caller's
    contract. Insufficient history (< ``WINDOW_DAYS_52W`` + 1 rows) falls back
    to the neutral pair (0.0 momentum, 0.5 proximity) via the underlying
    indicator functions' own short-window fallback — the same "neutral on
    insufficient data" behavior every other production indicator uses.
    """
    if not bars:
        return 0.0, 0.5
    closes = [float(r[1]) for r in bars]
    momentum_raw = compute_momentum(closes, n=WINDOW_DAYS_52W)
    bar_views = [
        BarView(
            ts=int(r[0]),
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[1]),
            volume=0.0,
        )
        for r in bars
    ]
    hi, lo = compute_donchian(bar_views, n=WINDOW_DAYS_52W)
    last_close = closes[-1]
    proximity = (last_close - lo) / (hi - lo) if hi > lo else 0.5
    return momentum_raw, proximity


def momentum_z_composite(
    bars_by_id: Mapping[str, Sequence[BarRow]],
    groups_by_id: Mapping[str, str],
) -> dict[str, float]:
    """Grouped-z XS-momentum+52wk-high composite, keyed by ``instrument_id``.

    Only instruments carrying >= ``WINDOW_DAYS_52W`` + 1 deduped-lagged rows
    enter the z-scored sub-population (per ``groups_by_id`` cohort, mirroring
    ``_ranking._grouped_pop_z``'s venue/asset_class grouping). Every other
    instrument (missing entirely, or too little history) gets an EXPLICIT
    0.0 (neutral) — never a value derived from a fallback raw signal folded
    into someone else's real population.
    """
    sufficient = [
        iid
        for iid, bars in bars_by_id.items()
        if len(bars) >= WINDOW_DAYS_52W + 1
    ]
    out = {iid: 0.0 for iid in bars_by_id}
    if not sufficient:
        return out
    raw = [momentum_composite_raw(bars_by_id[iid]) for iid in sufficient]
    momentum_raw = [r[0] for r in raw]
    proximity_raw = [r[1] for r in raw]
    groups = [groups_by_id.get(iid, "") for iid in sufficient]
    momentum_z = _grouped_pop_z(momentum_raw, groups)
    proximity_z = _grouped_pop_z(proximity_raw, groups)
    for i, iid in enumerate(sufficient):
        out[iid] = (momentum_z[i] + proximity_z[i]) / 2.0
    return out


__all__ = [
    "WINDOW_DAYS_52W",
    "BarRow",
    "dedup_daily_bars",
    "momentum_composite_raw",
    "momentum_z_composite",
]
