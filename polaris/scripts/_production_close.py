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
import json
import logging
import os
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.core.data.position_risk_persist import delete_position_risk_state
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    record_fault,
)
from polaris.core.lineage import record_segment_close
from polaris.scripts._production_close_effects import (
    _safe_lookup_regime,
    _safe_record_meta_label,
    _safe_run_g8,
    _safe_run_learners,
    _safe_update_cell_matrix,
    _safe_update_posterior,
)
from polaris.scripts._production_close_helpers import (
    _CLOSE_FULL_FILL_EPS,
    _close_excursion_r,
    _latest_bar_close,
    _persist_partial_close,
    real_pnl_r_from_fills,
)
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_close
from polaris.scripts._smoke_real_roundtrip import (
    CloseOrphan,
    real_capital_close_fill,
    real_okx_close_fill,
    resolve_okx_base_url,
)
from polaris.venues.capital import CapitalAdapter
from polaris.venues.okx import OKXAdapter

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

# Explicit re-export: ``real_pnl_r_from_fills`` / ``_close_excursion_r`` live in
# ``_production_close_helpers`` (line budget) but are part of this module's
# public surface — ``_production_pipeline`` re-exports the former and the close
# tests import both via ``from polaris.scripts._production_close import ...``.
__all__ = [
    "_close_excursion_r",
    "close_oldest_with_real_pnl",
    "close_specific_position",
    "real_pnl_r_from_fills",
]


async def _real_close_fill(
    *,
    trade: SimulatedTrade,
    fresh_mark: float | None = None,
    okx_adapter: Any = None,
    capital_session: Any = None,
) -> Fill | CloseOrphan | None:
    """Drive the real demo venue close leg → return the exit ``Fill``.

    P0 venue wire: OKX sells the entry ``base_qty``; Capital closes by
    ``deal_id``. Adapters are injected for testability; when ``okx_adapter``
    is ``None`` we build one from ``OKX_DEMO_*`` env. Returns ``None`` on
    reject / no-fill so the caller falls back to mark-to-market only.

    ``fresh_mark`` (FIX A): the latest 1m bar close the OKX cap-split sizes
    against (fall back to ``trade.entry_price`` only when no bar exists) so a
    risen-price close cannot tip a child over the venue cap.
    """
    if trade.venue == "okx":
        mark = fresh_mark if fresh_mark and fresh_mark > 0.0 else trade.entry_price
        if okx_adapter is not None:
            return await real_okx_close_fill(
                okx_adapter, inst_id=trade.symbol, base_qty=trade.base_qty,
                strategy_id=trade.strategy_id, mark_price=mark,
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
                strategy_id=trade.strategy_id, mark_price=mark,
            )
    # Capital CFD — close by deal_id captured at open.
    if capital_session is None:
        # Transient: no session this tick (e.g. token refresh) — retry next tick.
        logger.error("[real-close] Capital close needs a session — retry next tick")
        return None
    if not trade.deal_id:
        # No deal_id anywhere (positions.deal_id NULL + no fill order_id stash):
        # the open never captured one (a confirm that never left PENDING). The
        # venue position is un-addressable, so retrying forever just error-loops →
        # reconcile (terminal) and let the exit engine stop. flow_not_block: no
        # fault, no throttle; new opens DO capture deal_id (confirm-poll + persist).
        logger.warning(
            "[real-close] Capital position %s has no deal_id (un-addressable "
            "orphan) — reconciling",
            trade.position_id,
        )
        return CloseOrphan(available=0.0)
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


