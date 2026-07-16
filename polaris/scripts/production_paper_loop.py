"""Day 8 production paper loop — fixture-mode escape.

Spec: ``vault/_NOW.md`` Day 8 + functional review #82 + cumulative coherence
#81. Replaces ``smoke_paper_loop.run_smoke`` for paper runs by wiring every
layer to its real producer (Layer 0 universe refresh, Layer 1 bar ingest,
Layer 2 G1→G8, Layer 6 regime/recalc/swap, Layer 7 fence + circuit breaker
+ idempotent order keys). See :mod:`_production_pipeline` for the per-signal
G1-G7 driver and the close-path mark-to-market helpers.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from polaris.core.altdata.binance_deriv import BinanceDerivCollector
from polaris.core.altdata.cache import AltDataCache
from polaris.core.altdata.cftc_cot import CFTCCotCollector
from polaris.core.altdata.coinglass import CoinglassCollector
from polaris.core.altdata.crypto_fg import CryptoFearGreedCollector
from polaris.core.altdata.defillama_stables import DefiLlamaStablesCollector
from polaris.core.altdata.edgar_events import EdgarEventsCollector
from polaris.core.altdata.finnhub_earnings import FinnhubEarningsCollector
from polaris.core.altdata.fred_macro import FredMacroCollector
from polaris.core.altdata.myfxbook import MyfxbookCollector
from polaris.core.altdata.news_sentiment import NewsSentimentCollector
from polaris.core.altdata.okx_funding import OKXFundingCollector
from polaris.core.data.quote_writer import QuoteTickWriter
from polaris.core.data.technical_store_writer import TechnicalStoreWriter
from polaris.core.isolation.allocator_fence import (
    get_process_fence,
    reset_process_fence,
)
from polaris.core.isolation.circuit_breaker import resume_stale_permanent_halts
from polaris.core.lifecycle.recover import (
    hydrate_last_entry_by_key,
    hydrate_open_positions,
    reconcile_venue_positions,
)
from polaris.core.pipeline.agents._gpt_client import GPT_P0_MODEL, default_gpt_factory
from polaris.core.pipeline.config import ai_free_mode, ai_judge_mode
from polaris.core.sizing.ladder import materialize_credits, release_stale_draws
from polaris.core.ticks.config import tick_engine_enabled
from polaris.logging_config import DEFAULT_LOG_FILE, setup_polaris_logging
from polaris.scripts._production_atr import strategy_timeframe, timeframe_anchor_atr_pct
from polaris.scripts._production_boot_classes import (
    boot_hydrate_and_bootstrap_strategy_class,
)
from polaris.scripts._production_layers import (
    ALPACA_REFRESH_SEC,
    CAPITAL_REFRESH_SEC,
    OKX_REFRESH_SEC,
    refresh_alpaca_universe_once,
    refresh_capital_universe_once,
    refresh_focus_watchlist,
    refresh_okx_universe_once,
)
from polaris.scripts._production_probe_attach import wire_probe_sidecar
from polaris.scripts._production_retention import retention_producer
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick import (
    FOCUS_CYCLE_TARGET,
    _all_strategies,
    _evaluate_swaps,
    _is_finite_signal,
    _lookup_regime,
    _run_tick,
    _strategies_by_timeframe,
)
from polaris.scripts._production_tick_engine import (
    TickEngineState,
    run_tick_decision_loop,
)
from polaris.scripts._production_ws import (
    resubscribe_ws_clients,
    start_ws_producers,
)
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.scripts._smoke_real_roundtrip import resolve_okx_base_url
from polaris.scripts._static_ground import (
    SESSION_WARM_CADENCE_SEC,
    SESSION_WARM_RESOLUTIONS_DEFAULT,
    STATIC_GROUND_PARALLEL_DEFAULT,
    STATIC_GROUND_REFRESH_SEC,
    TICKER_GROUND_REFRESH_SEC,
    ingest_static_ground_bars,
    refresh_ticker_ground,
)
from polaris.scripts._virtual_account import resolve_real_roundtrip
from polaris.scripts.reconcile_alpaca_zombies import (
    reconcile_alpaca_qty_drift,
    reconcile_alpaca_venue_drift,
)
from polaris.scripts.reconcile_orphans import (
    flatten_venue_eod,
    reconcile_venue_orphans,
)
from polaris.storage.db_writer import DBWriter, dbwriter_enabled
from polaris.storage.schema import connect, connect_ro, init_db
from polaris.storage.schema_marketdata import init_marketdata_db, marketdata_db_path_for
from polaris.venues.alpaca import AlpacaAdapter, resolve_alpaca_credentials
from polaris.venues.capital.adapter import CapitalAdapter
from polaris.venues.capital.session import CapitalSession
from polaris.venues.okx import OKXAdapter, fetch_instruments
from polaris.venues.okx.adapter import DEMO_HEADERS as OKX_DEMO_HEADERS

logger = logging.getLogger(__name__)

__all__ = [
    "FOCUS_CYCLE_TARGET",
    "ProdLoopState",
    "main",
    "materialize_credits",
    "persist_altdata_snapshot",
    "reconcile_alpaca_qty_drift",
    "reconcile_alpaca_venue_drift",
    "release_stale_draws",
    "run_production_paper_loop",
    "_all_strategies",
    "_altdata_producer",
    "_evaluate_swaps",
    "_is_finite_signal",
    "_lookup_regime",
    "_run_tick",
    "_strategies_by_timeframe",
]

DEFAULT_TICK_SEC = 5.0
DEFAULT_DURATION_SEC = 60.0


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _build_okx_adapter() -> OKXAdapter | None:
    """Build an OKX demo adapter from ``OKX_DEMO_*`` env, or ``None`` if unset."""
    api_key = os.environ.get("OKX_DEMO_API_KEY", "")
    secret = os.environ.get("OKX_DEMO_SECRET", "")
    passphrase = os.environ.get("OKX_DEMO_PASSPHRASE", "")
    if not (api_key and secret and passphrase):
        return None
    base_url = resolve_okx_base_url(os.environ.get("OKX_DEMO_BASE"))
    return OKXAdapter(
        api_key=api_key, secret=secret, passphrase=passphrase, base_url=base_url,
    )


async def _load_okx_constraints(adapter: OKXAdapter) -> None:
    """Best-effort: fetch OKX SPOT dealing rules → install on the adapter.

    The constraint cache (lotSz / tickSz) lets the entry leg round sz/px before
    submit, preventing the precision-shaped OKX 400. A failure here is logged
    and swallowed (flow_not_block): the order path falls back to the prior
    un-rounded behaviour rather than blocking startup.
    """
    try:
        constraints = await fetch_instruments(
            base_url=adapter.base_url,
            client=adapter.client,
            extra_headers=OKX_DEMO_HEADERS if adapter.demo else None,
        )
        adapter.set_instrument_constraints(constraints)
        logger.info("[okx] loaded %d SPOT instrument constraints", len(constraints))
    except Exception:
        logger.exception(
            "[okx] instrument-constraint load failed — sz/px rounding disabled "
            "(entry still flows un-rounded)"
        )


async def _okx_signed_health_check(adapter: OKXAdapter) -> bool:
    """Probe OKX SIGNED auth once at boot → True healthy, False on auth failure.

    The single live blocker behind a 0-order weekend was a SILENT OKX demo
    credential regression (every signed call — orders AND ``/account/balance`` —
    returned 50105 / HTTP 401), while boot only exercised the PUBLIC (unsigned)
    market-data path, so the bot looked "healthy" while every real order rejected
    for hours. This calls ``fetch_balance`` (a signed call) once and, on an auth
    failure, emits a loud ``OKX AUTH FAIL`` banner naming the credential remedy.

    Pure DIAGNOSTIC — never blocks boot, never throttles, never cancels an order
    (flow_not_block): a failed probe only LOGS. Returns True on success, False on
    any failure (best-effort — never raises into the boot path).
    """
    try:
        await adapter.fetch_balance("USDT")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            logger.error(
                "[loop] 🔴 OKX AUTH FAIL — boot signed health check rejected "
                "(HTTP %d / likely 50105). The OKX_DEMO API key/passphrase is "
                "invalid/rotated/expired: ALL real OKX orders will REJECT until "
                "the credentials are refreshed (this is NOT a strategy/code "
                "fault). Refresh OKX_DEMO_* in .env and restart.",
                exc.response.status_code,
            )
            return False
        logger.warning(
            "[loop] OKX boot health check HTTP %d (non-auth) — orders may still "
            "flow; continuing", exc.response.status_code,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — best-effort probe, never blocks boot
        logger.warning(
            "[loop] OKX boot health check failed %r — could not confirm signed "
            "auth; continuing (orders will surface any real auth reject)", exc,
        )
        return False
    logger.info("[loop] OKX signed auth health check OK (balance reachable)")
    return True


def _build_alpaca_adapter() -> AlpacaAdapter | None:
    """Build an Alpaca PAPER adapter from creds, or ``None`` if unset (Track C).

    Mirrors ``_build_okx_adapter``: one adapter reused across ticks for the real
    equity round-trip. PAPER-only (the adapter refuses any live trade host).
    """
    api_key, secret = resolve_alpaca_credentials()
    if not (api_key and secret):
        return None
    return AlpacaAdapter(api_key=api_key, secret=secret)


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to ``default`` on absent/unparseable."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def alpaca_reconcile_import_enabled() -> bool:
    """True unless ``POLARIS_RECONCILE_VENUE_IMPORT=0`` — Alpaca ledger-venue
    re-sync (audit1 P0-4 ②).

    The startup VENUE reconcile-import (``reconcile_venue_positions``) was
    gated OFF by default (2026-06-01) behind 3 documented bugs: (1) a
    cross-event-loop crash — FIXED (P1-6: ``reconcile_venue_positions`` is
    async and awaits adapters on the caller's own loop); (2)
    imported-position insta-close — the exit engine reads ``positions``/
    ``fills`` directly (``load_active_position_rows``), not any uninitialised
    FSM, so an imported row recalcs exactly like a hydrated one; (3) a Capital
    ``deal_id=None`` close-error loop — Capital-SPECIFIC, so this flip scopes
    to ALPACA ONLY (``capital_adapter`` stays unwired at the call site) —
    Capital's own gap is untouched. ADOPT is the default (flow_not_block: a
    reconciled venue holding is put under exit-engine management, never
    liquidated) — 6 Alpaca positions ($73.6k: BB/ABBV/RMBI/WHG/TPCS/SEVN) sat
    venue-untracked, pinning buying power. Kill via ``=0`` if a regression
    surfaces.
    """
    return os.environ.get("POLARIS_RECONCILE_VENUE_IMPORT") != "0"


def _reconcile_import_atr_anchor(
    conn: sqlite3.Connection, instrument_id: str, now_ts: int,
    *, md_conn: sqlite3.Connection | None = None,
) -> tuple[float, str] | None:
    """[P0-5] Entry-time ATR-anchor resolver injected into
    ``reconcile_venue_positions`` — lives in ``scripts`` (not ``core``) so
    ``polaris.core.lifecycle.recover`` stays free of the ``scripts`` import
    (layering rail, ``test_core_layering.py``). Same tf→1m fallback chain the
    normal entry-stamp path uses; ``_reconcile_import`` is unregistered so
    ``strategy_timeframe`` resolves "1m" (measurement-only, graceful).

    ``md_conn`` (storage-split round 4 fix): bars is marketdata-domain, but
    ``recover.py``'s ``AtrAnchorFn`` contract always calls this with the
    TRADING conn positionally (``conn``) — the call site binds ``md_conn`` via
    ``functools.partial(_reconcile_import_atr_anchor, md_conn=state.md_conn)``
    so this keyword wins over the positional trading ``conn`` when wired.
    ``None`` falls back to ``conn`` (byte-identical for direct callers/tests).
    """
    return timeframe_anchor_atr_pct(
        md_conn if md_conn is not None else conn, instrument_id=instrument_id,
        timeframe=strategy_timeframe("_reconcile_import"), now_ts=now_ts,
    )


# ---------------------------------------------------------------------------
# Layer 0 — background producer
# ---------------------------------------------------------------------------


async def _layer0_producer(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    stop_evt: asyncio.Event,
    focus_conn: sqlite3.Connection | None = None,
    momentum_conn: sqlite3.Connection | None = None,
    md_focus_conn: sqlite3.Connection | None = None,
) -> None:
    """OKX (5min) + Capital (10min) + Alpaca (10min) refresh + focus recompute.

    Alpaca (Track C / US-equity) is an added producer; OKX/Capital cadence and
    behavior are unchanged. ``refresh_alpaca_universe_once`` is smoke-safe (no
    creds → 0 active, no rows persisted).

    STALL fix (#74): ``refresh_focus_watchlist`` is a PURE-SYNC, multi-second call
    (EntranceJudge over every active row + a large ``persist_focus`` executemany).
    Running it INLINE on this single event loop froze the loop for 6-33s every 15s
    → the tick task was starved (live: "[tick-engine] STALL gap=7.31s … shared-conn
    blocker?"). It is now OFFLOADED via ``asyncio.to_thread`` so the loop keeps
    cycling. The offloaded call uses a DEDICATED ``focus_conn`` (NOT the loop-owned
    ``conn``): a single ``sqlite3.Connection`` must never be touched from two
    threads at once, and ``_layer0_producer`` awaits each refresh before the next,
    so only ONE worker ever touches ``focus_conn`` at a time (WAL: 1-writer safe).
    The async universe refreshes (httpx I/O) stay on the loop (they already yield).

    Same STALL class, second instance: each ``refresh_*_universe_once`` runs an
    UNBOUNDED synchronous ``bars`` scan for the momentum-z shadow (already 1M+
    rows on the live Alpaca universe — see ``_momentum_z_shadow_async``). That
    scan is offloaded the same way, onto the DEDICATED READ-ONLY
    ``momentum_conn`` sidecar — never the loop-owned ``conn`` or ``focus_conn``.
    Every refresh in this function runs sequentially (awaited in turn), so only
    ONE worker ever touches ``momentum_conn`` at a time.

    flow_not_block / 9-stack untouched: only WHERE focus/momentum run changes,
    never which symbol is watched, ranked, or how it is sized.
    """
    fconn = focus_conn if focus_conn is not None else conn
    await refresh_okx_universe_once(conn, momentum_conn=momentum_conn)
    state.universe_refreshes += 1
    await refresh_capital_universe_once(conn, momentum_conn=momentum_conn)
    state.capital_refreshes += 1
    await refresh_alpaca_universe_once(conn, momentum_conn=momentum_conn, md_conn=state.md_conn)
    state.alpaca_refreshes += 1
    await _refresh_focus_offloaded(fconn, state, md_focus_conn=md_focus_conn)
    last_okx = time.monotonic()
    last_capital = time.monotonic()
    last_alpaca = time.monotonic()
    while not stop_evt.is_set():
        await asyncio.sleep(15.0)
        now = time.monotonic()
        if now - last_okx >= OKX_REFRESH_SEC:
            await refresh_okx_universe_once(conn, momentum_conn=momentum_conn)
            state.universe_refreshes += 1
            last_okx = now
        if now - last_capital >= CAPITAL_REFRESH_SEC:
            await refresh_capital_universe_once(conn, momentum_conn=momentum_conn)
            state.capital_refreshes += 1
            last_capital = now
        if now - last_alpaca >= ALPACA_REFRESH_SEC:
            await refresh_alpaca_universe_once(
                conn, momentum_conn=momentum_conn, md_conn=state.md_conn
            )
            state.alpaca_refreshes += 1
            last_alpaca = now
        await _refresh_focus_offloaded(fconn, state, md_focus_conn=md_focus_conn)


async def _refresh_focus_offloaded(
    focus_conn: sqlite3.Connection,
    state: ProdLoopState,
    *,
    md_focus_conn: sqlite3.Connection | None = None,
) -> None:
    """Run the blocking focus refresh on a worker thread (STALL-safe, #74).

    Uses the DEDICATED ``focus_conn`` and the DEDICATED ``focus_probe_conn`` sidecar
    (both thread-confined to this single offloaded path) so no ``sqlite3.Connection``
    is ever shared with the live tick loop. ``quote_writer`` / ``altdata_cache`` are
    in-mem read-only here (lean inputs); their reads are snapshot-safe (atomic
    ``list(...)`` copies — #74) + fail-soft.

    ``md_focus_conn`` (storage-split, 2026-07-14): a DEDICATED marketdata conn,
    thread-confined to this same offloaded worker — never ``state.md_conn``,
    which the main tick thread owns (sharing one ``sqlite3.Connection`` across
    two threads is the #74/#88 hazard). Passed through as ``refresh_focus_watchlist``'s
    ``md_conn`` so the final ``watchlist_focus`` persist + the candidate-sweep
    bars/ticker_ground reads land on the SAME file ``get_focus_targets`` reads
    from (``state.md_conn``), instead of silently falling back onto the trading
    ``focus_conn`` (writer/reader split-brain — zero focus, zero trades on a
    fresh init).

    2nd-defense (#74): the offloaded body is wrapped so an UNHANDLED error in the
    worker can never propagate out of ``layer0_task`` and kill it permanently — a
    dead Layer-0 task would freeze focus → stale watchlist → trade degrade. On error
    we log + continue; the next 15s tick retries (flow_not_block: never a hard stop).
    """
    try:
        await asyncio.to_thread(
            refresh_focus_watchlist,
            focus_conn,
            probe_conn=getattr(state, "focus_probe_conn", None),
            quote_writer=getattr(state, "quote_writer", None),
            altdata_cache=getattr(state, "altdata_cache", None),
            md_conn=md_focus_conn,
        )
    except Exception:  # noqa: BLE001 — must NOT kill layer0_task (focus must survive)
        logger.exception(
            "[layer0] offloaded focus refresh failed — skipping this cycle, "
            "retrying next tick (focus task stays alive)"
        )


# ---------------------------------------------------------------------------
# Layer 6 — alt-data EVIDENCE background producer (#6)
# ---------------------------------------------------------------------------


def persist_altdata_snapshot(
    conn: sqlite3.Connection,
    *,
    ts: int,
    source: str,
    asset_class: str,
    payload: dict[str, Any],
) -> None:
    """Append a raw alt-data EVIDENCE snapshot row (idempotent on ``(ts, source)``).

    Read-only audit context. This table is NEVER consulted by sizing / blocking
    / exit / halt logic — it backs the dashboard + G3/G7 evidence trail only.
    """
    conn.execute(
        "INSERT OR REPLACE INTO altdata_snapshot (ts, source, asset_class, payload_json) "
        "VALUES (?, ?, ?, ?)",
        (int(ts), source, asset_class, json.dumps(payload, separators=(",", ":"))),
    )


def _default_altdata_collectors(
    conn: sqlite3.Connection | None = None,
    *,
    db_writer: DBWriter | None = None,
) -> list[Any]:
    """The live alt-data EVIDENCE collectors (keyless/keyed graceful-skip).

    Every collector is registered; a missing key/creds yields a graceful ``{}``
    with NO network call (no throttle). FRED uses ``FRED_API_KEY``; OKX funding +
    Binance derivatives are keyless public market data; alt.me F&G needs no key.
    Coinglass (``COINGLASS_API_KEY``) + MyFxBook (``MYFXBOOK_EMAIL`` /
    ``MYFXBOOK_PASSWORD``) are activation-ready and key/creds-gated — they sit
    inert (``{}``, no network) until Jin adds credentials, then activate with no
    code change. News reuses the Alpaca paper creds (no creds → graceful ``{}``,
    no network) and classifies headlines via an async GPT call on its own 15min
    cadence (in-loop GPT = 0 holds — off the per-tick hot path).

    ``conn`` (optional): when supplied it is threaded into
    ``NewsSentimentCollector`` as ``shadow_conn`` (frontgate-scan item #7's
    timestamp-audit + dedup SHADOW log) AND into the frontgate-scan feed trio
    below (``EdgarEventsCollector`` / ``FinnhubEarningsCollector`` /
    ``DefiLlamaStablesCollector``) as their writer-path ``conn`` — the SAME
    connection the bot process already writes with (RO active-universe read +
    their own raw-event/snapshot table persistence + G3/G4 proximity SHADOW
    tags). Behavior-0: every collector's returned evidence dict is unchanged
    either way; ``None`` keeps every prior call site byte-identical, and the
    EDGAR/Finnhub collectors simply return ``{}`` without a ``conn`` (no
    active-universe symbols to resolve).

    ``db_writer`` (db-writer-reader-split, opt-in, 2026-07-12): threaded the
    same way as ``conn`` above — each collector routes its own writer-path
    INSERT/UPSERT through the shared single-RW-conn writer when both ``conn``
    and ``db_writer`` are wired and the kill switch is enabled; ``None`` is
    byte-identical to the pre-migration direct-write path.
    """
    return [
        OKXFundingCollector(),
        BinanceDerivCollector(),
        CryptoFearGreedCollector(),
        FredMacroCollector(),
        CFTCCotCollector(),
        NewsSentimentCollector(shadow_conn=conn, shadow_db_writer=db_writer),
        CoinglassCollector(),
        MyfxbookCollector(),
        # frontgate-scan feeds (#1-3) — SEC EDGAR filings / Finnhub earnings
        # calendar / DefiLlama stablecoins. Pure EVIDENCE; behavior-0 (no
        # gating/sizing touchpoint this wave).
        EdgarEventsCollector(conn=conn, db_writer=db_writer),
        FinnhubEarningsCollector(conn=conn, db_writer=db_writer),
        DefiLlamaStablesCollector(conn=conn, db_writer=db_writer),
    ]


async def _altdata_producer(
    conn: sqlite3.Connection,
    *,
    cache: AltDataCache,
    state: ProdLoopState,
    stop_evt: asyncio.Event,
    collectors: list[Any] | None = None,
    poll_sec: float = 30.0,
    db_writer: DBWriter | None = None,
) -> None:
    """Refresh alt-data EVIDENCE collectors on each one's own TTL cadence.

    SIGNAL/EVIDENCE only. Each collector is re-fetched only after its own
    ``ttl_sec`` has elapsed (OKX funding 300s, F&G 1800s, FRED 3600s). On a
    successful non-empty fetch the payload updates the ``AltDataCache`` singleton
    and a snapshot row is persisted. On a collector error or empty result the
    LAST cache value is kept untouched (graceful — fewer evidence sources, never
    a throttle / halt). The loop exits promptly when ``stop_evt`` is set.
    """
    active = (
        collectors if collectors is not None
        else _default_altdata_collectors(conn, db_writer=db_writer)
    )
    last_fetch: dict[str, float] = {}
    while not stop_evt.is_set():
        now_mono = time.monotonic()
        for coll in active:
            name = coll.name
            ttl = float(getattr(coll, "ttl_sec", 0) or 0)
            prev = last_fetch.get(name)
            if prev is not None and (now_mono - prev) < ttl:
                continue
            try:
                payload = await coll.fetch()
            except Exception as exc:  # noqa: BLE001 — never let a collector halt the loop
                logger.warning(
                    "[altdata] collector %s raised (%r) — keeping last cache "
                    "(no throttle)", name, exc,
                )
                state.altdata_errors += 1
                last_fetch[name] = now_mono  # respect cadence even on error
                continue
            last_fetch[name] = now_mono
            if not payload:
                # Empty result = keyless/parse skip. Keep last cache value.
                continue
            cache.set(name, payload, ttl_sec=int(ttl) or 1, now_ts=time.time())
            asset_class = (getattr(coll, "asset_classes", ()) or ("",))[0]
            # Operational narrative (INFO): a SUCCESSFUL evidence refresh. Only
            # the failure path logged before (WARNING), so the brain's live
            # FRED/funding/binance intake was invisible. One line per fetched
            # source surfaces the collection cadence (log only — no behaviour).
            logger.info(
                "[altdata] %s refreshed asset=%s ttl=%ds",
                name, asset_class or "-", int(ttl),
            )
            try:
                # db-writer-reader-split (opt-in): fire-and-forget submit to the
                # shared single-RW-conn writer instead of writing on the loop-
                # owned ``conn`` directly. Default (db_writer=None) is byte-
                # identical to the pre-split direct-write path.
                if db_writer is not None and dbwriter_enabled():
                    snap_ts, snap_name, snap_asset, snap_payload = (
                        int(time.time()), name, asset_class, payload
                    )

                    def _job(
                        c: sqlite3.Connection,
                        t: int = snap_ts, n: str = snap_name,
                        a: str = snap_asset, p: dict[str, Any] = snap_payload,
                    ) -> None:
                        persist_altdata_snapshot(
                            c, ts=t, source=n, asset_class=a, payload=p
                        )

                    db_writer.submit(_job, label="altdata_snapshot")
                else:
                    persist_altdata_snapshot(
                        conn, ts=int(time.time()), source=name,
                        asset_class=asset_class, payload=payload,
                    )
            except Exception:  # noqa: BLE001 — snapshot is audit-only, never fatal
                logger.exception("[altdata] snapshot persist failed for %s", name)
            state.altdata_refreshes += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_evt.wait(), timeout=poll_sec)


# ---------------------------------------------------------------------------
# STEP① static-ground coverage producer — full-universe bars + per-ticker ground
# ---------------------------------------------------------------------------


async def _static_ground_producer(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    stop_evt: asyncio.Event,
    capital_session: CapitalSession | None = None,
    alpaca_adapter: Any = None,
    refresh_sec: float = STATIC_GROUND_REFRESH_SEC,
    parallel: int = STATIC_GROUND_PARALLEL_DEFAULT,
    warm_resolutions: tuple[str, ...] = (),
    warm_cadence_sec: float = SESSION_WARM_CADENCE_SEC,
) -> None:
    """Background coverage-fill for the WHOLE active universe (Jin "맨날 들어오는 애들만").

    The 5s hot path (``_run_tick``) bar-ingests only the FOCUS subset, so only the
    same ~276 of ~1882 active tickers ever get a static ground — the candidate
    sweep (②) can only see those. This producer runs OFF the tick deadline on its
    own task and walks ``read_active_universe`` so EVERY active ticker gets {Yahoo
    multi-resolution bars, technical-capable, fused sentiment, event} ground.

    flow_not_block / non-blocking: it never touches the tick body, gates no
    entry/size/exit, and is bounded by the Semaphore + per-cycle total-timeout
    inside ``ingest_static_ground_bars`` (Yahoo IP-block guard, degrade-never-
    halt). The first walk runs immediately (the one-time fill); subsequent walks
    are incremental (the within-period Yahoo frame cache means a re-walk only re-
    fetches symbols whose bar period rolled). A cycle error never halts the loop.

    BARS ONLY — the per-ticker sentiment/event ground (EdgeScore input) is now
    materialized by ``_ticker_ground_producer`` on its OWN faster cadence so an
    EdgeScore is filled WHILE this multi-minute bar walk is still charging (live
    probe 2026-06-26: ticker_ground=0 during the first bar walk starved the sweep
    because the ground refresh sat behind a ~600s bar walk).

    Session pre-open WARM (#66, Jin "장 열기 전부터 거래가능 바 알아서 채워야"): when
    ``warm_resolutions`` is set the fill ALSO pulls those (1m) for open-imminent
    symbols (``session_warm_active``). WHILE ≥1 symbol is warm-active the next
    re-walk uses the short ``warm_cadence_sec`` (open-imminent precision) instead
    of the slow ``refresh_sec``; with 0 warm symbols it falls back to the normal
    cadence (idle degrade). ``warm_resolutions=()`` = warming OFF (kill-switch).
    """
    while not stop_evt.is_set():
        next_wait = refresh_sec
        try:
            bars_result = await ingest_static_ground_bars(
                conn,
                parallel=parallel,
                capital_session=capital_session,
                alpaca_adapter=alpaca_adapter,
                gpt_client_factory=None if ai_free_mode() else default_gpt_factory,
                warm_resolutions=warm_resolutions,
                # storage-split — static-ground bars persist into `bars`
                # (marketdata) via the SAME persist_bars path as the hot ingest.
                db_writer=state.md_db_writer,
                # storage-split (round 3 MED fix) — watchlist_focus (warm-
                # eligible FOCUS scoping) is marketdata-domain too.
                md_conn=state.md_conn,
            )
            state.static_ground_instruments = bars_result["instruments"]
            state.static_ground_bars += bars_result["bars"]
            state.static_ground_warm_bars += bars_result.get("warm_bars", 0)
            state.static_ground_cycles += 1
            # Open-imminent precision: re-walk soon while any symbol is warm-active
            # so the next 1m pull lands close to the cash open; else idle-degrade
            # back to the slow full re-walk cadence.
            if warm_resolutions and bars_result.get("warm_instruments", 0) > 0:
                next_wait = warm_cadence_sec
        except Exception:  # noqa: BLE001 — coverage fill never halts the bot
            logger.exception("[ground] static-ground cycle failed (non-fatal)")
            state.static_ground_errors += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_evt.wait(), timeout=next_wait)


async def _ticker_ground_producer(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    stop_evt: asyncio.Event,
    refresh_sec: float = TICKER_GROUND_REFRESH_SEC,
) -> None:
    """Materialize per-active-ticker sentiment/event ground on a fast cadence.

    Split off ``_static_ground_producer`` (live probe 2026-06-26): the ground
    refresh used to run only AFTER each ~600s bar walk, so during the first walk
    ``ticker_ground`` stayed empty and the candidate sweep (②) had no EdgeScore /
    no direction. ``refresh_ticker_ground`` is pure ``fuse_evidence`` (in-memory)
    + one INSERT/ticker in a single BEGIN/COMMIT with NO await inside the txn, so
    a full ~1882-row refresh is cheap and stays atomic w.r.t. the event loop (the
    tick body's own BEGIN can never interleave). It is also decoupled from the bar
    walk: it reads the live ``AltDataCache``, not stored bars, so it produces
    directional ground even before bars finish charging.

    flow_not_block / non-blocking: gates no entry/size/exit; a cache that is not
    yet warm just yields graceful-empty rows (the sweep can still enumerate them),
    and a cycle error never halts the loop. The first refresh runs immediately.
    """
    while not stop_evt.is_set():
        try:
            tickers = refresh_ticker_ground(
                conn, cache=getattr(state, "altdata_cache", None),
                # storage-split — ticker_ground is marketdata-domain.
                db_writer=state.md_db_writer,
            )
            state.static_ground_tickers = tickers
        except Exception:  # noqa: BLE001 — ground fill never halts the bot
            logger.exception("[ground] ticker-ground cycle failed (non-fatal)")
            state.static_ground_errors += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_evt.wait(), timeout=refresh_sec)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------



def _resolve_gpt_client(haiku: Any | None) -> Any | None:
    """Resolve the in-loop GPT client per POLARIS_AI_FREE (W3 cutover).

    flag=1 (default): build NO client at all — G3/G4/G7 run their
    deterministic primaries (model_used='python'), in-loop LLM calls = 0.
    flag=0 (legacy opt-out): the 2026-05-07 factory→permissive-Stub fallback,
    byte-identical. An explicitly injected client passes through untouched
    (tests / legacy callers).
    """
    if haiku is not None:
        return haiku
    if ai_free_mode():
        logger.info(
            "[loop] AI-FREE mode (POLARIS_AI_FREE) — deterministic "
            "G3/G4/G7, in-loop GPT calls = 0"
        )
        return None
    try:
        from polaris.core.pipeline.agents import _gpt_client as _gpt_mod

        client = _gpt_mod.default_gpt_factory()
        logger.info("[loop] GPT client = real (default_gpt_factory)")
        return client
    except RuntimeError as exc:
        logger.warning(
            "[loop] GPT factory unavailable (%s) — falling back to "
            "permissive StubGPTClient. G1/G3/G4 decisions will be "
            "stubbed PASS until OPENAI_API_KEY is configured.",
            exc,
        )
        return StubGPTClient()


def _resolve_judge_client() -> Any | None:
    """Build the #32 AI-JUDGE client (gpt-5-mini) for the live loop, or None.

    The judge runs over the bot's OWN information (technicals + fused alt-data
    evidence + regime + ground) and emits a STRUCTURALLY non-blocking verdict.
    It is INDEPENDENT of ``POLARIS_AI_FREE`` (the deterministic G3/G4/G7 still
    own the live decision) — the judge is a parallel measurement/refinement
    layer whose MODE is governed by ``POLARIS_AI_JUDGE_MODE`` (shadow default:
    log only, deterministic acts byte-identical).

    Graceful no-op: a missing ``OPENAI_API_KEY`` / any factory error → None, so
    the judge is simply DORMANT (the gates stay byte-identical to the no-judge
    path). The factory must never crash the loop. ``POLARIS_AI_JUDGE=0`` is an
    explicit opt-out (judge fully off).
    """
    if os.environ.get("POLARIS_AI_JUDGE", "1").strip().lower() in ("0", "false", "no", "off"):
        logger.info("[loop] AI-JUDGE disabled (POLARIS_AI_JUDGE=0) — judge dormant")
        return None
    try:
        client = default_gpt_factory(GPT_P0_MODEL)
    except Exception as exc:  # noqa: BLE001 — judge is additive; never crash the loop
        logger.warning(
            "[loop] AI-JUDGE client unavailable (%r) — judge dormant "
            "(deterministic gates byte-identical)",
            exc,
        )
        return None
    logger.info(
        "[loop] AI-JUDGE client = gpt-5-mini (mode=%s; deterministic decision acts)",
        ai_judge_mode(),
    )
    return client


async def run_production_paper_loop(
    *,
    duration_sec: float = DEFAULT_DURATION_SEC,
    tick_sec: float = DEFAULT_TICK_SEC,
    db_path: Path | None = None,
    haiku: Any = None,
    phase: str = "P1",
    real_roundtrip: bool = False,
) -> int:
    """Run the production paper loop. Caller owns DB / haiku / lifetime.

    ``phase`` (Day 9 F1+F2 default flipped to ``"P1"``):
    - **P1**: G6/G7/G8 forward the GPT client + ``GPT_P1_MODEL`` so every
      monitor/exit/reflect decision is GPT-backed (Jin per-gate AI mandate).
    - **P0**: G8 deterministic Python template + G6/G7 deterministic rules
      (use for offline determinism tests / dashboard parity audits).

    ``real_roundtrip=True`` (P0 venue wire) submits **real demo orders** on
    open + close instead of local synthetic fills. Must run against a dedicated
    DB (``--db data/polaris_live.sqlite``) so the simulate-only history is never
    co-mingled. The OKX adapter is built once from ``OKX_DEMO_*`` env and
    shared across ticks; Capital reuses the loop-owned session.
    """
    _load_dotenv()
    # VIRTUAL ACCOUNT (Jin 2026-07-07): POLARIS_VIRTUAL_ACCOUNT=1 forces
    # real_roundtrip off here too (defense in depth — this function is called
    # directly by smoke_paper_loop / tests, not only via ignite()). Every
    # venue touch point downstream is already gated behind real_roundtrip.
    real_roundtrip = resolve_real_roundtrip(real_roundtrip)
    target_db = db_path or Path("data/polaris.sqlite")
    # P0-1 venue-safety: real demo orders must NEVER write into the shared
    # simulate-only DB (``data/polaris.sqlite``). Refuse to start so a real
    # roundtrip cannot co-mingle live fills with the sim history.
    if real_roundtrip and target_db.name == "polaris.sqlite":
        raise ValueError(
            "real_roundtrip=True requires a dedicated live DB "
            "(e.g. --db data/polaris_live.sqlite); refusing to write real "
            f"orders into the sim DB {target_db}"
        )
    target_db.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(target_db)
    # storage-split (vault/50_research/storage-split-blueprint.md, 2026-07-14
    # wipe reset) — the marketdata firehose (bars/baseline/focus/ground/quote/
    # altdata/shadow) gets its OWN file + WAL lock, opened right alongside the
    # trading conn so every downstream wire-up below can reach it. ``conn``
    # (this function's pre-existing name) stays the TRADING connection,
    # unchanged in meaning for every existing caller.
    md_db_path = marketdata_db_path_for(target_db)
    md_conn = init_marketdata_db(md_db_path)
    haiku = _resolve_gpt_client(haiku)
    reset_process_fence()
    get_process_fence(conn)
    state = ProdLoopState()
    state.md_conn = md_conn
    # db-writer-reader-split (design SSOT:
    # vault/50_research/db-writer-reader-split-design_2026-07-08.md) — the
    # process's ONE RW sqlite connection, generalizing #74's per-writer offload
    # thread into a single shared writer thread/queue so the bot can never
    # contend its own WAL write lock again. Kill switch
    # (POLARIS_DBWRITER_ENABLED=0, default enabled) leaves ``state.db_writer``
    # None → every wired consumer below falls back to its pre-split dedicated-
    # conn behaviour with NO code change (same-restart rollback).
    if dbwriter_enabled():
        state.db_writer = DBWriter(target_db)
        state.db_writer.start()
        # SECOND writer instance, same queue machinery, marketdata file — the
        # actual lock-separation lever (storage-split item 3). Firehose
        # writers below submit here instead of ``state.db_writer``.
        state.md_db_writer = DBWriter(md_db_path)
        state.md_db_writer.start()
    # #32 — wire the AI-JUDGE client onto state so the G3/G4 orchestrator + the
    # G7 live-recalc exit run the per-ticker judge alongside the deterministic
    # decision (shadow default: logs only; deterministic acts byte-identical).
    # None when no OPENAI_API_KEY / opted out → judge dormant (graceful no-op).
    state.judge_client = _resolve_judge_client()
    # pts-classes (group WIRE) — hydrate the persisted strategy_class table +
    # bootstrap-replay every registered candidate on a completely empty table
    # (fresh DB / this feature's first boot). Fail-open inside the helper —
    # boot must never crash on a classification-layer failure; a failure
    # leaves strategy_class as-is (G5's resolve_strategy_class falls back to
    # EARN per-row on a missing row, byte-identical to pre-pts-classes).
    boot_hydrate_and_bootstrap_strategy_class(conn, now_ts=int(time.time()))
    try:
        state.open_trades.extend(hydrate_open_positions(conn))
    except Exception:
        logger.exception(
            "[hydrate] hydrate_open_positions failed — OPEN positions not restored"
        )
        raise
    if state.open_trades:
        logger.info(
            "[hydrate] restored %d open trades from prior session",
            len(state.open_trades),
        )
    # P0-2 boot-refire fix: restore the anti-churn novelty anchor
    # (state.last_entry_by_key) from persisted ``positions`` so a restart
    # does not present every (venue, symbol, strategy, side) as "novel"
    # (last_entry_bar=None → is_novel_reentry always True → the re-entry
    # cooldown is exempted unconditionally) and refire the same signal within
    # seconds of boot. flow_not_block: a genuinely NEW bar or a side flip is
    # still novel and flows — this only restores the comparison anchor.
    try:
        state.last_entry_by_key.update(hydrate_last_entry_by_key(conn))
    except Exception:
        logger.exception(
            "[hydrate] hydrate_last_entry_by_key failed — anti-churn anchor "
            "not restored (non-fatal, novelty falls back to always-novel)"
        )
    # Startup migration (flow_not_block): convert any legacy permanent HARD_HALT
    # (unblock_ts=None) into a bounded auto-resume so a strategy stalled before
    # this fix (e.g. tsmom/THETA-USDT, rsi_bb_pullback/CRV-USDT from the OKX 400)
    # resumes on its next signal instead of staying blocked forever.
    try:
        resumed = resume_stale_permanent_halts(conn, now_ts=int(time.time()))
        if resumed:
            conn.commit()
            logger.warning(
                "[startup] resumed %d stale permanent HARD_HALT(s) → bounded", resumed
            )
    except Exception:
        logger.exception("[startup] resume_stale_permanent_halts failed (non-fatal)")
    stop_evt = asyncio.Event()
    # STALL fix (#74) — DEDICATED live-DB handle for the OFFLOADED focus refresh.
    # The Layer-0 producer runs ``refresh_focus_watchlist`` on a worker thread
    # (asyncio.to_thread); it must use its OWN connection so the loop-owned ``conn``
    # is never touched from two threads at once. WAL (set by ``connect``) is
    # 1-writer/N-reader safe, and only one offloaded refresh runs at a time.
    focus_conn = connect(target_db)
    # Storage-split (2026-07-14) — DEDICATED marketdata-domain handle for the
    # SAME offloaded focus worker, thread-confined alongside ``focus_conn``.
    # ``state.md_conn`` cannot be reused here: it belongs to the main tick
    # thread (``get_focus_targets`` / ``sweep_forward_marks`` / bar reads in
    # ``_run_tick``), and handing it to this ``asyncio.to_thread`` worker would
    # share one ``sqlite3.Connection`` across two threads (#74/#88 hazard).
    # Without this, ``refresh_focus_watchlist``'s final persist silently falls
    # back onto ``focus_conn`` (trading DB) while every reader reads
    # ``watchlist_focus`` from the marketdata DB — writer/reader split-brain,
    # zero focus, zero trades on a fresh init.
    md_focus_conn = connect(md_db_path)
    # Same STALL class, second instance — DEDICATED READ-ONLY handle for the
    # OFFLOADED momentum-z shadow scan (``_momentum_z_shadow_async``): each
    # ``refresh_*_universe_once`` otherwise runs an unbounded synchronous
    # ``bars`` scan (1M+ rows on the live Alpaca universe) inline on this same
    # loop. ``mode=ro`` (belt-and-braces on top of the offload — this path
    # only ever SELECTs).
    momentum_conn = connect_ro(target_db)
    layer0_task = asyncio.create_task(
        _layer0_producer(
            conn,
            state=state,
            stop_evt=stop_evt,
            focus_conn=focus_conn,
            momentum_conn=momentum_conn,
            md_focus_conn=md_focus_conn,
        )
    )

    # WAL hygiene, from process start. The bot's writes kept SQLite's
    # autocheckpoint deferred, so the -wal grew unbounded (live: 729 MB); past a
    # few hundred MB every DB op walks a giant wal-index and goes multi-minute
    # UN-state slow → freeze. A periodic checkpoint on a throwaway connection,
    # run in a worker thread every ~15s, keeps the -wal bounded.
    # PASSIVE (not TRUNCATE/RESTART): TRUNCATE takes an EXCLUSIVE lock and waits
    # for every reader to drain — when the dashboard's 1s ``collect_snapshot`` is
    # a near-continuous reader, that wait holds the checkpoint lock while the bot's
    # own writes block on it → the loop wedges in UN-state (the bot was rock-solid
    # ALONE but froze the instant the dashboard attached). PASSIVE never blocks on
    # a reader and never holds the exclusive lock — it flushes whatever frames sit
    # behind no live snapshot, which the dashboard's sub-second read GAPS make
    # plenty — so the -wal stays bounded with ZERO deadlock surface.
    # Tick-stream decouple (vault/50_research/debates/tick_stream_decouple_2026-06-24.md):
    # quote_ticks is now a single-row LWW table (PK=instrument_id), bounded by
    # instrument count — it can no longer grow to the 645k-row/215MB state that
    # wedged the loop. The old 1Hz-INSERT ↔ 15s-DELETE writer lock contention is
    # structurally gone, so the prune-DELETE here is REMOVED (it would also delete
    # the lone latest row of a quiet instrument). The checkpoint keeps only the
    # PASSIVE WAL hygiene below.
    def _checkpoint_wal_blocking() -> None:
        ck = sqlite3.connect(str(target_db), timeout=10.0)
        try:
            # PASSIVE ONLY. It flushes frames behind no snapshot and NEVER blocks a
            # reader — the behaviour that ran 6h rock-solid. A reclaiming (TRUNCATE)
            # checkpoint forces a FULL checkpoint of every -wal frame; on a large
            # startup WAL (the bot bulk-fetches 240 bars × the whole focus/universe)
            # that heavy I/O contends with the bar-ingest writes and the loop falls
            # minutes behind (observed live). PASSIVE does not reclaim the file, so
            # the -wal high-water creeps slowly under the always-on dashboard reader
            # (~18 MB/h → only a ~24h concern, reset by the daily restart); that is
            # the accepted trade vs. a reclaim that destabilises the hot path.
            ck.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            ck.close()

    async def _wal_checkpoint_producer() -> None:
        # db-writer-reader-split: once the shared DBWriter is active it owns
        # the ONLY RW conn and its own periodic TRUNCATE checkpoint replaces
        # this PASSIVE-only producer entirely (design §4 — one checkpoint
        # owner, not two). This producer only runs when the kill switch left
        # ``state.db_writer`` None (byte-identical to the pre-split behaviour).
        if state.db_writer is not None:
            return
        while not stop_evt.is_set():
            try:
                await asyncio.wait_for(stop_evt.wait(), timeout=15.0)
                return  # stopped
            except TimeoutError:
                pass
            try:
                await asyncio.to_thread(_checkpoint_wal_blocking)
            except Exception:  # noqa: BLE001 — hygiene task, never halts the bot
                logger.debug("[wal] checkpoint cycle failed (busy)")

    wal_task = asyncio.create_task(_wal_checkpoint_producer())
    # Data-stream RETENTION producer (root fix for unbounded growth: the
    # retention DELETEs existed but nothing in the loop ever called them, so
    # data/ grew to 12 GB + the probe sidecar to 2.2 GB). Every 30 min it prunes
    # BOTH the live DB and data/probes.sqlite — allowlist-bounded (ledger
    # untouchable), degrade-never-crash. LIVE-DB writes route through the
    # shared db_writer (chunked jobs, interleaved with hot-path traffic —
    # forensic hunt 2026-07-09: the prior dedicated autocommit conn's 11
    # unwrapped DELETEs were the actual `database is locked` storm culprit);
    # the PROBE DB keeps its own dedicated worker-thread conn (separate file,
    # never contends the live DB's writers). WAL hygiene stays with db_writer's
    # own TRUNCATE checkpoint (or the PASSIVE producer above when db_writer is
    # kill-switched off).
    retention_task = asyncio.create_task(
        retention_producer(
            live_db=target_db,
            probe_db=target_db.parent / "probes.sqlite",
            stop_evt=stop_evt,
            db_writer=state.db_writer,
            # storage-split — prune the marketdata firehose (bars/baseline/
            # focus/shadow/altdata) against its own file/writer.
            md_db=md_db_path,
            md_db_writer=state.md_db_writer,
        )
    )
    # #6 — alt-data EVIDENCE producer. Populates the cache singleton on each
    # source's own TTL cadence; the cache feeds compute_and_flip_regime as
    # read-only regime evidence (SIGNAL only, never a throttle).
    altdata_cache = AltDataCache()
    # Share the cache onto state so the Layer-0 focus producer can build the
    # entrance-judge alt-data lens from the same fused evidence (audit
    # code_review_2026-06-24); the regime path keeps its direct local reference.
    state.altdata_cache = altdata_cache
    altdata_task = asyncio.create_task(
        _altdata_producer(
            conn, cache=altdata_cache, state=state, stop_evt=stop_evt,
            # storage-split — altdata_snapshot (+ edgar/finnhub/defillama +
            # their proximity SHADOW tags) is marketdata-domain; route to the
            # marketdata writer so the firehose never contends the trading WAL.
            db_writer=state.md_db_writer,
        )
    )

    capital_session: CapitalSession | None = None
    cap_key = os.environ.get("CAP_API_KEY")
    cap_email = os.environ.get("CAP_EMAIL")
    cap_pass = os.environ.get("CAP_PASSWORD")
    if cap_key and cap_email and cap_pass:
        capital_session = CapitalSession(
            api_key=cap_key, identifier=cap_email, password=cap_pass,
            auto_ping=False,
        )
        await capital_session.__aenter__()
        try:
            await capital_session.ensure_tokens()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[capital] auth failed: %r — running OKX-only", exc)
            await capital_session.aclose()
            capital_session = None

    # P4 — WS real-time price producers. One shared QuoteTickWriter (dedicated
    # conn, 1Hz off-loop flush) fed by one WS client per venue with focus
    # symbols. Spawned AFTER the Capital session so Capital WS reuses its token
    # (M4). Tasks are stored + torn down in the finally below (M5). WS is
    # additive: REST bar ingest stays the fallback, so a WS failure never halts.
    # storage-split — quote_ticks/tick_inflow are marketdata-domain: dedicated
    # conn AND db_writer fallback both point at the marketdata file.
    quote_writer = QuoteTickWriter(md_db_path, db_writer=state.md_db_writer)
    # Share the writer with the tick body so the exit recalc (#2) and G4 (#3) read
    # the in-mem live_px / ring (0 DB hits) and degrade to bar close when stale.
    state.quote_writer = quote_writer
    # STALL fix #88 — technical-store write OFF-LOADER (mirrors quote_writer). The
    # ④ #12 technical store wrote SYNCHRONOUSLY on the loop thread via the SHARED
    # tick conn (the focus fan-out's inline ``upsert_technicals``) → WAL-lock
    # contention with the 1Hz quote flush → tick STALL (#74 re-introduced). The
    # tick body now ``record``s the indicator snapshot in-mem; this 1Hz flush task
    # off-loads the write on a dedicated conn (its own thread). Torn down in the
    # finally below alongside the WS writer. Evidence keeps flowing (flow_not_block).
    # storage-split — ticker_technicals is marketdata-domain (same rationale).
    tech_store_writer = TechnicalStoreWriter(md_db_path, db_writer=state.md_db_writer)
    state.tech_store_writer = tech_store_writer
    tech_store_flush_task = asyncio.create_task(
        tech_store_writer.run_flush_loop(stop_evt)
    )
    # ADR-012 Slice 1 — open the SEPARATE probe tuning-log sidecar
    # (data/probes.sqlite, never the live DB → zero WAL contention) + register the
    # observe-mode probe bus/engine. Fail-open: a wiring failure leaves the attach
    # a no-op (the live exit is provably byte-identical). OBSERVE-ONLY this slice.
    wire_probe_sidecar(state, probe_db_path=str(target_db.parent / "probes.sqlite"))
    # STALL fix (#74) — DEDICATED probes.sqlite handle for the OFFLOADED focus
    # judgment telemetry. The offloaded ``refresh_focus_watchlist`` writes the
    # entrance-judgment sidecar; it must NOT reuse ``state.probe_conn`` (the
    # main-thread G6/close path also writes it) — separate connection per thread.
    # Fail-open: any error leaves it None (the focus-judgment write becomes a no-op).
    try:
        state.focus_probe_conn = connect(target_db.parent / "probes.sqlite")
    except Exception:  # noqa: BLE001 — telemetry sidecar is optional, never halts
        state.focus_probe_conn = None
        logger.warning(
            "[probe] focus-judgment sidecar conn failed — focus telemetry off"
        )
    ws_tasks, ws_clients = start_ws_producers(
        conn, writer=quote_writer, stop_evt=stop_evt,
        capital_session=capital_session, md_conn=md_conn,
    )

    # P0 venue wire: build a single OKX adapter for real-roundtrip runs so the
    # demo order endpoints are reachable across every tick (open + close).
    okx_adapter: OKXAdapter | None = None
    # Track C — single Alpaca PAPER adapter reused across ticks (mirror OKX).
    alpaca_adapter: AlpacaAdapter | None = None
    if real_roundtrip:
        okx_adapter = _build_okx_adapter()
        if okx_adapter is None:
            logger.warning(
                "[loop] real_roundtrip requested but OKX_DEMO_* env missing — "
                "OKX real orders disabled (per-tick env fallback will also fail)"
            )
        else:
            # Load per-symbol dealing rules (lotSz / tickSz) once so the entry
            # leg rounds sz/px before submit — the precision root-cause of the
            # OKX 400 entry reject. Best-effort: a load failure leaves the cache
            # empty (prior un-rounded path), never blocking startup.
            await _load_okx_constraints(okx_adapter)
            # SIGNED auth health check: the public constraint load above succeeds
            # even when the demo credentials are invalid (50105/401), so a silent
            # credential regression would otherwise look "healthy" while every
            # real order rejects. Probe a signed call once and loudly surface an
            # auth failure (flow_not_block — diagnostic only, never blocks boot).
            await _okx_signed_health_check(okx_adapter)
        alpaca_adapter = _build_alpaca_adapter()
        if alpaca_adapter is None:
            logger.warning(
                "[loop] real_roundtrip requested but ALPACA_PAPER_* creds missing "
                "— Alpaca equity real orders disabled (per-tick env fallback will "
                "also fail)"
            )
        # QTY-DRIFT FOLD/CLAMP — MUST run BEFORE the liquidation sweep below
        # (spec item C: fold/adopt precedes any close so a partial-fill-
        # truncated position is folded to its TRUE venue qty first; only what
        # remains genuinely untracked is a liquidation candidate). Same
        # best-effort / never-blocks-boot contract as the venue-drift pass.
        if (real_roundtrip and alpaca_reconcile_import_enabled()
                and alpaca_adapter is not None):
            try:
                touched = await reconcile_alpaca_qty_drift(
                    conn, alpaca_adapter, now_ts=int(time.time()),
                )
                if touched:
                    logger.warning(
                        "[reconcile] alpaca qty-drift folded/clamped %d "
                        "position(s) before the orphan sweep", touched,
                    )
            except Exception:
                logger.exception(
                    "[reconcile] reconcile_alpaca_qty_drift failed — qty drift "
                    "not corrected this boot (non-fatal)"
                )
        # CROSS-VENUE SELF-HEAL: close UNTRACKED venue orphans across OKX +
        # Capital + Alpaca every boot (the bot restarts daily). HARD GUARD: a
        # symbol/epic/ccy with a live tracked open/active DB row is NEVER touched
        # (the tracked-open set, re-read at sweep time). Orphans pin margin /
        # wallet equity → starve entries; this FREES capital to TRADE — not a
        # throttle (flow_not_block). Kill via POLARIS_ORPHAN_RECONCILE=0.
        # Best-effort, per-venue isolated — never blocks boot.
        if real_roundtrip and os.environ.get("POLARIS_ORPHAN_RECONCILE") != "0":
            boot_capital_adapter = (
                CapitalAdapter(capital_session)
                if capital_session is not None else None
            )
            try:
                freed = await reconcile_venue_orphans(
                    conn, okx_adapter=okx_adapter,
                    capital_adapter=boot_capital_adapter,
                    alpaca_adapter=alpaca_adapter, now_ts=int(time.time()),
                )
                total_freed = sum(freed.values())
                if total_freed:
                    logger.warning(
                        "[loop] boot orphan reconcile closed %d untracked venue "
                        "positions (okx=%d capital=%d alpaca=%d) — capital freed "
                        "to trade", total_freed, freed["okx"], freed["capital"],
                        freed["alpaca"],
                    )
            except Exception:
                logger.exception(
                    "[loop] boot orphan reconcile failed — orphans not freed this "
                    "boot (entries may still reject; bot continues)"
                )

        # LADDER RELEASE reconcile (boot pass, spec: "startup + periodic") —
        # a DRAW whose funding signal never opened (or already closed) a
        # position releases its headroom immediately on restart, mirroring the
        # boot orphan reconcile above. DB-only, best-effort.
        if os.environ.get("POLARIS_LADDER_SWEEP", "1") != "0":
            try:
                release_stale_draws(conn, now_ts=int(time.time()))
            except Exception:
                logger.exception(
                    "[loop] boot ladder RELEASE reconcile failed — continues"
                )

    # VENUE startup reconcile-import: after a fresh-DB reset the bot starts FLAT
    # and is BLIND to real venue holdings (Alpaca). hydrate (above) only reads
    # the DB; this fetches live Alpaca positions via the just-built adapter and
    # imports any NOT already tracked as status='open' + a synthetic entry fill
    # at the CURRENT mark (PnL~0). Runs AFTER adapters exist; OKX SPOT dust is
    # fungible wallet balance, skipped by default (import_okx_spot=False).
    # ADOPT-BY-DEFAULT (audit1 P0-4 ②, see alpaca_reconcile_import_enabled):
    # was gated OFF (2026-06-01) behind 3 documented bugs; #1 (cross-event-loop
    # crash) is fixed (P1-6: reconcile_venue_positions is now async and awaits
    # adapters on the caller's own loop), #2 (insta-close) does not reproduce (the exit engine
    # reads positions/fills directly), and #3 (Capital deal_id=None) is scoped
    # OUT by leaving capital_adapter unwired here — Capital's own gap is a
    # separate, untouched concern. flow_not_block: ADOPT into exit-engine
    # management, never liquidate.
    if real_roundtrip and alpaca_reconcile_import_enabled():
        try:
            def _atr_anchor_fn(
                _conn: sqlite3.Connection, _iid: str, _now_ts: int,
            ) -> tuple[float, str] | None:
                return _reconcile_import_atr_anchor(
                    _conn, _iid, _now_ts, md_conn=state.md_conn,
                )

            imported = await reconcile_venue_positions(
                conn, okx_adapter=None, capital_adapter=None,
                alpaca_adapter=alpaca_adapter, now_ts=int(time.time()),
                atr_anchor_fn=_atr_anchor_fn,
            )
        except Exception:
            logger.exception(
                "[reconcile] reconcile_venue_positions failed — live venue "
                "holdings not imported (bot stays blind to them this session)"
            )
            imported = []
        if imported:
            state.open_trades.extend(imported)
            logger.info(
                "[reconcile] imported %d untracked live venue positions into "
                "exit-engine management", len(imported),
            )

    # VENUE DRIFT reconcile (audit1 P0-4 ②, inverse of the adopt-import above):
    # the DB tracks a status='open' Alpaca position the LIVE venue no longer
    # holds (closed/liquidated externally — a demo auto-close, manual close
    # outside the bot, or a prior reset) and our ledger never learned about it.
    # Same gate + adapter as the import above (real_roundtrip + ALPACA-only
    # opt-out flag); a venue read failure inside reconcile_alpaca_venue_drift
    # already skips the WHOLE pass (fail-safe), so this call adds only a
    # graceful skip on an unexpected raise — never blocks boot.
    if real_roundtrip and alpaca_reconcile_import_enabled() and alpaca_adapter is not None:
        try:
            drifted = await reconcile_alpaca_venue_drift(
                conn, alpaca_adapter, now_ts=int(time.time()),
            )
            if drifted:
                logger.warning(
                    "[reconcile] alpaca venue-drift reconciled %d stale internal "
                    "open row(s) absent from the live venue book", drifted,
                )
        except Exception:
            logger.exception(
                "[reconcile] reconcile_alpaca_venue_drift failed — internal "
                "ledger drift not cleared this boot (non-fatal)"
            )

    # P5 — tick-decision engine. The fast (~500ms) live-WS decision loop runs
    # ALONGSIDE the bar pipeline below: it trades ONLY symbols with a fresh WS
    # tick (Phase-1 OKX), reusing compute_size (T4, 9-stack ban intact) +
    # reserve_and_submit for entries and the precise-exit engine for exits. The
    # bar pipeline is gated off the engine-owned OKX symbols (TICK_ENGINE_OWNS_OKX
    # — coexistence: open-dedup + source ownership = no double-trade). Tagged the
    # same as the WS producers: handle stored, cancelled + gathered in finally.
    tick_engine_state: TickEngineState | None = None
    tick_engine_task: asyncio.Task[Any] | None = None
    if tick_engine_enabled():
        tick_engine_state = TickEngineState()
        tick_engine_task = asyncio.create_task(
            run_tick_decision_loop(
                conn, state, stop_evt,
                okx_adapter=okx_adapter, capital_session=capital_session,
                alpaca_adapter=alpaca_adapter, phase=phase,
                real_roundtrip=real_roundtrip,
                tick_engine_state=tick_engine_state,
            )
        )
        logger.info("[loop] P5 tick-decision engine spawned (Phase-1 OKX)")

    # STEP① static-ground coverage producer — off the tick deadline, walks the
    # WHOLE active universe so EVERY active ticker gets bars (the candidate-sweep
    # ② input), not just the focus subset (Jin "맨날 들어오는 애들만"). Spawned AFTER the
    # adapters + altdata cache exist so the fill can reuse them. flow_not_block /
    # non-blocking — gates nothing; bounded by Semaphore + total-timeout (Yahoo
    # IP-block guard). The per-ticker sentiment/event ground is materialized by a
    # SEPARATE faster producer below so the sweep gets an EdgeScore while bars are
    # still charging (live probe 2026-06-26: ground=0 behind a ~600s bar walk).
    #
    # #66 session pre-open warm (Jin "장 열기 전부터 거래가능 바 알아서 채워야"): the producer
    # ALSO pulls 1m for open-imminent symbols so the recency gate sees fresh bars
    # at the cash open. POLARIS_SESSION_WARM_ENABLED=0 = kill-switch (warm OFF =
    # pre-#66 behavior). POLARIS_SESSION_WARM_RESOLUTIONS picks the warm intervals;
    # the open-imminent window + lead live in _session_map (single session truth).
    warm_on = os.environ.get("POLARIS_SESSION_WARM_ENABLED", "1") != "0"
    warm_resolutions = tuple(
        r.strip() for r in os.environ.get(
            "POLARIS_SESSION_WARM_RESOLUTIONS",
            ",".join(SESSION_WARM_RESOLUTIONS_DEFAULT),
        ).split(",") if r.strip()
    ) if warm_on else ()
    static_ground_task = asyncio.create_task(
        _static_ground_producer(
            conn, state=state, stop_evt=stop_evt,
            capital_session=capital_session, alpaca_adapter=alpaca_adapter,
            parallel=_env_int(
                "POLARIS_STATIC_GROUND_PARALLEL", STATIC_GROUND_PARALLEL_DEFAULT
            ),
            warm_resolutions=warm_resolutions,
            warm_cadence_sec=float(_env_int(
                "POLARIS_SESSION_WARM_CADENCE_SEC", int(SESSION_WARM_CADENCE_SEC)
            )),
        )
    )
    ticker_ground_task = asyncio.create_task(
        _ticker_ground_producer(conn, state=state, stop_evt=stop_evt)
    )
    logger.info(
        "[loop] STEP① static-ground bars + per-ticker ground producers spawned "
        "(all active)"
    )

    # EOD-flatten + periodic orphan-reconcile config (position lifecycle Jin
    # directed — '청산이 기본 베이스'). A persistent Capital adapter is reused across
    # ticks (mirrors okx_adapter/alpaca_adapter) for both hooks. flow_not_block:
    # this is EOD risk discipline + orphan hygiene, never an entry block/size cut.
    tick_capital_adapter = (
        CapitalAdapter(capital_session)
        if (real_roundtrip and capital_session is not None) else None
    )
    eod_flatten_on = os.environ.get("POLARIS_EOD_FLATTEN", "1") != "0"
    eod_lead_sec = _env_int("POLARIS_EOD_FLATTEN_LEAD_SEC", 900)
    orphan_reconcile_on = os.environ.get("POLARIS_ORPHAN_RECONCILE", "1") != "0"
    # Periodic re-sweep cadence in ticks (~30 min wall-clock at tick_sec=5 → 360).
    reconcile_every = max(1, _env_int(
        "POLARIS_ORPHAN_RECONCILE_EVERY_TICKS",
        max(1, int(1800 / tick_sec)) if tick_sec > 0 else 360,
    ))
    # Profit-sweep ladder sweeper cadence (vault/50_research/debates/
    # profit_sweep_ladder_2026-07-03.md §build-spec item 2): a ~5-minute
    # fallback pass so CREDIT materialization does not depend on an Alpaca
    # signal happening to arrive (the on-demand path inside compute_size only
    # runs then). ~5 min wall-clock at tick_sec=5 → 60 ticks.
    ladder_sweep_on = os.environ.get("POLARIS_LADDER_SWEEP", "1") != "0"
    ladder_sweep_every = max(1, _env_int(
        "POLARIS_LADDER_SWEEP_EVERY_TICKS",
        max(1, int(300 / tick_sec)) if tick_sec > 0 else 60,
    ))

    deadline = time.monotonic() + duration_sec
    tick_idx = 0
    try:
        while time.monotonic() < deadline:
            tick_idx += 1
            try:
                await _run_tick(
                    conn=conn, haiku=haiku, state=state,
                    capital_session=capital_session, tick_idx=tick_idx,
                    phase=phase, real_roundtrip=real_roundtrip,
                    okx_adapter=okx_adapter, alpaca_adapter=alpaca_adapter,
                    altdata_cache=altdata_cache,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("[tick %d] error: %r", tick_idx, exc)
                state.fault_events += 1
            # FIX 2/2 — keep the live WS set = (focus ∪ open positions). The WS
            # subscription is static-at-startup (clients re-send subscribe only on
            # reconnect), so push the current union into the live clients every
            # tick. ``_focus_by_venue`` reads ``get_focus_targets`` which now unions
            # held symbols, so a HELD position whose symbol left the dynamic focus
            # stays WS-subscribed (dashboard live price + exit precision) for as
            # long as it is held. Best-effort + idempotent (never forces a churn).
            if ws_clients:
                try:
                    await resubscribe_ws_clients(conn, ws_clients, md_conn=md_conn)
                except Exception:  # noqa: BLE001 — visibility refresh never halts
                    logger.exception("[ws] resubscribe (focus∪held) refresh failed")

            # EOD FLATTEN (default) — checked every tick (cheap: clock is cached,
            # window check is arithmetic). Fires only inside a venue's close
            # window, honours hold_overnight, idempotent (flips trade.closed on
            # first fill so re-ticks no-op). OKX 24/7 is hard-exempt. Never
            # flattens blind on a clock error / closed session (skip + defer).
            if eod_flatten_on and real_roundtrip:
                try:
                    await flatten_venue_eod(
                        conn, state, alpaca_adapter=alpaca_adapter,
                        capital_adapter=tick_capital_adapter,
                        lead_sec=eod_lead_sec, now_ts=int(time.time()),
                    )
                except Exception:  # noqa: BLE001 — lifecycle pass never halts loop
                    logger.exception("[eod] flatten pass failed — bot continues")

            # PERIODIC QTY-DRIFT FOLD/CLAMP — same cadence as the periodic
            # orphan sweep below, and runs FIRST in this pass (spec item C:
            # fold/adopt precedes liquidation) so a partial-fill drift that
            # re-accumulates mid-session is corrected before any close.
            if (orphan_reconcile_on and real_roundtrip
                    and alpaca_reconcile_import_enabled()
                    and alpaca_adapter is not None
                    and tick_idx % reconcile_every == 0):
                try:
                    await reconcile_alpaca_qty_drift(
                        conn, alpaca_adapter, now_ts=int(time.time()),
                    )
                except Exception:  # noqa: BLE001 — hygiene pass never halts loop
                    logger.exception(
                        "[reconcile] periodic qty-drift pass failed — continues"
                    )

            # PERIODIC ORPHAN RECONCILE — re-sweep re-accumulated untracked venue
            # holdings without a restart (boot does the first sweep). Tracked-set
            # guard protects live positions; per-venue isolated; best-effort.
            if (orphan_reconcile_on and real_roundtrip
                    and tick_idx % reconcile_every == 0):
                try:
                    await reconcile_venue_orphans(
                        conn, okx_adapter=okx_adapter,
                        capital_adapter=tick_capital_adapter,
                        alpaca_adapter=alpaca_adapter, now_ts=int(time.time()),
                    )
                except Exception:  # noqa: BLE001 — hygiene pass never halts loop
                    logger.exception("[reconcile] periodic sweep failed — continues")

            # PROFIT-SWEEP LADDER — periodic fallback sweep (~5min). Runs on
            # the SAME loop-owned ``conn`` as the reconcile passes above (pure
            # SQLite reads/writes, no venue I/O — unlike the multi-second focus
            # refresh, no dedicated offload connection is needed here). Never
            # inline in the close hot path (source-linted separately); this is
            # the ONLY periodic caller of materialize_credits/
            # release_stale_draws — the on-demand call inside compute_size
            # remains for the immediate-draw case. Best-effort, log-and-continue.
            if ladder_sweep_on and tick_idx % ladder_sweep_every == 0:
                try:
                    materialize_credits(conn, now_ts=int(time.time()))
                except Exception:  # noqa: BLE001 — hygiene pass never halts loop
                    logger.exception(
                        "[ladder] periodic CREDIT materialize failed — continues"
                    )
                try:
                    release_stale_draws(conn, now_ts=int(time.time()))
                except Exception:  # noqa: BLE001 — hygiene pass never halts loop
                    logger.exception(
                        "[ladder] periodic RELEASE reconcile failed — continues"
                    )
            await asyncio.sleep(tick_sec)
    finally:
        stop_evt.set()
        # P5 — tick-decision engine teardown (cooperative stop via stop_evt, then
        # cancel + await so the loop's final telemetry log flushes before exit).
        if tick_engine_task is not None:
            tick_engine_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await tick_engine_task
        layer0_task.cancel()
        altdata_task.cancel()
        wal_task.cancel()
        retention_task.cancel()
        static_ground_task.cancel()
        ticker_ground_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await layer0_task
        # STALL fix (#74) — the offloaded focus refresh has ended with layer0_task;
        # close its dedicated live-DB handle (no worker thread touches it now).
        with contextlib.suppress(Exception):
            focus_conn.close()
        with contextlib.suppress(Exception):
            md_focus_conn.close()
        with contextlib.suppress(Exception):
            momentum_conn.close()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await altdata_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await wal_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await retention_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await static_ground_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await ticker_ground_task
        # M5 — WS teardown. stop_evt (set above) ends every client.run + the
        # flush loop cooperatively; cancel then gather(return_exceptions=True)
        # joins them (final drain happens inside run_flush_loop on stop_evt),
        # then close the dedicated writer conn AFTER the flush task has ended.
        for t in ws_tasks:
            t.cancel()
        await asyncio.gather(*ws_tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            quote_writer.close()
        # STALL fix #88 — technical-store writer teardown. stop_evt (set above)
        # makes run_flush_loop do its FINAL DRAIN then return; await it (the loop is
        # cooperative — final flush happens on stop_evt) so the dedicated conn is
        # closed only AFTER the last batch has been written.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await tech_store_flush_task
        with contextlib.suppress(Exception):
            tech_store_writer.close()
        # db-writer-reader-split — every producer that could still submit a job
        # (WS flush tasks, tech-store flush task, altdata/ground producers) has
        # already been cancelled + awaited above, so it is safe to drain +
        # TRUNCATE-checkpoint + close the shared writer now. None when the kill
        # switch left it unwired (nothing to stop).
        if state.db_writer is not None:
            with contextlib.suppress(Exception):
                state.db_writer.stop()
        # storage-split — same drain/checkpoint/close for the marketdata writer.
        if state.md_db_writer is not None:
            with contextlib.suppress(Exception):
                state.md_db_writer.stop()
        with contextlib.suppress(Exception):
            md_conn.close()
        # ADR-012 — close the probe tuning-log sidecar (separate DB).
        if state.probe_conn is not None:
            with contextlib.suppress(Exception):
                state.probe_conn.close()
        # STALL fix (#74) — close the dedicated focus-judgment sidecar handle.
        if state.focus_probe_conn is not None:
            with contextlib.suppress(Exception):
                state.focus_probe_conn.close()
        if okx_adapter is not None:
            await okx_adapter.aclose()
        if alpaca_adapter is not None:
            await alpaca_adapter.aclose()
        if capital_session is not None:
            await capital_session.aclose()

    _log_summary(state, tick_idx)
    return 0 if state.closed_trades else 1


def _log_summary(state: ProdLoopState, tick_idx: int) -> None:
    """One-shot summary block for the operator log."""
    bars_by_tf = " ".join(
        f"{tf}={n}" for tf, n in sorted(state.bars_persisted_by_tf.items())
    ) or "-"
    sigs_by_tf = " ".join(
        f"{tf}={n}" for tf, n in sorted(state.signals_by_tf.items())
    ) or "-"
    venue_rejects = " ".join(
        f"{code}={n}" for code, n in sorted(state.venue_rejects_by_code.items())
    ) or "-"
    regime_hint = " ".join(
        f"{k}={n}" for k, n in sorted(state.regime_hint_stats.items())
    ) or "-"
    regime_v2_crosstab = " ".join(
        f"{k}={n}" for k, n in sorted(state.regime_v2_crosstab.items())
    ) or "-"
    fields = [
        ("ticks", tick_idx),
        ("universe_refresh", f"okx={state.universe_refreshes} capital={state.capital_refreshes}"),
        ("bars_persisted", f"{state.bars_persisted} (baseline_samples={state.bars_baseline_samples})"),
        ("bars_by_tf", bars_by_tf),
        (
            "static_ground",
            f"instruments={state.static_ground_instruments} "
            f"tickers={state.static_ground_tickers} "
            f"bars={state.static_ground_bars} cycles={state.static_ground_cycles} "
            f"errors={state.static_ground_errors}",
        ),
        ("signals_by_tf", sigs_by_tf),
        ("pipeline_runs", f"{state.pipeline_runs} (kills={state.pipeline_kills})"),
        ("g1/g2/g8 runs", f"{state.g1_runs} / {state.g2_emits} / {state.g8_runs}"),
        ("sized_count", state.sized_count),
        ("fence", f"{state.fence_reservations} (conflicts={state.fence_conflicts})"),
        ("idempotency_hits", state.idempotency_conflicts),
        ("fault_events", state.fault_events),
        ("venue_rejects", venue_rejects),
        ("venue_close_rejects", state.venue_close_rejects),
        ("supervisor_tasks", f"{state.supervised_tasks_total} (failed={state.supervised_tasks_failed})"),
        (
            "live_recalc",
            f"g6={state.recalc_g6_calls} g7={state.recalc_g7_calls} "
            f"widen={state.recalc_widen_applied} exit_now={state.recalc_exit_now} "
            f"swap={state.recalc_swap}",
        ),
        ("fills open/close", f"{state.fills_open} / {state.fills_close}"),
        ("closed_trades", len(state.closed_trades)),
        # Altdata hint instrumentation (in-memory counters; display only).
        ("regime_hint", regime_hint),
        # regime v2 twinlight crosstab (design-regime-v2-rollout.md W2,
        # behavior-0; in-memory counters, display only).
        ("regime_v2_crosstab", regime_v2_crosstab),
    ]
    logger.info("=========== production paper loop summary ===========")
    for name, value in fields:
        logger.info("%-18s: %s", name, value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="production_paper_loop")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SEC)
    parser.add_argument("--tick", type=float, default=DEFAULT_TICK_SEC)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--verbose", "-v", action="count", default=0)
    parser.add_argument("--log-file", type=str, default=DEFAULT_LOG_FILE)
    # Day 9 F1+F2 (Jin 2026-05-07 mandate): paper loop defaults to P1 so
    # G6/G7/G8 fire the GPT branch. Pass ``--phase P0`` to opt back into
    # the deterministic Python template (e.g. for offline determinism tests).
    parser.add_argument("--phase", type=str, choices=("P0", "P1"), default="P1")
    args = parser.parse_args(argv if argv is not None else None)
    log_level = "DEBUG" if args.verbose >= 2 else "INFO"
    setup_polaris_logging(level=log_level, log_file=args.log_file)
    return asyncio.run(
        run_production_paper_loop(
            duration_sec=args.duration, tick_sec=args.tick, db_path=args.db,
            phase=args.phase,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
