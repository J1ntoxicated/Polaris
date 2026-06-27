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
from dataclasses import replace
from typing import Any

import httpx

from polaris.core.altdata.fuser import fuse_evidence
from polaris.core.data.schema import Bar
from polaris.core.isolation.blocklist import load_blocklist
from polaris.core.live_recalc.regime_flip import detect_regime_flip, fetch_regime
from polaris.core.live_recalc.tick_recalc import (
    mark_position_dirty,
    run_live_recalc_cycle,
)
from polaris.core.probes.entrance import EntranceJudge
from polaris.core.probes.entrance_leans import (
    build_altdata_lean,
    build_regime_lean,
    build_technical_lean,
)
from polaris.core.probes.tuning_log import log_entrance_judgments
from polaris.core.universe.discovery import (
    deactivate_stale_active_rows,
    fetch_alpaca_instruments,
    fetch_capital_instruments,
    fetch_okx_instruments,
    merge_listing_timestamps,
    persist_universe,
    rank_active_universe,
)
from polaris.core.universe.schema import (
    FocusSelection,
    Tier,
    UniverseInstrument,
    is_capital_fx_major,
    tier_cadence,
)
from polaris.core.universe.watchlist import (
    cadence_fires,
    compute_dynamic_focus,
    persist_focus,
)
from polaris.scripts._candidate_sweep import (
    RTH_CLOSE_MIN,
    RTH_OPEN_MIN,
    SESSION_DOWNWEIGHT,
    SESSION_LEAD_SEC,
)
from polaris.scripts._production_asset_class import resolve_asset_class
from polaris.scripts._production_bars import (
    CAPITAL_RESOLUTION_BY_INTERVAL,
    TIMEFRAME_FETCH_CADENCE_SEC,
    bar_fetch_limit_for,
    fetch_bars_one,
    ingest_bars_for_focus,
    ingest_bars_per_timeframe,
    read_recent_bars,
    read_recent_bars_ondemand,
    staleness_threshold_for,
)
from polaris.scripts._production_indicators import compute_real_regime_signal
from polaris.scripts._session_map import session_group, session_transitions
from polaris.venues.capital.market_proxy import populate_capital_proxies
from polaris.venues.capital.session import CapitalSession
from polaris.venues.capital.session_calendar import capital_seconds_to_close

logger = logging.getLogger(__name__)

# Session-edge log dedup: the monotonic-ish UTC epoch of the last focus cycle
# that ran the ``[session]`` OPEN/CLOSE edge check. The edge detector fires only
# when a regional cash boundary falls in ``(prev, now]``, so a per-cycle call
# emits a ``[session]`` line ONLY on the actual open/close transition (never
# per-tick). ``None`` until the first cycle (which seeds the baseline, no edge).
_LAST_SESSION_CYCLE_TS: int | None = None


def _emit_session_edges(universe: Sequence[Any], now_ts: int) -> None:
    """Log ``[session] {region} OPEN/CLOSE symbols=N`` for any boundary just crossed.

    Operational narrative (INFO) — the global session clock turning over. Pure
    log: never gates trading (the TRADE weight is ``instrument_session_weight``,
    untouched). Edge-trigger only via ``session_transitions`` (no per-tick spam).
    ``symbols`` counts the watched universe rows mapped to that region's session
    group. Fail-open: any error here is swallowed (logging must never break the
    focus cycle).
    """
    global _LAST_SESSION_CYCLE_TS
    prev = _LAST_SESSION_CYCLE_TS
    _LAST_SESSION_CYCLE_TS = now_ts
    if prev is None:
        return  # first cycle seeds the baseline; no edge to report yet
    try:
        edges = session_transitions(prev, now_ts)
        if not edges:
            return
        region_counts: dict[str, int] = {}
        for ins in universe:
            grp = session_group(ins.symbol)
            if grp is not None:
                region_counts[grp] = region_counts.get(grp, 0) + 1
        for region, kind in edges:
            logger.info(
                "[session] %s %s symbols=%d",
                region, kind, region_counts.get(region, 0),
            )
    except Exception:  # noqa: BLE001 — a log helper must never break the cycle
        logger.debug("[session] edge-log skipped (non-fatal)")

# Layer 1 bar-ingest helpers + timeframe constants now live in ``_production_bars``;
# re-exported here so existing ``_production_layers`` import paths keep working.
__all__ = [
    "ALPACA_REFRESH_SEC",
    "CAPITAL_REFRESH_SEC",
    "CAPITAL_RESOLUTION_BY_INTERVAL",
    "OKX_REFRESH_SEC",
    "TIMEFRAME_FETCH_CADENCE_SEC",
    "bar_fetch_limit_for",
    "capital_active_ids_after_collapse_guard",
    "compose_regime_candidate",
    "compute_and_flip_regime",
    "fetch_bars_one",
    "fx_major_focus_targets",
    "get_focus_targets",
    "ingest_bars_for_focus",
    "ingest_bars_per_timeframe",
    "compute_signal_density_7d",
    "open_position_targets",
    "read_active_universe",
    "read_cell_scores_by_instrument",
    "read_recent_bars",
    "read_recent_bars_ondemand",
    "refresh_alpaca_universe_once",
    "refresh_capital_universe_once",
    "refresh_focus_watchlist",
    "refresh_okx_universe_once",
    "run_recalc_for_active_positions",
    "staleness_threshold_for",
]

OKX_REFRESH_SEC = 300
CAPITAL_REFRESH_SEC = 600
ALPACA_REFRESH_SEC = 600

# C1 — Capital active-universe collapse guard (forensic 2026-05-31): a healthy
# Capital fetch but a session-driven validity collapse (only 1 epic TRADEABLE on
# the weekend) left the active book at exactly 1 closed symbol (EURUSD_W) for 27h
# — FX is 24/5, so breadth must be preserved across the closure, not handed to a
# single survivor. When a refresh's active set collapses to ``<= FLOOR`` while the
# PRIOR book was healthy (``>= PRIOR_MIN``), KEEP the prior active set
# (flow_not_block: preserve breadth, never zero-out). A genuine FULL closure
# (active=0) stays the existing session_wait path — the guard does not resurrect
# it; those rows revive automatically next refresh once TRADEABLE.
CAPITAL_COLLAPSE_ACTIVE_FLOOR = 1
CAPITAL_COLLAPSE_PRIOR_MIN = 5


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
    active = rank_active_universe(instruments)
    active_ids = {ins.instrument_id for ins in active}
    persist_universe(conn, instruments, is_active_set=active_ids)
    logger.info("[L0/okx] universe %d → active %d", len(instruments), len(active))
    return len(active)


