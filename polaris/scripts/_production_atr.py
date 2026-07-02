"""Timeframe-aligned ATR% reads for the exit ruler (bug-fix, DEMO/PAPER).

The live recalc measured EVERY position against the latest 20×1m bars — a 1H
tsmom thesis was trailed on 1m noise (avg ~11.7min hold). These helpers read
the ATR% on the strategy's OWN timeframe so the trail width / R denominator
match the thesis horizon (design intent restored — 이익은 기다려 크게).

* ``strategy_timeframe`` — registry timeframe; unregistered (tick-engine
  signals) → "1m" = the pre-fix behaviour, byte-identical.
* ``bars_atr_pct`` — mean((high-low)/close) over the last ≤20 bars of ONE
  timeframe (same estimator as the existing 1m recalc — the only verified
  variable here is the timeframe). Degenerate windows (flat weekend bars,
  mean ≤ 1e-5 — mirrors ``_atr_pct_from_bars``) return ``None`` so a ~0
  anchor can never be stamped/served.
* ``timeframe_atr_pct`` — tf → 1m fallback chain + optional TTL cache
  (TTL = ``TIMEFRAME_FETCH_CADENCE_SEC[tf]``, the cadence a fresh bar can
  arrive at). ``None`` when no usable window exists (caller keeps its
  current-1m fallback — graceful, never halts).
* ``timeframe_anchor_atr_pct`` — same chain, plus WHICH timeframe produced
  the value (entry-anchor provenance for ``positions.entry_atr_timeframe``).

Measurement / exit precision only: never sizes, never blocks an entry, never
halts (flow_not_block / aggressive bias intact).
"""

from __future__ import annotations

from typing import Any

from polaris.scripts._production_bars import (
    BAR_TS_CLOCK_SKEW_SLACK_SEC,
    TIMEFRAME_FETCH_CADENCE_SEC,
)
from polaris.strategies import STRATEGY_REGISTRY

__all__ = [
    "DEGENERATE_ATR_PCT",
    "MIN_TF_BARS",
    "bars_atr_pct",
    "native_bars_seen_since",
    "strategy_timeframe",
    "timeframe_anchor_atr_pct",
    "timeframe_atr_pct",
    "timeframe_bar_rows",
]

# Minimum bars for a non-1m timeframe window to count as usable. NEVER raise
# this near the query LIMIT (20): a "<21" threshold would be always-true and
# silently lock every 1D strategy onto the 1m fallback (review BLOCKER).
MIN_TF_BARS: int = 5

# A window whose mean (high-low)/close is at/below this is degenerate (flat /
# stale bars) — mirrors the ``_atr_pct_from_bars`` close-path guard.
DEGENERATE_ATR_PCT: float = 1e-5

# (instrument_id, timeframe) → (atr_pct, computed_ts). TTL is the timeframe's
# bar-fetch cadence — a fresh bar cannot arrive faster, so staleness ≤ 1 bar.
TFAtrCache = dict[tuple[str, str], tuple[float, int]]


