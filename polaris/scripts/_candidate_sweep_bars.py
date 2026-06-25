"""STEP② candidate sweep — confirmed-bar technical primitives (pure helpers).

Split from ``_candidate_sweep.py`` to keep both files ≤500 LOC. Every function
here is PURE + look-ahead-safe: :func:`confirmed` is the single guard that drops
any bar whose close-ts > ``now_ts`` (a bar whose period has not closed), and the
ratio/expansion/trend reads consume ONLY confirmed bars. Linear + clamp only, no
learned weights, no per-ticker tuning (anti-overfit).

DEMO/PAPER. flow_not_block: these read the static ground to RANK candidates; they
gate no entry / size / exit.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Sequence
from typing import Any

from polaris.scripts._production_bars import read_recent_bars

# STEP① static-ground resolutions (1m intentionally absent for the bulk universe).
_SWEEP_RESOLUTIONS: tuple[str, ...] = ("15m", "1H", "1D")
# How many recent confirmed bars to read per resolution (trailing window).
_SWEEP_BAR_LIMIT = 60
# Period length per sweep resolution, in seconds. LOCAL map (not ``bar_seconds``)
# so a missing key can NEVER silently fall back to a cooldown default and corrupt
# the look-ahead guard — these are the only 3 sweep resolutions (the STEP① set).
_INTERVAL_SECONDS: dict[str, int] = {"15m": 900, "1H": 3600, "1D": 86400}


def bars_by_resolution(
    conn: sqlite3.Connection, *, venue: str, symbol: str
) -> dict[str, list[Any]]:
    """Read the static-ground bars for one ticker, keyed by resolution.

    Reads stored bars only (no fetch). ``read_recent_bars`` already excludes
    far-future-dated rows, but the sweep's own :func:`confirmed` guard is the
    authoritative look-ahead filter applied at consume time.
    """
    out: dict[str, list[Any]] = {}
    for res in _SWEEP_RESOLUTIONS:
        out[res] = read_recent_bars(
            conn, venue=venue, symbol=symbol,
            bar_interval=res, limit=_SWEEP_BAR_LIMIT,
        )
    return out


def confirmed(bars: Sequence[Any] | None, now_ts: int, interval: str) -> list[Any]:
    """Look-ahead guard — keep only bars whose period has FULLY CLOSED by ``now_ts``.

    ``Bar.ts`` is the period-OPEN timestamp (OKX/Yahoo store the candle open ts),
    so a bar is confirmed only when its period END (``ts + interval_seconds``) is
    ≤ ``now_ts``. A bar that opened at/before ``now_ts`` but is still FORMING
    (partial high/low/close) is dropped — admitting it would be a 1-period
    look-ahead (the bar's range/close has not finalized). This is the single
    authoritative filter — every consumer reads through it.

    ``interval`` is the resolution key ("15m"/"1H"/"1D"); an unknown key yields an
    empty result (fail-closed — never admit a bar we cannot bound). ``None``/empty
    bars → ``[]``.
    """
    if not bars:
        return []
    period = _INTERVAL_SECONDS.get(interval)
    if period is None:
        return []  # fail-closed: unknown resolution → never admit (no guard bound)
    return [b for b in bars if int(b.ts) + period <= now_ts]


def _true_range(bar: Any) -> float:
    """High-low range of one bar (intra-bar; prev-close gaps are not stored)."""
    return max(0.0, float(bar.high) - float(bar.low))


def _atr(bars: Sequence[Any]) -> float:
    """Mean true range over the bars (0.0 on empty)."""
    if not bars:
        return 0.0
    return statistics.fmean(_true_range(b) for b in bars)


def _normalize_range(bars: Sequence[Any]) -> float:
    """Mean range as a fraction of price (price-scale-free; 0.0 on empty)."""
    if not bars:
        return 0.0
    out: list[float] = []
    for b in bars:
        close = float(b.close)
        if close > 0.0:
            out.append(_true_range(b) / close)
    return statistics.fmean(out) if out else 0.0


def realized_expected_vol_ratio(
    bars15: Sequence[Any], bars1h: Sequence[Any], bars1d: Sequence[Any]
) -> float:
    """Recent realized vol vs the ticker's OWN ~1D baseline → [0,1] (clamp).

    Recent intraday vol = mean normalized range of the most-recent 15m+1H bars.
    Expected = the ticker's own 1D normalized-range baseline. ratio > 1 means the
    ticker is moving MORE than its own typical day → squashed into [0,1]. Own-
    baseline keeps it cross-asset comparable (a quiet FX major and a wild alt are
    each measured against themselves). No baseline (cold) → 0.0 (gate stays shut).
    """
    recent = _normalize_range(list(bars15)[-8:] + list(bars1h)[-4:])
    baseline = _normalize_range(bars1d)
    if baseline <= 0.0:
        return 0.0
    ratio = recent / baseline
    # ratio 1.0 (typical) → 0.5; 2.0+ (double its day) → ~1.0; flat → 0.0.
    if ratio <= 0.0:
        return 0.0
    return min(1.0, ratio / 2.0)


def range_expansion(bars15: Sequence[Any], bars1h: Sequence[Any]) -> float:
    """Recent range vs its own trailing median → [0,1] (clamp).

    Last few bars' range vs the median range of the trailing window. Expansion
    (recent > median) → high; contraction → low. Per-ticker trailing median (no
    cross-asset leakage). Empty/degenerate → 0.0.
    """
    series = list(bars15) if bars15 else list(bars1h)
    if len(series) < 4:
        return 0.0
    ranges = [_true_range(b) for b in series]
    median = statistics.median(ranges)
    if median <= 0.0:
        return 0.0
    recent = statistics.fmean(ranges[-3:])
    ratio = recent / median
    if ratio <= 0.0:
        return 0.0
    # median (no expansion) → 0.5; 2× median → 1.0; collapse → 0.0.
    return min(1.0, ratio / 2.0)


def trend_direction(bars: Sequence[Any]) -> int:
    """Sign of the trend over the confirmed bars: +1 up, -1 down, 0 flat.

    Compares the mean close of the first vs the last third of the window (robust
    to single-bar noise). Equal / too-short → 0 (no edge). Pure.
    """
    if len(bars) < 3:
        return 0
    third = max(1, len(bars) // 3)
    head = statistics.fmean(float(b.close) for b in bars[:third])
    tail = statistics.fmean(float(b.close) for b in bars[-third:])
    if tail > head:
        return 1
    if tail < head:
        return -1
    return 0