def _reconcile_orphan(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    trade: SimulatedTrade,
    trade_idx: int,
    available: float,
    now_ts: int,
) -> bool:
    """FIX 2 — mark a true-orphan position terminal so it stops being retried.

    ``status='reconciled'`` denotes VENUE-SIDE STATE-DRIFT RECOVERY (the wallet
    no longer holds the tracked base_qty — an over-count), NOT a trade outcome:
    NO close fill is persisted and NO realized PnL is recorded. The position is
    popped from ``state.open_trades`` (it is NOT appended to ``closed_trades`` —
    there is no outcome to reflect on), and an ``orphan_reconciled`` audit row is
    written to ``risk_events``. Returns ``True`` (handled) so the caller does not
    treat it as a transient reject. Idempotent: a re-attempt on an already
    reconciled row is a no-op UPDATE. flow_not_block — no fault, no throttle.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        if trade.position_id:
            conn.execute(
                "UPDATE positions SET status = 'reconciled', closed_ts = ?, "
                "exit_state = 'reconciled' WHERE position_id = ?",
                (now_ts, trade.position_id),
            )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error(
            "[close/orphan] %s:%s reconcile UPDATE failed (state preserved): %r",
            trade.venue, trade.symbol, exc,
        )
        state.venue_close_rejects += 1
        return False
    # Pop from open_trades by identity (defensive re-find, mirrors the close path).
    if 0 <= trade_idx < len(state.open_trades) and state.open_trades[trade_idx] is trade:
        state.open_trades.pop(trade_idx)
    else:
        with contextlib.suppress(ValueError):
            state.open_trades.remove(trade)
    trade.closed = True
    # Audit row (best-effort) — durable record of the reconcile for forensics.
    audit = json.dumps(
        {
            "venue": trade.venue, "symbol": trade.symbol,
            "position_id": trade.position_id, "base_qty": trade.base_qty,
            "available": available, "reason": "available~0 qty over-count",
        },
        separators=(",", ":"),
    )
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT INTO risk_events "
            "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
            "VALUES (?, ?, 'orphan_reconciled', ?, ?)",
            (uuid.uuid4().hex, trade.strategy_id, now_ts, audit),
        )
    logger.warning(
        "[close/orphan] %s:%s trade_id=%s reconciled (available=%.10f ~0, "
        "base_qty=%.10f over-count) — status='reconciled', no fill, no pnl; "
        "exit engine + hydrate stop retrying (state-drift recovery)",
        trade.venue, trade.symbol, trade.position_id or "-", available,
        trade.base_qty,
    )
    return True


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
        fresh_mark = _latest_bar_close(conn, venue=trade.venue, symbol=trade.symbol)
        real_fill = None
        try:
            real_fill = await _real_close_fill(
                trade=trade, fresh_mark=fresh_mark, okx_adapter=okx_adapter,
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
        if isinstance(real_fill, CloseOrphan):
            # FIX 2 — TRUE ORPHAN (wallet available ~0 while the book still tracks
            # base_qty, an over-count from the close-chunk fix). Mark the position
            # terminal (status='reconciled') so the exit engine + hydrate STOP
            # retrying it forever — WITHOUT fabricating a fill or PnL. This is
            # venue-side STATE-DRIFT RECOVERY, not a trade outcome.
            return _reconcile_orphan(
                conn, state=state, trade=trade, trade_idx=trade_idx,
                available=real_fill.available, now_ts=now_ts,
            )
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
        # FIX B: a genuine partial (child reject / within-child) returns less
        # than the tracked qty — keep the position OPEN with a reduced qty (see
        # _persist_partial_close); only a ~full fill closes + pops below.
        if real_fill.base_qty < trade.base_qty * (1.0 - _CLOSE_FULL_FILL_EPS):
            return _persist_partial_close(
                conn, state=state, trade=trade, close_fill=real_fill,
                pnl_usd=pnl_usd, now_ts=now_ts,
            )
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
        # P5 gap-b: drop the open-position risk row so the sizer's PortfolioState
        # reflects only live open risk (frees per-symbol/cluster/track headroom
        # for the next entry — capital rotation, not a throttle). PK-scoped on
        # ``open_ts`` == the open's ``opened_ts``, so a concurrent same-name open
        # at a different ts is untouched.
        delete_position_risk_state(
            conn,
            venue=trade.venue,
            symbol=trade.symbol,
            strategy=trade.strategy_id,
            opened_ts=trade.open_ts,
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
