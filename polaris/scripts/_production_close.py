"""Day 8 — production paper loop close-path + real PnL helpers.

Split out from ``_production_pipeline.py`` to keep both files under the
500-line budget. Owns:

* ``real_pnl_r_from_fills`` — entry fill + recent bars → R-units + exit price.
* ``close_oldest_with_real_pnl`` — pop the oldest open trade, compute real
  PnL, persist close fill + flip the position to CLOSED in one transaction,
  update Layer 4 cell matrix, fan out Layer 5 learner updates (fault-isolated
  per learner), invoke G8 reflector + log to ``gate_events``.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.data.fills_persist import persist_fill
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    record_fault,
)
from polaris.core.learners import ClosedTrade, LearnerScheduler
from polaris.core.pipeline.agents import post_trade_reflector_gate
from polaris.core.pipeline.agents._gpt_client import (
    GPT_P0_MODEL,
    GPT_P1_MODEL,
)
from polaris.core.pipeline.gate_orchestrator import log_gate_event
from polaris.core.pipeline.gate_state import (
    GATE_POST_TRADE_REFLECTOR,
    GateContext,
    SignalLifecycle,
)
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_close

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


def real_pnl_r_from_fills(
    conn: sqlite3.Connection, *, trade: SimulatedTrade
) -> tuple[float, float, float]:
    """Read entry fill + most recent bars; compute R-units from real bar drift.

    Returns ``(pnl_r, pnl_usd, exit_price)``. When the bar history is too
    short the R denominator falls back to ``entry_price × 0.5%`` so the
    calculation is finite — but the magnitude reflects the *actual* close
    drift, not a hard-coded sign.

    Day 8 codex P0 fix: matches the entry fill by ``contribution_id =
    position_id`` so two trades on the same (strategy, instrument) can never
    cross-price. Falls back to the legacy heuristic only when the trade has
    no ``position_id`` set (legacy callers).
    """
    if trade.position_id:
        row = conn.execute(
            """
            SELECT fill_price, size_usd FROM fills
            WHERE contribution_id = ? AND is_close = 0
            ORDER BY ts_ms ASC LIMIT 1
            """,
            (trade.position_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT fill_price, size_usd FROM fills
            WHERE strategy_id = ? AND instrument_id = ? AND is_close = 0
            ORDER BY ts_ms DESC LIMIT 1
            """,
            (trade.strategy_id, f"{trade.venue}:{trade.symbol}"),
        ).fetchone()
    if row is None:
        return (0.0, 0.0, trade.entry_price)
    entry_price = float(row[0])
    size_usd = float(row[1])
    trade.entry_price = entry_price
    bar_rows = conn.execute(
        """
        SELECT close, high, low FROM bars
        WHERE instrument_id = ? AND bar_interval = '1m'
        ORDER BY ts DESC LIMIT 14
        """,
        (f"{trade.venue}:{trade.symbol}",),
    ).fetchall()
    if not bar_rows:
        return (0.0, 0.0, entry_price)
    last_close = float(bar_rows[0][0])
    if entry_price <= 0.0 or last_close <= 0.0:
        return (0.0, 0.0, entry_price)
    atr_pct_samples = [
        (float(r[1]) - float(r[2])) / float(r[0])
        for r in bar_rows
        if float(r[0]) > 0.0
    ]
    atr_pct = sum(atr_pct_samples) / len(atr_pct_samples) if atr_pct_samples else 0.005
    pnl_abs = (
        (last_close - entry_price)
        if trade.side == "long"
        else (entry_price - last_close)
    )
    atr_usd = max(entry_price * atr_pct * 2.0, 1e-6)
    pnl_r = pnl_abs / atr_usd
    pnl_usd = (pnl_abs / entry_price) * size_usd
    pnl_r = max(-10.0, min(10.0, pnl_r))
    return (pnl_r, pnl_usd, last_close)


async def close_specific_position(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    position_id: str,
    now_ts: int,
    lookup_regime: Any,
    gpt_client: Any | None = None,
    phase: str = "P0",
) -> bool:
    """Day 9 F2.b — close a specific position by ``position_id``.

    Replaces the FIFO ``state.open_trades[0]`` pop when G6 emits EXIT_NOW
    for a *specific* position. Returns ``True`` when the close persisted,
    ``False`` when the position was not found in ``state.open_trades`` or
    the persist failed (state preserved).
    """
    target: SimulatedTrade | None = None
    target_idx: int | None = None
    for idx, trade in enumerate(state.open_trades):
        if trade.position_id == position_id:
            target = trade
            target_idx = idx
            break
    if target is None or target_idx is None:
        return False
    return await _close_trade_with_real_pnl(
        conn, state=state, trade=target, trade_idx=target_idx, now_ts=now_ts,
        lookup_regime=lookup_regime, gpt_client=gpt_client, phase=phase,
    )


