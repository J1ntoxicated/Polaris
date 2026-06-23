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
from polaris.core.data.position_risk_persist import delete_position_risk_state
from polaris.core.data.quote_writer import live_or_bar_price
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    record_fault,
)
from polaris.core.lineage import record_segment_close
from polaris.core.live_recalc.session_exit_rail import (
    _CAL_FX_INDICES,
    _CAL_US_EQUITY,
    _fx_in_session,
)
from polaris.core.streams import resolve_stream
from polaris.scripts._production_capital_sizing import capital_close_contract_factor
from polaris.scripts._production_close_effects import (
    _safe_backfill_probe_outcome,
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
    _note_pending_close,
    _persist_partial_close,
    _reconcile_orphan,
    close_pnl_usd_total,
    real_pnl_r_from_fills,
)
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_close
from polaris.scripts._smoke_real_roundtrip import (
    CloseOrphan,
    PendingClose,
    real_alpaca_close_fill,
    real_capital_close_fill,
    real_okx_close_fill,
    resolve_okx_base_url,
)
from polaris.venues.alpaca import AlpacaAdapter, resolve_alpaca_credentials
from polaris.venues.alpaca.equity_session_gate import stream_session_gate_active
from polaris.venues.capital import CapitalAdapter
from polaris.venues.okx import OKXAdapter

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

# Zombie-close drain. A venue close that keeps failing while the market is OPEN
# (the deal expired / was auto-closed on the demo while our book still tracks the
# position) is un-closeable forever. After this many consecutive IN-SESSION
# rejects on a position at least this old, mark it terminal (state-drift
# recovery) so the exit engine stops the infinite retry. Conservative: a
# transiently-rejected live position (market-closed / momentary) never reaches
# both bars because off-session rejects do not count toward the tally.
ZOMBIE_CLOSE_REJECT_LIMIT = 40
ZOMBIE_MIN_AGE_HOURS = 1.0


def _drain_in_session(venue: str, now_ts: int) -> bool:
    """In-session test for the zombie-close drain tally, dispatched on the venue's
    stream calendar — a close reject only counts toward terminal while the market
    is actually OPEN (an off-session reject is EXPECTED, not a zombie). Wiring the
    Alpaca-only ``stream_session_gate_active`` here was wrong: it recognizes only
    ``us_equity_cal`` and returns False for Capital's ``fx_indices_cal``, so the
    tally would have run 24/7 and drained a LIVE Capital position waiting through a
    weekend. Dispatch correctly: ``fx_indices_cal`` (Capital) → the Fri→Sun 22:00
    UTC weekend boundary; ``us_equity_cal`` (Alpaca) → the RTH gate; ``always_on``
    (OKX) / unknown → always in-session (count)."""
    try:
        cal = resolve_stream(venue).session_calendar
    except Exception:  # noqa: BLE001 — unregistered venue → count (fail toward drain)
        return True
    if cal == _CAL_FX_INDICES:
        return _fx_in_session(now_ts)
    if cal == _CAL_US_EQUITY:
        return not stream_session_gate_active(cal)
    return True

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


async def _real_alpaca_close(
    trade: SimulatedTrade, alpaca_adapter: Any
) -> Fill | PendingClose | CloseOrphan | None:
    """Alpaca close leg: SELL the tracked shares (build adapter from PAPER creds
    when none is injected, mirroring the OKX None-adapter env path). ``None``
    (transient — market closed / no creds) preserves the position so the rail's
    stale-overnight trigger re-arms the flatten at the next in-session open.
    ``trade.pending_close_ref`` (BUG E) is settled first — never two live sells.
    """
    if alpaca_adapter is None:
        api_key, secret = resolve_alpaca_credentials()
        if not (api_key and secret):
            logger.error("[real-close] ALPACA_PAPER_* creds missing — cannot close")
            return None
        async with AlpacaAdapter(api_key=api_key, secret=secret) as adapter:
            return await real_alpaca_close_fill(
                adapter, symbol=trade.symbol, base_qty=trade.base_qty,
                strategy_id=trade.strategy_id,
                pending_order_id=trade.pending_close_ref,
            )
    return await real_alpaca_close_fill(
        alpaca_adapter, symbol=trade.symbol, base_qty=trade.base_qty,
        strategy_id=trade.strategy_id,
        pending_order_id=trade.pending_close_ref,
    )


