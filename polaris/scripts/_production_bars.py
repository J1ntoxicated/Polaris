"""Day 8 production paper loop — Layer 1 per-tick bar ingest helpers.

Split out of ``_production_layers`` to keep both modules ≤500 LOC.
``_production_layers`` re-exports these names + the timeframe constants so
existing import paths (callers + tests) keep working. Fetches 1m/5m/15m/1H
bars for the active focus list and persists to ``bars`` + ``ticker_baseline_*``.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import httpx

from polaris.core.data.ingest import ingest_bars, persist_bars
from polaris.core.data.schema import Bar
from polaris.venues.capital.adapter import fetch_capital_bars
from polaris.venues.capital.session import CapitalSession
from polaris.venues.okx.adapter import fetch_okx_bars

logger = logging.getLogger(__name__)

# F10 — Strategy timeframe → venue resolution + cadence (sec).
# OKX `bar` query parameter accepts the canonical token directly. Capital
# `/prices` requires a textual resolution token (MINUTE / MINUTE_5 / ...).
CAPITAL_RESOLUTION_BY_INTERVAL: dict[str, str] = {
    "1m": "MINUTE",
    "5m": "MINUTE_5",
    "15m": "MINUTE_15",
    "1H": "HOUR",
}

# Per-timeframe fetch cadence — bars only need to be re-pulled when a fresh
# candle is likely to have closed. Honours BAR_INTERVALS = {1m, 5m, 15m, 1H}.
TIMEFRAME_FETCH_CADENCE_SEC: dict[str, float] = {
    "1m": 5.0,    # every tick
    "5m": 30.0,
    "15m": 60.0,
    "1H": 300.0,
}


async def fetch_bars_one(
    venue: str,
    symbol: str,
    asset_class: str,
    *,
    capital_session: CapitalSession | None = None,
    limit: int = 240,
    bar_interval: str = "1m",
) -> list[Bar]:
    """Single-instrument bar fetch. Returns canonical Bar list (newest last).

    F10 — Day 9: ``bar_interval`` defaults to ``1m`` for back-compat but the
    production loop now passes the per-strategy ``metadata.timeframe`` so
    Capital strategies (1H bars) no longer eat 1m candles silently.
    """
    if venue == "okx":
        try:
            bars = await fetch_okx_bars(
                symbol,
                bar_interval=bar_interval,
                limit=limit,
                asset_class=asset_class,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.debug("[L1/okx] %s fetch failed: %r", symbol, exc)
            return []
        # OKX returns newest first → flip to newest last.
        return list(reversed(bars))
    if venue == "capital":
        if capital_session is None:
            return []
        resolution = CAPITAL_RESOLUTION_BY_INTERVAL.get(bar_interval)
        if resolution is None:
            logger.warning(
                "[L1/capital] unsupported bar_interval=%r — skipping %s",
                bar_interval,
                symbol,
            )
            return []
        try:
            tokens = await capital_session.ensure_tokens()
            bars = await fetch_capital_bars(
                symbol,
                cst=tokens.cst,
                security_token=tokens.security_token,
                bar_interval=bar_interval,
                resolution=resolution,
                limit=limit,
                asset_class=asset_class,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.debug("[L1/capital] %s fetch failed: %r", symbol, exc)
            return []
        return bars
    return []


async def ingest_bars_per_timeframe(
    conn: sqlite3.Connection,
    focus: list[tuple[str, str, str, str]],
    *,
    timeframe_to_venues: dict[str, set[str]],
    last_fetch_monotonic_by_tf: dict[str, float],
    bars_persisted_by_tf: dict[str, int],
    capital_session: CapitalSession | None = None,
    limit: int = 240,
    now_mono: float | None = None,
) -> dict[str, int]:
    """F10 — Day 9: drive the per-timeframe ingest fan-out for one tick.

    Honours ``TIMEFRAME_FETCH_CADENCE_SEC`` so each timeframe bucket only
    fetches when its cadence is due. ``last_fetch_monotonic_by_tf`` and
    ``bars_persisted_by_tf`` are mutated in place (caller owns lifetimes).

    Codex F10 R2 fix: cadence is tracked per ``(timeframe, venue)`` keyed as
    ``"{tf}:{venue}"``. A partial-bucket failure (OKX 1H succeeds, Capital
    1H returns zero) must not defer the failing venue's retry by the full
    cadence window. Aggregate ``{"1H": ts}`` is also kept for back-compat
    test introspection — it tracks the *latest* per-tf success across all
    venues (max), not the gating predicate (which is per-(tf,venue)).

    Returns aggregate ``{"bars": N, "baseline_samples": M}`` for the tick.
    """
    mono = now_mono if now_mono is not None else time.monotonic()
    total_bars = 0
    total_baseline = 0
    for timeframe, venues_for_tf in timeframe_to_venues.items():
        cadence = TIMEFRAME_FETCH_CADENCE_SEC.get(timeframe, 5.0)
        # Per-(tf, venue) gate: only fetch venues whose individual cadence
        # is due. This is the R2 P1 fix that prevents partial-bucket
        # starvation when one venue in a mixed-venue bucket fails.
        for venue in sorted(venues_for_tf):
            tf_venue_key = f"{timeframe}:{venue}"
            last_v = last_fetch_monotonic_by_tf.get(tf_venue_key)
            if last_v is not None and (mono - last_v) < cadence:
                continue
            focus_for_v = [t for t in focus if t[0] == venue]
            if not focus_for_v:
                continue
            result = await ingest_bars_for_focus(
                conn, focus_for_v, capital_session=capital_session,
                limit=limit, bar_interval=timeframe,
            )
            total_bars += result["bars"]
            total_baseline += result["baseline_samples"]
            bars_persisted_by_tf[timeframe] = (
                bars_persisted_by_tf.get(timeframe, 0) + result["bars"]
            )
            # Codex F10 R1 P1-3 + R2 fix: cadence advances only when the
            # per-(tf, venue) fetch returned bars. A failing venue retries
            # next tick, not after the full cadence window.
            if result.get("symbols", 0) > 0:
                last_fetch_monotonic_by_tf[tf_venue_key] = mono
                # Mirror the latest success in the aggregate per-tf key so
                # introspection / dashboards can still display "last 1H
                # ingest 30s ago" rather than tracking 2N keys.
                last_fetch_monotonic_by_tf[timeframe] = mono
    return {"bars": total_bars, "baseline_samples": total_baseline}


async def ingest_bars_for_focus(
    conn: sqlite3.Connection,
    focus: list[tuple[str, str, str, str]],
    *,
    capital_session: CapitalSession | None = None,
    limit: int = 240,
    parallel: int = 8,
    bar_interval: str = "1m",
) -> dict[str, int]:
    """Fetch + persist + baseline-update bars for every focus entry.

    Returns aggregate counts ``{"bars": N, "baseline_samples": M, "symbols": K}``.
    Concurrency capped at ``parallel`` to keep REST burst polite.

    F10 — Day 9: ``bar_interval`` is forwarded to every adapter call so the
    production loop can ingest a different timeframe per strategy timeframe
    bucket (1m / 15m / 1H). The persisted ``bars`` row keeps its
    ``bar_interval`` column populated by the adapter.
    """
    sem = asyncio.Semaphore(parallel)
    out_bars: list[Bar] = []
    seen: set[str] = set()

    async def _one(target: tuple[str, str, str, str]) -> None:
        venue, symbol, asset_class, _group = target
        async with sem:
            bars = await fetch_bars_one(
                venue, symbol, asset_class,
                capital_session=capital_session, limit=limit,
                bar_interval=bar_interval,
            )
        if bars:
            out_bars.extend(bars)
            seen.add(f"{venue}:{symbol}")

    await asyncio.gather(*(_one(t) for t in focus))
    if not out_bars:
        return {"bars": 0, "baseline_samples": 0, "symbols": 0}
    # Codex F10 R1 P1-2 fix: baselines (ATR/size/volume) are minute-grained;
    # routing 5m/15m/1H bars into ``update_baseline_from_bars`` would
    # contaminate the minute-windowed state. Use the full ``ingest_bars``
    # pipeline only for 1m batches; for higher timeframes, persist the bars
    # without recomputing the baseline.
    if bar_interval != "1m":
        total_persisted = persist_bars(conn, out_bars)
        return {
            "bars": total_persisted,
            "baseline_samples": 0,
            "symbols": len(seen),
        }
    # 1m path — group by asset_class so update_baseline_from_bars partitions
    # the per-class baseline window correctly.
    by_class: dict[str, list[Bar]] = {}
    for b in out_bars:
        ac = "crypto" if b.venue == "okx" else "forex"
        by_class.setdefault(ac, []).append(b)
    total_persisted = 0
    total_baseline = 0
    for ac, group in by_class.items():
        result = ingest_bars(conn, group, asset_class=ac)
        total_persisted += result["bars"]
        total_baseline += result["baseline_samples"]
    return {
        "bars": total_persisted,
        "baseline_samples": total_baseline,
        "symbols": len(seen),
    }


def read_recent_bars(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    bar_interval: str = "1m",
    limit: int = 240,
) -> list[Bar]:
    """Read the most recent ``limit`` bars from SQLite (newest last)."""
    instrument_id = f"{venue}:{symbol}"
    rows = conn.execute(
        """
        SELECT instrument_id, underlying_group_id, venue, symbol, bar_interval,
               ts, open, high, low, close, volume, notional_usd, trade_count,
               vwap, bid_close, ask_close, spread_bps_close, source
        FROM bars
        WHERE instrument_id = ? AND bar_interval = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (instrument_id, bar_interval, int(limit)),
    ).fetchall()
    bars = [
        Bar(
            instrument_id=str(r[0]),
            underlying_group_id=str(r[1]),
            venue=str(r[2]),
            symbol=str(r[3]),
            bar_interval=str(r[4]),
            ts=int(r[5]),
            open=float(r[6]),
            high=float(r[7]),
            low=float(r[8]),
            close=float(r[9]),
            volume=float(r[10]),
            notional_usd=float(r[11] or 0.0),
            trade_count=int(r[12] or 0),
            vwap=float(r[13] or 0.0),
            bid_close=float(r[14] or 0.0),
            ask_close=float(r[15] or 0.0),
            spread_bps_close=float(r[16] or 0.0),
            source=str(r[17] or "rest"),
        )
        for r in rows
    ]
    bars.reverse()  # newest last
    return bars


