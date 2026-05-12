"""Day 8 production paper loop — Layer 0/1/6 wiring helpers.

Splits the per-layer plumbing out of ``production_paper_loop.py`` so the main
file stays under the 500-line budget. Each function here is invocable on its
own (smoke + tests cover them in isolation).

Layers covered
--------------
* **Layer 0** — Dynamic universe producer. Refreshes the OKX SPOT universe
  every 5 min and Capital CFD universe every 10 min (with chart-endpoint
  proxy compute). Persists to ``universe`` + ``watchlist_focus``.
* **Layer 1** — Per-tick bar ingest. Fetches 1m bars for the active focus
  list, persists to ``bars`` + ``ticker_baseline_*``.
* **Layer 6** — Per-tick recalc cycle. Marks dirty positions, runs
  ``regime_flip.detect_regime_flip`` per (venue, group), evaluates strategy
  swap candidates against the Layer 6 SSOT.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import Sequence

import httpx

from polaris.core.data.ingest import ingest_bars, persist_bars
from polaris.core.data.schema import Bar
from polaris.core.live_recalc.regime_flip import detect_regime_flip
from polaris.core.live_recalc.tick_recalc import (
    mark_position_dirty,
    run_live_recalc_cycle,
)
from polaris.core.universe.discovery import (
    apply_active_filters,
    fetch_capital_instruments,
    fetch_okx_instruments,
    persist_universe,
)
from polaris.core.universe.schema import UniverseInstrument
from polaris.core.universe.watchlist import compute_dynamic_focus, persist_focus
from polaris.scripts._production_indicators import compute_real_regime
from polaris.venues.capital.adapter import fetch_capital_bars
from polaris.venues.capital.market_proxy import populate_capital_proxies
from polaris.venues.capital.session import CapitalSession
from polaris.venues.okx.adapter import fetch_okx_bars

logger = logging.getLogger(__name__)

OKX_REFRESH_SEC = 300
CAPITAL_REFRESH_SEC = 600

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


# ---------------------------------------------------------------------------
# Layer 0 — universe producer
# ---------------------------------------------------------------------------


async def refresh_okx_universe_once(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> int:
    """Fetch OKX tickers → 4-axis filter → persist. Returns active count."""
    ts = now_ts if now_ts is not None else int(time.time())
    try:
        instruments = await fetch_okx_instruments(now_ts=ts)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0] OKX fetch failed: %r", exc)
        return 0
    active = apply_active_filters(instruments)
    active_ids = {ins.instrument_id for ins in active}
    persist_universe(conn, instruments, is_active_set=active_ids)
    logger.info("[L0/okx] universe %d → active %d", len(instruments), len(active))
    return len(active)


async def refresh_capital_universe_once(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> int:
    """Fetch Capital CFD nav → proxy compute → 4-axis → persist."""
    ts = now_ts if now_ts is not None else int(time.time())
    api_key = os.environ.get("CAP_API_KEY")
    email = os.environ.get("CAP_EMAIL")
    password = os.environ.get("CAP_PASSWORD")
    if not (api_key and email and password):
        logger.info("[L0/capital] credentials missing — skipping refresh")
        return 0
    try:
        instruments = await fetch_capital_instruments(now_ts=ts)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0] Capital fetch failed: %r", exc)
        return 0
    if not instruments:
        return 0
    # Populate proxies — best-effort (per-row failure leaves zeros so the
    # 4-axis filter rejects them cleanly).
    try:
        async with CapitalSession(
            api_key=api_key, identifier=email, password=password, auto_ping=False
        ) as session:
            tokens = await session.ensure_tokens()
            instruments = await populate_capital_proxies(
                instruments,
                cst=tokens.cst,
                security_token=tokens.security_token,
            )
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0/capital] proxy fetch failed: %r", exc)

    active = apply_active_filters(instruments)
    active_ids = {ins.instrument_id for ins in active}
    persist_universe(conn, instruments, is_active_set=active_ids)
    logger.info(
        "[L0/capital] universe %d → active %d (proxy-filtered)",
        len(instruments),
        len(active),
    )
    return len(active)


def read_active_universe(conn: sqlite3.Connection) -> list[UniverseInstrument]:
    """Read all is_active=1 rows from ``universe``."""
    rows = conn.execute(
        """
        SELECT venue, symbol, instrument_id, underlying_group_id, asset_class,
               quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct,
               depth_10bps_usd, signal_density_7d, listing_ts, last_seen_ts
        FROM universe
        WHERE is_active = 1
        """
    ).fetchall()
    return [
        UniverseInstrument(
            venue=str(r[0]),
            symbol=str(r[1]),
            instrument_id=str(r[2]),
            underlying_group_id=str(r[3]),
            asset_class=str(r[4]),
            quote_ccy=str(r[5]),
            state=str(r[6]),
            vol_24h_usd=float(r[7] or 0.0),
            spread_bps=float(r[8] or 0.0),
            atr_24h_pct=float(r[9] or 0.0),
            depth_10bps_usd=float(r[10] or 0.0),
            signal_density_7d=float(r[11] or 0.0),
            listing_ts=int(r[12]) if r[12] is not None else None,
            last_seen_ts=int(r[13] or 0),
        )
        for r in rows
    ]


def refresh_focus_watchlist(
    conn: sqlite3.Connection, *, cycle_ts: int | None = None
) -> int:
    """Compute dynamic focus over active universe + persist; return count."""
    ts = cycle_ts if cycle_ts is not None else int(time.time())
    universe = read_active_universe(conn)
    if not universe:
        return 0
    focus = compute_dynamic_focus(universe, cycle_ts=ts)
    persist_focus(conn, focus)
    logger.info("[L0/focus] universe=%d → focus=%d", len(universe), len(focus))
    return len(focus)


def get_focus_targets(
    conn: sqlite3.Connection, *, cycle_ts: int | None = None, max_n: int = 30
) -> list[tuple[str, str, str, str]]:
    """Read the latest focus cycle as ``(venue, symbol, asset_class, group_id)``.

    Returns up to ``max_n`` entries ordered by focus_rank ascending. Empty list
    if no cycle has been computed yet (caller falls back to BTC seed).
    """
    ts = cycle_ts if cycle_ts is not None else int(time.time())
    row = conn.execute(
        "SELECT MAX(cycle_ts) FROM watchlist_focus WHERE cycle_ts <= ?", (ts,)
    ).fetchone()
    if row is None or row[0] is None:
        return []
    latest_cycle = int(row[0])
    rows = conn.execute(
        """
        SELECT wf.venue, wf.symbol, u.asset_class, u.underlying_group_id
        FROM watchlist_focus wf
        LEFT JOIN universe u
          ON wf.venue = u.venue AND wf.symbol = u.symbol
        WHERE wf.cycle_ts = ?
        ORDER BY wf.focus_rank ASC
        LIMIT ?
        """,
        (latest_cycle, int(max_n)),
    ).fetchall()
    return [
        (str(r[0]), str(r[1]), str(r[2] or "crypto"), str(r[3] or ""))
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Layer 1 — bar ingest
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Layer 6 — recalc + regime flip
# ---------------------------------------------------------------------------


def compute_and_flip_regime(
    conn: sqlite3.Connection,
    *,
    venue: str,
    underlying_group_id: str,
    bars: Sequence[Bar],
    now_ts: int,
) -> str:
    """Compute candidate regime + run Layer 6 SSOT 2-consecutive-close gate.

    Returns the regime SSOT *after* applying the flip rule so callers using
    the Layer 6 SSOT receive the gated value (matches strategy_swap's
    ``_lookup_regime`` semantics).
    """
    candidate = compute_real_regime(bars)
    decision = detect_regime_flip(
        conn,
        venue=venue,
        underlying_group_id=underlying_group_id,
        candidate=candidate,
        now_ts=now_ts,
    )
    # Either the flip was confirmed (decision.to_regime is the new SSOT) or
    # the row stayed at the prior regime. Read back the persisted SSOT so the
    # caller sees what every other Layer 6 consumer sees.
    row = conn.execute(
        "SELECT regime FROM regime_state "
        "WHERE venue = ? AND underlying_group_id = ?",
        (venue, underlying_group_id),
    ).fetchone()
    if row is None:
        return candidate
    persisted = str(row[0])
    if decision.confirmed:
        logger.info(
            "[L6/regime] flip %s/%s → %s (%s)",
            venue, underlying_group_id, persisted, decision.reason,
        )
    return persisted


async def run_recalc_for_active_positions(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
) -> int:
    """Sweep active positions through Layer 6 dirty-mark recalc cycle.

    Returns count of positions evaluated. Marks each open position dirty
    (per-tick price proxy) so the cycle has something to evaluate.
    """
    rows = conn.execute(
        "SELECT position_id FROM positions WHERE status NOT IN ('closed', 'cancelled')"
    ).fetchall()
    for r in rows:
        mark_position_dirty(
            conn, position_id=str(r[0]), reason="tick_5s", now_ts=now_ts
        )
    if not rows:
        return 0
    active = [{"position_id": str(r[0])} for r in rows]
    await run_live_recalc_cycle(conn, now_ts=now_ts, active_positions=active)
    return len(rows)