async def close_oldest_with_real_pnl(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    now_ts: int,
    lookup_regime: Any,
    gpt_client: Any | None = None,
    phase: str = "P0",
) -> None:
    """F + G8 fix: real mark-to-market PnL + G8 reflector + gate_events log.

    R3 + R4 hardening:
    * Persist close fill + ``UPDATE positions ... status='closed'`` in one
      transaction (R2 P1).
    * Mutate ``state.open_trades`` only after the DB commit (R2 P1).
    * Record fault + state counter on persist failure (R3 P2).
    * Fault-isolate each learner update + the G8 invocation so a downstream
      exception cannot drop side effects for an already-committed close (R4
      P2).

    GPT P1 dispatch (codex 2026-05-07 P1.4 fix): ``phase="P1"`` forwards
    the ``gpt_client`` + ``GPT_P1_MODEL`` to the G8 reflector so the live
    paper harness can exercise the LLM-driven lesson branch. ``phase="P0"``
    keeps ``client=None`` (deterministic Python template per ADR-004 §Phase).
    """
    if not state.open_trades:
        return
    await _close_trade_with_real_pnl(
        conn, state=state, trade=state.open_trades[0], trade_idx=0,
        now_ts=now_ts, lookup_regime=lookup_regime,
        gpt_client=gpt_client, phase=phase,
    )


async def _close_trade_with_real_pnl(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    trade: SimulatedTrade,
    trade_idx: int,
    now_ts: int,
    lookup_regime: Any,
    gpt_client: Any | None,
    phase: str,
) -> bool:
    """Shared close path used by FIFO oldest + specific-position closes.

    ``trade_idx`` is the index in ``state.open_trades`` to pop on durable
    persist success. Returns ``True`` on persist + fan-out completion.
    """
    pnl_r, pnl_usd, exit_price = real_pnl_r_from_fills(conn, trade=trade)
    close_fill = simulate_close(trade, exit_price=exit_price)
    try:
        conn.execute("BEGIN IMMEDIATE")
        persist_fill(
            conn, close_fill, is_close=True, pnl_usd=pnl_usd,
            contribution_id=trade.position_id,
        )
        if trade.position_id:
            conn.execute(
                "UPDATE positions SET status = 'closed', closed_ts = ? "
                "WHERE position_id = ?",
                (now_ts, trade.position_id),
            )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error(
            "[close] persist_fill close failed (state preserved): %r", exc,
        )
        record_fault(
            conn, strategy_id=trade.strategy_id, fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"phase": "persist_fill_close", "exc": str(exc)},
        )
        state.fault_events += 1
        return False
    # Defensive: re-find the trade by identity in case ``state.open_trades``
    # mutated between caller load and now (concurrent close path).
    if 0 <= trade_idx < len(state.open_trades) and state.open_trades[trade_idx] is trade:
        state.open_trades.pop(trade_idx)
    else:
        with contextlib.suppress(ValueError):
            state.open_trades.remove(trade)
    trade.closed = True
    trade.pnl_r = pnl_r
    state.fills_close += 1
    state.closed_trades.append(trade)

    # Day 8 codex R5 P2 fix: every post-commit auxiliary step is wrapped so a
    # downstream failure cannot drop the rest of the fan-out. ``record_fault``
    # itself can raise (it writes to ``strategy_fault_events`` and reads from
    # ``strategy_halts``); ``_safe_record_fault`` swallows that with a log.
    won = pnl_r > 0.0
    regime = _safe_lookup_regime(lookup_regime, conn, trade)
    _safe_update_cell_matrix(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, won=won, now_ts=now_ts,
        state=state,
    )
    _safe_run_learners(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, won=won, now_ts=now_ts,
        state=state,
    )
    await _safe_run_g8(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, won=won, now_ts=now_ts,
        state=state, gpt_client=gpt_client, phase=phase,
    )
    return True


# ---------------------------------------------------------------------------
# Auxiliary fail-safe helpers (R5 P2 fix)
# ---------------------------------------------------------------------------


