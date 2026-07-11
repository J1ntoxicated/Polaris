"""Layer 6 — regime v2 twinlight SHADOW classifier (behavior-0).

DEMO/PAPER virtual account. Spec source:
``vault/50_research/backgate-plan/design-regime-v2-rollout.md`` W2 +
``vault/50_research/debates/regime_factory_2026-07-10.md`` (R1 FINAL-SPEC).

6-state shadow taxonomy computed + recorded BARE ALONGSIDE the canonical
4-label regime (``regime_flip.REGIME_VALUES`` / ``regime_state.regime`` /
``positions.entry_regime``) — zero live consumer until the W4 flip ladder.
This module never imports ``regime_flip`` and ``regime_flip`` is never
touched: the old 4-label path stays completely unmodified (behavior-0).

Taxonomy: direction (up / down / flat) x volatility (normal / expansion).
Features (all reused from ``polaris.core.indicators.production`` + the
existing ``ecdf_percentile`` utility — no new math primitives): returns
trend (``compute_momentum``) drives direction; ADX slope
(``compute_adx_14`` delta) + BB-width percentile + ATR percentile
(``ecdf_percentile`` of ``compute_bollinger`` / ``compute_atr_pct`` over a
trailing rolling population) drive volatility.

``classify_regime_v2`` is a PURE total function — no I/O, degrades to the
neutral ``"flat_normal"`` state on short/degenerate bar history (same
total-function contract every other indicator in ``production.py``
follows), never raises. ``fetch_regime_v2`` is the one DB read, itself a
fail-open ``_safe_lookup`` replica (design doc mandate) — an ALTER-guard
race, a locked row, or a legacy pre-migration DB degrades to ``None``.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from typing import Final

from polaris.core.classes.threshold_migration import ecdf_percentile
from polaris.core.data.schema import Bar
from polaris.core.indicators.production import (
    compute_adx_14,
    compute_atr_pct,
    compute_bollinger,
    compute_momentum,
)
from polaris.strategies.base import BarView

logger = logging.getLogger(__name__)

__all__ = ["REGIME_V2_VALUES", "classify_regime_v2", "fetch_regime_v2"]

REGIME_V2_VALUES: Final[tuple[str, ...]] = (
    "up_normal", "up_expansion",
    "down_normal", "down_expansion",
    "flat_normal", "flat_expansion",
)

# P0 shadow-classifier thresholds. SSOT = design-regime-v2-rollout.md W2 (the
# doc fixes the 6-state TAXONOMY + the 4 features; it does not fix exact cut
# points — none exist yet because this classifier has ZERO live consumers
# until the W4 flip ladder, so a threshold miss here has zero trading impact).
# Tunable at OOF-scoring time (W3, regime_v2_score.py).
_MIN_BARS: Final[int] = 50  # below this, degrade to the neutral "flat_normal"
_DIRECTION_FLAT_BAND_PCT: Final[float] = 0.3  # |momentum| below → "flat"
_VOL_EXPANSION_PCT: Final[float] = 0.66  # top-tercile BB/ATR percentile → expansion
_VOL_BORDERLINE_PCT: Final[float] = 0.50  # mid-band needs a rising ADX to tip
_ADX_SLOPE_WINDOW: Final[int] = 14  # bars back for the ADX delta ("slope")
_VOL_POPULATION_WINDOW: Final[int] = 100  # trailing bars sampled for the percentile population
_VOL_SAMPLE_STRIDE: Final[int] = 5  # sample every Nth bar (population build cost)
_BB_WINDOW: Final[int] = 20  # compute_bollinger's own default window


def _bb_width(closes: list[float]) -> float:
    lo, mid, hi = compute_bollinger(closes)
    return 0.0 if mid <= 0.0 else (hi - lo) / mid


def classify_regime_v2(bars: Sequence[Bar | BarView]) -> str:
    """Bar history (newest last) -> one of ``REGIME_V2_VALUES``.

    Total function: never raises, degrades to ``"flat_normal"`` on a short
    or degenerate window (same contract ``compute_real_regime_signal`` and
    the indicator primitives it's built from already follow).
    """
    if len(bars) < _MIN_BARS:
        return "flat_normal"
    closes = [float(b.close) for b in bars]

    # direction — signed momentum vs a flat band (single feature: returns trend)
    momentum_pct = compute_momentum(closes) * 100.0
    if momentum_pct > _DIRECTION_FLAT_BAND_PCT:
        direction = "up"
    elif momentum_pct < -_DIRECTION_FLAT_BAND_PCT:
        direction = "down"
    else:
        direction = "flat"

    # volatility — ADX slope (trend-strengthening tie-break) + BB-width/ATR
    # percentile within a trailing rolling population.
    adx_now = compute_adx_14(bars)
    adx_then = compute_adx_14(bars[: -_ADX_SLOPE_WINDOW])
    adx_slope = adx_now - adx_then

    pop_start = max(0, len(bars) - _VOL_POPULATION_WINDOW)
    bb_population: list[float] = []
    atr_population: list[float] = []
    for i in range(pop_start + _BB_WINDOW, len(bars), _VOL_SAMPLE_STRIDE):
        bb_population.append(_bb_width(closes[: i + 1]))
        atr_population.append(compute_atr_pct(bars[: i + 1]))
    bb_pct = ecdf_percentile(_bb_width(closes), bb_population)
    atr_pct = ecdf_percentile(compute_atr_pct(bars), atr_population)
    vol_score = (bb_pct + atr_pct) / 2.0

    if vol_score >= _VOL_EXPANSION_PCT or (
        vol_score >= _VOL_BORDERLINE_PCT and adx_slope > 0.0
    ):
        volatility = "expansion"
    else:
        volatility = "normal"

    return f"{direction}_{volatility}"


def fetch_regime_v2(
    conn: sqlite3.Connection, *, venue: str, underlying_group_id: str
) -> str | None:
    """Read ``regime_state.regime_v2`` — the twinlight shadow column.

    Fail-open ``_safe_lookup`` replica (design-regime-v2-rollout.md W2, the
    ``_production_close_effects._safe_lookup_regime`` pattern): ANY read
    failure (pre-migration legacy DB, locked row, etc.) degrades to
    ``None`` rather than raising — mirrors the existing degrade behavior
    ``positions.entry_regime`` already has for unseeded legacy rows. Zero
    consumers read this column until the W4 flip ladder.
    """
    try:
        row = conn.execute(
            "SELECT regime_v2 FROM regime_state "
            "WHERE venue = ? AND underlying_group_id = ?",
            (venue, underlying_group_id),
        ).fetchone()
    except sqlite3.Error as exc:  # noqa: BLE001 — fail-open, never blocks a caller
        logger.error("[L6/regime_v2] fetch_regime_v2 raised: %r", exc)
        return None
    return None if row is None or row[0] is None else str(row[0])
