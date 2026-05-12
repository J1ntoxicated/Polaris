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
import math
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.core.isolation.allocator_fence import (
    get_process_fence,
    reset_process_fence,
)
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    FAULT_NAN,
    record_fault,
    should_allow_new_entry,
)
from polaris.core.isolation.worker import (
    PipelineTaskSpec,
    supervise_pipeline_tasks,
)
from polaris.core.lifecycle.recover import hydrate_open_positions
from polaris.core.live_recalc.strategy_swap import (
    SwapCandidate,
    evaluate_strategy_swap,
)
from polaris.logging_config import DEFAULT_LOG_FILE, setup_polaris_logging
from polaris.scripts._production_indicators import (
    build_real_market_view,
    session_window_now,
)
from polaris.scripts._production_layers import (
    CAPITAL_REFRESH_SEC,
    OKX_REFRESH_SEC,
    compute_and_flip_regime,
    get_focus_targets,
    ingest_bars_per_timeframe,
    read_recent_bars,
    refresh_capital_universe_once,
    refresh_focus_watchlist,
    refresh_okx_universe_once,
    run_recalc_for_active_positions,
)
from polaris.scripts._production_pipeline import (
    close_oldest_with_real_pnl,
    close_specific_position,
    reserve_and_submit,
    run_pipeline_for_signal,
)
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.storage.schema import init_db
from polaris.strategies import (
    BaseStrategy,
    FXBreakoutBasketStrategy,
    RawSignal,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    SpotDonchianStrategy,
    TSMOMStrategy,
    VolumeBurstStrategy,
    XAUIndicesTrendStrategy,
)
from polaris.venues.capital.session import CapitalSession

logger = logging.getLogger(__name__)

DEFAULT_TICK_SEC = 5.0
DEFAULT_DURATION_SEC = 60.0
FOCUS_CYCLE_TARGET = 30


# ---------------------------------------------------------------------------
# Loop state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProdLoopState:
    """Per-run counters for the production paper loop (no fixtures)."""

    fills_open: int = 0
    fills_close: int = 0
    open_trades: list[SimulatedTrade] = field(default_factory=list)
    closed_trades: list[SimulatedTrade] = field(default_factory=list)
    pipeline_runs: int = 0
    pipeline_kills: int = 0
    sized_count: int = 0
    fence_reservations: int = 0
    fence_conflicts: int = 0
    idempotency_conflicts: int = 0
    fault_events: int = 0
    g1_runs: int = 0
    g2_emits: int = 0
    g8_runs: int = 0
    bars_persisted: int = 0
    bars_baseline_samples: int = 0
    universe_refreshes: int = 0
    capital_refreshes: int = 0
    # F11 — Day 9: Layer 7 SSOT supervisor counters (TaskGroup-managed).
    supervised_tasks_total: int = 0
    supervised_tasks_failed: int = 0
    # F10 — Day 9: per-timeframe ingest tracking. ``last_fetch_monotonic_by_tf``
    # gates per-timeframe fetches at their cadence (1m every tick / 15m 60s /
    # 1H 5min). ``bars_persisted_by_tf`` is logged per summary so smoke can
    # verify Capital strategies receive their 1H bars.
    last_fetch_monotonic_by_tf: dict[str, float] = field(default_factory=dict)
    bars_persisted_by_tf: dict[str, int] = field(default_factory=dict)
    signals_by_tf: dict[str, int] = field(default_factory=dict)
    # F1+F2 — Day 9: live recalc loop counters (G6/G7 per-tick GPT calls).
    recalc_g6_calls: int = 0
    recalc_g7_calls: int = 0
    recalc_widen_applied: int = 0
    recalc_exit_now: int = 0
    recalc_swap: int = 0


# ---------------------------------------------------------------------------
# Strategy registry (7 strategies — A8 + cumulative #81 fix: no emit cap)
# ---------------------------------------------------------------------------


def _all_strategies() -> list[BaseStrategy]:
    return [
        VolumeBurstStrategy(),
        TSMOMStrategy(),
        RSIBBPullbackStrategy(),
        SpotDonchianStrategy(),
        FXBreakoutBasketStrategy(),
        XAUIndicesTrendStrategy(),
        SessionBreakoutStrategy(),
    ]


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


