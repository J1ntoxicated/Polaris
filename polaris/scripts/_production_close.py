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
import os
import sqlite3
from typing import TYPE_CHECKING, Any

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    record_fault,
)
from polaris.core.lineage import record_segment_close
from polaris.core.live_recalc.excursion import compute_excursion_r
from polaris.scripts._production_close_effects import (
    _safe_lookup_regime,
    _safe_record_meta_label,
    _safe_run_g8,
    _safe_run_learners,
    _safe_update_cell_matrix,
    _safe_update_posterior,
)
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_close
from polaris.scripts._smoke_real_roundtrip import (
    real_capital_close_fill,
    real_okx_close_fill,
    resolve_okx_base_url,
)
from polaris.venues.capital import CapitalAdapter
from polaris.venues.okx import OKXAdapter

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


def real_pnl_r_from_fills(
    conn: sqlite3.Connection, *, trade: SimulatedTrade,
    exit_price_override: float | None = None,
) -> tuple[float, float, float]:
    """Read entry fill + most recent bars; compute R-units from real bar drift.

    Returns ``(pnl_r, pnl_usd, exit_price)``. When the bar history is too
    short the R denominator falls back to ``entry_price × 0.5%`` so the
    calculation is finite — but the magnitude reflects the *actual* close
    drift, not a hard-coded sign.

    ``exit_price_override`` (P1-6 venue-wire fix): when set (real-roundtrip
    close), ``pnl_r`` / ``pnl_usd`` are computed against the **real exit fill
    price** instead of the most recent bar close, using the same ATR
    denominator. This keeps Layer 4/5 telemetry consistent with what was
    actually traded — without it a loss exit could be logged as a win when
    the seeded bars happened to trend the other way.

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
        fallback_exit = exit_price_override if exit_price_override else trade.entry_price
        return (0.0, 0.0, fallback_exit)
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
    # P1-6: an override exit price must drive pnl_r/pnl_usd even when there is
    # no bar history (the real close already gives us the true exit level).
    if not bar_rows and exit_price_override is None:
        return (0.0, 0.0, entry_price)
    bar_close = float(bar_rows[0][0]) if bar_rows else entry_price
    exit_price = exit_price_override if exit_price_override else bar_close
    if entry_price <= 0.0 or exit_price <= 0.0:
        return (0.0, 0.0, entry_price)
    atr_pct = _atr_pct_from_bars(bar_rows)
    pnl_abs = (
        (exit_price - entry_price)
        if trade.side == "long"
        else (entry_price - exit_price)
    )
    atr_usd = max(entry_price * atr_pct * 2.0, 1e-6)
    pnl_r = pnl_abs / atr_usd
    pnl_usd = (pnl_abs / entry_price) * size_usd
    pnl_r = max(-10.0, min(10.0, pnl_r))
    return (pnl_r, pnl_usd, exit_price)


def _atr_pct_from_bars(bar_rows: list[Any]) -> float:
    """Mean (high-low)/close over recent 1m bars; 0.005 fallback when empty.

    Extracted from ``real_pnl_r_from_fills`` so the close-time excursion write
    shares the *same* ATR-pct denominator the realised-PnL path uses (no drift
    between pnl_r and mfe_r/mae_r R units). Pure.
    """
    samples = [
        (float(r[1]) - float(r[2])) / float(r[0])
        for r in bar_rows
        if float(r[0]) > 0.0
    ]
    return sum(samples) / len(samples) if samples else 0.005


def _close_excursion_r(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, exit_price: float,
) -> tuple[float, float]:
    """Best-effort ``(mfe_r, mae_r)`` for a position at close time.

    BUILD_SCHEMA prerequisite: the tick loop does not yet populate the
    ``positions.peak_price`` / ``trough_price`` extremes (a later precise-exit
    stream owns that). So this reads whatever extremes the position row carries
    and falls back to the observed entry/exit bounds when they are NULL — a
    position that only ever recorded its entry and exit still yields a finite,
    correctly-signed excursion (never *under*-states MFE/MAE relative to the
    realised move). The R denominator is re-derived from the same entry fill +
    recent bars as ``real_pnl_r_from_fills`` so pnl_r and mfe_r/mae_r share one
    risk unit. Returns ``(0.0, 0.0)`` only when entry price is unknowable.
    """
    entry_price = trade.entry_price
    if entry_price <= 0.0:
        row = conn.execute(
            "SELECT fill_price FROM fills WHERE contribution_id = ? "
            "AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
            (trade.position_id,),
        ).fetchone() if trade.position_id else None
        if row is None:
            return (0.0, 0.0)
        entry_price = float(row[0])
    if entry_price <= 0.0:
        return (0.0, 0.0)
    bar_rows = conn.execute(
        """
        SELECT close, high, low FROM bars
        WHERE instrument_id = ? AND bar_interval = '1m'
        ORDER BY ts DESC LIMIT 14
        """,
        (f"{trade.venue}:{trade.symbol}",),
    ).fetchall()
    atr_usd = max(entry_price * _atr_pct_from_bars(bar_rows) * 2.0, 1e-6)
    # Read tracked extremes; fall back to entry/exit bounds when the tick loop
    # has not populated them. peak = max(entry, exit, tracked_peak); trough =
    # min(entry, exit, tracked_trough) so the excursion is never under-stated.
    tracked_peak: float | None = None
    tracked_trough: float | None = None
    if trade.position_id:
        prow = conn.execute(
            "SELECT peak_price, trough_price FROM positions WHERE position_id = ?",
            (trade.position_id,),
        ).fetchone()
        if prow is not None:
            tracked_peak = None if prow[0] is None else float(prow[0])
            tracked_trough = None if prow[1] is None else float(prow[1])
    peak = max(entry_price, exit_price, tracked_peak or entry_price)
    trough = min(entry_price, exit_price, tracked_trough or entry_price)
    return compute_excursion_r(
        entry_price=entry_price, peak_price=peak, trough_price=trough,
        side=trade.side, atr_usd=atr_usd,
    )


async def _real_close_fill(
    *,
    trade: SimulatedTrade,
    okx_adapter: Any = None,
    capital_session: Any = None,
) -> Fill | None:
    """Drive the real demo venue close leg → return the exit ``Fill``.

    P0 venue wire: OKX sells the entry ``base_qty``; Capital closes by
    ``deal_id``. Adapters are injected for testability; when ``okx_adapter``
    is ``None`` we build one from ``OKX_DEMO_*`` env. Returns ``None`` on
    reject / no-fill so the caller falls back to mark-to-market only.
    """
    if trade.venue == "okx":
        if okx_adapter is not None:
            return await real_okx_close_fill(
                okx_adapter, inst_id=trade.symbol, base_qty=trade.base_qty,
                strategy_id=trade.strategy_id,
            )
        api_key = os.environ.get("OKX_DEMO_API_KEY", "")
        secret = os.environ.get("OKX_DEMO_SECRET", "")
        passphrase = os.environ.get("OKX_DEMO_PASSPHRASE", "")
        base_url = resolve_okx_base_url(os.environ.get("OKX_DEMO_BASE"))
        if not (api_key and secret and passphrase):
            logger.error("[real-close] OKX_DEMO_* env missing — cannot close")
            return None
        async with OKXAdapter(
            api_key=api_key, secret=secret, passphrase=passphrase, base_url=base_url,
        ) as adapter:
            return await real_okx_close_fill(
                adapter, inst_id=trade.symbol, base_qty=trade.base_qty,
                strategy_id=trade.strategy_id,
            )
    # Capital CFD — close by deal_id captured at open.
    if capital_session is None or not trade.deal_id:
        logger.error(
            "[real-close] Capital close needs session + deal_id (deal_id=%s)",
            trade.deal_id,
        )
        return None
    cap_adapter = CapitalAdapter(capital_session)
    return await real_capital_close_fill(
        cap_adapter, deal_id=trade.deal_id, strategy_id=trade.strategy_id,
    )


async def close_specific_position(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    position_id: str,
    now_ts: int,
    lookup_regime: Any,
    gpt_client: Any | None = None,
    phase: str = "P0",
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    close_reason: str = "",
) -> bool:
    """Day 9 F2.b — close a specific position by ``position_id``.

    Replaces the FIFO ``state.open_trades[0]`` pop when G6 emits EXIT_NOW
    for a *specific* position. Returns ``True`` when the close persisted,
    ``False`` when the position was not found in ``state.open_trades`` or
    the persist failed (state preserved).

    ``close_reason`` (lineage read-model only) names the exit trigger the
    caller fired on (precise-exit fsm / session calendar / rotation / g6); it
    is recorded onto the lineage segment and changes NO close behaviour.
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
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, close_reason=close_reason,
    )


