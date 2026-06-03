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

from polaris.core.altdata.cache import AltDataCache
from polaris.core.altdata.crypto_fg import CryptoFearGreedCollector
from polaris.core.altdata.fred_macro import FredMacroCollector
from polaris.core.altdata.okx_funding import OKXFundingCollector
from polaris.core.data.quote_writer import QuoteTickWriter
from polaris.core.isolation.allocator_fence import (
    get_process_fence,
    reset_process_fence,
)
from polaris.core.lifecycle.recover import (
    hydrate_open_positions,
    reconcile_venue_positions,
)
from polaris.core.ticks.config import tick_engine_enabled
from polaris.logging_config import DEFAULT_LOG_FILE, setup_polaris_logging
from polaris.scripts._production_layers import (
    ALPACA_REFRESH_SEC,
    CAPITAL_REFRESH_SEC,
    OKX_REFRESH_SEC,
    refresh_alpaca_universe_once,
    refresh_capital_universe_once,
    refresh_focus_watchlist,
    refresh_okx_universe_once,
)
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
from polaris.storage.schema import init_db
from polaris.venues.alpaca import AlpacaAdapter, resolve_alpaca_credentials
from polaris.venues.capital.adapter import CapitalAdapter
from polaris.venues.capital.session import CapitalSession
from polaris.venues.okx import OKXAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "FOCUS_CYCLE_TARGET",
    "ProdLoopState",
    "main",
    "persist_altdata_snapshot",
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


def _build_alpaca_adapter() -> AlpacaAdapter | None:
    """Build an Alpaca PAPER adapter from creds, or ``None`` if unset (Track C).

    Mirrors ``_build_okx_adapter``: one adapter reused across ticks for the real
    equity round-trip. PAPER-only (the adapter refuses any live trade host).
    """
    api_key, secret = resolve_alpaca_credentials()
    if not (api_key and secret):
        return None
    return AlpacaAdapter(api_key=api_key, secret=secret)


# ---------------------------------------------------------------------------
# Layer 0 — background producer
# ---------------------------------------------------------------------------


async def _layer0_producer(
    conn: sqlite3.Connection, *, state: ProdLoopState, stop_evt: asyncio.Event,
) -> None:
    """OKX (5min) + Capital (10min) + Alpaca (10min) refresh + focus recompute.

    Alpaca (Track C / US-equity) is an added producer; OKX/Capital cadence and
    behavior are unchanged. ``refresh_alpaca_universe_once`` is smoke-safe (no
    creds → 0 active, no rows persisted).
    """
    await refresh_okx_universe_once(conn)
    state.universe_refreshes += 1
    await refresh_capital_universe_once(conn)
    state.capital_refreshes += 1
    await refresh_alpaca_universe_once(conn)
    state.alpaca_refreshes += 1
    refresh_focus_watchlist(conn)
    last_okx = time.monotonic()
    last_capital = time.monotonic()
    last_alpaca = time.monotonic()
    while not stop_evt.is_set():
        await asyncio.sleep(15.0)
        now = time.monotonic()
        if now - last_okx >= OKX_REFRESH_SEC:
            await refresh_okx_universe_once(conn)
            state.universe_refreshes += 1
            last_okx = now
        if now - last_capital >= CAPITAL_REFRESH_SEC:
            await refresh_capital_universe_once(conn)
            state.capital_refreshes += 1
            last_capital = now
        if now - last_alpaca >= ALPACA_REFRESH_SEC:
            await refresh_alpaca_universe_once(conn)
            state.alpaca_refreshes += 1
            last_alpaca = now
        refresh_focus_watchlist(conn)


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


def _default_altdata_collectors() -> list[Any]:
    """The live alt-data EVIDENCE collectors (keyless/keyed graceful-skip).

    Keyless sources (Coinglass / MyFxBook) are intentionally omitted until keys
    are present — their stubs would only ever return ``{}``. FRED uses
    ``FRED_API_KEY`` (no key → graceful ``{}``, no network); OKX funding + alt.me
    F&G need no key.
    """
    return [OKXFundingCollector(), CryptoFearGreedCollector(), FredMacroCollector()]