def _safe_record_fault(
    conn: sqlite3.Connection, *, strategy_id: str, phase: str, exc: BaseException,
    now_ts: int, state: ProdLoopState,
) -> None:
    """Record a fault without ever propagating an exception out of this call."""
    try:
        record_fault(
            conn, strategy_id=strategy_id, fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"phase": phase, "exc": str(exc)[:200]},
        )
        state.fault_events += 1
    except Exception as inner:  # noqa: BLE001 — fault recording is best-effort
        logger.error(
            "[fault] record_fault itself failed phase=%s strategy=%s: %r",
            phase, strategy_id, inner,
        )


def _safe_lookup_regime(
    lookup_regime: Any, conn: sqlite3.Connection, trade: SimulatedTrade,
) -> str:
    try:
        return str(lookup_regime(conn, trade.venue, trade.symbol))
    except Exception as exc:  # noqa: BLE001 — fail-open to chop
        logger.error("[L6] lookup_regime raised: %r", exc)
        return "chop"


def _safe_update_cell_matrix(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
) -> None:
    try:
        update_on_trade_close(
            conn,
            trade=TradeClose(
                key=CellKeyP0(
                    exchange=trade.venue, strategy=trade.strategy_id,
                    ticker=trade.symbol, regime=regime,
                ),
                context=CellContext(
                    group="spot" if trade.venue == "okx" else "cfd",
                    session="asia", direction=trade.side,
                    liquidity_tier="top",
                ),
                pnl_r=pnl_r, won=won, closed_ts=now_ts,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.error("[L4] cell matrix update failed: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="cell_matrix_update", exc=exc, now_ts=now_ts, state=state,
        )


def _safe_run_learners(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
) -> None:
    try:
        sched = LearnerScheduler(conn, expected_holding_bars=20)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.error("[L5] LearnerScheduler init raised: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="learner_init", exc=exc, now_ts=now_ts, state=state,
        )
        return
    closed_record = ClosedTrade(
        trade_id=trade.signal_id, strategy_id=trade.strategy_id,
        ticker=trade.symbol, venue=trade.venue, regime=regime,
        session="asia", pnl_r=pnl_r, won=won, holding_bars=20, closed_ts=now_ts,
    )
    for learner in sched.learners:
        try:
            learner.update(closed_record, now_ts=now_ts)
        except Exception as exc:  # noqa: BLE001 — fault-isolate per learner
            logger.error(
                "[L5] learner %s update raised: %r",
                getattr(learner, "learner_id", "<unknown>"), exc,
            )
            _safe_record_fault(
                conn, strategy_id=trade.strategy_id,
                phase=f"learner_update_{getattr(learner, 'learner_id', 'unknown')}",
                exc=exc, now_ts=now_ts, state=state,
            )


async def _safe_run_g8(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
    gpt_client: Any | None = None, phase: str = "P0",
) -> None:
    g8_ctx = GateContext(
        run_id=uuid.uuid4().hex, signal_id=trade.signal_id,
        position_id=trade.position_id or f"pos_{trade.signal_id[:10]}",
        gate_id=GATE_POST_TRADE_REFLECTOR,
        venue=trade.venue, symbol=trade.symbol,
        strategy_id=trade.strategy_id,
        payload={
            "closed_trade": {
                "trade_id": trade.signal_id,
                "strategy_id": trade.strategy_id,
                "regime": regime, "session": "asia",
                "pnl_r": pnl_r, "won": won,
            },
            "closed_trade_count": len(state.closed_trades),
        },
        started_ts=now_ts, state=SignalLifecycle.CLOSED,
    )
    # codex 2026-05-07 P1.4 fix: phase-aware G8 dispatch so the production
    # paper harness can exercise the GPT P1 lesson branch (was hardcoded
    # ``client=None``, which silently kept G8 on the Python template even
    # when phase=="P1"). ADR-004 §Phase invariant honoured by passing
    # ``GPT_P0_MODEL`` (unused, client=None) at P0 vs. ``GPT_P1_MODEL`` +
    # gpt_client at P1.
    if phase == "P1" and gpt_client is not None:
        client_arg: Any = gpt_client
        model_arg = GPT_P1_MODEL
    else:
        client_arg = None
        model_arg = GPT_P0_MODEL
    try:
        g8_result = await post_trade_reflector_gate(
            g8_ctx, client=client_arg, conn=conn, model=model_arg,
        )
        log_gate_event(conn, g8_ctx, g8_result)
        state.g8_runs += 1
    except Exception as exc:  # noqa: BLE001 — G8 must not drop a closed trade
        logger.error("[G8] reflector raised: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="g8_reflector", exc=exc, now_ts=now_ts, state=state,
        )
