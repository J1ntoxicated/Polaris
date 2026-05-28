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

import logging
import os
import sqlite3
import time
from collections.abc import Sequence

import httpx

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
from polaris.scripts._production_bars import (
    CAPITAL_RESOLUTION_BY_INTERVAL,
    TIMEFRAME_FETCH_CADENCE_SEC,
    fetch_bars_one,
    ingest_bars_for_focus,
    ingest_bars_per_timeframe,
    read_recent_bars,
)
from polaris.scripts._production_indicators import compute_real_regime
from polaris.venues.capital.market_proxy import populate_capital_proxies
from polaris.venues.capital.session import CapitalSession

logger = logging.getLogger(__name__)

# Layer 1 bar-ingest helpers + timeframe constants now live in ``_production_bars``;
# re-exported here so existing ``_production_layers`` import paths keep working.
__all__ = [
    "CAPITAL_REFRESH_SEC",
    "CAPITAL_RESOLUTION_BY_INTERVAL",
    "OKX_REFRESH_SEC",
    "TIMEFRAME_FETCH_CADENCE_SEC",
    "compute_and_flip_regime",
    "fetch_bars_one",
    "get_focus_targets",
    "ingest_bars_for_focus",
    "ingest_bars_per_timeframe",
    "read_active_universe",
    "read_recent_bars",
    "refresh_capital_universe_once",
    "refresh_focus_watchlist",
    "refresh_okx_universe_once",
    "run_recalc_for_active_positions",
]

OKX_REFRESH_SEC = 300
CAPITAL_REFRESH_SEC = 600


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
