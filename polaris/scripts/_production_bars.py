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
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.data.ingest import ingest_bars_async, persist_bars
from polaris.core.data.schema import BAR_INTERVALS, Bar
from polaris.venues.capital.adapter import fetch_capital_bars
from polaris.venues.capital.session import CapitalSession
from polaris.venues.okx.adapter import fetch_okx_bars

logger = logging.getLogger(__name__)

# FIX 1/2 — trading-path FUTURE-dated bar guard. The Capital REST ts source-fix
# only corrects NEW bars; the live DB still holds ~5,793 stale +10h FUTURE-dated
# Capital bars (the AEST-naive snapshotTime parse). Every trading-path bar read
# (strategy/regime canvas, exit close-mark, cost-R ATR window) bounds ts at
# ``now + BAR_TS_CLOCK_SKEW_SLACK_SEC`` so a +10h bar is never the "most recent"
# canvas. Small slack absorbs legitimate clock skew (a just-closed bar a few
# seconds ahead stays visible). Mirrors the dashboard ``_last_prices`` guard.
BAR_TS_CLOCK_SKEW_SLACK_SEC = 120

# F10 — Strategy timeframe → venue resolution + cadence (sec).
# OKX `bar` query parameter accepts the canonical token directly. Capital
# `/prices` requires a textual resolution token (MINUTE / MINUTE_5 / ...).
CAPITAL_RESOLUTION_BY_INTERVAL: dict[str, str] = {
    "1m": "MINUTE",
    "5m": "MINUTE_5",
    "15m": "MINUTE_15",
    "1H": "HOUR",
    "1D": "DAY",  # canonical-interval coverage; Capital never routes 1D (alpaca-only).
}

# Alpaca `/v2/stocks/{symbol}/bars` `timeframe` query token per canonical
# interval. The equity strategies are daily (``1D`` → Alpaca ``1Day``); the
# intraday tokens are listed for table completeness but equities only use 1D.
ALPACA_TIMEFRAME_BY_INTERVAL: dict[str, str] = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1H": "1Hour",
    "1D": "1Day",
}

# Per-timeframe fetch cadence — bars only need to be re-pulled when a fresh
# candle is likely to have closed. Honours BAR_INTERVALS = {1m, 5m, 15m, 1H, 1D}.
TIMEFRAME_FETCH_CADENCE_SEC: dict[str, float] = {
    "1m": 5.0,    # every tick
    "5m": 30.0,
    "15m": 60.0,
    "1H": 300.0,
    "1D": 3600.0,  # daily bars close once/day — hourly re-pull is ample.
}


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_alpaca_ts(value: Any) -> int:
    """Alpaca bar ``t`` is RFC3339/ISO-8601 (e.g. ``2024-01-02T05:00:00Z``).

    Return seconds-epoch (UTC). A numeric value passes through; a malformed or
    missing value returns 0 (the bar is then dropped by the caller). The tz is
    forced to UTC so a non-UTC host does not shift every bar by its offset
    (same hardening as ``capital_price_row_to_bar``).
    """
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value:
        return 0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _alpaca_bar_to_canonical(
    row: dict[str, Any],
    *,
    symbol: str,
    bar_interval: str,
    underlying_group_id: str,
) -> Bar | None:
    """Convert one raw Alpaca bar dict (``t/o/h/l/c/v/n/vw``) to canonical Bar.

    Mirrors ``okx_candle_to_bar`` / ``capital_price_row_to_bar``: the
    ``instrument_id`` is ``alpaca:{symbol}``, ts is seconds-epoch UTC, OHLCV is
    cast via ``float``. Returns ``None`` for an unusable row (bad ts / non-
    positive open/close) so a single malformed candle never aborts the batch.
    """
    if bar_interval not in BAR_INTERVALS:
        return None
    ts = _parse_alpaca_ts(row.get("t"))
    if ts <= 0:
        return None
    o = _to_float(row.get("o"))
    h = _to_float(row.get("h"))
    low = _to_float(row.get("l"))
    c = _to_float(row.get("c"))
    if o <= 0.0 or c <= 0.0:
        return None
    vol = _to_float(row.get("v"))
    return Bar(
        instrument_id=f"alpaca:{symbol}",
        underlying_group_id=underlying_group_id,
        venue="alpaca",
        symbol=symbol,
        bar_interval=bar_interval,
        ts=ts,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=vol,
        notional_usd=c * vol if vol > 0 else 0.0,
        trade_count=int(_to_float(row.get("n"))),
        vwap=_to_float(row.get("vw")),
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="alpaca_rest",
    )


# Calendar-day lookback per interval. CRITICAL: the window [start, now] must hold
# FEWER bars than ``limit`` (240) — Alpaca returns the OLDEST ``limit`` bars after
# ``start`` (ascending), so a window wider than ``limit`` yields STALE bars (e.g.
# 800d → oldest 240 ending ~1y ago). 1D=330d ≈ 227 trading days: < 240 limit (so
# ALL are returned, ending at ~now = recent) AND ≥ the equity MA200 (~205) warmup.
_ALPACA_LOOKBACK_DAYS: dict[str, int] = {"1D": 330, "1H": 45, "15m": 8, "5m": 4, "1m": 2}