async def _altdata_producer(
    conn: sqlite3.Connection,
    *,
    cache: AltDataCache,
    state: ProdLoopState,
    stop_evt: asyncio.Event,
    collectors: list[Any] | None = None,
    poll_sec: float = 30.0,
) -> None:
    """Refresh alt-data EVIDENCE collectors on each one's own TTL cadence.

    SIGNAL/EVIDENCE only. Each collector is re-fetched only after its own
    ``ttl_sec`` has elapsed (OKX funding 300s, F&G 1800s, FRED 3600s). On a
    successful non-empty fetch the payload updates the ``AltDataCache`` singleton
    and a snapshot row is persisted. On a collector error or empty result the
    LAST cache value is kept untouched (graceful — fewer evidence sources, never
    a throttle / halt). The loop exits promptly when ``stop_evt`` is set.
    """
    active = collectors if collectors is not None else _default_altdata_collectors()
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
            try:
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
# Entry points
# ---------------------------------------------------------------------------


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
    if haiku is None:
        # 2026-05-07 Haiku→GPT migration: try real GPT factory first; fall back
        # to stub only on ImportError or missing OPENAI_API_KEY (logged loud so
        # operators see when paper mode silently degrades to permissive stub).
        try:
            from polaris.core.pipeline.agents._gpt_client import default_gpt_factory
            haiku = default_gpt_factory()
            logger.info("[loop] GPT client = real (default_gpt_factory)")
        except RuntimeError as exc:
            logger.warning(
                "[loop] GPT factory unavailable (%s) — falling back to "
                "permissive StubGPTClient. G1/G3/G4 decisions will be "
                "stubbed PASS until OPENAI_API_KEY is configured.",
                exc,
            )
            haiku = StubGPTClient()
    reset_process_fence()
    get_process_fence(conn)
    state = ProdLoopState()
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
    stop_evt = asyncio.Event()
    layer0_task = asyncio.create_task(
        _layer0_producer(conn, state=state, stop_evt=stop_evt)
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
    # Read-only DASHBOARD MIRROR. The dashboard's 1s collect_snapshot scans the
    # live DB (quote_ticks ~645k rows) and that concurrent random I/O against the
    # bot's writes wedged the event loop in UN-state — the bot was rock-solid
    # ALONE but froze the instant the dashboard attached, regardless of WAL size
    # or checkpoint mode. Decouple it: maintain a consistent read-only COPY via
    # the SQLite online-backup API (page-incremental, allows concurrent writes,
    # releases the GIL) every ~15s off the loop. Point the dashboard at this mirror
    # (POLARIS_DASH_DB=data/paper/dashboard_mirror.sqlite) → ZERO live contention.
    dash_mirror = target_db.parent / "dashboard_mirror.sqlite"

    def _checkpoint_wal_blocking() -> None:
        ck = sqlite3.connect(str(target_db), timeout=10.0)
        try:
            ck.execute("PRAGMA wal_checkpoint(PASSIVE)")
            mirror = sqlite3.connect(str(dash_mirror), timeout=10.0)
            try:
                ck.backup(mirror)
            finally:
                mirror.close()
        finally:
            ck.close()

    async def _wal_checkpoint_producer() -> None:
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
    # #6 — alt-data EVIDENCE producer. Populates the cache singleton on each
    # source's own TTL cadence; the cache feeds compute_and_flip_regime as
    # read-only regime evidence (SIGNAL only, never a throttle).
    altdata_cache = AltDataCache()
    altdata_task = asyncio.create_task(
        _altdata_producer(conn, cache=altdata_cache, state=state, stop_evt=stop_evt)
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
    quote_writer = QuoteTickWriter(target_db)
    # Share the writer with the tick body so the exit recalc (#2) and G4 (#3) read
    # the in-mem live_px / ring (0 DB hits) and degrade to bar close when stale.
    state.quote_writer = quote_writer
    ws_tasks, ws_clients = start_ws_producers(
        conn, writer=quote_writer, stop_evt=stop_evt,
        capital_session=capital_session,
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
        alpaca_adapter = _build_alpaca_adapter()
        if alpaca_adapter is None:
            logger.warning(
                "[loop] real_roundtrip requested but ALPACA_PAPER_* creds missing "
                "— Alpaca equity real orders disabled (per-tick env fallback will "
                "also fail)"
            )

    # VENUE startup reconcile-import: after a fresh-DB reset the bot starts FLAT
    # and is BLIND to real venue holdings (Alpaca/Capital). hydrate (above) only
    # reads the DB; this fetches live positions via the just-built adapters and
    # imports any NOT already tracked as status='open' + a synthetic entry fill
    # at the CURRENT mark (PnL~0). Runs AFTER adapters exist; OKX SPOT dust is
    # fungible wallet balance, skipped by default (import_okx_spot=False).
    # Gated OFF by default (2026-06-01): the venue reconcile-import had 3 live
    # bugs — _reconcile_capital crash (sync _run_coro vs running loop), imported
    # positions insta-closing (entry mark + uninitialised exit FSM), and a
    # Capital deal_id=None close-error loop. Re-enable only after those are fixed
    # (POLARIS_RECONCILE_VENUE_IMPORT=1). Until then the bot starts flat and
    # leaves venue holdings unmanaged (benign on DEMO) — stable trading first.
    if real_roundtrip and os.environ.get("POLARIS_RECONCILE_VENUE_IMPORT") == "1":
        capital_adapter = (
            CapitalAdapter(capital_session) if capital_session is not None else None
        )
        try:
            imported = reconcile_venue_positions(
                conn, okx_adapter=okx_adapter, capital_adapter=capital_adapter,
                alpaca_adapter=alpaca_adapter, now_ts=int(time.time()),
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
                    resubscribe_ws_clients(conn, ws_clients)
                except Exception:  # noqa: BLE001 — visibility refresh never halts
                    logger.exception("[ws] resubscribe (focus∪held) refresh failed")
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
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await layer0_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await altdata_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await wal_task
        # M5 — WS teardown. stop_evt (set above) ends every client.run + the
        # flush loop cooperatively; cancel then gather(return_exceptions=True)
        # joins them (final drain happens inside run_flush_loop on stop_evt),
        # then close the dedicated writer conn AFTER the flush task has ended.
        for t in ws_tasks:
            t.cancel()
        await asyncio.gather(*ws_tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            quote_writer.close()
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
    fields = [
        ("ticks", tick_idx),
        ("universe_refresh", f"okx={state.universe_refreshes} capital={state.capital_refreshes}"),
        ("bars_persisted", f"{state.bars_persisted} (baseline_samples={state.bars_baseline_samples})"),
        ("bars_by_tf", bars_by_tf),
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
