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
import json
import logging
import sqlite3
import time
from typing import Any

from polaris.core.altdata.fuser import fuse_evidence
from polaris.core.data.ingest import persist_bars
from polaris.scripts._production_bars import fetch_bars_one
from polaris.scripts._production_layers import read_active_universe

logger = logging.getLogger(__name__)

# Static-ground bar resolutions. These are the "ground" timeframes a candidate
# sweep + technical read need: 1D (MA200 / regime warmup), 1H + 15m (intraday
# structure). 1m is INTENTIONALLY ABSENT — its Yahoo window is 7d (thousands of
# rows) and only the FOCUS subset (hot path) needs intra-minute freshness; bulk-
# pulling 1m for ~1882 tickers would be the heaviest fetch for the least ground
# value. A /debate calibration target, never hardcoded at the call site.
STATIC_GROUND_RESOLUTIONS: tuple[str, ...] = ("1D", "1H", "15m")

# Bulk-fill concurrency (Yahoo IP-block guard). Mirrors populate_capital_proxies'
# Semaphore + total-timeout pattern (startup-fix). Higher than the 8-wide hot-path
# fan-out because this is off the tick deadline, but bounded so the bulk Yahoo
# fan-out (1882 × 3 resolutions) cannot trip an IP block. Env-tunable.
STATIC_GROUND_PARALLEL_DEFAULT = 16
# Per-cycle wall-clock ceiling (degrade-never-halt): whatever completed is kept,
# the rest retries next cycle. The fill is incremental — the within-period frame
# cache means a re-walk only re-fetches symbols whose bar period rolled.
STATIC_GROUND_TOTAL_TIMEOUT_SEC = 600.0
# How often the background producer re-walks the active universe. 1D bars roll
# once/day and the Yahoo frame cache holds intraday frames for their period, so a
# slow re-walk keeps coverage warm without re-hammering Yahoo. The FIRST walk runs
# immediately at startup (the one-time fill).
STATIC_GROUND_REFRESH_SEC = 900.0


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

    Returns ``{"instruments": K, "bars": N, "timed_out": bool}``.
    """
    active = read_active_universe(conn)
    if not active:
        return {"instruments": 0, "bars": 0, "timed_out": False}

    sem = asyncio.Semaphore(max(1, parallel))
    persisted_instruments: set[str] = set()
    total_bars = 0

    async def _one(inst: Any) -> None:
        nonlocal total_bars
        venue = inst.venue
        symbol = inst.symbol
        asset_class = inst.asset_class
        out: list[Any] = []
        for interval in resolutions:
            async with sem:
                try:
                    bars = await fetch_bars_one(
                        venue, symbol, asset_class,
                        capital_session=capital_session,
                        alpaca_adapter=alpaca_adapter,
                        limit=limit, bar_interval=interval,
                        gpt_client_factory=gpt_client_factory,
                    )
                except Exception as exc:  # noqa: BLE001 — one bad ticker never aborts
                    logger.debug(
                        "[ground] %s:%s/%s fetch failed: %r",
                        venue, symbol, interval, exc,
                    )
                    continue
            if bars:
                out.extend(bars)
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
        "resolutions=%s%s",
        len(persisted_instruments), total_bars, ",".join(resolutions),
        " (timed out — partial, retries next cycle)" if timed_out else "",
    )
    return {
        "instruments": len(persisted_instruments),
        "bars": total_bars,
        "timed_out": timed_out,
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
