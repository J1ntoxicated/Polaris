"""Day 8 — production paper loop pipeline + close path helpers.

Splits the per-signal G1→G8 driver and the close-path mark-to-market out of
``production_paper_loop.py`` so the main file stays under the 500-line budget.

Functions
---------
* ``run_pipeline_for_signal`` — drive one validated RawSignal through G1-G7,
  invoking AllocatorFence reservation + venue submit (paper).
* ``real_pnl_r_from_fills`` — read entry fill + recent bars + compute real
  R-units for the close path (replaces smoke loop's ``pnl_r=1.0`` placeholder).
* ``close_oldest_with_real_pnl`` — pop the oldest open trade, compute real
  PnL, persist close fill, update Layer 4 cell matrix + Layer 5 learners,
  invoke G8 reflector, log to ``gate_events``.

The venue open-reject classification surface (``EXTERNAL_NONFAULT_REJECT_CODES``
/ ``COMPLIANCE_REJECT_CODES`` / ``_is_external_reject`` / ``_handle_open_reject``)
lives in :mod:`_production_reject` (N3 split for the 500-LOC budget) and is
re-exported here — every prior import path / module-attribute access is kept.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from polaris.core.data.fills_persist import persist_fill
from polaris.core.data.position_risk_persist import persist_position_risk_state
from polaris.core.economics.maker_fill_shadow import log_maker_fill
from polaris.core.economics.price_through_shadow import log_price_through_entry
from polaris.core.isolation.allocator_fence import (
    AllocationRequest,
    ReservationConflictError,
    get_process_fence,
)
from polaris.core.isolation.blocklist import is_blocklisted
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    FAULT_REJECT,
    record_fault,
)
from polaris.core.isolation.order_keys import (
    IdempotencyConflictError,
    build_order_key,
    payload_hash,
    register_order_intent,
)
from polaris.core.lineage import record_segment_open
from polaris.core.live_recalc.regime_flip import fetch_regime
from polaris.core.live_recalc.regime_v2 import fetch_regime_v2
from polaris.core.metrics.risk_unit import risk_usd_at_entry
from polaris.core.sizing.constants import OKX_DEMO_STARTING_EQUITY_USD
from polaris.core.streams import derive_leverage, resolve_stream
from polaris.scripts._alpaca_open import (
    fetch_alpaca_buying_power,
    real_alpaca_open_fill,
)
from polaris.scripts._alpaca_pending_open import (
    PendingOpenRef,
    clear_pending_open,
    read_pending_open,
    true_up_alpaca_partial,
    upsert_pending_open,
)
from polaris.scripts._production_atr import timeframe_anchor_atr_pct
from polaris.scripts._production_capital_sizing import (
    CapitalOrderPlan,
    _peek_quote_usd_rate,
    maybe_evict_on_reject,
    translate_capital_order,
)
from polaris.scripts._production_reject import (
    COMPLIANCE_REJECT_CODES as COMPLIANCE_REJECT_CODES,
)
from polaris.scripts._production_reject import (
    EXTERNAL_NONFAULT_REJECT_CODES as EXTERNAL_NONFAULT_REJECT_CODES,
)
from polaris.scripts._production_reject import (
    _handle_open_reject,
)
from polaris.scripts._production_reject import (
    _is_external_reject as _is_external_reject,
)
from polaris.scripts._production_run_signal import run_pipeline_for_signal
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_open_fill
from polaris.scripts._smoke_real_roundtrip import (
    MIN_CAPITAL_LOT,
    OpenAttempt,
    fetch_okx_available_usdt,
    real_capital_open_fill,
    real_okx_open_fill,
    record_venue_orphan,
    resolve_okx_base_url,
)
from polaris.scripts.exit_strategy_config import _stop_atr_mult_for_strategy
from polaris.storage.db_writer import DBWriter
from polaris.strategies import STRATEGY_REGISTRY, RawSignal
from polaris.venues.alpaca import AlpacaAdapter, resolve_alpaca_credentials
from polaris.venues.capital import CapitalAdapter
from polaris.venues.capital.reopen_route import (
    clear_reopen_stamp,
    maybe_stamp_delayed_reopen,
    submit_suppressed_until_reopen,
)
from polaris.venues.okx import OKXAdapter

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


def _strategy_timeframe(strategy_id: str) -> str:
    """Look up strategy.metadata.timeframe by strategy_id (SSOT for order_key)."""
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return "1m"
    return cls.metadata.timeframe


def _persist_maker_fill_shadow(
    conn: sqlite3.Connection | None,
    *,
    attempt: OpenAttempt | None,
    run_id: str,
    strategy_id: str,
    venue: str,
    symbol: str,
    side: str,
    fill_price: float,
) -> None:
    """Persist ONE maker_fill_shadow row for a post-only ENTRY fill (FIX-EXEC).

    The persisting ``log_maker_fill`` was never wired into production, so the shadow
    table stayed empty live despite real maker fills. This writes the row from the
    ENTRY open path (the conn IS held, the fill happens exactly once → no
    double-count; the close path is untouched). A maker-only no-op: ``attempt`` None
    or carrying no ``maker_touch_px`` (a taker / sim fill) writes NOTHING — a taker
    entry never fabricates a maker shadow row. Entry leg is always a BUY (OKX
    post-only buy). Measurement only — degrade-never-crash (log_maker_fill swallows
    a sqlite error); the trade decision / sizing / the −1.0R rail are never touched.
    """
    if attempt is None or attempt.maker_touch_px is None:
        return
    log_maker_fill(
        conn,
        run_id=run_id,
        strategy_id=strategy_id,
        venue=venue,
        symbol=symbol,
        side="buy",
        touch_px=attempt.maker_touch_px,
        fill_px=fill_price,
        outcome=attempt.maker_outcome or "clean_fill",
        reposts=attempt.maker_reposts,
    )


def _fetch_touch_px(
    conn: sqlite3.Connection, *, venue: str, symbol: str, side: str
) -> float | None:
    """Best-effort passive-touch lookup for the price-through shadow (read-only).

    Returns the bid for a buy/long entry, the ask for a sell/short entry — the
    price a resting maker limit would have posted at. ``None`` on a missing
    ``quote_ticks`` row, a non-positive price, or any read error (degrade-
    never-crash — the caller skips the shadow row rather than fabricating a
    touch).
    """
    try:
        row = conn.execute(
            "SELECT bid, ask FROM quote_ticks WHERE venue = ? AND symbol = ? "
            "LIMIT 1",
            (venue, symbol),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    bid, ask = float(row[0] or 0.0), float(row[1] or 0.0)
    is_buy = side.strip().lower() in ("buy", "long")
    px = bid if is_buy else ask
    return px if px > 0.0 else None


def _persist_price_through_shadow(
    conn: sqlite3.Connection | None,
    *,
    venue: str,
    symbol: str,
    side: str,
    fill_price: float,
    touch_px: float | None,
    run_id: str,
    strategy_id: str,
    now_ts: int,
    db_writer: DBWriter | None = None,
) -> None:
    """Persist ONE ``price_through_shadow`` row for EVERY entry fill (maker_fill_sim
    R1 2026-07-12 debate).

    Unlike ``_persist_maker_fill_shadow`` (maker-only, stays honestly 0-row
    while ``real_roundtrip=False``), this fires on every taker AND maker entry.
    ``touch_px`` is resolved by the caller (``_fetch_touch_px``, called BEFORE
    the write-lock-held txn) — ``None`` (missing/stale quote) is a no-op, never
    a fabricated row. Resolution (traded-through / price-improvement /
    missed-opportunity) happens OFFLINE against forward bars, never here.
    Measurement only — never touches the trade decision.
    """
    if conn is None or touch_px is None:
        return
    log_price_through_entry(
        conn,
        run_id=run_id,
        strategy_id=strategy_id,
        venue=venue,
        symbol=symbol,
        side=side,
        fill_px=fill_price,
        touch_px=touch_px,
        now_ts=now_ts,
        db_writer=db_writer,
    )


# OKX venue BALANCE rejects that mean capital (not signal) blocked the fill —
# the capital-rotation trigger. Compliance/min-order/no-fill codes are NOT here.
_ROTATION_BALANCE_REJECT_CODES: frozenset[str] = frozenset(
    {"51008", "51131", "insufficient_balance"}
)


def _maybe_register_balance_rotation_candidate(
    state: ProdLoopState,
    *,
    sig: RawSignal,
    venue: str,
    reject_code: str | None,
    notional_usd: float,
) -> None:
    """Register a venue-balance-blocked signal as a rotation candidate (trig 2).

    Only fires for a BALANCE reject (OKX 51008/51131/insufficient_balance). The
    capital SCALE (``proposed_risk_pct``) is approximated from the sized notional
    vs the demo equity. Import is local to avoid a module-load cycle.
    """
    if reject_code not in _ROTATION_BALANCE_REJECT_CODES:
        return
    equity = EQUITY_USD_DEMO_DEFAULT
    proposed_risk_pct = (notional_usd / equity) if equity > 0.0 else 0.0
    from polaris.scripts._production_rotation import register_rotation_candidate

    register_rotation_candidate(
        state, sig=sig, proposed_risk_pct=proposed_risk_pct, venue=venue,
        binding_reason=f"insufficient_balance:{reject_code}",
    )

# Day 9 F12 fix: pull from sizing.constants SSOT so dashboard + pipeline agree.
EQUITY_USD_DEMO_DEFAULT = OKX_DEMO_STARTING_EQUITY_USD


# ---------------------------------------------------------------------------
# Layer 7 — fence + idempotent register + paper submit
# ---------------------------------------------------------------------------


async def _real_open_fill(
    *,
    venue: str,
    symbol: str,
    side: str,
    notional_usd: float,
    last_price: float,
    strategy_id: str,
    asset_class: str = "",
    strength: float = 0.0,
    okx_adapter: Any = None,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
    capital_lots: float | None = None,
    capital_contract_factor_usd: float | None = None,
    prefer_maker: bool = False,
    marketable_limit: bool = False,
    maker_no_fill: str = "market",
    alpaca_pending_order_id: str | None = None,
    alpaca_pending_client_order_id: str | None = None,
) -> OpenAttempt:
    """Drive the real demo venue entry leg → return an ``OpenAttempt``.

    ``alpaca_pending_order_id`` (open-confirm fix): a prior tick's carried-over
    unconfirmed Alpaca open ref, resolved before any fresh submit. ``None`` for
    every other venue/path (byte-identical).

    P0 venue wire: replaces the synthetic fill source with a live adapter
    response. OKX places a market buy then polls the order; Capital opens a
    position then confirms. The returned ``OpenAttempt`` carries the normalized
    ``fill`` + ``deal_id`` (Capital position id the close leg needs; ``None``
    for OKX, which closes by base_qty) on success, or the venue reject
    ``code``/``msg`` on reject / no-fill so the caller can classify it as an
    EXTERNAL venue event rather than a strategy fault.

    Adapters are injected for testability; when ``okx_adapter`` is ``None`` we
    construct one from the ``OKX_DEMO_*`` env (real network). Capital always
    needs the loop-owned ``capital_session``.
    """
    # Stream SSOT (design §2.1): the adapter-dispatch decision routes on the
    # resolved product_class (okx→spot, capital→cfd) instead of a venue literal.
    # Identical dispatch; per-venue bodies (creds / session) are unchanged.
    if resolve_stream(venue).product_class == "spot":
        # FIX 2: clamp the entry to the live OKX available USDT before submit
        # (best-effort, None=prior path) so we never re-emit an unfundable order.
        if okx_adapter is not None:
            return await real_okx_open_fill(
                okx_adapter, inst_id=symbol, notional_usd=notional_usd,
                strategy_id=strategy_id, last_price=last_price, strength=strength,
                available_usdt=await fetch_okx_available_usdt(okx_adapter),
                prefer_maker=prefer_maker, marketable_limit=marketable_limit,
                maker_no_fill=maker_no_fill,
            )
        api_key = os.environ.get("OKX_DEMO_API_KEY", "")
        secret = os.environ.get("OKX_DEMO_SECRET", "")
        passphrase = os.environ.get("OKX_DEMO_PASSPHRASE", "")
        base_url = resolve_okx_base_url(os.environ.get("OKX_DEMO_BASE"))
        if not (api_key and secret and passphrase):
            logger.error("[real-open] OKX_DEMO_* env missing — cannot submit")
            return OpenAttempt(fill=None, reject_code="env_missing")
        async with OKXAdapter(
            api_key=api_key, secret=secret, passphrase=passphrase, base_url=base_url,
        ) as adapter:
            return await real_okx_open_fill(
                adapter, inst_id=symbol, notional_usd=notional_usd,
                strategy_id=strategy_id, last_price=last_price, strength=strength,
                available_usdt=await fetch_okx_available_usdt(adapter),
                prefer_maker=prefer_maker, marketable_limit=marketable_limit,
                maker_no_fill=maker_no_fill,
            )

    # Track C — Alpaca US equity (additive; OKX/Capital paths above unchanged).
    if resolve_stream(venue).product_class == "equity":
        side_eq = "buy" if side == "long" else "sell"
        if alpaca_adapter is not None:
            return await real_alpaca_open_fill(
                alpaca_adapter, symbol=symbol, notional_usd=notional_usd,
                strategy_id=strategy_id, side=side_eq, last_price=last_price,
                buying_power=await fetch_alpaca_buying_power(alpaca_adapter),
                pending_order_id=alpaca_pending_order_id,
                pending_client_order_id=alpaca_pending_client_order_id,
            )
        api_key, secret = resolve_alpaca_credentials()
        if not (api_key and secret):
            logger.error("[real-open] ALPACA_PAPER_* env missing — cannot submit")
            return OpenAttempt(fill=None, reject_code="env_missing")
        async with AlpacaAdapter(api_key=api_key, secret=secret) as adapter:
            return await real_alpaca_open_fill(
                adapter, symbol=symbol, notional_usd=notional_usd,
                strategy_id=strategy_id, side=side_eq, last_price=last_price,
                buying_power=await fetch_alpaca_buying_power(adapter),
                pending_order_id=alpaca_pending_order_id,
                pending_client_order_id=alpaca_pending_client_order_id,
            )

    # Capital CFD — close needs the deal_id from the confirm.
    if capital_session is None:
        logger.error("[real-open] no Capital session — cannot submit %s", symbol)
        return OpenAttempt(fill=None, reject_code="no_session")
    cap_adapter = CapitalAdapter(capital_session)
    direction = "BUY" if side == "long" else "SELL"
    # Bug C fix: ``capital_lots`` = the T4 notional expressed in per-epic venue
    # lots; ``capital_contract_factor_usd`` makes the fill record the REAL
    # submitted exposure (size × level × factor — leverage scales margin only,
    # never exposure; the prior fixed MIN_CAPITAL_LOT stamped every live entry
    # $200.00). Degraded plan (None) → legacy 1-lot path byte-identical so the
    # entry always flows (flow_not_block). See _production_capital_sizing.
    leverage = derive_leverage(resolve_stream(venue), asset_class)
    size = (
        capital_lots
        if capital_lots is not None and capital_lots > 0.0
        else MIN_CAPITAL_LOT
    )
    return await real_capital_open_fill(
        cap_adapter, epic=symbol, direction=direction, size=size,
        strategy_id=strategy_id, last_price=last_price, leverage=leverage,
        contract_factor_usd=capital_contract_factor_usd,
    )


async def reserve_and_submit(
    *,
    conn: sqlite3.Connection,
    state: ProdLoopState,
    sig: RawSignal,
    venue: str,
    symbol: str,
    asset_class: str,
    underlying_group_id: str,
    notional_usd: float,
    last_price: float,
    now_ts: int,
    real_roundtrip: bool = False,
    okx_adapter: Any = None,
    capital_session: Any = None,
    alpaca_adapter: Any = None,
    prefer_maker: bool = False,
    marketable_limit: bool = False,
    maker_no_fill: str = "market",
) -> SimulatedTrade | None:
    """A2 + K fix: AllocatorFence reservation → idempotent register → submit.

    ``real_roundtrip=True`` (P0 venue wire) submits a real demo order via the
    venue adapter instead of the local synthetic fill. The fence reserve →
    register → confirm → atomic-persist contract is identical; only the fill
    payload source changes.

    ``prefer_maker`` (flow_pressure retune): route the OKX entry through the
    maker-first (post-only at touch → TAKER fallback) path even for a strong
    signal that would otherwise go straight to market. Cheaper when it rests at
    the touch (8 bps maker), never a missed trade (the post-only path always
    falls back to taker on a would-cross/reject/timeout). Default ``False`` keeps
    every existing caller byte-identical.

    ``marketable_limit`` ([[ab_letrun_maker_2026-06-24]] Build B): route the OKX
    entry through a marketable-limit (cross the spread with a cap_bps-capped
    limit — fills like a taker, never worse than the cap). For momentum/breakout
    families that cannot rest passively. Always falls back to market on
    no-fill/reject/timeout (flow_not_block). Default ``False`` is byte-identical.
    """
    # T7: asset_class is forwarded to _real_open_fill so Capital derives its
    # per-market leverage (FX 30 / index 20 / commodity 20 / crypto 2). Post
    # Bug C the leverage feeds ONLY the degraded legacy fill maths — the wired
    # path records exposure as lots × level × contract factor (margin ≠ exposure).
    # Task 3 / D2: skip a runtime-blocklisted (venue, symbol) BEFORE reserving —
    # the venue permanently refuses it (compliance), so reserving + submitting
    # would only churn. No reservation, no fault (it's an external decision).
    if is_blocklisted(conn, venue, symbol):
        logger.info("[L7/blocklist] %s:%s non-tradeable — skipping", venue, symbol)
        return None
    # CAPITAL TIMETABLE DELAY-ROUTE (P0 venue-reject wave): a prior tick's
    # market-closed reject on this EXACT (venue, symbol, strategy_id, side)
    # stamped the venue's own next-open instant (maybe_stamp_delayed_reopen,
    # called from the reject path below) — skip resubmitting until that
    # instant has passed instead of re-rejecting every tick (SG25 '16/16
    # reject'). flow_not_block: a DEFERRAL, never a permanent block — the
    # SAME signal flows again the instant the stamp expires. No reservation,
    # no fault (an external timing decision, not a strategy fault).
    if venue == "capital" and submit_suppressed_until_reopen(
        conn, venue=venue, symbol=symbol, strategy_id=sig.strategy_id,
        side=sig.side, now_ts=now_ts,
    ):
        logger.info(
            "[capital/reopen-route] %s:%s sid=%s side=%s still inside the "
            "closed window — deferring submit", venue, symbol,
            sig.strategy_id, sig.side,
        )
        return None
    # OPEN-CONFIRM FIX: an Alpaca open accepted (``pending_new``) but not yet
    # confirmed within a PRIOR tick's poll budget carries its venue_order_id
    # here. DUPLICATE-SUBMIT GUARD: while that ref is live, no fresh order is
    # placed on the SAME (venue, symbol, strategy_id, side) — the pending ref
    # is resolved (confirm-first) via ``alpaca_pending_order_id`` below instead.
    alpaca_pending: PendingOpenRef | None = None
    if real_roundtrip and venue == "alpaca":
        alpaca_pending = read_pending_open(
            conn, venue=venue, symbol=symbol, strategy_id=sig.strategy_id,
            side=sig.side,
        )
        # PARTIAL-FILL TRUE-UP: a position from a snapshot fill already exists
        # for this ref — fold the final venue qty into it here (best-effort,
        # never blocks this tick's fresh signal below) instead of routing it
        # through the fresh-submit dedup path (which would treat it as a new
        # open ready to duplicate-persist).
        if alpaca_pending is not None and alpaca_pending.state == "partial_trueup":
            adapter_for_trueup = alpaca_adapter
            if adapter_for_trueup is None:
                api_key, secret = resolve_alpaca_credentials()
                if api_key and secret:
                    async with AlpacaAdapter(api_key=api_key, secret=secret) as ad:
                        await true_up_alpaca_partial(
                            conn, ad, alpaca_pending, venue=venue, symbol=symbol,
                            strategy_id=sig.strategy_id, side=sig.side,
                        )
            else:
                await true_up_alpaca_partial(
                    conn, adapter_for_trueup, alpaca_pending, venue=venue,
                    symbol=symbol, strategy_id=sig.strategy_id, side=sig.side,
                )
            alpaca_pending = None  # never fed into the fresh-submit dedup path
    # NOTE: the prior OKX param/precision per-symbol cooldown SKIP (Jin
    # 2026-06-22) is REMOVED (Jin 2026-06-23). The root fix is the submit-path
    # min-size CLAMP-UP (OKXAdapter._round_px_sz → clamp_up_to_min) which bumps a
    # sub-min order UP to the venue minimum so it FLOWS — so the 51020 below-min
    # reject that the cooldown reacted to essentially stops occurring. A residual
    # param reject is still classified EXTERNAL/non-fault (no halt) but with NO
    # per-symbol skip (flow_not_block: the symbol is never blocked).
    # Bug C fix: translate the T4 notional into per-epic venue lots BEFORE the
    # fence reservation so the ledger / order payload / rotation candidate all
    # carry the notional ACTUALLY submitted (requested == submitted == recorded).
    # The fence is a ledger (no cap comparison); caps bind at T4 sizing off
    # position_risk_state — which now records real exposure — so a venue
    # min-deal bump self-corrects on the next entry. Degraded → None → legacy.
    capital_plan: CapitalOrderPlan | None = None
    if real_roundtrip and resolve_stream(venue).product_class == "cfd":
        capital_plan = await translate_capital_order(
            state=state, conn=conn, capital_session=capital_session,
            epic=symbol, notional_usd=notional_usd, last_price=last_price,
        )
        if capital_plan is not None:
            notional_usd = capital_plan.effective_notional_usd
    fence = get_process_fence(conn)
    order_key = build_order_key(
        strategy_id=sig.strategy_id, venue=venue, symbol=symbol,
        timeframe=_strategy_timeframe(sig.strategy_id), signal_ts=now_ts, side=sig.side,
    )
    payload = {
        "strategy_id": sig.strategy_id, "symbol": symbol, "side": sig.side,
        "notional_usd": float(notional_usd), "last_price": float(last_price),
    }
    payload_h = payload_hash(payload)
    request = AllocationRequest(
        strategy_id=sig.strategy_id, venue=venue, symbol=symbol, side=sig.side,
        correlation_group=sig.correlation_group,
        underlying_group_id=underlying_group_id,
        requested_notional=float(notional_usd),
        requested_risk=float(sig.sizing_hint),
        signal_id=sig.signal_id, order_key=order_key,
    )
    try:
        reservation = await fence.check_and_reserve(request, now_ts=now_ts)
    except ReservationConflictError as exc:
        state.fence_conflicts += 1
        logger.warning("[L7/fence] reservation conflict: %r", exc)
        return None
    state.fence_reservations += 1
    try:
        register_order_intent(
            conn, order_key=order_key, strategy_id=sig.strategy_id,
            venue=venue, symbol=symbol, timeframe=_strategy_timeframe(sig.strategy_id), signal_ts=now_ts,
            side=sig.side, payload_hash_value=payload_h, now_ts=now_ts,
        )
    except IdempotencyConflictError as exc:
        state.idempotency_conflicts += 1
        logger.error("[L7/idempotent] conflict: %r — releasing reservation", exc)
        await fence.release_reservation(
            reservation["reservation_id"],
            reason="idempotency_conflict", now_ts=now_ts,
        )
        record_fault(
            conn, strategy_id=sig.strategy_id, fault_type=FAULT_REJECT,
            now_ts=now_ts,
            detail={"phase": "idempotent_register", "exc": str(exc)},
        )
        state.fault_events += 1
        return None
    deal_id: str | None = None
    # FIX-EXEC ([[weekend_maker_honest_rerun_2026-06-28]]): carry the OKX post-only
    # maker-fill metadata (touch / reposts / outcome) from the real open leg to the
    # ENTRY persist below so ONE maker_fill_shadow row is written per maker fill.
    # ``None`` for the simulate path + a taker/market fill (no maker row → no
    # double-count). Measurement only — never touches the trade decision.
    maker_attempt: OpenAttempt | None = None
    if real_roundtrip:
        # P0 venue wire: real demo order via adapter. On reject / no-fill we
        # release the reservation + record a fault (orphan-free) and bail.
        # P0-2 fix: a venue/adapter *exception* (timeout, transport, parse)
        # must also release + fault here — it previously escaped the function,
        # bypassing release_reservation and leaking the order_key + reservation.
        try:
            attempt = await _real_open_fill(
                venue=venue, symbol=symbol, side=sig.side, notional_usd=notional_usd,
                last_price=last_price, strategy_id=sig.strategy_id,
                asset_class=asset_class, strength=sig.strength,
                okx_adapter=okx_adapter, capital_session=capital_session,
                alpaca_adapter=alpaca_adapter,
                capital_lots=capital_plan.lots if capital_plan else None,
                capital_contract_factor_usd=(
                    capital_plan.contract_factor_usd if capital_plan else None
                ),
                prefer_maker=prefer_maker, marketable_limit=marketable_limit,
                maker_no_fill=maker_no_fill,
                alpaca_pending_order_id=(
                    alpaca_pending.venue_order_id if alpaca_pending else None
                ),
                alpaca_pending_client_order_id=(
                    alpaca_pending.client_order_id if alpaca_pending else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 — venue I/O must not escape
            logger.error(
                "[L7/real] %s:%s entry adapter raised: %r — releasing reservation",
                venue, symbol, exc,
            )
            await fence.release_reservation(
                reservation["reservation_id"],
                reason="real_open_exception", now_ts=now_ts,
            )
            record_fault(
                conn, strategy_id=sig.strategy_id, fault_type=FAULT_EXCEPTION,
                now_ts=now_ts,
                detail={"phase": "real_open_fill", "venue": venue,
                        "symbol": symbol, "exc": str(exc)[:200]},
            )
            state.fault_events += 1
            return None
        if attempt.fill is None:
            # Bug C review fix: a size/step-shaped venue reject means the cached
            # constraint may be stale — evict it (cooldown bypassed) so the next
            # attempt re-fetches fresh dealing rules. Flow preserved.
            if resolve_stream(venue).product_class == "cfd":
                maybe_evict_on_reject(
                    state, epic=symbol, reject_code=attempt.reject_code,
                    reject_msg=attempt.reject_msg,
                )
                # CAPITAL TIMETABLE DELAY-ROUTE: a market-closed/offline
                # reject stamps this key's next-open instant (computed from
                # the SAME cached constraint the sizing path already warmed —
                # zero new network) so reserve_and_submit's pre-submit gate
                # (top of this function) defers the next attempt instead of
                # re-rejecting every tick. No-op for every other reject code.
                if venue == "capital":
                    maybe_stamp_delayed_reopen(
                        conn, venue=venue, symbol=symbol,
                        strategy_id=sig.strategy_id, side=sig.side,
                        reject_code=attempt.reject_code,
                        constraint=state.capital_constraints.peek(symbol),
                        now_ts=now_ts,
                    )
            # OPEN-CONFIRM FIX: still ACCEPTED-but-unfilled after this tick's poll
            # budget (fresh submit OR a carried-over pending ref that is still
            # live) → persist/refresh the pending ref so the NEXT tick confirms
            # this SAME order first instead of submitting a duplicate. A GENUINE
            # reject (no venue_order_id) clears any stale ref — nothing live
            # remains to carry forward.
            if real_roundtrip and venue == "alpaca":
                if attempt.venue_order_id:
                    upsert_pending_open(
                        conn, venue=venue, symbol=symbol,
                        strategy_id=sig.strategy_id, side=sig.side,
                        venue_order_id=attempt.venue_order_id,
                        client_order_id=attempt.client_order_id,
                        notional_usd=notional_usd, last_price=last_price,
                        now_ts=now_ts,
                    )
                    logger.info(
                        "[alpaca/open] pending confirm carryover %s:%s order=%s",
                        symbol, sig.strategy_id, attempt.venue_order_id,
                    )
                else:
                    clear_pending_open(
                        conn, venue=venue, symbol=symbol,
                        strategy_id=sig.strategy_id, side=sig.side,
                    )
            await _handle_open_reject(
                conn, fence=fence, state=state, sig=sig, venue=venue,
                symbol=symbol, reservation_id=reservation["reservation_id"],
                reject_code=attempt.reject_code, reject_msg=attempt.reject_msg,
                now_ts=now_ts,
                # ORPHAN NET: an accepted-but-unfilled real order is still LIVE
                # at the venue → its id is recorded as an orphan for reconcile
                # (only under real_roundtrip; the simulated path has no venue
                # order to leak). flow_not_block — tracking, not a throttle.
                venue_order_id=attempt.venue_order_id if real_roundtrip else None,
                unfilled_qty=attempt.unfilled_qty,
            )
            # Capital rotation TRIGGER SEAM 2 (Jin 2026-05-30): a venue BALANCE
            # reject (OKX 51008/51131 insufficient_balance) means capital — not
            # signal quality — blocked this fill. Register it as a rotation
            # candidate so a weak held can free capital for it next tick. The
            # capital SCALE is recovered from the sized notional vs equity (the
            # sizer already passed, so this is the real requested deploy). Other
            # rejects (compliance/min-order/no-fill) are NOT capital blocks and
            # are not registered.
            _maybe_register_balance_rotation_candidate(
                state, sig=sig, venue=venue,
                reject_code=attempt.reject_code, notional_usd=notional_usd,
            )
            return None
        fill, deal_id = attempt.fill, attempt.deal_id
        maker_attempt = attempt  # carries the maker touch/reposts/outcome (if any)
        # OPEN-CONFIRM FIX: a fill landed (fresh submit or a resolved carryover)
        # — clear any pending ref for this key so a future tick's dedup check
        # does not skip on a stale row.
        if real_roundtrip and venue == "alpaca":
            clear_pending_open(
                conn, venue=venue, symbol=symbol,
                strategy_id=sig.strategy_id, side=sig.side,
            )
        # A fresh Capital fill landed on this key — clear any stale
        # delay-route stamp (best-effort; a no-op if none existed).
        if venue == "capital":
            clear_reopen_stamp(
                conn, venue=venue, symbol=symbol,
                strategy_id=sig.strategy_id, side=sig.side,
            )
        # P0-5 fix: persist the close-relevant venue ref so a restart can
        # reconstruct it. For Capital the close needs the position ``deal_id``
        # (which differs from the top-level confirm dealId); persist it as the
        # fill ``order_id`` so hydration recovers it. OKX closes by base_qty,
        # so its order_id is left as the OKX ``ordId``.
        # Stream SSOT (design §2.1): cfd product (capital) closes by deal_id;
        # spot (okx) closes by base_qty. Routes on product_class — identical.
        if resolve_stream(venue).product_class == "cfd" and deal_id:
            fill = replace(fill, order_id=deal_id)
        trade = SimulatedTrade(
            signal_id=sig.signal_id, venue=venue, symbol=symbol,
            strategy_id=sig.strategy_id, side=sig.side, entry_price=fill.fill_price,
            notional_usd=fill.size_usd, open_ts=now_ts,
        )
    else:
        fill, trade = simulate_open_fill(
            signal=sig, venue=venue, last_price=last_price, notional_usd=notional_usd,
        )
    trade.venue_order_id = fill.order_id or None
    trade.deal_id = deal_id
    trade.base_qty = fill.base_qty
    # Day 8 codex R2 P1 fix: confirm the reservation FIRST so a failed
    # confirm cannot leave an orphan positions/fill row in SQLite without a
    # matching in-memory trade. After confirm we persist atomically; on
    # persist failure we release + record fault and the loop's caller never
    # sees the trade (orphan-free guarantee).
    try:
        await fence.confirm_reservation(
            reservation["reservation_id"],
            venue_order_ref=fill.order_id or fill.client_order_id or "demo",
            now_ts=now_ts,
        )
    except ReservationConflictError as exc:
        state.fence_conflicts += 1
        logger.error("[L7] confirm_reservation failed: %r", exc)
        record_fault(
            conn, strategy_id=sig.strategy_id, fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"phase": "confirm_reservation", "exc": str(exc)},
        )
        state.fault_events += 1
        # P0-3 fix: under real_roundtrip a venue position already exists, but
        # confirm failed so it is now untracked. Record a durable orphan with
        # the venue ref so a reconciliation pass can close the live exposure.
        if real_roundtrip:
            record_venue_orphan(
                conn, strategy_id=sig.strategy_id, venue=venue, symbol=symbol,
                side=sig.side, phase="confirm_reservation",
                venue_order_id=trade.venue_order_id, deal_id=deal_id,
                base_qty=fill.base_qty, now_ts=now_ts,
            )
        return None
    # Day 8 codex BLOCKER + R2 P1 fix: persist a real ``positions`` row +
    # tag the simulated trade with that id so Layer 6 recalc/swap and the
    # close path operate on the same persistent state Layer 4/5 see. Wrap
    # both writes in a single SQLite transaction so a failure cannot leave
    # half-state.
    # Instrument-unique. The tick-engine signals share a GENERIC signal_id
    # (e.g. 'tick_micro_reversion'), so ``sig.signal_id[:16] + now_ts`` ALONE
    # collided across EVERY instrument opened in the same tick. Two same-tick
    # opens then (a) overwrote each other's positions row (INSERT OR REPLACE on
    # the position_id PK below) and (b) cross-matched entry fills by
    # contribution_id in the close/recalc path → exploding pnl_usd/mfe_r (live:
    # a J225 close matched OIL_CRUDE's 94.168 entry → +$145k phantom, ~965,000x).
    # venue+symbol makes the id unique per instrument. Bar strategies already
    # vary signal_id per instrument, so this only tightens — never changes — them.
    position_id = f"pos_{sig.signal_id[:16]}_{venue}_{symbol}_{now_ts}"
    trade.position_id = position_id
    trade.correlation_group = sig.correlation_group
    trade.underlying_group_id = underlying_group_id
    # Entry-time ATR anchor — the R-unit denominator for this position's whole
    # life (pnl_r / mfe_r / mae_r), read on the strategy's OWN timeframe (tf →
    # 1m fallback; degenerate flat-bar windows rejected). No usable window →
    # NULL (legacy-graceful current-ATR fallback) — never a fake 0.005 anchor.
    entry_atr_pct: float | None = None
    entry_atr_timeframe: str | None = None
    try:
        anchor = timeframe_anchor_atr_pct(
            conn, instrument_id=f"{venue}:{symbol}",
            timeframe=_strategy_timeframe(sig.strategy_id), now_ts=now_ts,
        )
    except sqlite3.Error as exc:
        anchor = None
        logger.warning("[L7/open] entry ATR anchor read failed: %r", exc)
    if anchor is not None:
        entry_atr_pct, entry_atr_timeframe = anchor
    # The trade's per-trade 1R-in-dollars (stop distance × filled size), stamped
    # at entry. Re-based (2026-07-07): this IS AGAIN the realised-R denominator
    # (``realised_r`` reads ``positions.risk_usd`` at close — the per-stream
    # R_budget detour is gone). Measurement only — no sizing/entry change.
    entry_base_qty = float(
        fill.base_qty if fill.base_qty > 0 else notional_usd / max(last_price, 1e-6)
    )
    # Quote-ccy → USD conversion (audit rank 4, [[trade_mess_full_audit_2026-07-02_verdict]]):
    # ``fill.fill_price`` is in the instrument's QUOTE currency (Capital
    # USDJPY=JPY, J225=JPY, EU50=EUR, ...), so risk_usd_at_entry's raw product
    # inflates/deflates by the FX level unless converted (live: J225 risk_usd
    # stamped $47,615.89 vs real ≈$317). OKX/Alpaca are always USD-quoted →
    # rate stays 1.0. Peek-only (no network) — matches the #50 uPnL dashboard
    # fix's read pattern (bars first, cached constraint snapshot second); a
    # cold/degraded rate leaves risk_usd at the pre-fix raw-quote value rather
    # than blocking the entry (flow_not_block, measurement-only).
    quote_usd_rate = 1.0
    if resolve_stream(venue).product_class == "cfd":
        constraint = state.capital_constraints.peek(symbol)
        quote_ccy = constraint.quote_ccy if constraint is not None else ""
        rate = _peek_quote_usd_rate(
            state.capital_constraints, conn, quote_ccy, md_conn=state.md_conn,
        )
        if rate is not None and rate > 0.0:
            quote_usd_rate = rate
    # R-unit ruler bind fix (forward-fix, [[exit_peak_lock_bind_2026-07-10]]):
    # the live precise-exit engine denominates mfe_r / the peak-lock floor
    # against ``_stop_atr_mult_for_strategy``'s FEE_FLOOR_K-widened multiplier
    # (trade_mess_full_audit_2026-07-02, up to ~8-18x on tight-ATR Capital FX
    # legs) — but this stamp NEVER threaded it (silently defaulted to the flat
    # SSOT ``STOP_ATR_MULT=2.0``). Since the 2026-07-07 re-base made
    # ``positions.risk_usd`` the realised-R denominator again, a winner whose
    # peak-fraction floor correctly locked ~65% of its peak PRICE move (verified
    # against live fills) got its REPORTED realised R computed on a ~4-18x
    # NARROWER ruler than its own "MFE 3.2R" figure — reading as a ~10-15%
    # capture instead of the true ~65%. Same resolver the exit engine + the T4
    # sizer (``_production_run_signal.build_sizer_payload``) already use — this
    # only aligns the RULER; no sizing / entry / the G6 -1.0R rail (which reads
    # this SAME resolver directly, never risk_usd) changes.
    risk_usd = risk_usd_at_entry(
        entry_price=fill.fill_price,
        entry_atr_pct=entry_atr_pct if entry_atr_pct is not None else 0.0,
        base_qty=entry_base_qty,
        stop_atr_mult=_stop_atr_mult_for_strategy(
            sig.strategy_id,
            atr_pct=entry_atr_pct if entry_atr_pct is not None else 0.0,
        ),
        quote_usd_rate=quote_usd_rate,
    ) or None
    # Entry-regime anchor — the regime stamped at THIS fill (the entry-thesis
    # reference the adaptive thesis re-map compares the live regime against).
    # NULL when unseeded (legacy/smoke) so the re-map degrades safe. Resolved
    # once here and reused for the lineage segment below (no duplicate query).
    open_regime = (
        fetch_regime(conn, venue=venue, underlying_group_id=underlying_group_id)
        or ""
    )
    # regime v2 twinlight (design-regime-v2-rollout.md W2, behavior-0) —
    # entry_regime_v2 bare alongside entry_regime (구 4라벨), NEVER read by
    # sizing/gating/exit until the W4 flip ladder. fetch_regime_v2 is
    # internally fail-open (_safe_lookup replica) → None on any read failure
    # (pre-migration DB / locked row), stamping NULL — the SAME degrade
    # entry_regime already has for unseeded legacy rows.
    open_regime_v2 = fetch_regime_v2(
        conn, venue=venue, underlying_group_id=underlying_group_id
    )
    # PRICE-THROUGH SHADOW touch lookup (maker_fill_sim R1 2026-07-12 debate):
    # a best-effort quote_ticks read done BEFORE the write-lock-held txn below
    # (same placement as the regime lookups above) so the shadow
    # instrumentation never extends the entry's BEGIN IMMEDIATE hold time.
    shadow_touch_px = _fetch_touch_px(conn, venue=venue, symbol=symbol, side=sig.side)
    try:
        # autocommit-mode connection (init_db uses isolation_level=None) —
        # explicit BEGIN+COMMIT is an atomic boundary in SQLite. ROLLBACK
        # below if an exception fires.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO positions "
            "(position_id, venue, symbol, underlying_group_id, signal_id, "
            " strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
            " status, opened_ts, swap_count, deal_id, entry_atr_pct, "
            " entry_atr_timeframe, risk_usd, entry_regime, seed_tag, "
            " entry_regime_v2) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 0, ?, ?, ?, ?, ?, ?, ?)",
            (
                position_id, venue, symbol, underlying_group_id, sig.signal_id,
                sig.strategy_id, sig.strategy_id, sig.strategy_id, sig.side,
                entry_base_qty,
                now_ts, trade.deal_id, entry_atr_pct, entry_atr_timeframe,
                risk_usd, open_regime, sig.seed_tag, open_regime_v2,
            ),
        )
        # contribution_id ties the entry fill back to the position so the
        # close path can read the *exact* entry by position_id (P0 fix).
        persist_fill(conn, fill, is_close=False, contribution_id=position_id)
        # FIX-EXEC ([[weekend_maker_honest_rerun_2026-06-28]]): persist the maker-fill
        # shadow row HERE (the conn IS held, the fill happens exactly once → no
        # double-count; the close path is NOT touched). A taker/sim fill carries no
        # maker_touch_px → no-op (the persist is maker-only). Inside the open txn so
        # the row never outlives a rolled-back open. Measurement only — the dollar
        # truth / sizing / the −1.0R rail are never altered.
        _persist_maker_fill_shadow(
            conn, attempt=maker_attempt, run_id=sig.signal_id,
            strategy_id=sig.strategy_id, venue=venue, symbol=symbol,
            side=sig.side, fill_price=fill.fill_price,
        )
        # PRICE-THROUGH SHADOW (maker_fill_sim R1 2026-07-12 debate, Hybrid
        # C+A): fires on EVERY entry (unlike maker_fill_shadow above, which
        # legitimately stays 0-row while real_roundtrip=False — no real maker
        # fill can occur). ``shadow_touch_px`` was resolved BEFORE this txn
        # (quote_ticks read); resolution (traded-through / price-improvement /
        # missed-opportunity) is OFFLINE against forward bars
        # (tools/visualizer/price_through_channel.py) — never decided here.
        # db_writer-routed (new-write policy) so the shadow insert never
        # extends this txn's write-lock hold. Shadow-only — never touches the
        # fill / sizing / exit decision.
        _persist_price_through_shadow(
            conn, venue=venue, symbol=symbol, side=sig.side,
            fill_price=fill.fill_price, touch_px=shadow_touch_px,
            run_id=sig.signal_id, strategy_id=sig.strategy_id, now_ts=now_ts,
            # storage-split — price_through_shadow is marketdata-domain.
            db_writer=state.md_db_writer,
        )
        # P5 gap-b: populate the open-position risk row the sizer's
        # PortfolioState reads, so per-symbol/underlying/cluster/track caps bind
        # (capital-efficiency / opportunity-cost ceiling — NOT a defensive
        # throttle). Same txn as the fill so the row never outlives a rolled-back
        # open. ``opened_ts == now_ts`` is the PK the close path deletes on.
        persist_position_risk_state(
            conn,
            venue=venue,
            symbol=symbol,
            instrument_id=fill.instrument_id,
            underlying_group_id=underlying_group_id,
            strategy=sig.strategy_id,
            track=resolve_stream(venue).track,
            asset_class=asset_class,
            signal_strength=float(sig.strength),
            notional_usd=float(fill.size_usd),
            equity_usd=float(EQUITY_USD_DEMO_DEFAULT),
            opened_ts=now_ts,
        )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error("[L7] persist_fill / position open failed: %r", exc)
        record_fault(
            conn, strategy_id=sig.strategy_id, fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"phase": "persist_fill_open", "exc": str(exc)},
        )
        state.fault_events += 1
        # P0-4 fix: under real_roundtrip the venue position is live but the DB
        # write rolled back — it is now untracked. Record a durable orphan
        # (venue ref + base_qty) so reconciliation can recover/close it.
        # ``record_venue_orphan`` opens its own statement *after* ROLLBACK so
        # it commits even though the position/fill insert was reverted.
        if real_roundtrip:
            record_venue_orphan(
                conn, strategy_id=sig.strategy_id, venue=venue, symbol=symbol,
                side=sig.side, phase="persist_fill_open",
                venue_order_id=trade.venue_order_id, deal_id=deal_id,
                base_qty=fill.base_qty, now_ts=now_ts,
            )
        # Reservation already confirmed; release it so the order_key isn't
        # leaked. (No SQLite mutation happened thanks to ROLLBACK above.)
        await fence.release_reservation(
            reservation["reservation_id"],
            reason="persist_fail", now_ts=now_ts,
        )
        return None
    state.fills_open += 1
    # PARTIAL-FILL TRUE-UP: the persisted fill is a SNAPSHOT (poll budget
    # expired while still ``partially_filled``) — keep a ``partial_trueup``
    # pending ref naming THIS position so the next tick folds the final
    # venue qty in (see ``true_up_alpaca_partial``). No duplicate submit risk:
    # the fresh-submit dedup path above already routes a ``partial_trueup``
    # ref straight into the true-up, never back into ``_real_open_fill``.
    if real_roundtrip and venue == "alpaca" and maker_attempt is not None \
            and maker_attempt.partial_trueup and maker_attempt.venue_order_id:
        upsert_pending_open(
            conn, venue=venue, symbol=symbol, strategy_id=sig.strategy_id,
            side=sig.side, venue_order_id=maker_attempt.venue_order_id,
            client_order_id=maker_attempt.client_order_id,
            notional_usd=fill.size_usd, last_price=fill.fill_price,
            now_ts=now_ts, state="partial_trueup", position_id=position_id,
        )
        logger.info(
            "[alpaca/trueup] %s position=%s snapshot qty=%.9f — pending "
            "true-up ref kept for next tick", symbol, position_id, fill.base_qty,
        )
    # Successful open (INFO): the entry 거동 record — venue/ticker/side, the
    # entry fill price + notional, strategy, and the position_id that ties the
    # whole 6-effect close fan-out together (trade_id correlation). "opened" +
    # "fill" keywords surface it on the dashboard board.js pane. Log only.
    logger.info(
        "[L7/open] opened %s:%s trade_id=%s side=%s strategy=%s filled "
        "entry_price=%.6g notional_usd=%.2f mode=%s",
        venue, symbol, position_id, sig.side, sig.strategy_id,
        fill.fill_price, fill.size_usd, "real" if real_roundtrip else "sim",
    )
    # P3 self-evolve lineage (read-model, behaviour 0): record the ticker ↔
    # strategy ↔ regime open segment. Post-COMMIT + fail-open inside the helper
    # so a lineage write can never roll back / alter the already-persisted open.
    # Live trading never reads this row. ``open_regime`` is the SAME (venue,
    # underlying_group_id) regime already resolved + stamped into entry_regime.
    record_segment_open(
        conn, position_id=position_id, trade_id=position_id, venue=venue,
        ticker=symbol, strategy_id=sig.strategy_id, regime=open_regime,
        entry_ts=now_ts,
    )
    return trade



# ---------------------------------------------------------------------------
# Close-path helpers — re-exported from _production_close (split for line
# budget; original public names preserved).
# ---------------------------------------------------------------------------

from polaris.scripts._production_close import (  # noqa: E402  — keep at end
    close_oldest_with_real_pnl,
    close_specific_position,
    real_pnl_r_from_fills,
)

__all__ = [
    "close_oldest_with_real_pnl",
    "close_specific_position",
    "real_pnl_r_from_fills",
    "reserve_and_submit",
    "run_pipeline_for_signal",
]