def _lookup_regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    """Read the Layer 6 SSOT regime for a (venue, symbol). Defaults to 'chop'."""
    instrument_row = conn.execute(
        "SELECT underlying_group_id FROM universe WHERE venue = ? AND symbol = ?",
        (venue, symbol),
    ).fetchone()
    if instrument_row is None:
        return "chop"
    group_id = str(instrument_row[0])
    row = conn.execute(
        "SELECT regime FROM regime_state WHERE venue = ? AND underlying_group_id = ?",
        (venue, group_id),
    ).fetchone()
    if row is None:
        return "chop"
    return str(row[0])


def _is_finite_signal(sig: RawSignal) -> bool:
    """Reject a signal whose strength / sizing_hint is non-finite."""
    return math.isfinite(sig.strength) and math.isfinite(sig.sizing_hint)


def _evaluate_swaps(conn: sqlite3.Connection, *, now_ts: int) -> None:
    """Day 8 spec E + cumulative #81 X3: Layer 6 SSOT swap predicate."""
    rows = conn.execute(
        "SELECT position_id, active_strategy_id, venue, symbol, side, "
        "       underlying_group_id "
        "FROM positions WHERE status NOT IN ('closed', 'cancelled') LIMIT 10"
    ).fetchall()
    for r in rows:
        pos_id, active_strat, venue, symbol, side, group = (
            str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5] or ""),
        )
        candidate = SwapCandidate(
            position_id=pos_id,
            from_strategy_id=active_strat, to_strategy_id=active_strat,
            venue=venue, symbol=symbol, side=side,
            from_correlation_group=group, to_correlation_group=group,
        )
        evaluate_strategy_swap(conn, candidate=candidate, now_ts=now_ts, apply=False)


# ---------------------------------------------------------------------------
# Tick loop
# ---------------------------------------------------------------------------


def _strategies_by_timeframe(
    strategies: list[BaseStrategy],
) -> dict[str, list[BaseStrategy]]:
    """Bucket strategies by their ``metadata.timeframe`` (F10 — Day 9).

    Each bucket runs against a venue/symbol MarketView built from bars at the
    matching ``bar_interval``. This eliminates the 1m hardcode that silently
    starved Capital strategies of usable history.
    """
    out: dict[str, list[BaseStrategy]] = {}
    for s in strategies:
        out.setdefault(s.metadata.timeframe, []).append(s)
    return out


# F10 R2/R3 — ``_is_fetch_due`` was removed (codex R3 P2 nit). The
# orchestrator delegates cadence gating to ``ingest_bars_per_timeframe``,
# which now keys cadence per ``(timeframe, venue)``. A standalone "by
# timeframe only" helper would re-introduce the partial-bucket starvation
# bug if a caller reused it.