def strategy_timeframe(strategy_id: str) -> str:
    """``StrategyMetadata.timeframe`` for ``strategy_id``; unregistered → "1m".

    Tick-engine signals (micro_reversion / flow_pressure / burst_rider) are not
    registered → "1m" keeps their exit ruler byte-identical to pre-fix.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    return cls.metadata.timeframe if cls is not None else "1m"


def timeframe_bar_rows(
    conn: Any,
    *,
    instrument_id: str,
    timeframe: str,
    now_ts: int,
    min_bars: int = 1,
) -> list[tuple[Any, ...]] | None:
    """Last ≤20 ``timeframe`` bars (``ts, close, high, low, volume``, newest
    first) for ``instrument_id`` at/before ``now_ts``.

    Same shape/query pattern as the recalc's pre-fix 1m ``bar_row`` read (see
    ``_production_recalc.load_active_position_rows``) so ``_recent_market_state``
    / ``_recent_tick_drift`` (momentum_drift) can be scoped to the ACTIVE
    strategy's OWN timeframe — the missing counterpart to ``bars_atr_pct``
    ([[1d_exit_horizon_fix_2026-07-02]] follow-up: the drift FLOOR was
    timeframe-scaled but the drift SIGNAL itself was still always read from 1m
    bars). Returns ``None`` when fewer than ``min_bars`` rows exist so the
    caller can fall back to the 1m window (graceful, never halts).
    """
    ts_upper = int(now_ts) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    rows = conn.execute(
        """
        SELECT ts, close, high, low, volume FROM bars
        WHERE instrument_id = ? AND bar_interval = ? AND ts <= ?
        ORDER BY ts DESC LIMIT 20
        """,
        (instrument_id, timeframe, ts_upper),
    ).fetchall()
    if len(rows) < max(1, min_bars):
        return None
    return list(rows)


def bars_atr_pct(
    conn: Any,
    *,
    instrument_id: str,
    timeframe: str,
    now_ts: int,
    min_bars: int = 1,
) -> float | None:
    """ATR% from the last ≤20 ``timeframe`` bars at/before ``now_ts``.

    Estimator = mean((high-low)/close) — identical to the recalc 1m window so
    the only changed variable is the timeframe. FUTURE-dated bars (stale +10h
    Capital ghosts) are excluded via the same clock-skew bound every trading
    path uses. Returns ``None`` when fewer than ``min_bars`` usable samples
    exist OR the window is degenerate (flat bars → mean ≤ 1e-5).
    """
    venue, _, symbol = instrument_id.partition(":")
    ts_upper = int(now_ts) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    rows = conn.execute(
        """
        SELECT close, high, low FROM bars
        WHERE venue = ? AND symbol = ? AND bar_interval = ? AND ts <= ?
        ORDER BY ts DESC LIMIT 20
        """,
        (venue, symbol, timeframe, ts_upper),
    ).fetchall()
    samples = [
        (float(r[1]) - float(r[2])) / float(r[0])
        for r in rows
        if float(r[0]) > 0.0
    ]
    if len(samples) < max(1, min_bars):
        return None
    mean = sum(samples) / len(samples)
    return mean if mean > DEGENERATE_ATR_PCT else None


def native_bars_seen_since(
    conn: Any, *, instrument_id: str, timeframe: str, open_ts: int, now_ts: int
) -> int:
    """Count of ``timeframe`` bars closed STRICTLY AFTER ``open_ts`` (and at/
    before ``now_ts``) — the exit maturity gate's bars-seen measure
    ([[waveB_sizing_params_2026-07-02]] agenda 3). Same ``bars`` table +
    venue/symbol/bar_interval query shape as ``bars_atr_pct`` — no new infra.
    Wall-clock is NEVER used for this count: a session close / data gap would
    otherwise fake elapsed development the thesis never actually lived
    through. The bar forming/closed AT open does not count as "seen since
    open" (``ts > open_ts``, not ``>=``). FUTURE-dated bars beyond ``now_ts``
    are excluded via the same clock-skew bound every bar-read path uses.
    """
    venue, _, symbol = instrument_id.partition(":")
    ts_upper = int(now_ts) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    row = conn.execute(
        "SELECT COUNT(*) FROM bars "
        "WHERE venue = ? AND symbol = ? AND bar_interval = ? "
        "AND ts > ? AND ts <= ?",
        (venue, symbol, timeframe, int(open_ts), ts_upper),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def timeframe_anchor_atr_pct(
    conn: Any,
    *,
    instrument_id: str,
    timeframe: str,
    now_ts: int,
) -> tuple[float, str] | None:
    """``(atr_pct, timeframe_used)`` via the tf → 1m fallback chain.

    Used to stamp the entry-time anchor (``positions.entry_atr_pct`` +
    ``entry_atr_timeframe``) and by the historical correction script — the
    provenance records WHICH ruler produced the anchor. ``None`` = no usable
    window anywhere → the caller stores NULL (legacy-graceful), never a fake
    0.005 or a degenerate ~0 anchor.
    """
    atr = bars_atr_pct(
        conn, instrument_id=instrument_id, timeframe=timeframe, now_ts=now_ts,
        min_bars=1 if timeframe == "1m" else MIN_TF_BARS,
    )
    if atr is not None:
        return (atr, timeframe)
    if timeframe != "1m":
        atr = bars_atr_pct(
            conn, instrument_id=instrument_id, timeframe="1m", now_ts=now_ts,
            min_bars=1,
        )
        if atr is not None:
            return (atr, "1m")
    return None


def timeframe_atr_pct(
    conn: Any,
    *,
    instrument_id: str,
    timeframe: str,
    now_ts: int,
    cache: TFAtrCache | None = None,
) -> float | None:
    """Current ATR% on ``timeframe`` (tf → 1m fallback → ``None``), cached.

    Cache TTL = ``TIMEFRAME_FETCH_CADENCE_SEC[timeframe]`` — exactly the
    cadence a fresh bar of that timeframe can arrive, so a cached value is at
    most one fetch-cycle stale. Misses are two indexed LIMIT-20 reads. ``None``
    results are not cached (a just-warming instrument retries next call).
    """
    ttl = TIMEFRAME_FETCH_CADENCE_SEC.get(timeframe, 5.0)
    if cache is not None:
        hit = cache.get((instrument_id, timeframe))
        if hit is not None and (int(now_ts) - hit[1]) < ttl:
            return hit[0]
    got = timeframe_anchor_atr_pct(
        conn, instrument_id=instrument_id, timeframe=timeframe, now_ts=now_ts,
    )
    if got is None:
        return None
    atr = got[0]
    if cache is not None:
        cache[(instrument_id, timeframe)] = (atr, int(now_ts))
    return atr
