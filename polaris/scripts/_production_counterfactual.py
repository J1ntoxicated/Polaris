"""Gate→outcome counterfactual instrumentation (BUILD, behavior 0).

DEMO/PAPER only — virtual funds. This module MEASURES the G3 entry gate
(historically G3/G4 GPT — G4 is abolished as a decision step, P2a group A)
so its value can be judged with data:

* ``record_pipeline_cohort`` — one ``gate_kill_counterfactuals`` row per G3
  decision on the bar entry pipeline. KILL rows capture the decision-time
  mark (live WS tick preferred, bar close fallback — recorded as
  ``mark_source`` so stale marks are auditable) + the round-trip REAL fee in
  the SAME per-unit ATR-R basis as the forward returns
  (``2 × real_fee_usd(venue, mark) / atr_usd`` ≡ fee_rate / atr_pct —
  price-level independent). PASS rows share the SAME mark/horizon estimator so
  KILL-vs-PASS is apples-to-apples (the exit engine's effect is isolated into
  the auxiliary ``realized_pnl_r`` view column instead of polluting the
  comparison).
* ``backfill_run_position_id`` — stamps a successful open's ``position_id``
  onto the run's pre-position ``gate_events`` rows (run_id-scoped, indexed)
  so the PASS cohort joins gate_events → positions.pnl_r without relying on
  the non-unique tick-engine ``signal_id``.
* ``sweep_forward_marks`` — resolves pending forward marks (1h/4h/24h) from
  ALREADY-INGESTED 1m bars (zero new network calls), throttled + LIMIT-batched
  by the caller's tick. Rows whose symbol never re-ingests bars are terminated
  ``unresolvable`` after a grace window so they cannot starve the batch.

Every write here is fail-open (warning, never a crash) and NOTHING in this
module is read by any entry/exit/sizing/gate decision — instrumentation only.
The KILL counterfactual ignores fill reality (slippage / partial fills /
min-order rejects), so fwd_r is an UPPER-BOUND estimate of what a killed
signal would have earned.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.economics.fees import real_fee_usd
from polaris.core.pipeline.gate_state import (
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)

if TYPE_CHECKING:
    from polaris.scripts._production_state import ProdLoopState
    from polaris.strategies import RawSignal

logger = logging.getLogger(__name__)

__all__ = [
    "CF_SWEEP_THROTTLE_SEC",
    "HORIZONS",
    "backfill_run_position_id",
    "record_pipeline_cohort",
    "sweep_forward_marks",
]

# Forward-mark horizons: (column label, seconds after the gate decision).
HORIZONS: tuple[tuple[str, int], ...] = (("1h", 3600), ("4h", 14400), ("24h", 86400))

# Sweep cadence/batch — piggybacks the bar-ingest tick; never a hot-path scan.
CF_SWEEP_THROTTLE_SEC: float = 60.0
SWEEP_LIMIT: int = 200

# A WS tick older than this (monotonic age) is not a usable decision mark —
# fall back to the strategy-timeframe bar close (recorded in mark_source).
MARK_FRESH_SEC: float = 60.0

# Terminal stamp: once every horizon has expired AND this grace has passed
# with the 24h mark still unresolved (symbol fell out of focus → no bars),
# the row is closed out ``unresolvable`` so it stops occupying the sweep batch.
UNRESOLVABLE_GRACE_SEC: int = 3 * 86400

# R-unit floor mirrors _production_close_helpers (atr_usd >= 0.01% of price).
_ATR_USD_FLOOR_FRAC: float = 1e-4


def _resolve_mark(
    state: ProdLoopState, *, venue: str, symbol: str, last_price: float,
    timeframe: str,
) -> tuple[float, str]:
    """Decision-time mark: fresh live WS mid preferred, bar close fallback.

    The bar close (``last_price`` = strategy-timeframe closes[-1]) can be up
    to one bar stale (Capital 1H strategies) — attributing pre-KILL drift to
    the counterfactual. A fresh WS tick removes that bias; the source label is
    persisted so stale-mark rows can be excluded/audited in the cohort query.
    """
    quote_writer = getattr(state, "quote_writer", None)
    if quote_writer is not None:
        px = quote_writer.live_px(f"{venue}:{symbol}")
        if px is not None:
            mid, mono = float(px[0]), float(px[1])
            if mid > 0.0 and (time.monotonic() - mono) <= MARK_FRESH_SEC:
                return mid, "ws_tick"
    return last_price, f"bar_close:{timeframe}"


def record_pipeline_cohort(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    ctx: GateContext,
    results: list[GateResult],
    sig: RawSignal,
    venue: str,
    symbol: str,
    regime: str,
    last_price: float,
    atr_pct: float,
    timeframe: str,
    now_ts: int,
) -> None:
    """Append one KILL/PASS cohort row for this pipeline run (fail-open).

    KILL row: the run terminated KILLED at G3 (``ctx.gate_id`` is the killer;
    the KILL result is always last). Exception fail-closed KILLs are included
    and distinguishable via ``model_used``/``reason``. PASS row: the run
    cleared G3, regardless of the later G5 outcome (the gate passed it;
    sizing is not its call) — so the killer-gate check comes FIRST: only a G3
    KILL takes the KILL branch, a G5 cap-kill after a G3 PASS/MODIFY still
    records PASS. G1/G2/G5 KILLs without a G3 clear record nothing. Never
    branches the pipeline.

    P2a group A (2026-07-16): Gate 4 (Pre-Entry Watcher) is abolished as a
    decision step — G3 wires directly to G5 and its own decision tokens are
    PASS/MODIFY/KILL (``GateDecision.PROCEED`` was G4-only and is no longer
    emitted here). "Cleared G3" is identified POSITIONALLY: the orchestrator
    walks G1→G2→G3→G5 strictly sequentially with no variable-length branch
    before G3, so ``results[2]`` is G3's own result whenever the run reaches
    that far (a G1/G2 KILL truncates ``results`` to length < 3 first).

    Storage-split (2026-07-14): ``gate_kill_counterfactuals`` is a
    marketdata-domain table (owned by ``MARKETDATA_DDL`` — ``sweep_forward_marks``
    reads/updates it on ``state.md_conn``, per ``_production_tick.py``). The
    INSERT below writes ``state.md_conn`` when set, falling back to ``conn``
    (byte-identical for every existing single-conn test/caller — ``state.md_conn``
    defaults to ``None``). Without this, pending rows accumulate in the trading
    file forever while the sweep's marketdata copy stays empty — the
    gate_kill_value probe never resolves anything.
    """
    try:
        if not results:
            return
        killed_by_gpt_gate = (
            ctx.state is SignalLifecycle.KILLED
            and ctx.gate_id == GATE_SIGNAL_VALIDATOR
            and any(r.decision == GateDecision.KILL for r in results)
        )
        if killed_by_gpt_gate:
            last = results[-1]
            decision, gate_id = "KILL", int(ctx.gate_id)
            reason = str(last.payload.get("reason", ""))[:200]
            model_used = str(last.model_used or "")
        elif len(results) >= 3 and results[2].decision in (
            GateDecision.PASS, GateDecision.MODIFY,
        ):
            decision, gate_id = "PASS", GATE_SIGNAL_VALIDATOR
            reason, model_used = "g3_pass", ""
        else:
            return
        mark, mark_source = _resolve_mark(
            state, venue=venue, symbol=symbol, last_price=last_price,
            timeframe=timeframe,
        )
        if mark <= 0.0:
            return  # no usable mark → no counterfactual (never a fake row)
        atr_usd = max(mark * atr_pct * 2.0, mark * _ATR_USD_FLOOR_FRAC)
        cost_r = (2.0 * real_fee_usd(venue, mark)) / atr_usd
        _md = state.md_conn if state.md_conn is not None else conn
        _md.execute(
            """
            INSERT INTO gate_kill_counterfactuals
                (event_id, run_id, signal_id, gate_id, decision, venue, symbol,
                 strategy_id, side, regime, reason, model_used, decision_ts,
                 mark_price, mark_ts, mark_source, atr_pct, atr_usd, cost_r,
                 created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, ctx.run_id, sig.signal_id, gate_id, decision,
                venue, symbol, sig.strategy_id, sig.side, regime, reason,
                model_used, now_ts, mark, now_ts, mark_source, atr_pct,
                atr_usd, cost_r, now_ts,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — instrumentation must never crash
        logger.warning(
            "[gate-cohort] counterfactual row dropped %s:%s: %r",
            venue, symbol, exc,
        )


def backfill_run_position_id(
    conn: sqlite3.Connection, *, run_id: str, position_id: str | None
) -> None:
    """Stamp this run's pre-position gate_events rows with the opened position.

    run_id-scoped (idx_gate_events_run covers it) so the non-unique tick-engine
    ``signal_id`` can never cross-link a foreign run. Only NULL position_id
    rows (G1-G5, logged before the position existed) are touched. Fail-open.
    """
    if not position_id:
        return
    try:
        conn.execute(
            "UPDATE gate_events SET position_id = ? "
            "WHERE run_id = ? AND position_id IS NULL",
            (position_id, run_id),
        )
    except sqlite3.Error as exc:
        logger.warning(
            "[gate-cohort] position_id backfill dropped run_id=%s: %r",
            run_id, exc,
        )


async def sweep_forward_marks(
    conn: sqlite3.Connection, *, now_ts: int, limit: int = SWEEP_LIMIT
) -> int:
    """Resolve pending forward marks from already-ingested 1m bars.

    For each pending row whose horizon(s) have expired, the FIRST 1m bar at or
    after ``decision_ts + horizon`` supplies ``fwd_close/fwd_ts`` (weekend /
    session gaps resolve to the first post-gap bar — ``fwd_ts`` keeps the lag
    auditable) and ``fwd_r = sign(side) × (fwd_close − mark) / atr_usd``.
    Reads run outside any transaction (cooperative yields between rows); the
    collected UPDATEs are applied in ONE short synchronous transaction so the
    5s tick never interleaves a foreign BEGIN. Returns rows updated.
    """
    try:
        rows = conn.execute(
            """
            SELECT event_id, venue, symbol, side, decision_ts, mark_price,
                   atr_usd, fwd_r_1h, fwd_r_4h, fwd_r_24h
            FROM gate_kill_counterfactuals
            WHERE fwd_r_24h IS NULL AND unresolvable = 0
              AND decision_ts + 3600 <= ?
            ORDER BY decision_ts ASC LIMIT ?
            """,
            (now_ts, int(limit)),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[gate-cohort] sweep read failed: %r", exc)
        return 0
    if not rows:
        return 0

    updates: list[tuple[str, list[Any]]] = []
    for i, row in enumerate(rows):
        if i % 20 == 0:
            await asyncio.sleep(0)  # event-loop fairness (tick-engine / WS)
        event_id, venue, symbol = str(row[0]), str(row[1]), str(row[2])
        side, decision_ts = str(row[3]), int(row[4])
        mark, atr_usd = float(row[5]), float(row[6])
        existing = {"1h": row[7], "4h": row[8], "24h": row[9]}
        sign = 1.0 if side == "long" else -1.0
        sets: list[str] = []
        params: list[Any] = []
        resolved_24h = existing["24h"] is not None
        for label, horizon_sec in HORIZONS:
            if existing[label] is not None:
                continue
            horizon_ts = decision_ts + horizon_sec
            if horizon_ts > now_ts:
                continue
            bar = conn.execute(
                "SELECT close, ts FROM bars WHERE instrument_id = ? "
                "AND bar_interval = '1m' AND ts >= ? ORDER BY ts ASC LIMIT 1",
                (f"{venue}:{symbol}", horizon_ts),
            ).fetchone()
            if bar is None:
                continue
            fwd_close, fwd_ts = float(bar[0]), int(bar[1])
            fwd_r = sign * (fwd_close - mark) / atr_usd if atr_usd > 0.0 else 0.0
            sets.extend(
                [f"fwd_close_{label} = ?", f"fwd_ts_{label} = ?", f"fwd_r_{label} = ?"]
            )
            params.extend([fwd_close, fwd_ts, fwd_r])
            if label == "24h":
                resolved_24h = True
        # Terminal stamp (starvation guard): every horizon expired + grace
        # passed and the 24h mark still has no bar → close out the row.
        if (
            not resolved_24h
            and decision_ts + 86400 + UNRESOLVABLE_GRACE_SEC <= now_ts
        ):
            sets.append("unresolvable = 1")
        sets.append("resolve_attempts = resolve_attempts + 1")
        params.append(event_id)
        updates.append(
            (
                "UPDATE gate_kill_counterfactuals SET "
                + ", ".join(sets)
                + " WHERE event_id = ?",
                params,
            )
        )

    # One short sync transaction (no awaits inside — shared-conn safety).
    try:
        conn.execute("BEGIN IMMEDIATE")
        for sql, sql_params in updates:
            conn.execute(sql, sql_params)
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.warning("[gate-cohort] sweep update failed: %r", exc)
        return 0
    return len(updates)
