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
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.core.data.position_risk_persist import delete_position_risk_state
from polaris.core.data.quote_writer import live_or_bar_price
from polaris.core.economics.fees import real_fee_usd
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    record_fault,
)
from polaris.core.lineage import record_segment_close
from polaris.core.live_recalc.session_exit_rail import (
    _CAL_FX_INDICES,
    _CAL_US_EQUITY,
    _fx_in_session,
    alpaca_close_retry_should_defer,
)
from polaris.core.metrics.risk_unit import realised_r
from polaris.core.streams import resolve_stream
from polaris.scripts._production_capital_sizing import capital_close_contract_factor
from polaris.scripts._production_close_classes import update_strategy_class_on_close
from polaris.scripts._production_close_effects import (
    _safe_backfill_probe_outcome,
    _safe_lookup_entry_regime,
    _safe_record_calibration_outcome,
    _safe_record_meta_label,
    _safe_run_g8,
    _safe_run_learners,
    _safe_update_cell_matrix,
    _safe_update_posterior,
    compute_net_pnl_r,
    fold_close_slice,
)
from polaris.scripts._production_close_helpers import (
    _CLOSE_FULL_FILL_EPS,
    _close_excursion_r,
    _latest_bar_close,
    _note_pending_close,
    _persist_partial_close,
    _position_risk_usd,
    _reconcile_orphan,
    close_pnl_usd_total,
    real_pnl_r_from_fills,
)
from polaris.scripts._production_close_virtual import safe_update_virtual_trace
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_close
from polaris.scripts._smoke_real_roundtrip import (
    CloseOrphan,
    PendingClose,
    SiblingDrainClose,
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


def _okx_sibling_base(
    state: ProdLoopState, trade: SimulatedTrade,
) -> float:
    """Sum tracked ``base_qty`` of OTHER still-open OKX positions on the SAME base
    ccy (POOLED-WALLET FIX). The OKX SPOT demo holds ONE fungible base-ccy balance
    shared across every concurrent same-ccy position; when ``base_qty`` exceeds the
    live wallet availBal at close, a non-zero sibling sum means the missing pool is
    EXPLAINED by a sibling that drained/holds it (book a mark close), vs a genuine
    over-count when zero (reconcile). Keyed on the OKX base ccy (left of ``-``);
    EXCLUDES ``trade`` itself (identity). Pure read over in-mem open_trades."""
    base_ccy = trade.symbol.split("-")[0].upper()
    total = 0.0
    for other in state.open_trades:
        if other is trade or other.venue != "okx":
            continue
        if other.symbol.split("-")[0].upper() == base_ccy:
            total += max(0.0, float(other.base_qty))
    return total


def _has_close_fill(conn: sqlite3.Connection, trade: SimulatedTrade) -> bool:
    """True when a close fill is already persisted for this position (idempotency).

    The 0.7% second-order base-fee dust a later tick re-flags as un-sellable must
    NOT churn a duplicate reconcile/orphan when the position SUBSTANTIVELY closed
    earlier. Keyed on ``contribution_id == position_id`` (the close fill's
    position link). No position_id (legacy) → False (cannot dedupe → fall through
    to the normal orphan/drain path). Read-only; never raises into the close path.
    """
    if not trade.position_id:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM fills WHERE contribution_id = ? AND is_close = 1 LIMIT 1",
            (trade.position_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _has_entry_fill(conn: sqlite3.Connection, trade: SimulatedTrade) -> bool:
    """True when a REAL entry fill is persisted for this position.

    Distinguishes a genuinely-tracked position (its base WAS real, opened with a
    live entry fill) from a GHOST over-count (a close-chunk base_qty the wallet
    never actually held — no entry fill). The OKX pooled-wallet WINNER leak
    ([[okx_winner_reconcile_leak_2026-06-26]]) fires for the former: a real
    position whose shared base-ccy pool was drained by now-CLOSED same-ccy
    siblings reads availBal~0 with no OPEN sibling, so it fell to CloseOrphan and
    was reconciled-with-NULL — discarding the winner's edge (SOL 9.75R). A real
    entry fill means the base WAS sold into the shared USDT pool (proceeds
    conserved) → it must be MARK-closed (real pnl_r), not dropped. Keyed on
    ``contribution_id == position_id``. No position_id (legacy) → False (fall
    through to the existing reconcile). Read-only; never raises into the close
    path.
    """
    if not trade.position_id:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM fills WHERE contribution_id = ? AND is_close = 0 LIMIT 1",
            (trade.position_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _finalize_already_closed(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    trade: SimulatedTrade,
    trade_idx: int,
    now_ts: int,
) -> bool:
    """Idempotently retire a position that ALREADY has a persisted close fill.

    The residual base-fee dust path: the position closed on a prior tick, a later
    tick re-detects the sub-min remainder as un-sellable and would otherwise churn
    a second orphan/reconcile row. Mark it terminal as ``status='closed'`` (it WAS
    closed), pop it from ``open_trades``, and stop retrying — WITHOUT a second
    close fill or an orphan audit row. flow_not_block; returns ``True`` (handled).
    """
    with contextlib.suppress(sqlite3.Error):
        conn.execute("BEGIN IMMEDIATE")
        if trade.position_id:
            conn.execute(
                "UPDATE positions SET status = 'closed', closed_ts = ?, "
                "exit_state = 'closed' WHERE position_id = ? "
                "AND status NOT IN ('closed', 'reconciled')",
                (now_ts, trade.position_id),
            )
            # Ghost-row fix (2026-07-10 RCA): this idempotent-terminal path
            # skipped the sizer's open-position risk row delete same as
            # ``_reconcile_orphan`` did — DELETE is naturally idempotent (a
            # re-drive with no matching row is a no-op), so unconditional here
            # is safe.
            delete_position_risk_state(
                conn,
                venue=trade.venue,
                symbol=trade.symbol,
                strategy=trade.strategy_id,
                opened_ts=trade.open_ts,
            )
        conn.execute("COMMIT")
    if 0 <= trade_idx < len(state.open_trades) and state.open_trades[trade_idx] is trade:
        state.open_trades.pop(trade_idx)
    else:
        with contextlib.suppress(ValueError):
            state.open_trades.remove(trade)
    trade.closed = True
    state.close_reject_counts.pop(
        trade.position_id or f"{trade.venue}:{trade.symbol}:{trade.open_ts}", None
    )
    logger.info(
        "[close/idempotent] %s:%s trade_id=%s already has a close fill — residual "
        "sub-min dust, retire terminal (no re-churn, no duplicate orphan)",
        trade.venue, trade.symbol, trade.position_id or "-",
    )
    return True


def _synth_okx_mark_close(trade: SimulatedTrade, *, mark: float | None) -> Fill:
    """Synthesize a MARK-to-market close Fill for a pooled-wallet-drain position.

    The shared OKX base wallet was drained by a still-open same-ccy sibling, so no
    order is submitted (nothing left to sell) — but the base WAS real and its
    proceeds were realized at the wallet level by the sibling. This books THIS
    position's close at the REAL fresh ``mark`` on its REAL tracked ``base_qty`` so
    it keeps its true pnl_r in the R ledger (no survivorship bias). NOT a
    fabricated price: ``mark`` is the live WS mid / latest 1m bar close the close
    path already resolved (falls back to the entry price only when no mark exists).
    """
    px = mark if mark and mark > 0.0 else trade.entry_price
    base_qty = max(0.0, float(trade.base_qty))
    quote_qty = base_qty * px
    return Fill(
        venue="okx",
        instrument_id=f"okx:{trade.symbol}",
        strategy_id=trade.strategy_id,
        side="sell",
        size_usd=quote_qty,
        fill_price=px,
        fee_usd=real_fee_usd("okx", notional_usd=quote_qty),
        slippage_bps=0.0,
        ts_ms=int(time.time() * 1000),
        order_id=f"polDrainMark{uuid.uuid4().hex[:8]}",
        client_order_id=None,
        base_qty=base_qty,
        quote_qty=quote_qty,
        state="filled",
    )


def _synth_capital_mark_close(trade: SimulatedTrade, *, mark: float | None) -> Fill:
    """Synthesize a MARK-to-market close Fill for an externally-closed Capital
    position ([[capital_external_close_reconcile_leak_2026-06-26]]).

    The Capital CFD demo closed the deal itself (EOD flatten / demo expiry /
    a venue-side stop) before our ``close_position`` order — the venue rejected
    our close AND no longer lists the dealId. The position WAS real (a persisted
    entry fill) and DID realise a close at the venue; only OUR booking is missing.
    No order is submitted (the venue already closed it) — this books THIS
    position's close at the REAL fresh ``mark`` on its REAL tracked ``base_qty``
    so it keeps its realised pnl_r in the R ledger (no survivorship bias). The
    realised PnL itself is recomputed in ``real_pnl_r_from_fills`` from the entry
    fill's persisted ``size_usd`` (the true CFD exposure) vs this ``fill_price``
    — the homologous Capital twin of ``_synth_okx_mark_close``. ``mark`` is the
    live WS mid / latest 1m bar close the close path already resolved.
    """
    px = mark if mark and mark > 0.0 else trade.entry_price
    base_qty = max(0.0, float(trade.base_qty))
    quote_qty = base_qty * px
    return Fill(
        venue="capital",
        instrument_id=f"capital:{trade.symbol}",
        strategy_id=trade.strategy_id,
        side="sell" if trade.side == "long" else "buy",
        size_usd=quote_qty,
        fill_price=px,
        fee_usd=real_fee_usd("capital", notional_usd=quote_qty),
        slippage_bps=0.0,
        ts_ms=int(time.time() * 1000),
        order_id=f"polCapExtMark{uuid.uuid4().hex[:8]}",
        client_order_id=None,
        base_qty=base_qty,
        quote_qty=quote_qty,
        state="filled",
    )


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
    okx_sibling_base: float = 0.0,
) -> Fill | PendingClose | CloseOrphan | SiblingDrainClose | None:
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
                sibling_base=okx_sibling_base,
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
                sibling_base=okx_sibling_base,
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
    cadence: str = "bar",
) -> bool:
    """Day 9 F2.b — close a specific position by ``position_id``.

    Replaces the FIFO ``state.open_trades[0]`` pop when G6 emits EXIT_NOW
    for a *specific* position. Returns ``True`` when the close persisted,
    ``False`` when the position was not found in ``state.open_trades`` or
    the persist failed (state preserved).

    ``close_reason`` (lineage read-model only) names the exit trigger the
    caller fired on (precise-exit fsm / session calendar / rotation / g6); it
    is recorded onto the lineage segment and changes NO close behaviour.

    ``cadence`` (hardening #7, measurement-only) tags WHICH exit pass fired this
    close — 'bar' (~5s recalc) or 'tick' (sub-second tick pass) — stamped onto
    ``positions.exit_cadence`` for the close_reason × cadence rollup. Never reads
    back into any close/exit/sizing decision.
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
        close_reason=close_reason, cadence=cadence,
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
    cadence: str = "bar",
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
        close_reason=close_reason, cadence=cadence,
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
    cadence: str = "bar",
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
    # P0 off-session close-retry defer (Alpaca only): a decided close (#26 FSM /
    # G6 / G7 / session-forced-exit) called the venue EVERY tick even while the
    # US-equity market is closed — Alpaca rejects every time, and the zombie-
    # drain tally deliberately never counts an off-session reject (a live
    # position waiting for the reopen must not be abandoned), so the retry ran
    # UNBOUNDED forever off-session (observed 825 retries/symbol). Skip the
    # venue call entirely this tick — NO reject counted, position PRESERVED,
    # retried once at the next in-session tick (flow_not_block: the close still
    # fires the moment the market reopens; this only suppresses the off-session
    # no-op spam). Reuses the existing RTH predicate; Capital/OKX unaffected.
    if (
        real_roundtrip
        and trade.venue == "alpaca"
        and alpaca_close_retry_should_defer(_CAL_US_EQUITY, now_ts)
    ):
        logger.debug(
            "[close/real] %s:%s trade_id=%s close deferred — off-session "
            "(no venue call, state preserved)",
            trade.venue, trade.symbol, trade.position_id or "-",
        )
        return False
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
        # Capital external-close fix: did a REAL recent price source (1m bar OR
        # live WS tick) exist, independent of the entry-price fallback? Re-resolve
        # with a 0.0 fallback so ``> 0`` is true ONLY for a genuine fresh mark —
        # the Capital mark-close reconcile-leak fix books only on a real price,
        # else it stays conservative (reconcile). 0 extra I/O (same in-mem read).
        has_fresh_mark = live_or_bar_price(
            state.quote_writer,
            f"{trade.venue}:{trade.symbol}",
            bar_mark if bar_mark is not None else 0.0,
        ) > 0.0
        # Bug C fix: peek-only (no I/O) close-fill exposure factor for the
        # Capital branch of ``_real_close_fill`` (okx/alpaca never reach it).
        cap_factor: float | None = None
        if trade.venue not in ("okx", "alpaca"):
            cap_factor = capital_close_contract_factor(
                state=state, conn=conn, epic=trade.symbol,
            )
        # POOLED-WALLET FIX: tracked base of OTHER open same-ccy OKX positions, so
        # the OKX close can tell a sibling-explained shared-wallet drain (book a
        # mark close) from a genuine over-count (reconcile). 0 for non-OKX.
        sibling_base = (
            _okx_sibling_base(state, trade) if trade.venue == "okx" else 0.0
        )
        real_fill = None
        try:
            real_fill = await _real_close_fill(
                trade=trade, fresh_mark=fresh_mark, okx_adapter=okx_adapter,
                capital_session=capital_session, alpaca_adapter=alpaca_adapter,
                capital_contract_factor_usd=cap_factor,
                okx_sibling_base=sibling_base,
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
        # IDEMPOTENCY GUARD (0.7% second-order dust): a sub-min orphan/drain signal
        # on a position that ALREADY has a persisted close fill substantively closed
        # on a prior tick — the residual base-fee dust a later tick re-flags as
        # un-sellable must NOT churn a duplicate reconcile/orphan row. Mark it
        # terminal as 'closed' (it WAS closed) + stop retrying. No second fill, no
        # orphan audit.
        if isinstance(real_fill, (CloseOrphan, SiblingDrainClose)) and _has_close_fill(
            conn, trade
        ):
            return _finalize_already_closed(
                conn, state=state, trade=trade, trade_idx=trade_idx, now_ts=now_ts,
            )
        if isinstance(real_fill, SiblingDrainClose):
            # POOLED-WALLET DRAIN — a still-open same-ccy sibling drained the
            # SHARED OKX base wallet, so THIS position's availBal reads ~0 even
            # though its base WAS real and WAS sold into the pool (wallet proceeds
            # conserved). Book a MARK-to-market close at the fresh mark so the
            # position keeps its REAL pnl_r and stays in the R ledger / learner
            # feed — NOT dropped as a tracking failure (the survivorship bias that
            # excluded ~32% of flow_pressure positions). flow_not_block; no order
            # is submitted (nothing left to sell), this is a bookkeeping close at
            # the real live mark on the real tracked qty.
            real_fill = _synth_okx_mark_close(trade, mark=fresh_mark)
        if isinstance(real_fill, CloseOrphan):
            # OKX POOLED-WALLET WINNER LEAK FIX ([[okx_winner_reconcile_leak_
            # 2026-06-26]]): the sibling-drain mark-close (above) only fires while a
            # same-ccy sibling is STILL OPEN (sibling_base>0). But the OKX SPOT demo
            # shares ONE fungible base wallet, and the draining same-ccy siblings
            # (the KILLed tick scalpers micro_reversion/flow_pressure/burst_rider)
            # routinely CLOSED FIRST — so a volume_burst winner's later close saw
            # availBal~0 with sibling_base==0 and fell here, reconciled-with-NULL,
            # DISCARDING its edge (SOL 9.75R / ETH 1.77R MFE, 7/18 volume_burst
            # positions, avg MFE 1.8R = 3x the closed cohort). A position with a
            # REAL entry fill WAS real and its base WAS sold into the shared USDT
            # pool (proceeds conserved); dropping it is the SAME survivorship bias
            # the SiblingDrainClose fix removed, just on the no-open-sibling
            # timeline. When we can still price it (usable fresh mark), book a
            # MARK-to-market close so the winner keeps its real pnl_r (exit-capture,
            # flow_not_block — no order submitted, nothing left to sell). A GHOST
            # over-count (no real entry fill) or an un-priceable position falls
            # through to the unchanged reconcile.
            if (
                trade.venue == "okx"
                and fresh_mark is not None
                and fresh_mark > 0.0
                and _has_entry_fill(conn, trade)
            ):
                logger.info(
                    "[close/okx] %s:%s trade_id=%s pooled-wallet drain, NO open "
                    "sibling but REAL entry fill — book mark close (kept in R "
                    "ledger, not reconciled-with-NULL) [winner-leak fix]",
                    trade.venue, trade.symbol, trade.position_id or "-",
                )
                real_fill = _synth_okx_mark_close(trade, mark=fresh_mark)
            elif (
                trade.venue == "capital"
                and has_fresh_mark
                and _has_entry_fill(conn, trade)
            ):
                # CAPITAL EXTERNAL-CLOSE LEAK FIX ([[capital_external_close_
                # reconcile_leak_2026-06-26]]): ``real_capital_close_fill`` returns
                # CloseOrphan when our ``close_position`` is rejected AND the dealId
                # is "no longer listed" — the Capital CFD demo CLOSED THE DEAL ITSELF
                # (EOD flatten / demo expiry / a venue-side stop) before our order.
                # The position WAS real (a persisted entry fill) and DID realise a
                # close at the venue — only OUR booking is missing. DB-proven leak:
                # 3/6 Capital closes ended status='reconciled' / pnl_r=NULL (EURUSD
                # fx_breakout_basket / fx_range_fade, each with a real entry fill).
                # Dropping it is the SAME survivorship bias the OKX winner-leak fix
                # removed, just on the Capital track. With a REAL entry fill AND a
                # genuine fresh mark (1m bar / live tick — NOT the stale entry
                # fallback), book a MARK-to-market close so the position keeps its
                # realised pnl_r (exit-capture, flow_not_block — no order submitted,
                # the venue already closed it). No entry fill (un-addressable orphan)
                # or no real fresh mark falls through to the conservative reconcile.
                logger.info(
                    "[close/capital] %s:%s trade_id=%s close rejected AND deal no "
                    "longer listed (external close) but REAL entry fill + fresh "
                    "mark — book mark close (kept in R ledger, not reconciled-with-"
                    "NULL) [external-close leak fix]",
                    trade.venue, trade.symbol, trade.position_id or "-",
                )
                real_fill = _synth_capital_mark_close(trade, mark=fresh_mark)
            else:
                # FIX 2 — TRUE ORPHAN (wallet available ~0 while the book still
                # tracks base_qty, an over-count from the close-chunk fix) with NO
                # sibling AND no real base to book. Mark the position terminal
                # (status='reconciled') so the exit engine + hydrate STOP retrying
                # it forever — WITHOUT fabricating a fill or PnL. Venue-side
                # STATE-DRIFT RECOVERY, not a trade outcome.
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
            # P2 R-ledger-leak fix: the slice's R, rescaled by THIS position's
            # own staked risk (``realised_r`` is linear in pnl_usd, so a slice's
            # R is its fraction of the whole-position R automatically). Stamped
            # cumulatively onto positions.pnl_r inside the persist txn so a
            # partial-only close (LUNA / DOT) is no longer NULL.
            slice_pnl_r = realised_r(
                pnl_usd=pnl_usd, risk_usd=_position_risk_usd(conn, trade.position_id),
            )
            persisted = _persist_partial_close(
                conn, state=state, trade=trade, close_fill=real_fill,
                pnl_usd=pnl_usd, now_ts=now_ts, slice_pnl_r=slice_pnl_r,
            )
            if persisted:
                # Fold the slice into the cell matrix / learners / meta-label /
                # posterior with the SAME real-fee NET pipeline the full close
                # uses — so partial + remainder sum to exactly ONE whole-position
                # fold (no double-count). G8 + segment + excursion stay terminal-
                # only (fire once at the full close). Measurement/learning only.
                fold_close_slice(
                    conn, trade=trade, lookup_regime=lookup_regime,
                    slice_pnl_r=slice_pnl_r, slice_pnl_usd=pnl_usd,
                    now_ts=now_ts, state=state,
                )
            return persisted
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
    # ``atr_risk_usd`` (3rd return, unused below) stays the EXCURSION ruler for
    # mfe_r/mae_r (a per-trade stop-distance unit derived from possibly-refreshed
    # bars) — untouched. ``realized_atr_r`` (the probe-outcome ruler) is now
    # rescaled by THIS position's persisted ``risk_usd`` (the SAME canonical
    # denominator ``final_slice_pnl_r`` below uses) instead of a second,
    # independently-recomputed ATR risk figure — one ruler, one source, so
    # positions.pnl_r / segments.pnl_r / probe.realized_pnl_r agree exactly for
    # a single-shot full close (2026-07-07 re-base, /debate-cleared).
    mfe_r, mae_r, _atr_risk_usd = _close_excursion_r(
        conn, trade=trade, exit_price=exit_price, state=state,
    )
    risk_usd = _position_risk_usd(conn, trade.position_id)
    realized_atr_r = realised_r(pnl_usd=pnl_usd, risk_usd=risk_usd)
    # P2 R-ledger-leak fix: this FINAL slice's R, rescaled by the position's own
    # staked risk. ``pnl_usd`` is already sliced to the un-closed remainder
    # (real_pnl_r_from_fills with close_base_qty); ``realised_r`` is LINEAR in
    # pnl_usd, so this is the remainder's fraction of the whole-position R. For
    # a SINGLE-SHOT full close (no prior partial) the slice IS the whole →
    # final_slice_pnl_r == ``pnl_r`` exactly (same function, same inputs), so
    # the fold + stamp below stay byte-identical. For a partial-then-full close,
    # the partials already folded + stamped their fractions, so folding only
    # this remainder makes partial + remainder sum to exactly ONE whole-position
    # fold (no double-count) and the accumulated positions.pnl_r reaches the
    # whole-position R.
    final_slice_pnl_r = realised_r(pnl_usd=pnl_usd, risk_usd=risk_usd)
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
            # Gate→outcome instrumentation: the realised R is stamped onto the
            # SAME existing UPDATE so the PASS cohort reads its outcome via
            # gate_events.position_id → positions.pnl_r. P2 R-ledger-leak fix —
            # ACCUMULATE this final slice's R onto any prior partial-slice stamps
            # (``COALESCE(pnl_r,0) + final_slice``) so the durable pnl_r reaches
            # the whole-position R whether the position closed single-shot (prior
            # = NULL → 0 + whole = whole, byte-identical) or via partials
            # (Σ partials + remainder = whole). Reconciled zombies never reach
            # this statement → stay NULL (correct: tracking failure, not a trade
            # outcome). Measurement only.
            conn.execute(
                "UPDATE positions SET status = 'closed', closed_ts = ?, "
                "mfe_r = ?, mae_r = ?, exit_state = 'closed', "
                "pnl_r = COALESCE(pnl_r, 0.0) + ?, "
                "exit_cadence = ? "
                "WHERE position_id = ?",
                (now_ts, mfe_r, mae_r, final_slice_pnl_r, cadence, trade.position_id),
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

    # REAL-FEE CANONICAL (FIX 1): compute the real-fee NET R/USD ONCE here (reads
    # the per-fill REAL fees.fee_usd for both legs, leg-once) and feed the SAME net
    # to the cell matrix, the Layer-5 learners, the meta-label, the posterior AND
    # the ``won`` verdict. Previously only the posterior subtracted the fee while
    # cell/learners folded the fee-FREE gross R — a fee≈gross scalp looked like a
    # winner to the cell stats and a loss to the posterior. ``won`` is now the NET
    # sign. fills.pnl_usd (GROSS) + fills.fee_usd (REAL) stay the single truth
    # (#46); net is an in-memory derivation. Degenerate atr_usd → net==gross
    # (no blow-up). Measurement/learning only — never read by sizing, the 9-stack
    # ban + -1.0R rail are untouched. Fail-open inside compute_net_pnl_r. Computed
    # before the close INFO log so the logged ``won`` matches the net verdict the
    # cell/learners/posterior fold (the log otherwise reported the gross sign).
    # P2 R-ledger-leak fix: fold ONLY this FINAL slice's gross R (== ``pnl_r`` for
    # a single-shot full close, so byte-identical; the remainder fraction for a
    # partial-then-full close, so partial folds + this fold sum to exactly ONE
    # whole-position fold — no double-count). ``gross_pnl_usd`` is already this
    # slice's pnl_usd, so the R and $ folded here are consistent.
    pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        conn, trade=trade, gross_pnl_r=final_slice_pnl_r, gross_pnl_usd=pnl_usd,
    )
    won = pnl_r_net > 0.0

    # Successful close (INFO): the realised-close 거동 record Jin asked for —
    # the venue/ticker, the close fill price + size, the realised pnl_r/pnl_usd,
    # the held duration, and whether it was a win. ``won`` is the real-fee NET
    # sign (== the cell/learner/posterior verdict), not the fee-free gross sign;
    # pnl_r/pnl_usd stay the realised GROSS price-drift values. Pairs with the
    # EXIT-TRIGGER reason INFO in run_precise_exit (correlate on
    # trade_id=position_id). The "filled" + "close" keywords surface it on the
    # dashboard board.js pane. Log only — the close already committed above; this
    # changes no behaviour.
    held_seconds = max(0, now_ts - getattr(trade, "open_ts", now_ts))
    logger.info(
        "[close] closed %s:%s trade_id=%s filled exit_price=%.6g size_usd=%.2f "
        "pnl_r=%.2f pnl_usd=%.2f held=%ds won=%s mode=%s",
        trade.venue, trade.symbol, trade.position_id or "-",
        exit_price, close_fill.size_usd, pnl_r, pnl_usd, held_seconds,
        won, "real" if real_roundtrip else "sim",
    )

    # Day 8 codex R5 P2 fix: every post-commit auxiliary step is wrapped so a
    # downstream failure cannot drop the rest of the fan-out. ``record_fault``
    # itself can raise (it writes to ``strategy_fault_events`` and reads from
    # ``strategy_halts``); ``_safe_record_fault`` swallows that with a log.
    # P1-10 fix — fold key is the ADMITTING regime (positions.entry_regime),
    # not the live SSOT at close time (a cell can flip between entry and close,
    # crediting/blaming a different cell than the one that sized the entry).
    regime = _safe_lookup_entry_regime(lookup_regime, conn, trade)
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
    # FIX 1 — cell matrix learns the REAL-fee NET R (not gross): the same net the
    # posterior folds, so the edge statistics + the posterior agree on every close.
    _safe_update_cell_matrix(
        conn, trade=trade, regime=regime, pnl_r=pnl_r_net, won=won, now_ts=now_ts,
        state=state,
    )
    # FIX 1 — Layer-5 learner network folds the SAME net R + net ``won``.
    _safe_run_learners(
        conn, trade=trade, regime=regime, pnl_r=pnl_r_net, won=won, now_ts=now_ts,
        state=state,
    )
    # Meta-labeling (#10) — collection-only triple-barrier label per close.
    # Never gates sizing/exits; fail-open inside the helper. FIX 1 — net R + won.
    _safe_record_meta_label(
        conn, trade=trade, regime=regime, pnl_r=pnl_r_net, won=won, now_ts=now_ts,
        state=state,
    )
    # ADR-012 — backfill the probe tuning-log outcome cols (giveback / realized
    # R / time-to-exit) onto the SEPARATE probes.sqlite sidecar so the offline
    # /debate calibration joins observe-mode would-be decisions to the truth.
    # Collection-only, fail-open inside the helper; never gates sizing/exits.
    _safe_backfill_probe_outcome(
        state=state, trade=trade, realized_atr_r=realized_atr_r,
        mfe_r=mfe_r, mae_r=mae_r, now_ts=now_ts,
    )
    # Edge-validation Phase 1 — cost-adjusted expectancy posterior (measure +
    # display only; never wired into sizing). Fail-open inside the helper.
    # Edge-validation posterior + the strategy×regime parent2 prior (audit
    # code_review_2026-06-24) are both charged inside this helper from the SAME
    # cost-adjusted net R — measure/seed only, never read by sizing. Fail-open.
    _safe_update_posterior(
        conn, trade=trade, regime=regime, pnl_r_net=pnl_r_net,
        pnl_r_gross=final_slice_pnl_r, now_ts=now_ts,
    )
    # Probability calibration shadow (#4, G5) — fills the realized outcome onto
    # this signal's sizing-time p_pos snapshot. Measurement only, fail-open.
    _safe_record_calibration_outcome(
        conn, trade=trade, won=won, pnl_r_net=pnl_r_net, now_ts=now_ts,
    )
    # VIRTUAL ACCOUNT (Jin 2026-07-07): weekly per-exchange trace (TRACE, never
    # RESET — the account compounds continuously) + reset-only-on-ruin check.
    # Measurement hygiene only — never blocks/skips a trade. Fail-open inside.
    safe_update_virtual_trace(
        conn, trade=trade, now_ts=now_ts,
    )
    # FIX 1 — G8 post-trade reflector folds the SAME net R + net ``won`` as the
    # cell/learners/posterior so the reflection lesson is fee-coherent (a gross R
    # with a net won verdict would contradict). Measurement/learning only.
    await _safe_run_g8(
        conn, trade=trade, regime=regime, pnl_r=pnl_r_net, won=won, now_ts=now_ts,
        state=state, gpt_client=gpt_client, phase=phase,
    )
    # pts-classes (group WIRE) — the ONE real close-confirmation point: rolls
    # up score_F for this track and evaluates the transition state machine,
    # persisting any class change to strategy_class. Terminal-only (never
    # fires on a partial-close slice — fold_close_slice does not call this).
    # Fail-open inside the helper (mirrors every _safe_* sibling above) — a
    # classification failure must never unwind this already-committed close.
    update_strategy_class_on_close(conn, trade=trade, now_ts=now_ts, state=state)
    return True