def _read_capital_active_ids(conn: sqlite3.Connection) -> set[str]:
    """Prior Capital active (``is_active=1``) instrument_ids (collapse-guard read)."""
    try:
        return {
            str(r[0])
            for r in conn.execute(
                "SELECT instrument_id FROM universe "
                "WHERE venue = 'capital' AND is_active = 1"
            ).fetchall()
        }
    except sqlite3.Error:
        return set()


def capital_active_ids_after_collapse_guard(
    *,
    new_active_ids: set[str],
    prior_active_ids: set[str],
    fetched_count: int,
) -> set[str]:
    """Preserve prior Capital breadth when a refresh's active set abnormally collapses.

    Returns the active instrument_id set to persist. The new set is used as-is
    UNLESS it has collapsed to ``<= CAPITAL_COLLAPSE_ACTIVE_FLOOR`` while the prior
    book was healthy (``>= CAPITAL_COLLAPSE_PRIOR_MIN``) and the fetch itself was
    non-empty — the forensic 1-symbol weekend collapse. In that case the PRIOR
    active set is kept (flow_not_block: breadth preserved across the session
    closure, never zeroed out to a single stale survivor).

    A genuine full closure (``new_active_ids`` empty) is the legitimate
    session_wait path and is returned unchanged (the guard never resurrects a
    zeroed book; those rows revive next refresh once TRADEABLE). Cold start (no
    prior book) is also a no-op.
    """
    if fetched_count <= 0:
        return new_active_ids
    if not new_active_ids:
        return new_active_ids  # full closure → legitimate session_wait, not a collapse
    if (
        len(new_active_ids) <= CAPITAL_COLLAPSE_ACTIVE_FLOOR
        and len(prior_active_ids) >= CAPITAL_COLLAPSE_PRIOR_MIN
    ):
        return prior_active_ids
    return new_active_ids


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

    active = rank_active_universe(instruments)
    new_active_ids = {ins.instrument_id for ins in active}
    # C1 collapse guard: preserve prior breadth on a session-driven 1-symbol
    # collapse (read prior BEFORE the upsert overwrites it).
    prior_active_ids = _read_capital_active_ids(conn)
    active_ids = capital_active_ids_after_collapse_guard(
        new_active_ids=new_active_ids,
        prior_active_ids=prior_active_ids,
        fetched_count=len(instruments),
    )
    guarded = active_ids != new_active_ids
    persist_universe(conn, instruments, is_active_set=active_ids)
    if guarded:
        logger.info(
            "[L0/capital] collapse guard: rank→active %d (prior %d) — KEEPING prior "
            "breadth %d (session-driven 1-symbol collapse, flow_not_block)",
            len(new_active_ids), len(prior_active_ids), len(active_ids),
        )
    logger.info(
        "[L0/capital] universe %d → active %d (continuous-rank)",
        len(instruments),
        len(active_ids),
    )
    return len(active_ids)