def _alpaca_bars_start(bar_interval: str) -> str:
    """Lower-bound ``start`` date for the Alpaca /bars call — REQUIRED.

    Without ``start`` the v2 bars endpoint returns an empty list (verified live:
    AAPL/1Day → 0 bars sans start, 240 with start). The lookback is tuned so the
    window holds < ``limit`` bars → Alpaca returns them all ending at ~now
    (recent), not the oldest ``limit`` (stale). See ``_ALPACA_LOOKBACK_DAYS``.
    """
    days = _ALPACA_LOOKBACK_DAYS.get(bar_interval, 330)
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


async def fetch_alpaca_bars(
    adapter: Any,
    symbol: str,
    *,
    bar_interval: str = "1D",
    limit: int = 240,
    asset_class: str = "equity",
) -> list[Bar]:
    """Fetch + normalize Alpaca equity bars to canonical Bars (newest last).

    Wraps ``AlpacaAdapter.fetch_bars`` (raw ``list[dict]`` with keys
    ``t/o/h/l/c/v/n/vw``). The canonical ``bar_interval`` is mapped to the
    Alpaca ``timeframe`` token (``1D`` → ``1Day``). Alpaca returns bars in
    chronological ascending order (newest last) — the canonical contract — so
    no reversal is needed (unlike OKX, which is newest-first). A fetch failure
    logs + returns ``[]`` (mirror of the OKX / Capital branches).
    """
    timeframe = ALPACA_TIMEFRAME_BY_INTERVAL.get(bar_interval)
    if timeframe is None:
        logger.warning(
            "[L1/alpaca] unsupported bar_interval=%r — skipping %s",
            bar_interval,
            symbol,
        )
        return []
    start = _alpaca_bars_start(bar_interval)
    try:
        raw = await adapter.fetch_bars(
            symbol, timeframe=timeframe, limit=limit, start=start
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.debug("[L1/alpaca] %s fetch failed: %r", symbol, exc)
        return []
    underlying = compute_underlying_group_id("alpaca", symbol, asset_class=asset_class)
    out: list[Bar] = []
    for row in raw:
        bar = _alpaca_bar_to_canonical(
            row, symbol=symbol, bar_interval=bar_interval,
            underlying_group_id=underlying,
        )
        if bar is not None:
            out.append(bar)
    logger.debug(
        "[alpaca] bars fetched %s/%s requested=%d got=%d",
        symbol, bar_interval, limit, len(out),
    )
    return out


async def fetch_bars_one(
    venue: str,
    symbol: str,
    asset_class: str,
    *,
    capital_session: CapitalSession | None = None,
    alpaca_adapter: Any = None,
    limit: int = 240,
    bar_interval: str = "1m",
) -> list[Bar]:
    """Single-instrument bar fetch. Returns canonical Bar list (newest last).

    F10 — Day 9: ``bar_interval`` defaults to ``1m`` for back-compat but the
    production loop now passes the per-strategy ``metadata.timeframe`` so
    Capital strategies (1H bars) no longer eat 1m candles silently.

    Stream-coverage P0: ``venue == 'alpaca'`` (daily equity bars) routes to
    ``fetch_alpaca_bars`` when an ``alpaca_adapter`` is threaded through; with
    no adapter it returns ``[]`` (mirror of the capital ``None`` session guard).
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
    if venue == "alpaca":
        if alpaca_adapter is None:
            return []
        # Alpaca returns chronological ascending (newest last) — no reversal.
        return await fetch_alpaca_bars(
            alpaca_adapter,
            symbol,
            bar_interval=bar_interval,
            limit=limit,
            asset_class=asset_class,
        )
    return []


async def ingest_bars_per_timeframe(
    conn: sqlite3.Connection,
    focus: list[tuple[str, str, str, str]],
    *,
    timeframe_to_venues: dict[str, set[str]],
    last_fetch_monotonic_by_tf: dict[str, float],
    bars_persisted_by_tf: dict[str, int],
    capital_session: CapitalSession | None = None,
    alpaca_adapter: Any = None,
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
                alpaca_adapter=alpaca_adapter,
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
    alpaca_adapter: Any = None,
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
                capital_session=capital_session,
                alpaca_adapter=alpaca_adapter, limit=limit,
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
        # Async ingest: persist + sample-append on the loop, baseline
        # sort/percentile offloaded to a worker thread (shared conn stays on
        # the loop) so the per-1m-tick recompute doesn't block the engine.
        result = await ingest_bars_async(conn, group, asset_class=ac)
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
    """Read the most recent ``limit`` bars from SQLite (newest last).

    FUTURE-dated bars (``ts > now + BAR_TS_CLOCK_SKEW_SLACK_SEC``) are excluded so
    a stale +10h Capital bar is never returned as the most-recent canvas.
    """
    instrument_id = f"{venue}:{symbol}"
    ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    rows = conn.execute(
        """
        SELECT instrument_id, underlying_group_id, venue, symbol, bar_interval,
               ts, open, high, low, close, volume, notional_usd, trade_count,
               vwap, bid_close, ask_close, spread_bps_close, source
        FROM bars
        WHERE instrument_id = ? AND bar_interval = ? AND ts <= ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (instrument_id, bar_interval, ts_upper, int(limit)),
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


