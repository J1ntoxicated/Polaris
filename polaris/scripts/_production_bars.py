"""Day 8 production paper loop — Layer 1 per-tick bar ingest helpers.

Split out of ``_production_layers`` to keep both modules ≤500 LOC.
``_production_layers`` re-exports these names + the timeframe constants so
existing import paths (callers + tests) keep working. Fetches 1m/5m/15m/1H
bars for the active focus list and persists to ``bars`` + ``ticker_baseline_*``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.data.ingest import (
    _db_path_from_conn,
    ingest_bars_async,
    ingest_bars_offloaded,
    persist_bars,
    persist_bars_offloaded,
)
from polaris.core.data.schema import BAR_INTERVALS, Bar
from polaris.datastream import emit as datastream_emit
from polaris.scripts._production_asset_class import resolve_bar_asset_class
from polaris.scripts._session_map import equity_fetch_active
from polaris.scripts._yahoo_bars import (
    fetch_yahoo_bars,
    is_okx_native_preferred,
    should_fetch_exchange_fallback,
)
from polaris.storage.db_writer import DBWriter
from polaris.venues.alpaca.adapter import ALPACA_BARS_MULTI_CHUNK
from polaris.venues.capital.adapter import fetch_capital_bars
from polaris.venues.capital.session import CapitalSession
from polaris.venues.okx.adapter import fetch_okx_bars

logger = logging.getLogger(__name__)

# Recency-guard dedup (logging only). A dead feed re-trips the staleness guard on
# every read (34k+ identical WARNINGs on the live log). This module-level set
# records which ``(instrument_id, bar_interval)`` are CURRENTLY flagged stale so
# the runtime log gets ONE WARNING on the fresh→stale transition (operator health
# signal) and the per-read detail goes to the datastream sink. Cleared per
# instrument the first read after fresh data lands. Single-process loop, so a
# plain module set is safe; it does not gate any decision (the ``return []`` is
# unchanged whether or not we log).
_RECENCY_STALE_FLAGGED: set[tuple[str, str]] = set()

# FIX 1/2 — trading-path FUTURE-dated bar guard. The Capital REST ts source-fix
# only corrects NEW bars; the live DB still holds ~5,793 stale +10h FUTURE-dated
# Capital bars (the AEST-naive snapshotTime parse). Every trading-path bar read
# (strategy/regime canvas, exit close-mark, cost-R ATR window) bounds ts at
# ``now + BAR_TS_CLOCK_SKEW_SLACK_SEC`` so a +10h bar is never the "most recent"
# canvas. Small slack absorbs legitimate clock skew (a just-closed bar a few
# seconds ahead stays visible). Mirrors the dashboard ``_last_prices`` guard.
BAR_TS_CLOCK_SKEW_SLACK_SEC = 120

# Recency guard (Jin 2026-06-22 coherence audit). When a venue feed goes dead
# the ``bars`` table keeps serving its last-stored candle, so the strategy /
# regime / exit canvas would make decisions on a price that is provably stale
# (the live Alpaca feed went 98.7h dark). ``read_recent_bars`` accepts a
# ``freshness_threshold_sec``: if the NEWEST stored bar is older than the
# threshold, the accessor returns ``[]`` so the trading path's existing
# ``if not bars / len(bars) < 30: continue`` skips the symbol. DATA-HEALTH gate
# (no live price = cannot trade), NOT a defensive throttle — flow_not_block is
# preserved: a fresh feed is untouched and the guard auto-clears the instant
# fresh data lands (it is a pure function of the stored ts, not a sticky flag).
# Applies to ALL venues. The default below (36h) clears a normal daily-bar
# cadence (≤1 bar/day, plus weekends) yet is far tighter than the 98.7h dead-
# feed incident; callers pass a tighter window for intraday timeframes.
BAR_STALENESS_MAX_SEC = 36 * 3600

# Per-timeframe freshness window (sec) for the recency guard. A bar of interval
# T should be re-published roughly every T; the threshold is a GENEROUS multiple
# so a normal cadence (plus a weekend gap on daily/equity feeds) never trips,
# while a dead feed (many missed candles) does. Tuned vs the 98.7h Alpaca
# incident: 1D=36h (covers a weekend; a 99h gap is caught), intraday windows are
# tens of minutes (a 99h gap is caught immediately). Unmapped → BAR_STALENESS_MAX_SEC.
_BAR_STALENESS_BY_INTERVAL: dict[str, float] = {
    "1m": 30 * 60.0,      # 1m bars: 30min silence = dead
    "5m": 60 * 60.0,
    "15m": 120 * 60.0,
    "1H": 6 * 3600.0,
    "4H": 8 * 3600.0,     # 4h cadence → 8h silence = dead (generous 2x cadence)
    "1D": float(BAR_STALENESS_MAX_SEC),  # 36h — covers a normal weekend gap
}


def staleness_threshold_for(bar_interval: str) -> float:
    """Recency-guard freshness window (sec) for a canonical ``bar_interval``.

    Generous multiple of the bar cadence so a healthy feed is never flagged, yet
    a dead feed (the 98.7h Alpaca incident) is. Unmapped interval →
    ``BAR_STALENESS_MAX_SEC``. Pure; used by the trading-path bar reads.
    """
    return _BAR_STALENESS_BY_INTERVAL.get(bar_interval, float(BAR_STALENESS_MAX_SEC))


# Incremental-fetch cache layer (Alpaca 429 fix, 2026-06-24). Bars are a CACHE:
# "have the current period → serve it; missing/rolled → fetch only the gap." The
# bot previously re-fetched the FULL bar window for EVERY focus symbol on EVERY
# cadence-due tick. With ~99 Alpaca equity symbols force-seated into the 1m
# regime bucket (5s cadence), that issued ~99 /v2/stocks/{symbol}/bars requests
# per tick → Alpaca free-tier 429 → bars 9-50h stale → US-equity track dark. The
# two helpers below + the ``skip_if_current``/``since_ts`` gates make the fetch
# INCREMENTAL: skip a re-fetch when the current period's bar is already held, and
# bound the window at the last stored bar. Pure fetch-efficiency, NOT a throttle
# (flow_not_block: a missing/rolled period always fetches; no symbol is blocked).
# INTRADAY only — skip-if-fresh is an intra-period gate and must never be applied
# to ``1D``. Alpaca daily bars are stamped at the RTH SESSION OPEN (04:00/05:00Z),
# NOT UTC midnight, so a ``floor(now, 86400)`` boundary would not line up with the
# stored daily ts. ``1D`` is therefore intentionally ABSENT → ``current_period_
# open_ts`` returns ``now`` for it → the skip gate never fires for daily (the 1D
# refetch is hourly, not the 5s storm, so it does not need skip-if-fresh — only
# the incremental ``since_ts`` window, which is session-open-agnostic).
_PERIOD_SECONDS_BY_INTERVAL: dict[str, int] = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1H": 3600,
    # 4H period = 14400s. NB: the UTC floor here does NOT line up with OKX's
    # UTC+8 4H boundary, so 4H is NOT placed in any ``skip_if_current`` set —
    # the period is mapped only so ``current_period_open_ts`` returns a sane
    # floor (never the now-fallback) for incremental-window bookkeeping.
    "4H": 4 * 3600,
}


def current_period_open_ts(bar_interval: str, now: int) -> int:
    """Seconds-epoch (UTC) of the OPEN of the in-progress bar for ``bar_interval``.

    ``floor(now / period) * period`` for the INTRADAY intervals (1m/5m/15m/1H),
    whose stored ts are epoch-aligned to the period. ``1D`` is deliberately
    unmapped → returns ``now`` (never skips): Alpaca daily bars are stamped at the
    session open, not a UTC-midnight boundary, so a daily skip gate would be
    unsound. Unmapped interval → ``now`` (conservative, flow_not_block).
    """
    period = _PERIOD_SECONDS_BY_INTERVAL.get(bar_interval)
    if period is None or period <= 0:
        return now
    return (now // period) * period


def last_stored_bar_ts(
    conn: sqlite3.Connection, instrument_id: str, bar_interval: str
) -> int | None:
    """Newest stored bar ts for ``(instrument_id, bar_interval)`` or ``None``.

    ``MAX(ts)`` is an index lookup on the bars PK ``(instrument_id, bar_interval,
    ts)`` — cheap enough to call per focus symbol each tick. Drives both the
    skip-if-fresh gate and the incremental ``since_ts`` window.
    """
    row = conn.execute(
        "SELECT MAX(ts) FROM bars WHERE instrument_id = ? AND bar_interval = ?",
        (instrument_id, bar_interval),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


# F10 — Strategy timeframe → venue resolution + cadence (sec).
# OKX `bar` query parameter accepts the canonical token directly. Capital
# `/prices` requires a textual resolution token (MINUTE / MINUTE_5 / ...).
CAPITAL_RESOLUTION_BY_INTERVAL: dict[str, str] = {
    "1m": "MINUTE",
    "5m": "MINUTE_5",
    "15m": "MINUTE_15",
    "1H": "HOUR",
    "4H": "HOUR_4",  # canonical-interval coverage; 4H is OKX-only (crypto swing).
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
# candle is likely to have closed. Honours BAR_INTERVALS = {1m,5m,15m,1H,4H,1D}.
# 🚨 4H MUST be explicit here: without it the ``.get(tf, 5.0)`` fallback would
# re-fetch 4H every 5s (≈ 31 req/s across the OKX focus list) = the #21 candles
# "Too Many" storm + the #74 tick-engine STALL. A 4H bar closes once / 4h, so an
# hourly re-pull is already generous; this single entry is the storm guard.
TIMEFRAME_FETCH_CADENCE_SEC: dict[str, float] = {
    "1m": 5.0,    # every tick
    "5m": 30.0,
    "15m": 60.0,
    "1H": 300.0,
    "4H": 3600.0,  # 4H closes once/4h — hourly re-pull is ample (storm guard).
    "1D": 3600.0,  # daily bars close once/day — hourly re-pull is ample.
}

# Cadence PHASE offset (forensic wf_1f586d0a tick-body fix #2, Jin 2026-07-10).
# 5m/15m/1H cadences (30s/60s/300s) share a common multiple (LCM=300s), so
# without a phase offset all three buckets become "due" on the SAME epoch
# tick every 300s, stacking their DB writes into one instant — a measured
# contributor to the write-lock contention behind the 45.2s median tick body.
# The offset shifts WHEN within each cadence period a bucket becomes due —
# the FREQUENCY (how often) is completely unchanged, so this is NOT a
# throttle (flow_not_block: every bucket still fires exactly once per its own
# cadence). 5m keeps offset 0 as the reference; 15m/1H are shifted away from
# it AND from each other (see ``is_timeframe_due_epoch`` — the three due-sets
# are proven pairwise disjoint over one full LCM period by
# ``test_cadence_phase_offset.py``). A timeframe absent here (1m/4H/1D)
# defaults to 0 (byte-identical to no offset) — only the LCM-colliding trio
# is targeted.
TIMEFRAME_FETCH_PHASE_OFFSET_SEC: dict[str, float] = {
    # Presence in this table = participates in the epoch bootstrap gate
    # (max first-fetch defer == own cadence: 1m 5s / 5m 30s — negligible).
    # 4H/1D are DELIBERATELY absent: their 3600s cadence would defer the
    # first-ever post-restart fetch up to ~1h (daily-bar starvation class,
    # review MAJOR 2026-07-10) — they fetch immediately, ungated.
    "1m": 0.0,
    "5m": 0.0,
    "15m": 10.0,
    "1H": 20.0,
}


def is_timeframe_due_epoch(timeframe: str, now_epoch: int) -> bool:
    """Pure, stateless epoch-mod cadence-due check honouring the phase offset.

    ``True`` iff ``now_epoch`` (seconds-epoch UTC) falls on this timeframe's
    due slot: ``(now_epoch - offset) % cadence == 0``. Same cadence/frequency
    as ``TIMEFRAME_FETCH_CADENCE_SEC`` — only the phase within the period
    shifts per ``TIMEFRAME_FETCH_PHASE_OFFSET_SEC``. Used as an ADDITIVE
    bootstrap gate (see ``ingest_bars_per_timeframe``'s ``now_epoch`` param) —
    it never replaces the existing per-(timeframe,venue) monotonic
    since-last-fetch cadence tracking, it only spreads the very first
    due-instant for the LCM-colliding buckets so their steady-state firing
    stays staggered forever after.
    """
    # Review MAJOR (2026-07-10 tick-w1): only the explicitly-offset (LCM-
    # colliding) timeframes get the bootstrap gate. An absent timeframe must
    # be due IMMEDIATELY — gating it on epoch%cadence deferred the first-ever
    # 4H/1D fetch up to ~1h after each 07:30 restart (daily-bar starvation
    # class), contradicting the offset table's own "absent = byte-identical"
    # contract.
    if timeframe not in TIMEFRAME_FETCH_PHASE_OFFSET_SEC:
        return True
    cadence = int(TIMEFRAME_FETCH_CADENCE_SEC.get(timeframe, 5.0))
    if cadence <= 0:
        return True
    offset = int(TIMEFRAME_FETCH_PHASE_OFFSET_SEC[timeframe]) % cadence
    return (int(now_epoch) - offset) % cadence == 0

# Default per-fetch bar count. Intraday timeframes keep 240 (the Alpaca free-tier
# window that avoids stale-tail / 429 pressure). 1D needs MORE: the deepest 1D
# strategy warmup is equity_52wk_high_breakout (HIGH_LOOKBACK 252 + 1 = 253
# bars); a 240 cap left ``warmup_ok`` ALWAYS False → that strategy was INERT
# (no-emit across every equity symbol). Yahoo (the PRIMARY 1D bar source) holds a
# 2y window (~500 trading bars), so a deeper 1D limit just slices a longer tail —
# the warmup-depth fix is purely additive (no block / no warmup hurdle lowered;
# the 252-bar-high edge is untouched). Intraday tf stay 240 so the Alpaca 429
# avoidance is preserved exactly.
#
# 15m → 400 (Jin 2026-07-09, rsi_bb_pullback silent-INERT part A): the overnight
# source audit measured ~50-56% of OKX 15m bars as forward-fill synthetic (flat
# OHLC / vol<=0); rsi_bb_pullback's ma_200 filter needs 200 REAL bars, so the
# 240-bar window (~120 real at that synthetic fraction) left only ~14% of the
# 227-symbol universe with a computable ma_200 (the strategy was 0/2244 signals
# for want of real-bar depth, not the entry threshold). A 400-bar window at the
# same ~50% real fraction yields ~200 real bars, clearing ma_200 for the
# majority of the universe. The DB already holds 1300+ 15m bars/symbol, so this
# is purely a deeper READ slice of existing history — no new fetch pressure, and
# OKX 15m is NOT on the Alpaca-429-bounded path this default protects. Mirrors
# the existing 1D→260 warmup-depth precedent. ALL-MODES infra (not env-gated):
# read depth only, no threshold lowered, no edge changed — REAL rsi_bb stays
# dispatch-off regardless.
_DEFAULT_BAR_FETCH_LIMIT = 240
# 15m raised 400 -> 600 (Alpaca sleeve Wave 1.5, §1 #5): equity_opening_range_
# breakout needs 560 REAL 15m bars (20 sessions x 26 bars/session + buffer) for
# warmup_ok — 400 permanently capped the canvas BELOW that (silent INERT: the
# strategy would never reach warmup regardless of how much DB history exists).
# 600 clears 560 with margin and stays comfortably above rsi_bb_pullback's
# diluted ~400 need (its own 205-bar warmup against a ~50% synthetic-fill OKX
# 15m canvas) — a strictly ADDITIVE widen, no existing 15m consumer narrows.
_BAR_FETCH_LIMIT_BY_INTERVAL: dict[str, int] = {"1D": 260, "15m": 600}


def bar_fetch_limit_for(timeframe: str) -> int:
    """Per-timeframe bar-fetch/read limit. 1D → 260 (clears the 253 equity_52wk
    warmup); 15m → 600 (clears the 560-bar equity_opening_range_breakout warmup
    AND the ma_200/200-real-bar rsi_bb_pullback warmup against the ~50%
    synthetic-fill 15m canvas); every other timeframe → 240 (Alpaca 429
    avoidance preserved)."""
    return _BAR_FETCH_LIMIT_BY_INTERVAL.get(timeframe, _DEFAULT_BAR_FETCH_LIMIT)


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


# Incremental re-fetch overlap (sec). When history exists we lower-bound the
# window at the last stored bar MINUS this slack so a just-closed boundary bar is
# re-captured (Alpaca's ``start`` is inclusive; the overlap absorbs clock skew +
# an in-progress→closed bar update + the weekend gap on 1D). The dominant
# incremental path is 1D (Alpaca strategies are all daily); ``start`` is
# day-granular (``%Y-%m-%d``), and ``sort='desc'`` + ``limit`` cap the response,
# so this stays a small re-pull (a few days of bars), never the full 330d window.
_ALPACA_INCREMENTAL_OVERLAP_SEC = 2 * 86400  # 2 days — covers a weekend gap on 1D


def _alpaca_bars_start(bar_interval: str, *, since_ts: int | None = None) -> str:
    """Lower-bound ``start`` date for the Alpaca /bars call — REQUIRED.

    Without ``start`` the v2 bars endpoint returns an empty list (verified live:
    AAPL/1Day → 0 bars sans start, 240 with start). The lookback is tuned so the
    window holds < ``limit`` bars → Alpaca returns them all ending at ~now
    (recent), not the oldest ``limit`` (stale). See ``_ALPACA_LOOKBACK_DAYS``.

    Incremental (2026-06-24): when ``since_ts`` is given (we already hold history)
    the window is bounded at ``max(fixed_lookback_start, since_ts - overlap)`` —
    a re-fetch then returns only the few NEW bars since the last stored one
    (small payload), NOT the full lookback. The ``max(...)`` floor means a
    ``since_ts`` older than the fixed lookback NEVER widens the window past warmup
    (we never fetch more than the fixed window needs). First fetch (``since_ts``
    None) keeps the full fixed-lookback warmup.
    """
    days = _ALPACA_LOOKBACK_DAYS.get(bar_interval, 330)
    fixed_start_dt = datetime.now(UTC) - timedelta(days=days)
    if since_ts is not None:
        incr_start_dt = datetime.fromtimestamp(
            max(0, since_ts - _ALPACA_INCREMENTAL_OVERLAP_SEC), tz=UTC
        )
        # Tighter (more recent) of the two — never widen past the fixed warmup.
        start_dt = max(fixed_start_dt, incr_start_dt)
    else:
        start_dt = fixed_start_dt
    return start_dt.strftime("%Y-%m-%d")


async def fetch_alpaca_bars(
    adapter: Any,
    symbol: str,
    *,
    bar_interval: str = "1D",
    limit: int = 240,
    asset_class: str = "equity",
    since_ts: int | None = None,
) -> list[Bar]:
    """Fetch + normalize Alpaca equity bars to canonical Bars (newest last).

    Wraps ``AlpacaAdapter.fetch_bars`` (raw ``list[dict]`` with keys
    ``t/o/h/l/c/v/n/vw``). The canonical ``bar_interval`` is mapped to the
    Alpaca ``timeframe`` token (``1D`` → ``1Day``). Alpaca returns bars in
    chronological ascending order (newest last) — the canonical contract — so
    no reversal is needed (unlike OKX, which is newest-first). A fetch failure
    logs + returns ``[]`` (mirror of the OKX / Capital branches).

    Incremental (2026-06-24): ``since_ts`` (the last stored bar ts) tightens the
    ``start`` window to just past the held history so a re-fetch returns only the
    new bars (small payload), cutting the Alpaca 429 pressure. ``None`` → the
    full fixed-lookback warmup (first fetch). See ``_alpaca_bars_start``.
    """
    timeframe = ALPACA_TIMEFRAME_BY_INTERVAL.get(bar_interval)
    if timeframe is None:
        logger.warning(
            "[L1/alpaca] unsupported bar_interval=%r — skipping %s",
            bar_interval,
            symbol,
        )
        return []
    start = _alpaca_bars_start(bar_interval, since_ts=since_ts)
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
    # FRESHNESS FIX (2026-06-22): the adapter now requests ``sort='desc'`` so the
    # FRESHEST bars survive the ``limit`` cap (the asc default truncated to the
    # OLDEST 240 → stale canvas). Re-sort ascending to honour the canonical
    # newest-LAST contract regardless of the venue sort order.
    out.sort(key=lambda b: b.ts)
    logger.debug(
        "[alpaca] bars fetched %s/%s requested=%d got=%d",
        symbol, bar_interval, limit, len(out),
    )
    return out


async def fetch_alpaca_bars_multi(
    adapter: Any,
    symbols: list[str],
    *,
    bar_interval: str = "1D",
    limit: int = 240,
    asset_class: str = "equity",
    since_ts: int | None = None,
    wait_for_token: bool = False,
    chunk_size: int = ALPACA_BARS_MULTI_CHUNK,
) -> dict[str, list[Bar]]:
    """Fetch + normalize Alpaca bars for MANY symbols, chunked (A2 429-storm fix).

    Root cause: the static-ground off-tick fallback issued ONE
    ``/v2/stocks/{symbol}/bars`` request per unmapped/throttled symbol — with
    Yahoo IP-throttled that was ~1,491 single fetches per walk, dumped onto the
    Alpaca 200/min IEX cap. ``AlpacaAdapter.fetch_bars_multi`` batches many
    symbols into ONE request; this wrapper chunks ``symbols`` at ``chunk_size``
    (~100/request — ~1,491 symbols → ~15 requests) and normalizes each row the
    same way :func:`fetch_alpaca_bars` does.

    Per-symbol recency/skip semantics are PRESERVED: every requested symbol is a
    key in the returned dict (even if Alpaca returned no bars for it → ``[]``),
    so a caller iterating ``symbols`` never silently drops one. A chunk-level
    fetch error degrades that whole chunk's symbols to ``[]`` (one bad chunk
    never aborts the others — degrade-never-halt) rather than raising.

    ``wait_for_token`` forwards to :meth:`AlpacaAdapter.fetch_bars_multi` (A2
    WAIT-NOT-DROP): the off-tick static-ground caller passes ``True`` so a
    drained token bucket is WAITED OUT rather than proceeding token-less into a
    429; the live tick path never calls this batch fetcher.

    ``limit`` here is the PER-SYMBOL warmup depth of the caller's *intent*
    (mirrors :func:`fetch_alpaca_bars`'s per-symbol ``limit``), but is NOT
    forwarded to Alpaca as-is: the multi-symbol endpoint's ``limit`` is an
    AGGREGATE cap across the whole chunk, not per-symbol, so
    ``AlpacaAdapter.fetch_bars_multi`` drains ``next_page_token`` pagination at
    its own max page size instead, guaranteeing every symbol gets its full
    per-chunk history regardless of chunk width. See the pagination note on
    :meth:`AlpacaAdapter.fetch_bars_multi`.
    """
    out: dict[str, list[Bar]] = {}
    if not symbols:
        return out
    timeframe = ALPACA_TIMEFRAME_BY_INTERVAL.get(bar_interval)
    if timeframe is None:
        logger.warning(
            "[L1/alpaca] unsupported bar_interval=%r — skipping multi-fetch of %d symbol(s)",
            bar_interval, len(symbols),
        )
        return dict.fromkeys(symbols, [])
    start = _alpaca_bars_start(bar_interval, since_ts=since_ts)
    for i in range(0, len(symbols), max(1, chunk_size)):
        chunk = symbols[i : i + chunk_size]
        try:
            raw_by_symbol = await adapter.fetch_bars_multi(
                chunk, timeframe=timeframe, limit=limit, start=start,
                wait_for_token=wait_for_token,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.debug(
                "[L1/alpaca] multi-fetch chunk (%d symbols) failed: %r",
                len(chunk), exc,
            )
            for symbol in chunk:
                out[symbol] = []
            continue
        for symbol in chunk:
            underlying = compute_underlying_group_id(
                "alpaca", symbol, asset_class=asset_class
            )
            bars: list[Bar] = []
            for row in raw_by_symbol.get(symbol, []):
                bar = _alpaca_bar_to_canonical(
                    row, symbol=symbol, bar_interval=bar_interval,
                    underlying_group_id=underlying,
                )
                if bar is not None:
                    bars.append(bar)
            bars.sort(key=lambda b: b.ts)  # canonical newest-last (mirror single-fetch)
            out[symbol] = bars
    logger.info(
        "[alpaca] multi-fetch bars=%s/%s symbols=%d chunks=%d",
        bar_interval, len(symbols),
        sum(1 for v in out.values() if v),
        (len(symbols) + chunk_size - 1) // max(1, chunk_size),
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
    since_ts: int | None = None,
    gpt_client_factory: Any = None,
    alpaca_multi_cache: dict[str, list[Bar]] | None = None,
    wait_for_token: bool = False,
) -> list[Bar]:
    """Single-instrument bar fetch. Returns canonical Bar list (newest last).

    Yahoo PRIMARY (2026-06-24): bar HISTORY is fetched from Yahoo Finance first
    (free/unlimited-grade) so the exchange REST bar endpoints stop getting
    hammered (the Alpaca free-tier 429 storm). The exchange path is a throttled
    FALLBACK only — reached when Yahoo returns no bars (unmapped symbol / error /
    empty frame), and then gated by ``should_fetch_exchange_fallback`` so an
    unmapped symbol cannot REST-storm every cadence tick. The live entry/exit
    price still flows via the exchange WS quote path — UNTOUCHED; only the
    bar-HISTORY source changes here. flow_not_block: no entry/size/exit is gated.

    F10 — Day 9: ``bar_interval`` defaults to ``1m`` for back-compat but the
    production loop now passes the per-strategy ``metadata.timeframe`` so
    Capital strategies (1H bars) no longer eat 1m candles silently.

    Stream-coverage P0: ``venue == 'alpaca'`` (daily equity bars) routes to
    ``fetch_alpaca_bars`` when an ``alpaca_adapter`` is threaded through; with
    no adapter it returns ``[]`` (mirror of the capital ``None`` session guard).

    Incremental (2026-06-24): ``since_ts`` (the last stored bar ts for this
    instrument/interval) is forwarded to the Alpaca branch to tighten the fetch
    window. OKX/Capital ignore it (their windows are already small + healthy) —
    behaviour for those venues is byte-identical.

    ``gpt_client_factory``: forwarded to the Yahoo resolver so a deterministically
    unmapped symbol can be resolved to its Yahoo ticker via ONE gpt-5-mini lookup
    (keyless-safe — a missing key just disables the GPT branch, exchange fallback
    still covers it).

    A2 429-storm fix: ``alpaca_multi_cache`` (default ``None`` — the live 5s tick
    path never passes it, byte-identical behaviour) lets a bulk caller (the
    static-ground off-tick walk) pre-fetch ALL its Alpaca symbols via ONE batched
    ``fetch_alpaca_bars_multi`` call and hand the result in here. When present and
    Yahoo misses for an ``alpaca`` symbol, the cached batch result is served
    INSTEAD OF issuing a new single-symbol Alpaca request — this is what collapses
    ~1,491 single fallback fetches to ~15 batch requests. A symbol absent from the
    cache (Alpaca returned nothing for it) degrades to ``[]``, same as a normal
    single-fetch miss (no symbol is silently dropped from the caller's own
    accounting — the caller still iterates every requested symbol).

    Capital bars 429-storm fix: ``wait_for_token`` forwards to
    ``fetch_capital_bars``'s pacing bucket (default ``False`` — the live 5s tick
    path uses the bounded soft-cap acquire, byte-identical behaviour). The
    off-tick static-ground full-universe walk passes ``wait_for_token=True`` (a
    real wait, no cadence deadline to protect) so its exotic-FX/commodity
    exchange-fallback fan-out paces under Capital's ~10 req/s demo ceiling
    instead of bursting past it.
    """
    # Yahoo PRIMARY — bar HISTORY only (live price WS path untouched).
    yahoo_bars = await fetch_yahoo_bars(
        venue, symbol, asset_class,
        bar_interval=bar_interval, limit=limit,
        gpt_client_factory=gpt_client_factory,
    )
    if yahoo_bars:
        return yahoo_bars  # already newest-last; no reversal needed.
    # Yahoo had nothing. For an Alpaca symbol with a pre-fetched batch cache
    # available, serve from it — NO per-symbol network call (the 429-storm fix).
    if venue == "alpaca" and alpaca_multi_cache is not None:
        return alpaca_multi_cache.get(symbol, [])
    # Yahoo had nothing → exchange FALLBACK, throttled per (venue, symbol) so the
    # unmapped tail cannot recreate a REST storm (fetch-efficiency, not a block).
    #
    # ① OKX-NATIVE preferred BYPASS (🚨 freshness-regression trap): a top-N major is
    # INTENTIONALLY routed OKX-native (map_to_yahoo_ticker → None), so it must NOT be
    # held by the 300s unmapped-tail cooldown — that would starve BTC 1m to a 5-min
    # refetch (the精밀도↑ intent flipping to신선도↓). The cooldown exists to stop the
    # genuinely-UNMAPPED tail from re-storming the exchange; a preferred symbol is
    # the deliberate native path, and the OKX candles bucket already guards the
    # storm, so the cooldown is bypassed for it. Non-preferred symbols keep it.
    if not (
        venue == "okx" and is_okx_native_preferred(symbol)
    ) and not should_fetch_exchange_fallback(
        venue, symbol, time.monotonic(), bar_interval=bar_interval,
    ):
        return []
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
                wait_for_token=wait_for_token,
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
            since_ts=since_ts,
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
    now_epoch: int | None = None,
    skip_if_current: set[tuple[str, str]] | None = None,
    gpt_client_factory: Any = None,
    db_writer: DBWriter | None = None,
) -> dict[str, int]:
    """F10 — Day 9: drive the per-timeframe ingest fan-out for one tick.

    Honours ``TIMEFRAME_FETCH_CADENCE_SEC`` so each timeframe bucket only
    fetches when its cadence is due. ``last_fetch_monotonic_by_tf`` and
    ``bars_persisted_by_tf`` are mutated in place (caller owns lifetimes).

    ``now_epoch`` (forensic wf_1f586d0a fix #2, OPT-IN — ``None`` keeps
    byte-identical legacy behaviour): when given, the FIRST-EVER fetch for a
    (timeframe, venue) key additionally waits for
    ``is_timeframe_due_epoch(timeframe, now_epoch)`` before firing, instead of
    firing immediately. This staggers the cold-start due-instant for the
    LCM-colliding buckets (5m/15m/1H); once a bucket has fired once, its
    normal monotonic-since-last-fetch cadence tracking (below) takes over and
    the staggered phase persists for the life of the process. A bucket with
    zero offset (1m/5m/4H/1D) is unaffected either way.

    ``skip_if_current`` (Alpaca 429 fix, 2026-06-24): forwarded to
    ``ingest_bars_for_focus`` — a set of ``(venue, bar_interval)`` whose symbols
    skip the network re-fetch when the current-period bar is already stored (the
    regime-only Alpaca 1m bucket). flow_not_block: a rolled/missing period always
    fetches. See ``ingest_bars_for_focus``.

    Codex F10 R2 fix: cadence is tracked per ``(timeframe, venue)`` keyed as
    ``"{tf}:{venue}"``. A partial-bucket failure (OKX 1H succeeds, Capital
    1H returns zero) must not defer the failing venue's retry by the full
    cadence window. Aggregate ``{"1H": ts}`` is also kept for back-compat
    test introspection — it tracks the *latest* per-tf success across all
    venues (max), not the gating predicate (which is per-(tf,venue)).

    Returns aggregate ``{"bars": N, "baseline_samples": M, "skipped_fresh": S}``
    for the tick (``skipped_fresh`` = symbols whose fetch was skipped because the
    current-period bar was already cached — the 429-fix effectiveness signal).
    """
    mono = now_mono if now_mono is not None else time.monotonic()
    total_bars = 0
    total_baseline = 0
    total_skipped = 0
    for timeframe, venues_for_tf in timeframe_to_venues.items():
        cadence = TIMEFRAME_FETCH_CADENCE_SEC.get(timeframe, 5.0)
        # Per-timeframe fetch depth: 1D pulls 260 bars (clears the 253-bar
        # equity_52wk warmup that 240 starved → INERT no-emit); intraday tf keep
        # the caller's limit (240, Alpaca 429 avoidance). max(...) so an explicit
        # caller limit is never shrunk. flow_not_block — additive depth only.
        tf_limit = max(limit, bar_fetch_limit_for(timeframe))
        # Per-(tf, venue) gate: only fetch venues whose individual cadence
        # is due. This is the R2 P1 fix that prevents partial-bucket
        # starvation when one venue in a mixed-venue bucket fails.
        for venue in sorted(venues_for_tf):
            tf_venue_key = f"{timeframe}:{venue}"
            last_v = last_fetch_monotonic_by_tf.get(tf_venue_key)
            if last_v is not None and (mono - last_v) < cadence:
                continue
            # Cold-start phase stagger (fix #2, opt-in via now_epoch): only
            # gates the FIRST-EVER fetch for this key — once fired, the
            # since-last-fetch check above governs every subsequent tick.
            if (
                last_v is None
                and now_epoch is not None
                and not is_timeframe_due_epoch(timeframe, now_epoch)
            ):
                continue
            focus_for_v = [t for t in focus if t[0] == venue]
            if not focus_for_v:
                continue
            result = await ingest_bars_for_focus(
                conn, focus_for_v, capital_session=capital_session,
                alpaca_adapter=alpaca_adapter,
                limit=tf_limit, bar_interval=timeframe,
                skip_if_current=skip_if_current,
                gpt_client_factory=gpt_client_factory,
                db_writer=db_writer,
            )
            total_bars += result["bars"]
            total_baseline += result["baseline_samples"]
            total_skipped += result.get("skipped_fresh", 0)
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
    # Cache-skip visibility (Alpaca 429 fix): one DEBUG line when symbols were
    # skipped this tick, so the runtime log shows the redundant-fetch reduction
    # (e.g. "~99 Alpaca 1m fetches collapsed to the once-per-minute roll").
    if total_skipped > 0:
        logger.debug(
            "[ingest] skip_if_current skipped %d cached current-period symbol(s)",
            total_skipped,
        )
    return {
        "bars": total_bars,
        "baseline_samples": total_baseline,
        "skipped_fresh": total_skipped,
    }


async def ingest_bars_for_focus(
    conn: sqlite3.Connection,
    focus: list[tuple[str, str, str, str]],
    *,
    capital_session: CapitalSession | None = None,
    alpaca_adapter: Any = None,
    limit: int = 240,
    parallel: int = 8,
    bar_interval: str = "1m",
    skip_if_current: set[tuple[str, str]] | None = None,
    gpt_client_factory: Any = None,
    db_writer: DBWriter | None = None,
) -> dict[str, int]:
    """Fetch + persist + baseline-update bars for every focus entry.

    Returns aggregate counts
    ``{"bars": N, "baseline_samples": M, "symbols": K, "skipped_fresh": S}``.
    Concurrency capped at ``parallel`` to keep REST burst polite.

    F10 — Day 9: ``bar_interval`` is forwarded to every adapter call so the
    production loop can ingest a different timeframe per strategy timeframe
    bucket (1m / 15m / 1H). The persisted ``bars`` row keeps its
    ``bar_interval`` column populated by the adapter.

    Incremental cache (Alpaca 429 fix, 2026-06-24):
    - ``skip_if_current``: a set of ``(venue, bar_interval)`` for which a symbol
      is SKIPPED (no network fetch) when its current-period bar is already
      stored. Used for the regime-only Alpaca 1m bucket, which has no strategy
      consumer of intra-minute updates, so re-fetching the in-progress equity 1m
      bar every 5s storms the API for zero canvas benefit. The skip auto-clears
      the instant the period rolls (pure function of the stored ts) and a
      missing/rolled period always fetches — flow_not_block, never a throttle.
      OKX 1m (volume_burst) is NOT in the set, so its intra-minute freshness is
      untouched.
    - For every venue, when prior history exists the last stored bar ts is passed
      as ``since_ts`` so the Alpaca window is incremental (small payload). OKX /
      Capital ignore ``since_ts`` (byte-identical behaviour).
    """
    sem = asyncio.Semaphore(parallel)
    out_bars: list[Bar] = []
    seen: set[str] = set()
    skipped_fresh = 0
    now_s = int(time.time())

    async def _one(target: tuple[str, str, str, str]) -> None:
        nonlocal skipped_fresh
        venue, symbol, asset_class, _group = target
        instrument_id = f"{venue}:{symbol}"
        # #84 equity data-fetch gate (covers EVERY timeframe incl. the 1m regime
        # bucket): when the US-equity cash market is fully closed (overnight /
        # weekend) AND no #66 pre-open warm window is active, SKIP the fetch for
        # this equity — the closed market makes no new bars, so a fetch only storms
        # Alpaca/yfinance. OKX crypto (24/7) + Capital are never gated. flow_not_block:
        # not a trade block (focus list / WS / dashboard unchanged) — only the fetch
        # is skipped, and RTH / the warm window resumes it automatically.
        if not equity_fetch_active(venue, asset_class, symbol, now_s):
            skipped_fresh += 1
            return
        # Cache gate: last stored bar drives both skip-if-fresh and incremental
        # window. One indexed MAX(ts) lookup per symbol (cheap on the bars PK).
        last_ts = last_stored_bar_ts(conn, instrument_id, bar_interval)
        if (
            skip_if_current is not None
            and (venue, bar_interval) in skip_if_current
            and last_ts is not None
            and last_ts >= current_period_open_ts(bar_interval, now_s)
        ):
            # Current-period bar already held → no fetch (flow_not_block: a rolled
            # or missing period still fetches; this only drops the wasteful
            # in-progress re-pull that has no strategy consumer).
            skipped_fresh += 1
            return
        async with sem:
            bars = await fetch_bars_one(
                venue, symbol, asset_class,
                capital_session=capital_session,
                alpaca_adapter=alpaca_adapter, limit=limit,
                bar_interval=bar_interval,
                since_ts=last_ts,
                gpt_client_factory=gpt_client_factory,
            )
        if bars:
            out_bars.extend(bars)
            seen.add(f"{venue}:{symbol}")

    await asyncio.gather(*(_one(t) for t in focus))
    if not out_bars:
        return {
            "bars": 0, "baseline_samples": 0, "symbols": 0,
            "skipped_fresh": skipped_fresh,
        }
    # STALL residual fix (#90): the per-tick persist + baseline-append DB write
    # is the dominant on-loop blocker (29k-60k row executes bracketing the live
    # tick-engine STALL gap). Offload the WHOLE write to a worker thread on a
    # DEDICATED conn opened from the loop conn's db path (the #74/#88 pattern).
    # An in-memory loop conn (tests) has no path → keep the write on the loop
    # (``db_path is None``), behaviour-identical.
    db_path = _db_path_from_conn(conn)
    # Codex F10 R1 P1-2 fix: baselines (ATR/size/volume) are minute-grained;
    # routing 5m/15m/1H bars into ``update_baseline_from_bars`` would
    # contaminate the minute-windowed state. Use the full ``ingest_bars``
    # pipeline only for 1m batches; for higher timeframes, persist the bars
    # without recomputing the baseline.
    if bar_interval != "1m":
        if db_path is not None:
            total_persisted = await persist_bars_offloaded(
                db_path, out_bars, db_writer=db_writer
            )
        else:
            total_persisted = persist_bars(conn, out_bars)
        # Higher-tf ingest visibility (INFO): the 1m path logs ``[ingest]`` from
        # core.data.ingest, but the 15m/1H/1D/5m batches persist silently here —
        # so a trade on a 1H bar strategy could not be traced to its bar ingest.
        # One line per (timeframe, venue) fetch-batch; the caller's per-(tf,venue)
        # cadence gate already throttles fetches, so this never spams per tick.
        # Log only — never gates ingest.
        if out_bars:
            venues = {b.venue for b in out_bars}
            logger.info(
                "[ingest] bars=%d symbols=%d timeframe=%s venues=%s",
                total_persisted, len(seen), bar_interval, ",".join(sorted(venues)),
            )
        return {
            "bars": total_persisted,
            "baseline_samples": 0,
            "symbols": len(seen),
            "skipped_fresh": skipped_fresh,
        }
    # 1m path — group by asset_class so update_baseline_from_bars partitions
    # the per-class baseline window correctly.
    by_class: dict[str, list[Bar]] = {}
    for b in out_bars:
        # P2.1 fix (2026-06-22): partition by the bar's REAL asset_class — the
        # canonical underlying_group_id prefix (crypto/forex/index/commodity/
        # equity) — instead of the OKX-vs-else venue binary that lumped Capital
        # commodity/index + Alpaca equity into one "forex" baseline bucket.
        # Hardening #5 (2026-06-23): the prefix is now VALIDATED against the known
        # set; a missing/garbage prefix is a LOUD, venue-correct, COUNTED fallback
        # (was a silent venue binary). A well-formed group_id stays byte-identical.
        ac = resolve_bar_asset_class(
            venue=b.venue, symbol=b.symbol,
            group_id=b.underlying_group_id, conn=conn,
        )
        by_class.setdefault(ac, []).append(b)
    total_persisted = 0
    total_baseline = 0
    for ac, group in by_class.items():
        # STALL residual fix (#90): persist + sample-append + baseline recompute
        # ALL run off the loop on a dedicated conn (``ingest_bars_offloaded``) so
        # the per-1m-tick write never holds the event loop. Awaited sequentially
        # per asset_class → only one ingest worker touches the dedicated conn at
        # a time (WAL single-writer safe). Identical persisted rows/baselines to
        # the prior ``ingest_bars_async``; only WHERE the write runs changed. The
        # in-mem (tests, ``db_path is None``) fallback keeps the on-loop async
        # path (still offloads the heavy sort, byte-identical output).
        if db_path is not None:
            result = await ingest_bars_offloaded(
                db_path, group, asset_class=ac, db_writer=db_writer
            )
        else:
            result = await ingest_bars_async(conn, group, asset_class=ac)
        total_persisted += result["bars"]
        total_baseline += result["baseline_samples"]
    return {
        "bars": total_persisted,
        "baseline_samples": total_baseline,
        "symbols": len(seen),
        "skipped_fresh": skipped_fresh,
    }


def read_recent_bars(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    bar_interval: str = "1m",
    limit: int = 240,
    freshness_threshold_sec: float | None = None,
) -> list[Bar]:
    """Read the most recent ``limit`` bars from SQLite (newest last).

    FUTURE-dated bars (``ts > now + BAR_TS_CLOCK_SKEW_SLACK_SEC``) are excluded so
    a stale +10h Capital bar is never returned as the most-recent canvas.

    Recency guard (Jin 2026-06-22): when ``freshness_threshold_sec`` is given and
    the NEWEST returned bar is older than ``now - freshness_threshold_sec``, the
    feed is dead/stale — return ``[]`` so the caller skips the symbol rather than
    deciding on a provably stale price. DATA-HEALTH gate, not a throttle: it is a
    pure function of the stored ts, so it auto-clears the instant a fresh bar
    lands. ``None`` (default) keeps the legacy behaviour for callers that have
    not opted into the guard.
    """
    instrument_id = f"{venue}:{symbol}"
    now = int(time.time())
    ts_upper = now + BAR_TS_CLOCK_SKEW_SLACK_SEC
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
    # Recency guard — refuse a provably stale canvas (dead-feed data-health gate).
    if bars and freshness_threshold_sec is not None:
        newest_ts = bars[-1].ts
        key = (instrument_id, bar_interval)
        if newest_ts < now - freshness_threshold_sec:
            age_h = (now - newest_ts) / 3600.0
            # Dedup: ONE runtime WARNING on the fresh→stale transition (operator
            # health signal); the per-read detail goes to the datastream sink.
            if key not in _RECENCY_STALE_FLAGGED:
                _RECENCY_STALE_FLAGGED.add(key)
                logger.warning(
                    "[recency] %s %s newest bar %.1fh old (> %.1fh threshold) — "
                    "feed stale, skipping symbol (no dead-price decision; "
                    "auto-clears on fresh data)",
                    instrument_id, bar_interval, age_h,
                    freshness_threshold_sec / 3600.0,
                )
            datastream_emit(
                "bars/recency-stale",
                instrument_id=instrument_id,
                bar_interval=bar_interval,
                age_h=round(age_h, 1),
                threshold_h=round(freshness_threshold_sec / 3600.0, 1),
            )
            return []
        # Fresh read — clear any prior stale flag so the next dead-feed event
        # re-logs a transition WARNING (logging-only state reset).
        _RECENCY_STALE_FLAGGED.discard(key)
    return bars


# On-demand live-refetch cooldown (Jin 2026-06-27 "데이터 proactive"). When the
# recency guard returns [] (stale/empty store) the trading path used to SKIP the
# symbol. Instead we now REFETCH live — but a per-(venue, symbol, interval)
# cooldown spaces those refetches so a stale-symbol storm cannot hammer the
# venues (the live OKX 660× "Too Many"/429 hazard). Default 60s; env-tunable via
# ``POLARIS_ONDEMAND_FETCH_COOLDOWN_SEC`` (no-hardcode). Module-level dict =
# single-process loop, mirrors ``should_fetch_exchange_fallback``. Cleared
# per-test in conftest. NEVER gates a decision: a never-fetched symbol always
# fetches immediately; a cooldown'd one degrades to the same skip as before.
def _ondemand_cooldown_sec() -> float:
    raw = os.environ.get("POLARIS_ONDEMAND_FETCH_COOLDOWN_SEC")
    if raw is None or raw.strip() == "":
        return 60.0
    try:
        val = float(raw.strip())
    except ValueError:
        return 60.0
    return val if val > 0 else 60.0


ONDEMAND_FETCH_COOLDOWN_SEC: float = _ondemand_cooldown_sec()
_ONDEMAND_LAST_MONO: dict[tuple[str, str, str], float] = {}


def should_fetch_ondemand(
    venue: str, symbol: str, bar_interval: str, now_mono: float
) -> bool:
    """True if an on-demand live refetch may run for this key now.

    True (and records the time) when never-fetched or the cooldown elapsed;
    False inside the window. Per-(venue, symbol, interval) so a 1m refetch and a
    1H refetch for the same symbol are independent. STORM GUARD only (OKX 429
    protection) — never gates a trading decision (flow_not_block)."""
    key = (venue, symbol, bar_interval)
    last = _ONDEMAND_LAST_MONO.get(key)
    if last is None or (now_mono - last) >= ONDEMAND_FETCH_COOLDOWN_SEC:
        _ONDEMAND_LAST_MONO[key] = now_mono
        return True
    return False


async def read_recent_bars_ondemand(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    asset_class: str,
    bar_interval: str = "1m",
    limit: int = 240,
    freshness_threshold_sec: float | None = None,
    capital_session: CapitalSession | None = None,
    alpaca_adapter: Any = None,
    gpt_client_factory: Any = None,
    now_mono: float | None = None,
    fetch_fn: Callable[..., Awaitable[list[Bar]]] | None = None,
) -> list[Bar]:
    """``read_recent_bars`` that, on a STALE read, FETCHES live instead of skipping.

    "데이터 없으면 가져온다" — the data-proactive default. Steps:
      1. Read with the recency guard. FRESH → return as-is (byte-identical to
         ``read_recent_bars``; NO fetch, no cost).
      2. STALE/empty → if the per-(venue, symbol, interval) cooldown allows,
         refetch live via ``fetch_bars_one`` (Yahoo PRIMARY; its own exchange-
         fallback cooldown + within-period frame cache further throttle OKX), then
         ``persist_bars`` the fresh candles and re-read.
      3. Refetch empty / cooldown'd / raised → return [] (degrade-never-crash;
         the SAME skip as before — no new block, flow_not_block).

    Storm/OKX-429 guard: the cooldown (``POLARIS_ONDEMAND_FETCH_COOLDOWN_SEC``,
    default 60s) + dedup. ``fetch_fn`` is injectable for tests (defaults to
    ``fetch_bars_one``). DATA layer only — no entry/size/exit touched.
    """
    bars = read_recent_bars(
        conn, venue=venue, symbol=symbol, bar_interval=bar_interval,
        limit=limit, freshness_threshold_sec=freshness_threshold_sec,
    )
    if bars:
        return bars
    # Stale or empty store → go get fresh data (cooldown-gated).
    mono = now_mono if now_mono is not None else time.monotonic()
    if not should_fetch_ondemand(venue, symbol, bar_interval, mono):
        return []  # within cooldown → degrade to the prior skip (storm guard)
    fetch = fetch_fn if fetch_fn is not None else fetch_bars_one
    last_ts = last_stored_bar_ts(conn, f"{venue}:{symbol}", bar_interval)
    try:
        fresh = await fetch(
            venue, symbol, asset_class,
            capital_session=capital_session,
            alpaca_adapter=alpaca_adapter,
            limit=limit, bar_interval=bar_interval,
            since_ts=last_ts,
            gpt_client_factory=gpt_client_factory,
        )
    except Exception as exc:  # noqa: BLE001 — one symbol's fetch never crashes the tick
        logger.debug(
            "[ondemand] %s:%s/%s refetch failed: %r",
            venue, symbol, bar_interval, exc,
        )
        return []
    if not fresh:
        return []  # nothing live either → graceful skip (data genuinely absent)
    # persist_bars already drops any non-finite OHLC row (batch-survivable) and is
    # idempotent (INSERT OR REPLACE). ``commit`` flushes the write if any caller
    # left an explicit transaction open; the tick body itself spans none here, so
    # the subsequent read sees the just-written bars regardless.
    persist_bars(conn, fresh)
    conn.commit()
    return read_recent_bars(
        conn, venue=venue, symbol=symbol, bar_interval=bar_interval,
        limit=limit, freshness_threshold_sec=freshness_threshold_sec,
    )


