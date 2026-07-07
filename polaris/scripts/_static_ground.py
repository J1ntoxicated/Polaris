"""STEP① static-ground expansion — full-universe bar + per-ticker ground fill.

Jin "맨날 들어오는 애들만" (only the same names ever come in). The per-tick
trading path (``_run_tick`` → ``ingest_bars_per_timeframe``) bar-ingests only the
FOCUS subset (``FOCUS_CYCLE_TARGET`` watched names, tier-gated) — the binding REST
+ DB cost on the 5s hot path. So only ~276 of ~1882 active instruments ever get a
static ground (bars + technical-capable + sentiment + event), and the candidate
sweep (②, follow-up) can only see those few. The other ~1600 active tickers are
never evaluated.

This module is the TOEHOLD: a SEPARATE, background, non-blocking coverage-fill
that walks the WHOLE active universe and gives every active ticker its static
ground — Yahoo multi-resolution bars (free/unlimited-grade) + the fused
sentiment/event EVIDENCE (graceful-empty where no source covers it). It feeds ②;
it is NOT the candidate sweep, the move-watcher, or any gate.

ABSOLUTE: this is OBSERVATION coverage, never a trading decision. AGGRESSIVE /
flow_not_block preserved — no entry / size / exit / halt is gated here. The only
throttle is the Semaphore + per-cycle total-timeout that keeps the bulk Yahoo fan-
out from an IP-block (a WORSE all-venue outage); a missing/slow symbol degrades
the fill, never the bot. The hot path is UNTOUCHED — the fill runs on its own task
and reuses the existing within-period Yahoo frame cache, so an overlap with a
focus symbol within its bar period is a cache hit (no double fetch).

DEMO/PAPER only.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import sqlite3
import time
from typing import Any

from polaris.core.altdata.fuser import fuse_evidence
from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.scripts._production_bars import (
    ALPACA_TIMEFRAME_BY_INTERVAL,
    fetch_alpaca_bars_multi,
    fetch_bars_one,
)
from polaris.scripts._production_layers import read_active_universe
from polaris.scripts._session_map import equity_fetch_active, session_warm_active
from polaris.strategies import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

# Static-ground bar resolutions. These are the "ground" timeframes a candidate
# sweep + technical read need: 1D (MA200 / regime warmup), 1H + 15m (intraday
# structure). 1m is INTENTIONALLY ABSENT — its Yahoo window is 7d (thousands of
# rows) and only the FOCUS subset (hot path) needs intra-minute freshness; bulk-
# pulling 1m for ~1882 tickers would be the heaviest fetch for the least ground
# value. A /debate calibration target, never hardcoded at the call site.
STATIC_GROUND_RESOLUTIONS: tuple[str, ...] = ("1D", "1H", "15m")

# Session pre-open WARM resolutions (#66, Jin "장 열기 전부터 거래가능 바 알아서 채워야").
# The base resolutions above intentionally OMIT 1m (too heavy for the whole
# universe). But a symbol whose cash session is about to open (``session_warm_
# active``) needs its 1m bars fresh AT the open so the recency gate (1m=30min)
# does not skip it. So for ONLY the open-imminent symbols (Capital indices +
# Alpaca US equities — never crypto/FX, see ``session_warm_active``) the fill
# ADDS these resolutions. Decoupled from the base set so 1m stays scoped to the
# handful of open-imminent names. Default; the producer reads
# ``POLARIS_SESSION_WARM_RESOLUTIONS`` so it is env-tunable (and '' = OFF).
SESSION_WARM_RESOLUTIONS_DEFAULT: tuple[str, ...] = ("1m",)
# Short re-walk cadence used WHILE ≥1 symbol is open-imminent (warm-active), so
# the 1m pull lands close to the open. With 0 warm symbols the producer falls
# back to ``STATIC_GROUND_REFRESH_SEC`` (idle degrade). env-tunable default.
SESSION_WARM_CADENCE_SEC = 60.0

# Bulk-fill concurrency (Yahoo IP-block guard). Mirrors populate_capital_proxies'
# Semaphore + total-timeout pattern (startup-fix). Higher than the 8-wide hot-path
# fan-out because this is off the tick deadline, but bounded so the bulk Yahoo
# fan-out (1882 × 3 resolutions) cannot trip an IP block. Default; the producer
# call site reads ``POLARIS_STATIC_GROUND_PARALLEL`` so it is env-tunable live.
#
# Charge-rate tune (live probe 2026-06-26): at 16-wide a cycle reached only ~435
# of 1882 instruments before the 600s ceiling (~7.4s per Yahoo history fetch —
# 1D=2y frames + network). The sweep then scored candidates on partial bars
# (366/1650). The ONLY lever that raises throughput is the inflight width (the
# per-interval Semaphore already keeps inflight == parallel), so widen to 32. At
# ~0.135 fetch/s/slot that is ~4.3 req/s ≈ 260 req/min — well under a Yahoo IP
# throttle, and the within-period frame cache means a re-walk only re-pulls rolled
# periods. ~2× the per-cycle reach; flow_not_block (off the tick deadline).
STATIC_GROUND_PARALLEL_DEFAULT = 32
# Per-cycle wall-clock ceiling (degrade-never-halt): whatever completed is kept,
# the rest retries next cycle. The fill is incremental — the within-period frame
# cache means a re-walk only re-fetches symbols whose bar period rolled.
STATIC_GROUND_TOTAL_TIMEOUT_SEC = 600.0
# How often the background producer re-walks the active universe. 1D bars roll
# once/day and the Yahoo frame cache holds intraday frames for their period, so a
# slow re-walk keeps coverage warm without re-hammering Yahoo. The FIRST walk runs
# immediately at startup (the one-time fill).
STATIC_GROUND_REFRESH_SEC = 900.0
# Per-ticker sentiment/event ground (EdgeScore input) is materialized on its OWN
# cadence, DECOUPLED from the multi-minute bar walk above. fuse_evidence is pure
# in-memory + a single INSERT per ticker, so a full ~1882-row refresh is cheap
# (one BEGIN/COMMIT, no await inside the txn). Running it every 180s — instead of
# only AFTER each ~600s bar walk — fills EdgeScore early and keeps it warm so the
# candidate sweep gets directional ground while bars are still charging (live
# probe 2026-06-26: ticker_ground=0 during the first bar walk starved the sweep).
TICKER_GROUND_REFRESH_SEC = 180.0


def warm_eligible_symbols(conn: sqlite3.Connection) -> frozenset[tuple[str, str]]:
    """Latest-cycle ``(venue, symbol)`` rows that are TRADE-eligible FOCUS names.

    A2 SCOPE fix: the 1m session-warm pass used to run over EVERY universe-wide
    open-imminent symbol (~1,491 names for Alpaca equities alone) even though
    equity strategies are all ``1D`` — nothing in the pipeline consumes
    universe-wide 1m bars. The only consumer of pre-open 1m freshness is the
    live tick/recency-guard path for names the bot can actually ACT on, i.e. the
    trade-eligible FOCUS set (``watchlist_focus`` latest cycle, ``trade_eligible
    = 1``, the ~150-name set) — mirrors ``_production_layers.trade_ineligible_
    set``'s COALESCE contract so a legacy/un-judged row (NULL) still counts as
    eligible (flow-preserving). An empty/absent focus cycle → empty set (the
    caller then skips warming entirely rather than falling back to the
    universe-wide 1,491, which is the whole point of this scope-down).
    """
    row = conn.execute("SELECT MAX(cycle_ts) FROM watchlist_focus").fetchone()
    if row is None or row[0] is None:
        return frozenset()
    latest_cycle = int(row[0])
    rows = conn.execute(
        "SELECT venue, symbol FROM watchlist_focus "
        "WHERE cycle_ts = ? AND COALESCE(trade_eligible, 1) = 1",
        (latest_cycle,),
    ).fetchall()
    return frozenset((str(r[0]), str(r[1])) for r in rows)


def capital_tradeable_symbols() -> frozenset[str]:
    """SSOT-derived union of every symbol a REGISTERED Capital strategy trades.

    Capital bars 429-storm fix (PRIORITY half): read each ``STRATEGY_REGISTRY``
    entry whose ``metadata.venue == "capital"`` and pull its module-level
    ``SUPPORTED_SYMBOLS`` / ``BASKET_SYMBOLS`` frozenset via introspection — NOT
    a hand-maintained duplicate list (would drift the moment a strategy's symbol
    set changes). Used only to ORDER the static-ground walk (tradeable-first);
    it narrows nothing — every active Capital instrument (incl. the untradeable
    exotic-FX/commodity tail) is still walked (flow_not_block).
    """
    out: set[str] = set()
    for cls in STRATEGY_REGISTRY.values():
        if cls.metadata.venue != "capital":
            continue
        mod = inspect.getmodule(cls)
        syms = getattr(mod, "SUPPORTED_SYMBOLS", None) or getattr(
            mod, "BASKET_SYMBOLS", None
        )
        if syms:
            out.update(syms)
    return frozenset(out)


async def _prefetch_alpaca_multi(
    active: list[Any],
    *,
    resolutions: tuple[str, ...],
    alpaca_adapter: Any,
    limit: int,
) -> dict[str, dict[str, list[Bar]]]:
    """Batch-prefetch the Alpaca exchange-fallback for every resolution (A2).

    Root cause: Yahoo (PRIMARY) is IP-throttled under the full-universe walk, so
    nearly every Alpaca-venue symbol falls through to the single-symbol exchange
    fallback — ~1,491 individual ``/v2/stocks/{symbol}/bars`` requests dumped onto
    the 200 req/min IEX cap. This issues ONE chunked multi-symbol request per
    resolution instead (``fetch_alpaca_bars_multi``, ~100 symbols/request — ~1,491
    symbols collapse to ~15 requests total across the 3 base resolutions).
    ``wait_for_token=True``: this off-tick pass has no cadence deadline, so a
    drained bucket is WAITED OUT (real wait) rather than proceeding token-less
    into a 429 (WAIT-NOT-DROP). Returns ``{resolution: {symbol: [Bar,...]}}``; an
    unsupported resolution / no adapter → empty inner dict (graceful, mirrors the
    single-fetch skip).
    """
    cache: dict[str, dict[str, list[Bar]]] = {}
    if alpaca_adapter is None:
        return cache
    alpaca_symbols = sorted(
        {inst.symbol for inst in active if inst.venue == "alpaca"}
    )
    if not alpaca_symbols:
        return cache
    for interval in resolutions:
        if interval not in ALPACA_TIMEFRAME_BY_INTERVAL:
            continue  # unsupported resolution for Alpaca — single-fetch path skips it too
        try:
            cache[interval] = await fetch_alpaca_bars_multi(
                alpaca_adapter, alpaca_symbols,
                bar_interval=interval, limit=limit,
                wait_for_token=True,
            )
        except Exception as exc:  # noqa: BLE001 — one bad resolution never aborts the walk
            logger.debug(
                "[ground] alpaca multi-prefetch %s failed (%d symbols): %r",
                interval, len(alpaca_symbols), exc,
            )
    return cache


async def ingest_static_ground_bars(
    conn: sqlite3.Connection,
    *,
    resolutions: tuple[str, ...] = STATIC_GROUND_RESOLUTIONS,
    parallel: int = STATIC_GROUND_PARALLEL_DEFAULT,
    total_timeout_sec: float = STATIC_GROUND_TOTAL_TIMEOUT_SEC,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
    gpt_client_factory: Any = None,
    limit: int = 240,
    warm_resolutions: tuple[str, ...] = (),
    now_ts: int | float | None = None,
) -> dict[str, Any]:
    """Fetch + persist Yahoo multi-resolution bars for EVERY active instrument.

    Walks ``read_active_universe`` (is_active=1) — the WHOLE active universe, not
    the focus subset — and fetches each instrument's bars at every ``resolution``
    via ``fetch_bars_one`` (Yahoo PRIMARY; exchange path is its throttled
    fallback). Concurrency is Semaphore-capped at ``parallel`` and the whole fan-
    out is bounded by ``total_timeout_sec`` (degrade-never-halt: partial work is
    persisted, the rest retries next cycle). A per-symbol fetch error is swallowed
    so one bad ticker never aborts the walk.

    Reuses the within-period Yahoo frame cache in ``_yahoo_bars`` (keyed
    ``(venue, symbol, bar_interval)`` per period), so a symbol the hot path already
    pulled this period is a cache hit here — no double fetch. flow_not_block: this
    only WIDENS observation; it gates nothing.

    A2 BATCH fix: before the per-instrument walk, every Alpaca-venue symbol's
    exchange-fallback bars are PRE-FETCHED in bulk (``_prefetch_alpaca_multi`` —
    ~15 chunked multi-symbol requests instead of up to ~1,491 single-symbol
    requests). Yahoo stays PRIMARY and unchanged (tried first, per-symbol,
    inside ``fetch_bars_one``); the prefetched cache is only consulted on a
    Yahoo miss for an ``alpaca`` symbol, replacing what used to be an individual
    exchange request. Per-symbol recency semantics are unaffected — a symbol
    absent from Alpaca's batch response still degrades to ``[]`` exactly like a
    single-fetch miss (no symbol is silently dropped).

    Session pre-open WARM (#66, Jin "장 열기 전부터 거래가능 바 알아서 채워야"): if
    ``warm_resolutions`` is non-empty, every instrument that BOTH (a)
    ``session_warm_active`` flags as open-imminent (Capital indices + Alpaca US
    equities in their ``[open - WARM_LEAD_MIN, close)`` window — never
    crypto/FX, see the predicate) AND (b) is a TRADE-ELIGIBLE FOCUS name
    (``warm_eligible_symbols`` — the ~150-name ``watchlist_focus`` set) ALSO
    gets those resolutions (1m) fetched, so its bars are fresh AT the cash open
    instead of 0.5h-stale (which the recency gate would skip). A2 SCOPE fix:
    equity strategies are all ``1D`` — nothing consumes universe-wide 1m bars,
    so restricting (b) to the actually-tradeable FOCUS set (not the ~1,491
    universe-wide open-imminent names) drops the single biggest wasted fetch
    without losing any consumer (OKX crypto is never warm-scoped here —
    ``session_warm_active`` already excludes it). DATA-ONLY: warming pre-fills
    stored bars; entry / sizing / exit are untouched. An overlap with a base
    resolution is skipped (no double fetch). ``warm_resolutions=()`` (the
    default / kill-switch) = warming OFF. ``now_ts`` (UTC epoch) drives the
    warm-window check (defaults to ``time.time()``).

    Returns ``{"instruments": K, "bars": N, "timed_out": bool,
    "warm_instruments": W, "warm_bars": M}``.
    """
    active = read_active_universe(conn)
    if not active:
        return {"instruments": 0, "bars": 0, "timed_out": False,
                "warm_instruments": 0, "warm_bars": 0}

    # Capital bars 429-storm fix (PRIORITY half): schedule the tradeable Capital
    # set (GOLD/US100/UK100/... — what registered strategies actually trade)
    # FIRST, so it wins the semaphore + bars-pacing bucket ahead of the exotic
    # FX/commodity tail (SEKMXN/HKDTRY/...) that Yahoo has no series for. A
    # stable sort (Python's ``sorted`` is stable) preserves the DB's existing
    # relative order within each group. ADD-only reordering — every active
    # instrument (all venues, incl. the exotic tail) is still walked
    # (flow_not_block: this prioritizes, never narrows, observation).
    _capital_tradeable = capital_tradeable_symbols()
    if _capital_tradeable:
        active = sorted(
            active,
            key=lambda inst: not (
                inst.venue == "capital" and inst.symbol in _capital_tradeable
            ),
        )

    warm_set = frozenset(warm_resolutions)
    warm_now = int(now_ts) if now_ts is not None else int(time.time())
    # A2 SCOPE: the warm pass is restricted to trade-eligible FOCUS names — the
    # only symbols anything downstream actually consumes 1m bars for.
    warm_eligible = warm_eligible_symbols(conn) if warm_set else frozenset()
    # A2 BATCH: pre-fetch the Alpaca exchange-fallback for every base resolution
    # in ONE chunked pass (Yahoo stays PRIMARY, tried per-symbol as before).
    alpaca_multi_cache = await _prefetch_alpaca_multi(
        active, resolutions=resolutions,
        alpaca_adapter=alpaca_adapter, limit=limit,
    )
    sem = asyncio.Semaphore(max(1, parallel))
    persisted_instruments: set[str] = set()
    warm_instruments: set[str] = set()
    total_bars = 0
    warm_bars = 0

    async def _fetch_interval(
        venue: str, symbol: str, asset_class: str, interval: str
    ) -> list[Any]:
        async with sem:
            try:
                return await fetch_bars_one(
                    venue, symbol, asset_class,
                    capital_session=capital_session,
                    alpaca_adapter=alpaca_adapter,
                    limit=limit, bar_interval=interval,
                    gpt_client_factory=gpt_client_factory,
                    alpaca_multi_cache=alpaca_multi_cache.get(interval),
                    # Capital bars 429-storm fix: off-tick walk has no cadence
                    # deadline, so it WAITS for a real token (wait-not-drop)
                    # instead of bursting past Capital's ~10 req/s demo ceiling.
                    wait_for_token=True,
                )
            except Exception as exc:  # noqa: BLE001 — one bad ticker never aborts
                logger.debug(
                    "[ground] %s:%s/%s fetch failed: %r",
                    venue, symbol, interval, exc,
                )
                return []

    async def _one(inst: Any) -> None:
        nonlocal total_bars, warm_bars
        venue = inst.venue
        symbol = inst.symbol
        asset_class = inst.asset_class
        # #84 equity data-fetch gate: when the US-equity cash market is fully
        # closed (overnight / weekend) AND no #66 pre-open warm window is active,
        # SKIP the whole resolution walk for this equity — the closed market
        # produces no new bars, so fetching only storms Alpaca/yfinance (the
        # weekend 429 flood). OKX crypto (24/7) + Capital FX/index/commodity are
        # never gated (equity_fetch_active returns True for them). flow_not_block:
        # this is "don't fetch absent data", not a trade block; RTH/warm resumes it.
        # Uses ``warm_now`` — the SAME clock as the #66 warm window below (the
        # ``now_ts`` param, else ``time.time()``) — so the gate and the warm OR
        # never disagree on "what time is it" (a 13:10 pre-open warm survives).
        if not equity_fetch_active(venue, asset_class, symbol, warm_now):
            return
        out: list[Any] = []
        for interval in resolutions:
            out.extend(await _fetch_interval(venue, symbol, asset_class, interval))
        # Pre-open WARM: ADD the warm resolutions for open-imminent symbols only
        # (skip any already pulled as a base resolution — no double fetch). A2
        # SCOPE: additionally restricted to the trade-eligible FOCUS set — a
        # universe-wide open-imminent name that the bot cannot act on (not in
        # ``watchlist_focus``) gets no 1m warm fetch (equity strategies are all
        # 1D; nothing consumes universe-wide 1m bars — this was the single
        # biggest wasted fetch in the #66 warm pass).
        if (
            warm_set
            and (venue, symbol) in warm_eligible
            and session_warm_active(venue, asset_class, symbol, warm_now)
        ):
            for interval in warm_set:
                if interval in resolutions:
                    continue
                out.extend(await _fetch_interval(venue, symbol, asset_class, interval))
        if out:
            # persist_bars is a sync DB write; cheap (INSERT OR REPLACE on the PK).
            # GUARD (live probe 2026-06-25): a real Yahoo frame can carry a malformed
            # candle (NULL/NaN close → IntegrityError on the NOT NULL bars.close).
            # An unguarded raise here aborted the WHOLE gather and poisoned the
            # shared txn. Wrap + SAVEPOINT-rollback so one bad batch is skipped and
            # the walk + the already-persisted instruments survive (degrade-never-halt).
            try:
                conn.execute("SAVEPOINT ground_persist")
                n = persist_bars(conn, out)
                conn.execute("RELEASE ground_persist")
            except Exception as exc:  # noqa: BLE001 — one bad batch never aborts the walk
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK TO ground_persist")
                    conn.execute("RELEASE ground_persist")
                logger.debug(
                    "[ground] %s:%s persist skipped (bad batch): %r",
                    venue, symbol, exc,
                )
                return
            if n:
                total_bars += n
                persisted_instruments.add(f"{venue}:{symbol}")
                warm_n = sum(1 for b in out if b.bar_interval in warm_set)
                if warm_n:
                    warm_bars += warm_n
                    warm_instruments.add(f"{venue}:{symbol}")

    tasks = [asyncio.ensure_future(_one(inst)) for inst in active]
    timed_out = False
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=total_timeout_sec)
    except TimeoutError:
        timed_out = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if total_bars:
        conn.commit()
    logger.info(
        "[ground] static-ground bars: %d instrument(s) / %d bars / "
        "resolutions=%s%s%s",
        len(persisted_instruments), total_bars, ",".join(resolutions),
        (f" + warm {len(warm_instruments)} inst / {warm_bars} bars "
         f"({','.join(warm_set)})" if warm_set else ""),
        " (timed out — partial, retries next cycle)" if timed_out else "",
    )
    return {
        "instruments": len(persisted_instruments),
        "bars": total_bars,
        "timed_out": timed_out,
        "warm_instruments": len(warm_instruments),
        "warm_bars": warm_bars,
    }


def _persist_ticker_ground(
    conn: sqlite3.Connection,
    *,
    inst: Any,
    now_ts: int,
    has_sentiment: bool,
    has_event: bool,
    ground: dict[str, Any],
) -> None:
    """Upsert one per-ticker ground row (LWW on instrument_id)."""
    conn.execute(
        "INSERT OR REPLACE INTO ticker_ground (instrument_id, venue, symbol, "
        "underlying_group_id, asset_class, updated_ts, has_sentiment, has_event, "
        "ground_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            inst.instrument_id, inst.venue, inst.symbol,
            inst.underlying_group_id, inst.asset_class, now_ts,
            1 if has_sentiment else 0, 1 if has_event else 0,
            json.dumps(ground, separators=(",", ":")),
        ),
    )


def refresh_ticker_ground(
    conn: sqlite3.Connection,
    *,
    cache: Any,
    now_ts: int | None = None,
) -> int:
    """Materialize per-active-ticker sentiment/event ground (built on the fuser).

    For EVERY active instrument, run the EXISTING ``fuse_evidence`` over its group
    against the live ``AltDataCache`` and persist a per-ticker ``ticker_ground``
    row. A ticker whose group HAS a fresh covering source (news_sentiment /
    crypto_fg / okx_funding / fred_macro / cftc_cot) carries that fused evidence
    (``has_sentiment``/``has_event`` flags + the evidence dict); a ticker with NO
    covering source gets a graceful EMPTY row (so the candidate sweep can still
    enumerate it without a special case). No new source is invented — this is the
    fuser, applied to the WHOLE active universe instead of the focus subset.

    ``cache=None`` (smoke/replay) → no-op (returns 0). EVIDENCE only: the written
    rows feed the candidate sweep as SIGNAL ground; nothing here sizes / blocks /
    exits / halts (flow_not_block). Returns the number of rows written.
    """
    if cache is None:
        return 0
    active = read_active_universe(conn)
    if not active:
        return 0
    ts = now_ts if now_ts is not None else int(time.time())
    written = 0
    # Single explicit transaction over the whole ~1882-row walk (event-loop-stall
    # fix, adversarial review 2026-06-25): under autocommit each INSERT would emit
    # its own WAL frame (~1882 fsyncs) and block the loop — the same hazard
    # quote_writer wraps. fuse_evidence is pure in-memory + _persist_ticker_ground
    # is a single INSERT, so NO await is taken inside the txn → it stays atomic
    # w.r.t. the event loop (the tick body's own BEGIN can never interleave).
    conn.execute("BEGIN")
    try:
        for inst in active:
            try:
                _hint, _conf, evidence = fuse_evidence(
                    inst.underlying_group_id, cache
                )
            except Exception as exc:  # noqa: BLE001 — one bad group never aborts
                logger.debug(
                    "[ground] fuse_evidence failed for %s: %r",
                    inst.underlying_group_id, exc,
                )
                evidence = {}
            # "sentiment" ground = any directional alt-data evidence present for the
            # group (the fused scores/sources). "event" ground = a discrete catalyst
            # surfaced in the evidence (regime label / news headline). Both are pure
            # presence flags so the sweep can cheaply triage covered vs empty.
            has_sentiment = bool(evidence)
            has_event = bool(evidence.get("label") or evidence.get("news_headline"))
            _persist_ticker_ground(
                conn, inst=inst, now_ts=ts,
                has_sentiment=has_sentiment, has_event=has_event,
                ground=evidence,
            )
            written += 1
        conn.execute("COMMIT")
    except Exception:
        with contextlib.suppress(Exception):
            conn.execute("ROLLBACK")
        raise
    logger.info("[ground] per-ticker sentiment/event ground: %d ticker(s)", written)
    return written


def read_ticker_ground(
    conn: sqlite3.Connection, instrument_id: str
) -> dict[str, Any] | None:
    """Read one ticker's static ground (the candidate-sweep ② input), or None.

    Returns ``{"has_sentiment": bool, "has_event": bool, "updated_ts": int,
    "ground": dict}`` for a known instrument, ``None`` if it has no ground row yet
    (graceful — the sweep treats an absent ground as "not yet covered").
    """
    row = conn.execute(
        "SELECT has_sentiment, has_event, updated_ts, ground_json "
        "FROM ticker_ground WHERE instrument_id = ?",
        (instrument_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        ground = json.loads(row[3]) if row[3] else {}
    except (ValueError, TypeError):
        ground = {}
    return {
        "has_sentiment": bool(row[0]),
        "has_event": bool(row[1]),
        "updated_ts": int(row[2]),
        "ground": ground,
    }