async def _run_tick(
    *,
    conn: sqlite3.Connection,
    haiku: Any,
    state: ProdLoopState,
    capital_session: CapitalSession | None,
    tick_idx: int,
    phase: str = "P0",
) -> None:
    """One 5-second cycle (Day 8 spec B + D + E + F + Day 9 F10).

    F10 contract: bars ingest + market view + strategy eval are partitioned
    by ``strategy.metadata.timeframe``. The hardcoded ``timeframe="1m"``
    that previously starved Capital strategies of their 1H history is gone.
    """
    now_ts = int(time.time())
    now_mono = time.monotonic()
    focus = get_focus_targets(conn, cycle_ts=now_ts, max_n=FOCUS_CYCLE_TARGET)
    if not focus:
        focus = [
            ("okx", "BTC-USDT", "crypto", "crypto:BTC"),
            ("okx", "ETH-USDT", "crypto", "crypto:ETH"),
        ]

    strategies = _all_strategies()
    strategies_by_tf = _strategies_by_timeframe(strategies)

    # F10 — fetch bars per timeframe at the appropriate cadence (delegated
    # to ``ingest_bars_per_timeframe`` so the orchestrator stays light).
    #
    # Codex F10 R1 P1-1 fix: Layer 6 regime SSOT is computed from 1m bars
    # for *every* focus venue, not just venues that have a 1m strategy.
    # Capital has no 1m strategy, but its symbols still need fresh 1m bars
    # so ``compute_and_flip_regime`` doesn't fall through to "chop". Force
    # the 1m bucket to cover every focus venue.
    timeframe_to_venues = {
        tf: {s.metadata.venue for s in strats}
        for tf, strats in strategies_by_tf.items()
    }
    all_focus_venues = {t[0] for t in focus}
    timeframe_to_venues.setdefault("1m", set()).update(all_focus_venues)
    ingest_totals = await ingest_bars_per_timeframe(
        conn, focus,
        timeframe_to_venues=timeframe_to_venues,
        last_fetch_monotonic_by_tf=state.last_fetch_monotonic_by_tf,
        bars_persisted_by_tf=state.bars_persisted_by_tf,
        capital_session=capital_session, limit=240, now_mono=now_mono,
    )
    state.bars_persisted += ingest_totals["bars"]
    state.bars_baseline_samples += ingest_totals["baseline_samples"]

    await run_recalc_for_active_positions(conn, now_ts=now_ts)
    # Day 9 F1+F2 — live recalc loop with G6/G7 GPT per-position invocation.
    # Replaces the entry-time-only G6 wiring + FIFO-oldest close path with a
    # per-tick AI supervisory pass over every active position. Phase=P1
    # forwards the GPT client; phase=P0 keeps decisions deterministic.
    await recalc_active_positions(
        conn, state=state, now_ts=now_ts, gpt_client=haiku, phase=phase,
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
    )
    # Regime is computed off 1m bars (Layer 6 SSOT — keep stable across tf
    # buckets so swap predicate doesn't oscillate with strategy timeframe).
    regime_by_group: dict[tuple[str, str], str] = {}
    for venue, symbol, _ac, group_id in focus:
        if not group_id:
            continue
        bars_1m = read_recent_bars(conn, venue=venue, symbol=symbol, bar_interval="1m")
        if not bars_1m:
            continue
        regime_by_group[(venue, group_id)] = compute_and_flip_regime(
            conn, venue=venue, underlying_group_id=group_id,
            bars=bars_1m, now_ts=now_ts,
        )

    universe_rows: list[dict[str, Any]] = []
    cur = conn.execute(
        "SELECT venue, symbol, vol_24h_usd FROM universe WHERE is_active = 1 LIMIT 50"
    )
    for r in cur.fetchall():
        universe_rows.append(
            {"venue": r[0], "symbol": r[1], "vol_24h_usd": float(r[2] or 0.0)}
        )
    # Day 9 F11 fix: build PipelineTaskSpec list + delegate execution to
    # ``supervise_pipeline_tasks`` (Layer 7 SSOT). Replaces the bare
    # ``asyncio.create_task`` + ``asyncio.gather(..., return_exceptions=True)``
    # site that bypassed ``supervise_strategies``.
    pipeline_specs: list[PipelineTaskSpec] = []
    for timeframe, strategies_for_tf in strategies_by_tf.items():
        venues_for_tf = {s.metadata.venue for s in strategies_for_tf}
        for venue, symbol, asset_class, group_id in focus:
            if venue not in venues_for_tf:
                continue
            bars = read_recent_bars(
                conn, venue=venue, symbol=symbol, bar_interval=timeframe,
            )
            if len(bars) < 30:
                continue
            regime = regime_by_group.get((venue, group_id), "chop")
            mv = build_real_market_view(
                venue=venue, symbol=symbol, timeframe=timeframe, bars=bars,
                spread_bps=5.0, session_open_window=session_window_now(now_ts),
            )
            for strategy in strategies_for_tf:
                if strategy.metadata.venue != venue:
                    continue
                strategy_id = strategy.metadata.strategy_id
                # Day 9 F11 fix: respect the Layer 7 circuit breaker — skip
                # HALTed strategies (HARD_HALT / SOFT_HALT / RISK_ONLY) so
                # the per-cycle fan-out cannot bypass the 4-state machine.
                if not should_allow_new_entry(conn, strategy_id, now_ts=now_ts):
                    continue
                try:
                    sig = strategy.generate_raw_signal(mv)
                except Exception as exc:  # noqa: BLE001 — must isolate
                    record_fault(
                        conn, strategy_id=strategy_id,
                        fault_type=FAULT_EXCEPTION, now_ts=now_ts,
                        detail={"phase": "generate_raw_signal", "exc": str(exc)[:200]},
                    )
                    state.fault_events += 1
                    continue
                if sig is None:
                    continue
                if not _is_finite_signal(sig):
                    record_fault(
                        conn, strategy_id=strategy_id,
                        fault_type=FAULT_NAN, now_ts=now_ts,
                        detail={"phase": "raw_signal_nan", "signal_id": sig.signal_id},
                    )
                    state.fault_events += 1
                    continue
                state.signals_by_tf[timeframe] = (
                    state.signals_by_tf.get(timeframe, 0) + 1
                )

                def _factory(
                    *, _strategy: Any = strategy, _sig: Any = sig,
                    _venue: str = venue, _symbol: str = symbol,
                    _asset_class: str = asset_class, _group_id: str = group_id,
                    _regime: str = regime, _atr_pct: float = mv.atr_pct,
                    _last_price: float = mv.last_price,
                ) -> Any:
                    return run_pipeline_for_signal(
                        conn=conn, haiku=haiku, state=state, strategy=_strategy,
                        sig=_sig, venue=_venue, symbol=_symbol,
                        asset_class=_asset_class,
                        underlying_group_id=_group_id, regime=_regime,
                        bars_atr_pct=_atr_pct, last_price=_last_price,
                        universe_rows=universe_rows, now_ts=now_ts,
                        reserve_and_submit=reserve_and_submit,
                        phase=phase,
                    )

                pipeline_specs.append(
                    PipelineTaskSpec(strategy_id=strategy_id, coro_factory=_factory)
                )
    if pipeline_specs:
        results = await supervise_pipeline_tasks(
            pipeline_specs, conn=conn, now_ts=now_ts,
            fault_phase="pipeline_supervisor",
        )
        for r in results:
            if r["exception"] is not None:
                state.fault_events += 1
        state.supervised_tasks_total += len(pipeline_specs)
        state.supervised_tasks_failed += sum(
            1 for r in results if r["exception"] is not None
        )

    _evaluate_swaps(conn, now_ts=now_ts)

    if state.open_trades:
        # codex 2026-05-07 P1.4 fix: forward gpt_client + phase so G8
        # can exercise the GPT P1 lesson branch when phase=="P1". P0 keeps
        # the deterministic Python template (client=None inside the close).
        await close_oldest_with_real_pnl(
            conn, state=state, now_ts=now_ts,
            lookup_regime=_lookup_regime,
            gpt_client=haiku, phase=phase,
        )

    tf_summary = ",".join(
        f"{tf}={state.bars_persisted_by_tf.get(tf, 0)}"
        for tf in sorted(strategies_by_tf)
    )
    logger.info(
        "[tick %d] focus=%d bars_by_tf=%s open=%d closed=%d sized=%d kills=%d",
        tick_idx, len(focus), tf_summary, len(state.open_trades),
        len(state.closed_trades), state.sized_count, state.pipeline_kills,
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
) -> int:
    """Run the production paper loop. Caller owns DB / haiku / lifetime.

    ``phase`` (Day 9 F1+F2 default flipped to ``"P1"``):
    - **P1**: G6/G7/G8 forward the GPT client + ``GPT_P1_MODEL`` so every
      monitor/exit/reflect decision is GPT-backed (Jin per-gate AI mandate).
    - **P0**: G8 deterministic Python template + G6/G7 deterministic rules
      (use for offline determinism tests / dashboard parity audits).
    """
    _load_dotenv()
    target_db = db_path or Path("data/polaris.sqlite")
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

    deadline = time.monotonic() + duration_sec
    tick_idx = 0
    try:
        while time.monotonic() < deadline:
            tick_idx += 1
            try:
                await _run_tick(
                    conn=conn, haiku=haiku, state=state,
                    capital_session=capital_session, tick_idx=tick_idx,
                    phase=phase,
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