async def _real_close_fill(
    *,
    trade: SimulatedTrade,
    fresh_mark: float | None = None,
    okx_adapter: Any = None,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
    capital_contract_factor_usd: float | None = None,
) -> Fill | PendingClose | CloseOrphan | None:
    """Drive the real demo venue close leg → return the exit ``Fill``.

    P0 venue wire: OKX sells the entry ``base_qty``; Alpaca sells the tracked
    shares; Capital closes by ``deal_id``. Adapters are injected for testability;
    when ``okx_adapter`` / ``alpaca_adapter`` is ``None`` we build one from env.
    Returns ``None`` on reject / no-fill so the caller falls back to mark-to-market.

    ``fresh_mark`` (FIX A): the latest 1m bar close the OKX cap-split sizes
    against (fall back to ``trade.entry_price`` only when no bar exists) so a
    risen-price close cannot tip a child over the venue cap.
    """
    if trade.venue == "alpaca":
        # Alpaca US-equity: SELL the tracked shares (the venue close that was
        # previously UNWIRED → orphan → shares never sold). BEFORE Capital.
        return await _real_alpaca_close(trade, alpaca_adapter)
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
    # Bug C fix: when the constraint cache is warm (usually warmed at entry)
    # the close fill's size_usd records the REAL exposure (size × level ×
    # lotSize × quote→USD); a cache miss keeps the legacy maths (current
    # behaviour) — PnL is keyed off the ENTRY size_usd either way.
    return await real_capital_close_fill(
        cap_adapter, deal_id=trade.deal_id, strategy_id=trade.strategy_id,
        pending_ref=trade.pending_close_ref,
        contract_factor_usd=capital_contract_factor_usd,
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
    alpaca_adapter: Any = None,
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
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        close_reason=close_reason,
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
    alpaca_adapter: Any = None,
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
        capital_session=capital_session, alpaca_adapter=alpaca_adapter,
        close_reason=close_reason,
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
    alpaca_adapter: Any = None,
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
    # Stable key for the zombie-drain reject tally; reset on any forward progress.
    drain_pid = trade.position_id or f"{trade.venue}:{trade.symbol}:{trade.open_ts}"
    if real_roundtrip:
        # P0 venue wire: drive the real close leg FIRST so pnl_r/pnl_usd are
        # computed against the actual exit fill (not the pre-close bar drift).
        # Execution-default: the close-split sizing + slippage reference mark at
        # the LIVE WS mid, falling back to the most-recent bar close only when no
        # fresh tick (graceful degrade). The venue sell still fills live (market
        # order); this only stops the delayed bar from sizing the 51201 cap-split
        # children + the close slippage_bps reference.
        bar_mark = _latest_bar_close(conn, venue=trade.venue, symbol=trade.symbol)
        fresh_mark = live_or_bar_price(
            state.quote_writer,
            f"{trade.venue}:{trade.symbol}",
            bar_mark if bar_mark is not None else trade.entry_price,
        )
        # Bug C fix: peek-only (no I/O) close-fill exposure factor for the
        # Capital branch of ``_real_close_fill`` (okx/alpaca never reach it).
        cap_factor: float | None = None
        if trade.venue not in ("okx", "alpaca"):
            cap_factor = capital_close_contract_factor(
                state=state, conn=conn, epic=trade.symbol,
            )
        real_fill = None
        try:
            real_fill = await _real_close_fill(
                trade=trade, fresh_mark=fresh_mark, okx_adapter=okx_adapter,
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                capital_contract_factor_usd=cap_factor,
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
        if isinstance(real_fill, PendingClose):
            # BUG E: live-but-unconfirmed close order → confirm-first next tick
            # (no duplicate re-fire); falls into the no-fill tally below.
            _note_pending_close(trade, real_fill.ref)
            real_fill = None
        elif real_fill is not None:
            # Fill / CloseOrphan — any pending close order is settled either way.
            trade.pending_close_ref = None
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
            state.venue_close_rejects += 1
            # ZOMBIE DRAIN. Tally consecutive rejects ONLY while the venue session
            # is OPEN — an off-session reject (weekend / off-RTH) is expected and
            # must never accumulate, else a live position waiting for the open
            # would be wrongly abandoned. A position that keeps failing to close
            # in-session AND is old is un-closeable (deal expired / auto-closed on
            # the demo) — drain it terminal (same state-drift recovery as a true
            # orphan; live: 5 positions × ~120 rejects = 579, retried forever).
            in_session = _drain_in_session(trade.venue, now_ts)
            age_h = (now_ts - int(trade.open_ts)) / 3600.0
            if in_session:
                cnt = state.close_reject_counts.get(drain_pid, 0) + 1
                state.close_reject_counts[drain_pid] = cnt
            else:
                cnt = state.close_reject_counts.get(drain_pid, 0)
            if in_session and cnt >= ZOMBIE_CLOSE_REJECT_LIMIT and age_h >= ZOMBIE_MIN_AGE_HOURS:
                logger.warning(
                    "[close/real] %s:%s un-closeable ZOMBIE — %d in-session venue "
                    "rejects over %.1fh, reconciling terminal (venue-side "
                    "state-drift recovery)",
                    trade.venue, trade.symbol, cnt, age_h,
                )
                state.close_reject_counts.pop(drain_pid, None)
                return _reconcile_orphan(
                    conn, state=state, trade=trade, trade_idx=trade_idx,
                    available=0.0, now_ts=now_ts,
                )
            logger.warning(
                "[close/real] %s:%s close rejected / no-fill — state preserved "
                "(external, no fault) [reject #%d in_session=%s %.1fh]",
                trade.venue, trade.symbol, cnt, in_session, age_h,
            )
            return False
        # P1-6: recompute pnl_r AND pnl_usd from the real exit price so the
        # Layer 4 cell matrix + Layer 5 learner updates reflect what actually
        # traded (a real loss must not be logged as a win because the seeded
        # bars trended up).
        # BUG A: pass the slice qty so pnl_usd is THIS fill's share of the
        # position PnL (full AND partial — the partial-then-remainder final
        # slice was the residual full-PnL re-stamping path).
        pnl_r, pnl_usd, exit_price = real_pnl_r_from_fills(
            conn, trade=trade, exit_price_override=real_fill.fill_price,
            close_base_qty=real_fill.base_qty,
        )
        close_fill = real_fill
        # FIX B: a genuine partial (child reject / within-child) returns less
        # than the tracked qty — keep the position OPEN with a reduced qty (see
        # _persist_partial_close); only a ~full fill closes + pops below.
        if real_fill.base_qty < trade.base_qty * (1.0 - _CLOSE_FULL_FILL_EPS):
            # Forward progress (a real partial fill) — reset the zombie tally so a
            # genuinely-filling position is never drained as un-closeable.
            state.close_reject_counts.pop(drain_pid, None)
            return _persist_partial_close(
                conn, state=state, trade=trade, close_fill=real_fill,
                pnl_usd=pnl_usd, now_ts=now_ts,
            )
    else:
        # SIM exit (non-real-roundtrip): execution-default mark = the LIVE WS mid,
        # falling back to the bar close inside real_pnl_r_from_fills only when no
        # fresh tick. A live tick → it drives the sim exit fill/pnl directly (via
        # exit_price_override); no tick → override stays None and the bar close
        # remains the graceful fallback. Real-roundtrip already marks at the real
        # venue fill (above) — untouched.
        live_override = live_or_bar_price(
            state.quote_writer, f"{trade.venue}:{trade.symbol}", 0.0,
        )
        pnl_r, pnl_usd, exit_price = real_pnl_r_from_fills(
            conn, trade=trade,
            exit_price_override=live_override if live_override > 0.0 else None,
        )
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
            # Gate→outcome instrumentation: ``pnl_r`` (move quality,
            # qty-invariant — the FINAL full-close slice's R) is stamped onto
            # the SAME existing UPDATE so the PASS cohort reads its outcome via
            # gate_events.position_id → positions.pnl_r. Partial closes /
            # reconciled zombies never reach this statement → stay NULL
            # (correct: not a completed trade outcome). Measurement only.
            conn.execute(
                "UPDATE positions SET status = 'closed', closed_ts = ?, "
                "mfe_r = ?, mae_r = ?, exit_state = 'closed', pnl_r = ? "
                "WHERE position_id = ?",
                (now_ts, mfe_r, mae_r, pnl_r, trade.position_id),
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
    state.close_reject_counts.pop(drain_pid, None)  # full close — drop the drain tally
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
        # Segment pnl_usd = POSITION-cumulative close-fill sum (this final
        # slice included) — last-slice stamping would under-state every
        # partially-closed position now that BUG A slices per fill.
        record_segment_close(
            conn, position_id=trade.position_id, exit_ts=now_ts,
            exit_reason=close_reason or "exit", pnl_r=pnl_r,
            pnl_usd=close_pnl_usd_total(conn, trade=trade, fallback=pnl_usd),
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
    # ADR-012 — backfill the probe tuning-log outcome cols (giveback / realized
    # R / time-to-exit) onto the SEPARATE probes.sqlite sidecar so the offline
    # /debate calibration joins observe-mode would-be decisions to the truth.
    # Collection-only, fail-open inside the helper; never gates sizing/exits.
    _safe_backfill_probe_outcome(
        state=state, trade=trade, pnl_r=pnl_r, mfe_r=mfe_r, mae_r=mae_r,
        now_ts=now_ts,
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