async def refresh_alpaca_universe_once(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> int:
    """Fetch Alpaca US-equity assets → rank → persist. Returns active count.

    Track C (additive). Coarse liquidity proxies are set at fetch time
    (``_alpaca``); a per-row proxy refine step is deferred to the dashboards /
    learners, so this path stays minimal (fetch → rank → persist), mirroring
    the OKX producer. Returns 0 when credentials are missing (smoke-safe).
    """
    ts = now_ts if now_ts is not None else int(time.time())
    try:
        instruments = await fetch_alpaca_instruments(now_ts=ts)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("[L0] Alpaca fetch failed: %r", exc)
        return 0
    if not instruments:
        return 0
    # B2: carry each name's FIRST-seen listing_ts across cycles so the <24h
    # new-listing watchdog has a real first-seen ts (was NULL for every row →
    # watchdog never fired). The raw fetch stamps listing_ts=None; merge with the
    # prior persisted timestamps and stamp `ts` on genuinely new names.
    prev = _read_alpaca_listing_prev(conn)
    instruments = merge_listing_timestamps(prev, instruments, now_ts=ts)
    active = rank_active_universe(instruments)
    active_ids = {ins.instrument_id for ins in active}
    persist_universe(conn, instruments, is_active_set=active_ids)
    # B2: deactivate the prior-active names that dropped OUT of this fetch (the
    # 21-day ghost). persist_universe only UPDATEs fetched rows, so a name absent
    # from Alpaca's churning ~13k /v2/assets set lingers is_active=1 forever — a
    # universe-membership hygiene correction (a name that LEFT the venue is no
    # longer eligible), not a defensive throttle (flow_not_block).
    swept = deactivate_stale_active_rows(
        conn, venue="alpaca", fetched_ids={ins.instrument_id for ins in instruments}
    )
    logger.info(
        "[L0/alpaca] universe %d → active %d (continuous-rank, stale_swept=%d)",
        len(instruments),
        len(active),
        swept,
    )
    return len(active)


def _read_alpaca_listing_prev(conn: sqlite3.Connection) -> list[UniverseInstrument]:
    """Prior (instrument_id, listing_ts) for Alpaca as merge_listing_timestamps input.

    Only ``instrument_id`` + ``listing_ts`` are consulted by the merge, so this
    builds minimal carrier rows (other fields are placeholders, never persisted —
    the merge returns the CURRENT fetch rows with timestamps carried over). Any
    sqlite error degrades to ``[]`` (every current row is then treated as new and
    stamped ``now`` — flow_not_block, never breaks the refresh).
    """
    try:
        rows = conn.execute(
            "SELECT instrument_id, listing_ts FROM universe WHERE venue = 'alpaca'"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        UniverseInstrument(
            venue="alpaca",
            symbol="",
            instrument_id=str(r[0]),
            underlying_group_id="",
            asset_class="equity",
            quote_ccy="USD",
            state="live",
            vol_24h_usd=0.0,
            spread_bps=0.0,
            atr_24h_pct=0.0,
            depth_10bps_usd=0.0,
            listing_ts=int(r[1]) if r[1] is not None else None,
        )
        for r in rows
    ]


def read_active_universe(conn: sqlite3.Connection) -> list[UniverseInstrument]:
    """Read all is_active=1 rows from ``universe``."""
    rows = conn.execute(
        """
        SELECT venue, symbol, instrument_id, underlying_group_id, asset_class,
               quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct,
               depth_10bps_usd, signal_density_7d, listing_ts, last_seen_ts,
               last_price
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
            last_price=float(r[14] or 0.0),
        )
        for r in rows
    ]


SIGNAL_DENSITY_WINDOW_SEC = 7 * 86_400


def compute_signal_density_7d(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> dict[str, float]:
    """Per-``instrument_id`` count of EMITTED signals in the trailing 7d window.

    Producer for the ``signal_density_7d`` rank axis (RANK_WEIGHT_SIGNAL_DENSITY_Z
    = 0.25, the 2nd-largest focus weight) which had NO producer and sat 0.0 across
    every active row — ~25% of the designed ranking signal was permanently inert.
    Counts rows in the ``signals`` table (``instrument_id`` = ``venue:symbol``,
    written by ``persist_emitted_signal``) with ``ts`` inside the window. This is
    pure focus-RANKING enrichment (flow_not_block): a denser-signal name ranks
    higher, nothing is blocked, sized, or vetoed. Read-only; empty/missing →
    ``{}`` (the existing 0.0 default then applies, so focus never breaks).
    """
    ts = now_ts if now_ts is not None else int(time.time())
    cutoff = ts - SIGNAL_DENSITY_WINDOW_SEC
    try:
        rows = conn.execute(
            "SELECT instrument_id, COUNT(*) FROM signals "
            "WHERE ts >= ? GROUP BY instrument_id",
            (cutoff,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(r[0]): float(r[1]) for r in rows}


def read_cell_scores_by_instrument(conn: sqlite3.Connection) -> dict[str, float]:
    """Aggregate ``cell_matrix_p0`` into one score per ``instrument_id`` (venue:ticker).

    Producer for the ``cell_scores`` rank axis (RANK_WEIGHT_CELL_Z = 0.10) that the
    production focus call never populated — the cell-routing learner held live rows
    but had zero influence on WHICH symbols got focus. ``cell_matrix_p0`` is keyed
    ``(exchange, strategy, ticker, regime)``; this collapses the strategy/regime
    fan-out to a single per-(exchange, ticker) **n_eff-weighted mean score** (the
    same conviction-weighted view the routing layer uses) and keys it as
    ``f"{exchange}:{ticker}"`` to match ``UniverseInstrument.instrument_id``. Cells
    with ``n_eff <= 0`` are skipped (no evidence yet). Read-only, flow_not_block:
    a better-performing cell lifts its symbol's focus rank, never blocks. Empty →
    ``{}`` (cell axis stays 0.0, focus unaffected).
    """
    try:
        rows = conn.execute(
            "SELECT exchange, ticker, n_eff, score FROM cell_matrix_p0 WHERE n_eff > 0"
        ).fetchall()
    except sqlite3.Error:
        return {}
    acc: dict[str, list[float]] = {}
    for exchange, ticker, n_eff, score in rows:
        key = f"{exchange}:{ticker}"
        bucket = acc.setdefault(key, [0.0, 0.0])  # [sum(n*score), sum(n)]
        bucket[0] += float(n_eff) * float(score)
        bucket[1] += float(n_eff)
    return {k: (s / n if n > 0 else 0.0) for k, (s, n) in acc.items()}


def _fused_scores(
    altdata_cache: Any, underlying_group_id: str, now_ts: int
) -> dict[str, float] | None:
    """Adapt ``fuse_evidence`` → the per-label score dict the alt-data lens reads.

    Calls the SAME fuser the Layer-6 regime path uses (read-only, no DB write) and
    returns ``ev["scores"]`` (``{bull_trend, bear_trend, chop, crisis}``) or
    ``None`` when there is no fresh evidence (keyless / failing collector → the
    lens degrades to neutral). Fail-soft: any fuser error → None (never blocks the
    focus refresh — flow_not_block).
    """
    try:
        _hint, _conf, ev = fuse_evidence(
            underlying_group_id, altdata_cache, now_ts=now_ts
        )
    except Exception:  # noqa: BLE001 — evidence is optional; degrade to neutral
        return None
    raw = ev.get("scores") if isinstance(ev, dict) else None
    if not isinstance(raw, dict):
        return None
    return {str(k): float(v) for k, v in raw.items()}


def _candidate_sweep_enabled() -> bool:
    """STEP② candidate sweep gate — env ``POLARIS_CANDIDATE_SWEEP`` (default ON).

    ``1`` (default) = the sweep is the focus producer (today's movers). ``0`` =
    fall back to the legacy ``compute_dynamic_focus`` merit rank (rollback / tests).
    flow_not_block: the gate only swaps WHICH ordering produces ``focus_rank``;
    both paths emit valid ``FocusSelection`` rows via the SAME ``persist_focus``.
    """
    raw = os.environ.get("POLARIS_CANDIDATE_SWEEP")
    if raw is None or raw == "":
        return True
    return raw.strip() not in ("0", "false", "False", "no", "off")


def _build_venue_weights(
    universe: Sequence[UniverseInstrument], now_ts: int
) -> dict[str, float]:
    """Session-modulated per-venue allocation weights (read-only, flow_not_block).

    OKX is 24/7 (steady 1.0). Capital index/commodity/equity rows are weighted
    DOWN when their cash session is near/at close (``capital_seconds_to_close``);
    FX majors are 24/5-exempt (None → full weight). Alpaca equities are weighted
    DOWN outside US RTH. This only re-allocates dynamic SEATS across venues — it
    never blocks an entry or cuts a size (cap 200 still WIDENS the live set). A
    venue is never zeroed while it has rows (min floor keeps it observable).
    """
    venues = {ins.venue for ins in universe}
    weights: dict[str, float] = {}
    # Capital weight = min over its asset classes' session proximity (closest
    # close dominates). Default 1.0 when exempt/open.
    cap_classes = {ins.asset_class for ins in universe if ins.venue == "capital"}
    for venue in venues:
        if venue == "capital":
            weights[venue] = _capital_session_weight(cap_classes, now_ts)
        elif venue == "alpaca":
            weights[venue] = _alpaca_session_weight(now_ts)
        else:
            weights[venue] = 1.0
    return weights


def _capital_session_weight(asset_classes: set[str], now_ts: int) -> float:
    """Lowest session weight across Capital asset classes.

    Within ``SESSION_LEAD_SEC`` of a cash close → ``SESSION_DOWNWEIGHT`` (fewer
    seats, never zeroed); 24/5-exempt / unknown class → full weight. Knobs are
    env-overridable /debate calibration targets (see ``_candidate_sweep``).
    """
    weight = 1.0
    for ac in asset_classes:
        secs = capital_seconds_to_close(ac, now_ts)
        if secs is None:
            continue  # 24/5-exempt or unknown → full weight
        if secs <= SESSION_LEAD_SEC:
            weight = min(weight, SESSION_DOWNWEIGHT)  # near/at close — never zero
    return weight


def _alpaca_session_weight(now_ts: int) -> float:
    """US-equity session weight — 1.0 inside RTH, ``SESSION_DOWNWEIGHT`` outside.

    RTH window = ``[RTH_OPEN_MIN, RTH_CLOSE_MIN]`` UTC minutes-of-day (EDT ref,
    matches the equity session gate). Never zero (flow_not_block). Env-overridable.
    """
    import datetime as _dt

    local = _dt.datetime.fromtimestamp(now_ts, tz=_dt.UTC)
    if local.weekday() >= 5:  # weekend
        return SESSION_DOWNWEIGHT
    minute = local.hour * 60 + local.minute
    return 1.0 if RTH_OPEN_MIN <= minute <= RTH_CLOSE_MIN else SESSION_DOWNWEIGHT


def _sweep_focus(
    conn: sqlite3.Connection,
    universe: Sequence[UniverseInstrument],
    now_ts: int,
    *,
    opportunity_scores: dict[str, float],
    trade_eligible: dict[str, bool],
    quote_writer: Any | None = None,
) -> list[FocusSelection]:
    """Build focus rows from the STEP② candidate sweep (today's movers).

    Reads the prior focus cycle for hysteresis, the open positions for force-seat,
    and the session-modulated venue weights, then delegates to the pure
    ``select_candidate_focus``. cap = ``POLARIS_FOCUS_CYCLE_TARGET`` (default 200).
    The EntranceJudge ``opportunity_scores`` + ``trade_eligible`` decouple are
    threaded through so the judgment telemetry + WATCH/TRADE split are PRESERVED on
    the sweep path (the sweep changes only focus_rank/bucket ordering).

    ``quote_writer`` (#39) is the per-symbol activation provider — the sweep reads
    its in-mem accumulator (``activation_metrics``) so an intraday-active major
    outranks a calm wide-daily name. A missing writer degrades the live-motion
    activation components to neutral (the name scores on bars+spread alone).
    """
    # Deferred import breaks the _production_layers ⇆ _candidate_sweep_select ⇆
    # _static_ground import cycle (the sweep reads read_ticker_ground from
    # _static_ground, which imports read_active_universe from here).
    from polaris.scripts._candidate_sweep_select import select_candidate_focus

    cap = _sweep_cap()
    prev_symbols = _prev_focus_symbols(conn, now_ts)
    open_targets = open_position_targets(conn)
    venue_weights = _build_venue_weights(universe, now_ts)
    return select_candidate_focus(
        conn, universe, now_ts=now_ts, cap=cap,
        bucket_counts=None, venue_weights=venue_weights,
        open_targets=open_targets, prev_focus_symbols=prev_symbols,
        opportunity_scores=opportunity_scores or None,
        trade_eligible=trade_eligible or None,
        activation_provider=quote_writer,
    )


def _sweep_cap() -> int:
    """Candidate-focus cap (env ``POLARIS_FOCUS_CYCLE_TARGET``; default 200)."""
    raw = os.environ.get("POLARIS_FOCUS_CYCLE_TARGET")
    if raw is None or raw == "":
        return 200
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return 200


def _prev_focus_symbols(conn: sqlite3.Connection, now_ts: int) -> set[str]:
    """Instrument-ids of the latest focus cycle ≤ now_ts (hysteresis input)."""
    row = conn.execute(
        "SELECT MAX(cycle_ts) FROM watchlist_focus WHERE cycle_ts < ?", (now_ts,)
    ).fetchone()
    if row is None or row[0] is None:
        return set()
    rows = conn.execute(
        "SELECT venue, symbol FROM watchlist_focus WHERE cycle_ts = ?", (int(row[0]),)
    ).fetchall()
    return {f"{r[0]}:{r[1]}" for r in rows}


def refresh_focus_watchlist(
    conn: sqlite3.Connection,
    *,
    cycle_ts: int | None = None,
    probe_conn: sqlite3.Connection | None = None,
    run_id: str = "",
    quote_writer: Any | None = None,
    altdata_cache: Any | None = None,
) -> int:
    """Compute dynamic focus over active universe + persist; return count.

    Task 3 / D2: runtime-blocklisted (venue, symbol) — venue-permanent
    compliance rejects (51155) — are excluded so they never enter focus and
    cannot churn the order path.

    B1 ranking-brain wiring (2026-06-23): the two previously-dead rank axes are
    fed live here — ``signal_density_7d`` (per-instrument 7d emit count) is merged
    onto the in-memory rows, and ``cell_scores`` (n_eff-weighted cell_matrix_p0)
    is passed into the focus call — so ~35% of the designed rank weight that sat
    inert (0.0 across all rows) now orders focus. Both are RANKING inputs only
    (flow_not_block): no entry is blocked, no size cut, no veto.

    Increment 1 (2026-06-24): the EntranceJudge ACTIVATES here — it scores every
    candidate across the deterministic liquidity + ATR lenses (always present from
    the universe rows) into an ``opportunity_score`` + a ``trade_eligible`` flag.

    Lean wiring (audit code_review_2026-06-24): the three previously-dead optional
    lenses are now fed from data the loop already holds — ``technical`` from the
    live tick mid-drift window (``quote_writer``), ``regime`` from the regime_state
    SSOT directional bias, ``altdata`` from the already-fused ``fuse_evidence``
    tilt (``altdata_cache``). SIGNAL-ONLY: the leans tilt the ``opportunity_score``
    rank only and touch ZERO sizing multiplier (9-stack untouched). A missing
    writer/cache/regime degrades the affected lens to neutral (flow_not_block).
    The score feeds the focus rank composite AND persists per row; the flag
    decouples the WATCH set from the TRADE set (consumed at ``_run_entries``).
    When ``probe_conn`` is supplied the per-candidate judgment (incl. the ambiguous
    conflict flag) is written to the ``entrance_judgments`` sidecar — PURE
    TELEMETRY, NO AI call, the deferred Increment-2 advisory seam. AI-free /
    deterministic; 9-stack untouched (the score is never a sizing multiplier).
    """
    ts = cycle_ts if cycle_ts is not None else int(time.time())
    universe = read_active_universe(conn)
    if not universe:
        return 0
    blocked = load_blocklist(conn)
    if blocked:
        universe = [
            ins for ins in universe if (ins.venue, ins.symbol) not in blocked
        ]
        if not universe:
            return 0
    density = compute_signal_density_7d(conn, now_ts=ts)
    if density:
        universe = [
            replace(ins, signal_density_7d=density.get(ins.instrument_id, 0.0))
            for ins in universe
        ]
    cell_scores = read_cell_scores_by_instrument(conn)
    # STAGE 1 rank-attention gradient (2026-06-24): the focus COUNT-CAP (12-48
    # window + per-class quota) is GONE — ``compute_dynamic_focus`` now watches
    # EVERY active row and assigns a cadence tier {S,A,B,T} from the single merit
    # rank. No [12,48] width inputs remain (flow_not_block: tier differentiates
    # poll CADENCE, never membership — all active rows are watched).
    # Increment 1 — entrance judgment (deterministic, AI-free). Score every
    # candidate; feed opportunity_score into the rank composite + persist the
    # eligibility flag. flow_not_block: a high judgment lifts rank, the permissive
    # floor keeps MOST symbols trade-eligible (a low judgment narrows priority,
    # never vetoes — an un-judged/ambiguous row stays eligible).
    #
    # LEAN WIRING (audit code_review_2026-06-24): the 3 optional lenses
    # (technical / regime / altdata) were never fed → permanently neutral, so the
    # judge ranked on liquidity+ATR alone. They are now built from data the loop
    # ALREADY holds — the live tick window (quote_writer), the regime_state SSOT,
    # and the already-fused alt-data tilt — and threaded as signed per-instrument
    # leans. SIGNAL-ONLY: leans tilt opportunity_score (rank) only, ZERO sizing
    # multiplier (9-stack untouched). A missing writer/cache/regime degrades the
    # affected lens to neutral (flow_not_block — never an error, never a block).
    technical_lean = build_technical_lean(universe, quote_writer)
    regime_lean = build_regime_lean(
        universe,
        lambda venue, group: fetch_regime(
            conn, venue=venue, underlying_group_id=group
        ),
    )
    altdata_lean = (
        build_altdata_lean(
            universe,
            lambda group: _fused_scores(altdata_cache, group, ts),
        )
        if altdata_cache is not None
        else {}
    )
    judge = EntranceJudge()
    judge_readings = judge.judge_universe(
        universe,
        technical_lean=technical_lean or None,
        regime_lean=regime_lean or None,
        altdata_lean=altdata_lean or None,
    )
    opportunity_scores = {
        iid: r.opportunity_score for iid, r in judge_readings.items()
    }
    trade_eligible = {iid: r.trade_eligible for iid, r in judge_readings.items()}
    # STEP② candidate sweep (default ON): the focus PRODUCER becomes the dynamic
    # two-stage sweep over the static ground (today's movers, cap 200 buckets)
    # instead of the static liquidity merit rank — so the same high-vol names no
    # longer monopolize focus. flow_not_block: the sweep emits valid FocusSelection
    # rows (focus_rank/tier/bucket) via the SAME persist_focus; no entry/size/exit
    # is gated and 9-stack is untouched (the sweep produces no sizing multiplier).
    # The legacy merit producer stays intact behind POLARIS_CANDIDATE_SWEEP=0 for
    # rollback. The EntranceJudge telemetry (opportunity_score/trade_eligible flag
    # + the ambiguity sidecar) is preserved on both paths.
    if _candidate_sweep_enabled():
        focus = _sweep_focus(
            conn, universe, ts,
            opportunity_scores=opportunity_scores,
            trade_eligible=trade_eligible,
            quote_writer=quote_writer,
        )
    else:
        focus = compute_dynamic_focus(
            universe,
            cell_scores=cell_scores or None,
            cycle_ts=ts,
            opportunity_scores=opportunity_scores or None,
            trade_eligible=trade_eligible or None,
        )
    persist_focus(conn, focus)
    # Ambiguity seam — write the per-candidate judgment (incl. ambiguous flag) to
    # the sidecar. PURE TELEMETRY (NO AI, NO runtime consumer; deferred Inc-2).
    if probe_conn is not None and judge_readings:
        log_entrance_judgments(
            probe_conn, ts=ts, run_id=run_id, cycle_ts=ts, readings=judge_readings
        )
    n_ineligible = sum(1 for v in trade_eligible.values() if not v)
    n_ambiguous = sum(1 for r in judge_readings.values() if r.ambiguous)
    tier_counts: dict[str, int] = {}
    for f in focus:
        tier_counts[f.tier] = tier_counts.get(f.tier, 0) + 1
    # Operational narrative: surface regional session OPEN/CLOSE edges (the global
    # session clock turning over). Edge-trigger only — emits nothing on a normal
    # mid-session cycle. Log only; never gates trading.
    _emit_session_edges(universe, ts)
    logger.info(
        "[L0/focus] universe=%d → watched=%d (signal_density=%d, cell_scores=%d, "
        "tiers=%s | judged=%d trade_ineligible=%d ambiguous=%d)",
        len(universe), len(focus), len(density), len(cell_scores), tier_counts,
        len(judge_readings), n_ineligible, n_ambiguous,
    )
    return len(focus)


def open_position_targets(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, str, str]]:
    """Read every OPEN position as a focus-shaped ``(venue, symbol, asset_class,
    group_id)`` tuple, across ALL venues (OKX / Capital / Alpaca).

    FIX 2/2 — held-position visibility/precision. A position whose symbol is NOT
    in the dynamic focus still needs LIVE WS quotes + fresh bars (dashboard live
    price + exit precision). ``asset_class`` is resolved via a LEFT JOIN to
    ``universe`` (the focus source) and falls back to the ``underlying_group_id``
    prefix (e.g. ``crypto:HYPE`` → ``crypto``) when the held name has aged out of
    the active universe. De-duplicated on ``(venue, symbol)`` so multiple
    positions on one name yield one target. ADD-only (flow_not_block): this never
    blocks an entry, it only forces a held name to stay watched while held.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT p.venue, p.symbol, u.asset_class, p.underlying_group_id
        FROM positions p
        LEFT JOIN universe u
          ON p.venue = u.venue AND p.symbol = u.symbol
        WHERE p.status NOT IN ('closed', 'cancelled', 'reconciled')
        """
    ).fetchall()
    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        venue, symbol = str(r[0]), str(r[1])
        if (venue, symbol) in seen:
            continue
        seen.add((venue, symbol))
        group_id = str(r[3] or "")
        # Hardening #5 (2026-06-23): a NULL universe class OR an unknown group_id
        # prefix is now a LOUD, validated, venue-correct fallback (WARN + an
        # idempotent ``asset_class_fallback`` counter), not a silent flat 'crypto'.
        asset_class = resolve_asset_class(
            universe_class=None if r[2] is None else str(r[2]),
            venue=venue, symbol=symbol, group_id=group_id,
            source="held_position", conn=conn,
        )
        out.append((venue, symbol, asset_class, group_id))
    return out


def fx_major_focus_targets(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, str, str]]:
    """Every LIVE curated Capital FX major as a focus-shaped target tuple.

    FX-freshness fix (2026-06-24). The curated FX majors (EURUSD / GBPUSD /
    AUDUSD / USDJPY / USDCAD) are kept in the ACTIVE universe by the rank-side
    keep-floor (``is_capital_fx_major``), BUT the continuous merit rank still
    sorts them to the TAIL tier (Tier-T, ``focus_rank`` ~1100-1900 in the live
    DB) because Capital exposes no 24h notional → they rank on ATR alone and the
    quiet majors lose to the high-ATR exotics. At Tier-T cadence they are polled
    so rarely that they never accumulate bars (live DB: USDJPY/EURUSD/GBPUSD/
    USDCAD = ZERO bars), so the non-USD CFD path had only the snapshot rate.

    This seats every live major into the focus union — exactly like a held
    position — so the bar ingest ALWAYS fetches them regardless of tier cadence.
    ADD-only (flow_not_block): seats the majors ALONGSIDE the exotics, removes
    nothing, and touches no ranking weight. ``asset_class`` comes from the
    universe row (FX majors are always ``forex``).

    Matching is SUFFIX-INSENSITIVE via ``is_capital_fx_major`` (which normalizes
    the epic): Capital lists the AUD/USD major as ``AUDUSD_ZERO`` (NOT a bare
    ``AUDUSD`` row — verified live), so an exact ``symbol IN (majors)`` filter
    would silently drop AUDUSD. We select all live ``capital`` forex rows and let
    the normalizing predicate pick the majors — same logic the strategy basket
    uses (``fx_breakout_basket._normalize_basket_symbol``). ``EURUSD_W`` (weekend
    variant) is excluded by the ``state='live'`` guard (it lists as ``closed``).
    """
    rows = conn.execute(
        """
        SELECT venue, symbol, asset_class, underlying_group_id
        FROM universe
        WHERE venue = 'capital' AND state = 'live' AND asset_class = 'forex'
        """
    ).fetchall()
    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        venue, symbol = str(r[0]), str(r[1])
        # Normalizing predicate matches AUDUSD_ZERO / EURUSD / ... → the 5 majors,
        # and rejects every exotic/cross. De-dup on (venue, symbol).
        if not is_capital_fx_major(venue, symbol) or (venue, symbol) in seen:
            continue
        seen.add((venue, symbol))
        out.append((venue, symbol, str(r[2] or "forex"), str(r[3] or "")))
    return out


def _firing_tiers(cycle_index: int, k: int, m: int) -> list[str]:
    """Tiers whose cadence fires on ``cycle_index`` (S/A always; B@K; T@M)."""
    tiers: tuple[Tier, ...] = ("S", "A", "B", "T")
    return [t for t in tiers if cadence_fires(t, cycle_index, k, m)]


def get_focus_targets(
    conn: sqlite3.Connection,
    *,
    cycle_ts: int | None = None,
    max_n: int = 30,
    eligible_only: bool = False,
    cycle_index: int | None = None,
    cadence_k: int | None = None,
    cadence_m: int | None = None,
) -> list[tuple[str, str, str, str]]:
    """Read the latest focus cycle as ``(venue, symbol, asset_class, group_id)``.

    Returns up to ``max_n`` dynamic-focus entries (ordered by focus_rank asc)
    UNIONED with every OPEN position's symbol (:func:`open_position_targets`).
    The held symbols are appended AFTER the dynamic picks and are NOT subject to
    ``max_n`` — a held name can never fall off the watched/subscribed set while
    its position is open (FIX 2/2: dashboard live price + exit precision). This
    is the single seam both the bar ingest (``_run_tick``) and the WS
    subscription (``_focus_by_venue``) read, so the union keeps held symbols
    bar-ingested AND WS-subscribed for as long as they are held.

    Increment 1 DECOUPLE: ``eligible_only`` splits WATCH from TRADE at this single
    seam. ``False`` (default) = the WATCH set — the FULL focus selection, so bar
    ingest + WS subscription + dashboard keep seeing EVERY valid symbol
    (flow_not_block; no symbol is dropped from observation). ``True`` = the TRADE
    set — only rows with ``trade_eligible=1`` (the entry pass calls it this way).
    Held positions are force-seated into BOTH sets (a held name is never starved
    out of the trade set by an eligibility downgrade). A NULL ``trade_eligible``
    (legacy/un-judged row) reads as eligible (COALESCE → 1, flow-preserving).

    STAGE 1 CADENCE: when ``cycle_index`` is given, the dynamic picks are filtered
    to the tiers whose cadence FIRES this cycle — S/A every cycle, B every ``K``,
    T every ``M`` (``cadence_k``/``cadence_m`` default to :func:`tier_cadence`).
    This is the per-cycle poll set, NOT a membership cut: every active row is still
    persisted/watched (flow_not_block); cadence only governs HOW OFTEN it is
    polled. ``cycle_index=None`` (legacy callers, held-position union) → no cadence
    filter (full watch). Held positions are always force-seated regardless of tier.

    Empty dynamic focus + no open positions → empty list (caller falls back to
    BTC seed). Union is ADD-only (flow_not_block): never blocks an entry.
    """
    ts = cycle_ts if cycle_ts is not None else int(time.time())
    focus: list[tuple[str, str, str, str]] = []
    row = conn.execute(
        "SELECT MAX(cycle_ts) FROM watchlist_focus WHERE cycle_ts <= ?", (ts,)
    ).fetchone()
    if row is not None and row[0] is not None:
        latest_cycle = int(row[0])
        # DECOUPLE: the trade set filters to eligible rows; the watch set takes
        # all. COALESCE(trade_eligible,1)=1 keeps a legacy/un-judged row eligible.
        eligible_clause = (
            " AND COALESCE(wf.trade_eligible, 1) = 1" if eligible_only else ""
        )
        params: list[object] = [latest_cycle]
        # CADENCE: filter to firing tiers this cycle (legacy/un-tiered row →
        # COALESCE 'T', so it polls at the tail cadence; flow_not_block).
        tier_clause = ""
        if cycle_index is not None:
            k, m = tier_cadence()
            k = cadence_k if cadence_k is not None else k
            m = cadence_m if cadence_m is not None else m
            firing = _firing_tiers(int(cycle_index), int(k), int(m))
            placeholders = ", ".join("?" for _ in firing)
            tier_clause = f" AND COALESCE(wf.tier, 'T') IN ({placeholders})"
            params.extend(firing)
        params.append(int(max_n))
        rows = conn.execute(
            f"""
            SELECT wf.venue, wf.symbol, u.asset_class, u.underlying_group_id
            FROM watchlist_focus wf
            LEFT JOIN universe u
              ON wf.venue = u.venue AND wf.symbol = u.symbol
            WHERE wf.cycle_ts = ?{eligible_clause}{tier_clause}
            ORDER BY wf.focus_rank ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        focus = [
            (
                str(r[0]),
                str(r[1]),
                # P2.2 fix (2026-06-22): a NULL asset_class (universe JOIN-miss)
                # falls back to the canonical group_id prefix (5-class) BEFORE
                # "crypto" — so an aged-out Alpaca equity / Capital commodity is
                # not mislabelled crypto (which would apply the wrong 2.0%
                # regime vol-floor). venue untouched; signal-only, flow_not_block.
                str(r[2]) if r[2] else (
                    str(r[3]).split(":", 1)[0]
                    if r[3] and ":" in str(r[3]) else "crypto"
                ),
                str(r[3] or ""),
            )
            for r in rows
        ]
    # Force-seat held symbols (additive, not truncated by max_n). De-dup on
    # (venue, symbol) so a held name already in the dynamic focus is not doubled.
    seen = {(v, s) for v, s, _ac, _g in focus}
    for target in open_position_targets(conn):
        if (target[0], target[1]) in seen:
            continue
        seen.add((target[0], target[1]))
        focus.append(target)
    # FX-freshness (2026-06-24): force-seat live curated Capital FX majors into
    # the WATCH set so the bar ingest always fetches them (they rank Tier-T and
    # would otherwise starve — live DB showed ZERO bars for USDJPY/EURUSD/...).
    # WATCH-only (``not eligible_only``): this seats them for OBSERVATION/bars,
    # NOT into the TRADE set — trade-eligibility stays owned by the entrance
    # judge (decouple preserved). ADD-only, never truncated by max_n.
    if not eligible_only:
        for target in fx_major_focus_targets(conn):
            if (target[0], target[1]) in seen:
                continue
            seen.add((target[0], target[1]))
            focus.append(target)
    return focus



# ---------------------------------------------------------------------------
# Layer 6 — recalc + regime flip
# ---------------------------------------------------------------------------


_REGIME_LABELS: tuple[str, ...] = ("bull_trend", "bear_trend", "chop", "crisis")
# Conviction floor mirrors fuser.CONVICTION_FLOOR — an evidence label must clear
# this before it can tilt a borderline price candidate.
_EVIDENCE_CONVICTION_FLOOR = 1.5


def compose_regime_candidate(
    price_candidate: str,
    price_strength: float,
    evidence_scores: dict[str, float],
) -> str:
    """Weighted price↔evidence synthesis — SIGNAL-only label, no side effects.

    Price is the base. Evidence (the fuser's per-label scores) can *tilt* the
    candidate ONLY when its winning conviction beats the price conviction:

      * No evidence scores → price candidate stands (fallback).
      * A price-derived ``crisis`` is NEVER tilted away (safety; a price crash
        is the highest-priority signal and keeps the immediate-flip path).
      * Otherwise compare conviction: ``price_strength`` (0..1) scaled to the
        evidence score-space vs the best evidence score above the conviction
        floor. The stronger conviction wins; ties go to price (status-quo bias,
        NOT a throttle — synthesis only relabels, it never reduces flow).

    This is a pure relabel. It does not size, block, exit, or halt.
    """
    if price_candidate == "crisis":
        return "crisis"
    if not evidence_scores:
        return price_candidate
    best_label = max(evidence_scores, key=lambda k: evidence_scores[k])
    best_score = evidence_scores[best_label]
    if best_score < _EVIDENCE_CONVICTION_FLOOR or best_label == price_candidate:
        return price_candidate
    if best_label not in _REGIME_LABELS:
        return price_candidate
    # Project price conviction into the evidence score-space. A full-strength
    # (1.0) price regime maps to a conviction comparable to a strong evidence
    # win (~3.0); evidence must out-convict it to tilt.
    price_conviction = price_strength * 3.0
    if best_score > price_conviction:
        return best_label
    return price_candidate


def _compose_confidence(
    price_strength: float,
    composed_candidate: str,
    price_candidate: str,
    evidence_scores: dict[str, float],
) -> float:
    """Dynamic confidence (P5) in ``[0, 1]`` from L3 strength + L2/L1 agreement.

    Base = price strength. Agreement bonus when the evidence's winning label
    matches the composed candidate (L3↔L2/L1 concur); a mild penalty when
    evidence disagreed but did not win the tilt. Computed BEFORE the
    detect_regime_flip confirm gate (does NOT alter the gate).
    """
    conf = max(0.0, min(1.0, price_strength))
    if not evidence_scores:
        return max(0.1, conf)  # price-only: never report a zero-confidence label
    best_label = max(evidence_scores, key=lambda k: evidence_scores[k])
    best_score = evidence_scores[best_label]
    if best_score >= _EVIDENCE_CONVICTION_FLOOR:
        if best_label == composed_candidate:
            conf = conf + 0.25 * (1.0 - conf)  # concurrence → tighten toward 1
        elif best_label != price_candidate:
            conf = conf * 0.85  # unresolved disagreement → slightly less certain
    return max(0.1, min(1.0, conf))


def compute_and_flip_regime(
    conn: sqlite3.Connection,
    *,
    venue: str,
    underlying_group_id: str,
    bars: Sequence[Bar],
    now_ts: int,
    altdata_cache: Any = None,
    asset_class: str = "crypto",
    hint_stats: dict[str, int] | None = None,
) -> str:
    """Compute candidate regime + run Layer 6 SSOT 2-consecutive-close gate.

    Returns the regime SSOT *after* applying the flip rule so callers using
    the Layer 6 SSOT receive the gated value (matches strategy_swap's
    ``_lookup_regime`` semantics).

    ``asset_class`` (threaded from the focus row) selects the per-class vol floor
    so the L3 price candidate's TREND/CRISIS thresholds are normalized to the
    instrument's own scale — FX/index trends now flip to bull/bear instead of
    sitting in ``chop`` forever. The 2-consecutive-close confirm gate below is
    unchanged; this only affects which candidate label the L3 signal proposes.

    ``altdata_cache`` (#6) supplies alt-data EVIDENCE only. The L3 price signal
    is the base; the fuser's per-label scores tilt a *borderline* candidate via
    ``compose_regime_candidate`` (price conviction vs evidence conviction):

      * The price-derived candidate is computed first and always stands when
        there is no fresh evidence (failing/keyless collector → price-only;
        correct fallback, NOT a defensive throttle).
      * Evidence NEVER downgrades a price-derived ``crisis``.
      * ``candidate_source`` (P4) tags the candidate: a ``crisis`` that came
        from PRICE keeps the immediate-flip fast path; a ``crisis`` introduced
        by EVIDENCE must clear the SAME 2-consecutive-close gate (no bypass).
      * The (possibly tilted) candidate STILL clears the unchanged confirm
        gate. Evidence is additive context only; it does not size, block,
        exit, or write learner/risk state. SIGNAL-only.

    ``hint_stats`` (instrumentation only, default None = byte-identical): an
    in-memory counter dict accumulating how the fuser's conviction-floor hint
    fared vs the composed candidate / final SSOT label (``hint_total`` /
    ``hint_tilt_lost_to_price`` / ``hint_final_mismatch``). The hint is a
    REDUNDANT projection of the same evidence scores compose consumes
    (intentionally retired as an input — regime_layered debate 2026-05-31);
    these counters only measure that redundancy. Per-call DB writes: 0.
    """
    price_candidate, price_strength, _price_ev = compute_real_regime_signal(
        bars, asset_class=asset_class
    )
    candidate = price_candidate
    evidence: dict[str, Any] = {}
    evidence_scores: dict[str, float] = {}
    hint: str | None = None
    if altdata_cache is not None:
        hint, _conf, ev = fuse_evidence(
            underlying_group_id, altdata_cache, now_ts=now_ts
        )
        evidence = ev
        raw_scores = ev.get("scores") if isinstance(ev, dict) else None
        if isinstance(raw_scores, dict):
            evidence_scores = {str(k): float(v) for k, v in raw_scores.items()}
        candidate = compose_regime_candidate(
            price_candidate, price_strength, evidence_scores
        )
    # P5: dynamic confidence from L3 strength + L2/L1 agreement (pre-gate).
    confidence = _compose_confidence(
        price_strength, candidate, price_candidate, evidence_scores
    )
    # P4: a crisis candidate that PRICE did not produce is evidence-derived and
    # must NOT take the immediate-flip path.
    candidate_source = (
        "evidence"
        if candidate == "crisis" and price_candidate != "crisis"
        else "price"
    )
    # Regime candidate (DEBUG): the pre-gate composition — the L3 price candidate,
    # the (possibly evidence-tilted) composed candidate, its source, and the
    # dynamic confidence. The 2-consecutive-close confirm gate below is unchanged;
    # only a confirmed flip emits INFO. Log only — never sizes/blocks/exits.
    logger.debug(
        "[L6/regime] candidate %s/%s price=%s tilt=%s source=%s "
        "price_strength=%.3f confidence=%.3f hint=%s",
        venue, underlying_group_id, price_candidate, candidate,
        candidate_source, price_strength, confidence, hint,
    )
    decision = detect_regime_flip(
        conn,
        venue=venue,
        underlying_group_id=underlying_group_id,
        candidate=candidate,
        now_ts=now_ts,
        evidence=evidence,
        confidence=confidence,
        candidate_source=candidate_source,
    )
    # Either the flip was confirmed (decision.to_regime is the new SSOT) or
    # the row stayed at the prior regime. Read back the persisted SSOT so the
    # caller sees what every other Layer 6 consumer sees.
    row = conn.execute(
        "SELECT regime FROM regime_state "
        "WHERE venue = ? AND underlying_group_id = ?",
        (venue, underlying_group_id),
    ).fetchone()
    # Hint instrumentation (in-memory counters ONLY — no DB write, no branch):
    # how often the fuser's floor-cleared hint diverged from the composed
    # candidate (evidence lost the price-conviction tilt) and from the final
    # SSOT label (post 2-close gate). hint is None when conviction < floor.
    if hint_stats is not None and hint is not None:
        final_label = candidate if row is None else str(row[0])
        hint_stats["hint_total"] = hint_stats.get("hint_total", 0) + 1
        if hint != candidate:
            hint_stats["hint_tilt_lost_to_price"] = (
                hint_stats.get("hint_tilt_lost_to_price", 0) + 1
            )
        if hint != final_label:
            hint_stats["hint_final_mismatch"] = (
                hint_stats.get("hint_final_mismatch", 0) + 1
            )
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
        "SELECT position_id FROM positions "
        "WHERE status NOT IN ('closed', 'cancelled', 'reconciled')"
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
