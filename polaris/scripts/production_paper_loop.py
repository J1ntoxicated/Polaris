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
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from polaris.core.isolation.allocator_fence import (
    get_process_fence,
    reset_process_fence,
)
from polaris.core.lifecycle.recover import hydrate_open_positions
from polaris.logging_config import DEFAULT_LOG_FILE, setup_polaris_logging
from polaris.scripts._production_layers import (
    CAPITAL_REFRESH_SEC,
    OKX_REFRESH_SEC,
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
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.scripts._smoke_real_roundtrip import resolve_okx_base_url
from polaris.storage.schema import init_db
from polaris.venues.capital.session import CapitalSession
from polaris.venues.okx import OKXAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "FOCUS_CYCLE_TARGET",
    "ProdLoopState",
    "main",
    "run_production_paper_loop",
    "_all_strategies",
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


# ---------------------------------------------------------------------------
# Layer 0 — background producer
# ---------------------------------------------------------------------------


async def _layer0_producer(
    conn: sqlite3.Connection, *, state: ProdLoopState, stop_evt: asyncio.Event,
) -> None:
    """OKX (5min) + Capital (10min) refresh + focus recompute on cadence."""
    await refresh_okx_universe_once(conn)
    state.universe_refreshes += 1
    await refresh_capital_universe_once(conn)
    state.capital_refreshes += 1
    refresh_focus_watchlist(conn)
    last_okx = time.monotonic()
    last_capital = time.monotonic()
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
        refresh_focus_watchlist(conn)


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

    # P0 venue wire: build a single OKX adapter for real-roundtrip runs so the
    # demo order endpoints are reachable across every tick (open + close).
    okx_adapter: OKXAdapter | None = None
    if real_roundtrip:
        okx_adapter = _build_okx_adapter()
        if okx_adapter is None:
            logger.warning(
                "[loop] real_roundtrip requested but OKX_DEMO_* env missing — "
                "OKX real orders disabled (per-tick env fallback will also fail)"
            )

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
                    okx_adapter=okx_adapter,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("[tick %d] error: %r", tick_idx, exc)
                state.fault_events += 1
            await asyncio.sleep(tick_sec)
    finally:
        stop_evt.set()
        layer0_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await layer0_task
        if okx_adapter is not None:
            await okx_adapter.aclose()
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