async def close_oldest_with_real_pnl(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    now_ts: int,
    lookup_regime: Any,
    gpt_client: Any | None = None,
    phase: str = "P0",
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    close_reason: str = "",
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
        real_roundtrip=real_roundtrip, okx_adapter=okx_adapter,
        capital_session=capital_session, close_reason=close_reason,
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
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    close_reason: str = "",
) -> bool:
    """Shared close path used by FIFO oldest + specific-position closes.

    ``trade_idx`` is the index in ``state.open_trades`` to pop on durable
    persist success. Returns ``True`` on persist + fan-out completion.

    ``real_roundtrip=True`` (P0 venue wire) submits a real demo close order
    and derives ``exit_price``/``pnl`` from the actual exit fill; the sim path
    keeps the existing mark-to-market drift. PnL_R is always recomputed from
    the entry fill so Layer 4/5 telemetry stays consistent.
    """
    if real_roundtrip:
        # P0 venue wire: drive the real close leg FIRST so pnl_r/pnl_usd are
        # computed against the actual exit fill (not the pre-close bar drift).
        real_fill = None
        try:
            real_fill = await _real_close_fill(
                trade=trade, okx_adapter=okx_adapter,
                capital_session=capital_session,
            )
        except Exception as exc:  # noqa: BLE001 — venue I/O must not escape
            # Genuine adapter exception (transport/parse) — internal fault.
            logger.error(
                "[close/real] %s:%s close adapter raised: %r — state preserved",
                trade.venue, trade.symbol, exc,
            )
            record_fault(
                conn, strategy_id=trade.strategy_id, fault_type=FAULT_EXCEPTION,
                now_ts=now_ts,
                detail={"phase": "real_close_fill_exc", "symbol": trade.symbol},
            )
            state.fault_events += 1
            return False
        if real_fill is None:
            # Venue reject / no-fill (EXTERNAL — e.g. 51020 min-order, compliance,
            # market closed) is NOT a strategy fault: preserve the position +
            # retry next tick. A venue decision must not trip the strategy
            # circuit breaker (integrity-only philosophy; close failure is
            # self-healing). The prior unconditional FAULT_EXCEPTION here caused
            # a HARD_HALT cascade when a sub-min position kept failing to close.
            logger.warning(
                "[close/real] %s:%s close rejected / no-fill — state preserved (external, no fault)",
                trade.venue, trade.symbol,
            )
            state.venue_close_rejects += 1
            return False
        # P1-6: recompute pnl_r AND pnl_usd from the real exit price so the
        # Layer 4 cell matrix + Layer 5 learner updates reflect what actually
        # traded (a real loss must not be logged as a win because the seeded
        # bars trended up).
        pnl_r, pnl_usd, exit_price = real_pnl_r_from_fills(
            conn, trade=trade, exit_price_override=real_fill.fill_price,
        )
        close_fill = real_fill
    else:
        pnl_r, pnl_usd, exit_price = real_pnl_r_from_fills(conn, trade=trade)
        close_fill = simulate_close(trade, exit_price=exit_price)
    # BUILD_SCHEMA: persist final MFE/MAE (R units) + exit_state at close.
    # Best-effort from tracked peak/trough vs entry — measurement only, never
    # gates sizing or blocks entry. Computed BEFORE the write txn (pure read).
    mfe_r, mae_r = _close_excursion_r(conn, trade=trade, exit_price=exit_price)
    try:
        conn.execute("BEGIN IMMEDIATE")
        persist_fill(
            conn, close_fill, is_close=True, pnl_usd=pnl_usd,
            contribution_id=trade.position_id,
        )
        if trade.position_id:
            conn.execute(
                "UPDATE positions SET status = 'closed', closed_ts = ?, "
                "mfe_r = ?, mae_r = ?, exit_state = 'closed' "
                "WHERE position_id = ?",
                (now_ts, mfe_r, mae_r, trade.position_id),
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

    # Successful close (INFO): the realised-close 거동 record Jin asked for —
    # the venue/ticker, the close fill price + size, the realised pnl_r/pnl_usd,
    # the held duration, and whether it was a win. Pairs with the EXIT-TRIGGER
    # reason INFO in run_precise_exit (correlate on trade_id=position_id). The
    # "filled" + "close" keywords surface it on the dashboard board.js pane.
    # Log only — the close already committed above; this changes no behaviour.
    held_seconds = max(0, now_ts - getattr(trade, "open_ts", now_ts))
    logger.info(
        "[close] closed %s:%s trade_id=%s filled exit_price=%.6g size_usd=%.2f "
        "pnl_r=%.2f pnl_usd=%.2f held=%ds won=%s mode=%s",
        trade.venue, trade.symbol, trade.position_id or "-",
        exit_price, close_fill.size_usd, pnl_r, pnl_usd, held_seconds,
        pnl_r > 0.0, "real" if real_roundtrip else "sim",
    )

    # Day 8 codex R5 P2 fix: every post-commit auxiliary step is wrapped so a
    # downstream failure cannot drop the rest of the fan-out. ``record_fault``
    # itself can raise (it writes to ``strategy_fault_events`` and reads from
    # ``strategy_halts``); ``_safe_record_fault`` swallows that with a log.
    won = pnl_r > 0.0
    regime = _safe_lookup_regime(lookup_regime, conn, trade)
    # P3 self-evolve lineage (read-model, behaviour 0): stamp exit_ts /
    # exit_reason / realised pnl onto the open lineage segment. Post-commit +
    # fail-open inside the helper — never alters the already-committed close.
    # ``close_reason`` falls back to 'exit' when the caller did not name a
    # trigger (e.g. plain G6 EXIT_NOW / FIFO close).
    if trade.position_id:
        record_segment_close(
            conn, position_id=trade.position_id, exit_ts=now_ts,
            exit_reason=close_reason or "exit", pnl_r=pnl_r, pnl_usd=pnl_usd,
        )
    _safe_update_cell_matrix(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, won=won, now_ts=now_ts,
        state=state,
    )
    _safe_run_learners(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, won=won, now_ts=now_ts,
        state=state,
    )
    # Meta-labeling (#10) — collection-only triple-barrier label per close.
    # Never gates sizing/exits; fail-open inside the helper.
    _safe_record_meta_label(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, won=won, now_ts=now_ts,
        state=state,
    )
    # Edge-validation Phase 1 — cost-adjusted expectancy posterior (measure +
    # display only; never wired into sizing). Fail-open inside the helper.
    _safe_update_posterior(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, pnl_usd=pnl_usd,
        now_ts=now_ts,
    )
    await _safe_run_g8(
        conn, trade=trade, regime=regime, pnl_r=pnl_r, won=won, now_ts=now_ts,
        state=state, gpt_client=gpt_client, phase=phase,
    )
    return True
