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
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from polaris.core.data.fills_persist import persist_fill
from polaris.core.isolation.allocator_fence import (
    AllocationRequest,
    ReservationConflictError,
    get_process_fence,
)
from polaris.core.isolation.blocklist import add_blocklist, is_blocklisted
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
from polaris.core.sizing.constants import OKX_DEMO_STARTING_EQUITY_USD
from polaris.scripts._production_run_signal import run_pipeline_for_signal
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_open_fill
from polaris.scripts._smoke_real_roundtrip import (
    MIN_CAPITAL_LOT,
    OpenAttempt,
    real_capital_open_fill,
    real_okx_open_fill,
    record_venue_orphan,
    resolve_okx_base_url,
)
from polaris.strategies import STRATEGY_REGISTRY, RawSignal
from polaris.venues.capital import CapitalAdapter
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

# Day 9 F12 fix: pull from sizing.constants SSOT so dashboard + pipeline agree.
EQUITY_USD_DEMO_DEFAULT = OKX_DEMO_STARTING_EQUITY_USD

# Venue rejects that are EXTERNAL events, not strategy faults — they must NOT
# trip the per-strategy circuit breaker (flow_not_block / no_block_filter).
#   51155 = OKX US-region compliance (pair not tradeable in region)
#   51008 / 51131 = insufficient balance (portfolio/sizing, transient)
#   no_fill = order accepted but unfilled / liquidity / transport no-fill
# A reject code OUTSIDE this set is treated as a possible internal/client bug
# and still records a FAULT_REJECT so real anomalies can eventually halt.
EXTERNAL_NONFAULT_REJECT_CODES: frozenset[str] = frozenset(
    {"51155", "51008", "51131", "no_fill"}
)
# OKX compliance reject → permanent blocklist (never auto-clears). Balance /
# no-fill codes are transient and are NOT blocklisted.
COMPLIANCE_REJECT_CODES: frozenset[str] = frozenset({"51155"})
# Capital external (non-fault) reject statuses: market closed / not tradeable.
CAPITAL_EXTERNAL_REJECT_CODES: frozenset[str] = frozenset(
    {"MARKET_CLOSED", "MARKET_OFFLINE", "INSTRUMENT_NOT_TRADEABLE", "REJECTED"}
)


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
    okx_adapter: Any = None,
    capital_session: Any = None,
) -> OpenAttempt:
    """Drive the real demo venue entry leg → return an ``OpenAttempt``.

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
    if venue == "okx":
        if okx_adapter is not None:
            return await real_okx_open_fill(
                okx_adapter, inst_id=symbol, notional_usd=notional_usd,
                strategy_id=strategy_id, last_price=last_price,
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
                strategy_id=strategy_id, last_price=last_price,
            )

    # Capital CFD — close needs the deal_id from the confirm.
    if capital_session is None:
        logger.error("[real-open] no Capital session — cannot submit %s", symbol)
        return OpenAttempt(fill=None, reject_code="no_session")
    cap_adapter = CapitalAdapter(capital_session)
    direction = "BUY" if side == "long" else "SELL"
    return await real_capital_open_fill(
        cap_adapter, epic=symbol, direction=direction, size=MIN_CAPITAL_LOT,
        strategy_id=strategy_id, last_price=last_price,
    )


def _is_external_reject(venue: str, reject_code: str | None) -> bool:
    """A venue reject that is EXTERNAL (not a strategy/client fault).

    ``None`` (generic no-fill) is external. OKX codes in
    ``EXTERNAL_NONFAULT_REJECT_CODES`` (compliance / balance / no-fill) and
    Capital market-closed / not-tradeable statuses are external. Everything
    else is treated as a possible internal/client bug and still faults.
    """
    if reject_code is None:
        return True
    if reject_code in EXTERNAL_NONFAULT_REJECT_CODES:
        return True
    return venue == "capital" and reject_code in CAPITAL_EXTERNAL_REJECT_CODES


async def _handle_open_reject(
    conn: sqlite3.Connection,
    *,
    fence: Any,
    state: ProdLoopState,
    sig: RawSignal,
    venue: str,
    symbol: str,
    reservation_id: str,
    reject_code: str | None,
    reject_msg: str | None,
    now_ts: int,
) -> None:
    """Release the reservation + classify a real-open no-fill (Task 2 / D1).

    EXTERNAL venue rejects (compliance / balance / market-closed / no-fill) are
    NOT strategy faults — they release the reservation and bump a telemetry
    counter, but they do NOT trip the circuit breaker (flow_not_block). A
    compliance reject (51155) also adds the (venue, symbol) to the permanent
    runtime blocklist. A reject code outside the external set is a possible
    internal/client bug and still records a FAULT_REJECT so real anomalies can
    eventually halt.
    """
    await fence.release_reservation(
        reservation_id, reason="real_open_no_fill", now_ts=now_ts,
    )
    code_key = reject_code or "no_fill"
    if _is_external_reject(venue, reject_code):
        state.venue_rejects_by_code[code_key] = (
            state.venue_rejects_by_code.get(code_key, 0) + 1
        )
        logger.warning(
            "[L7/real] %s:%s external venue reject code=%s msg=%s — "
            "released, NO strategy fault",
            venue, symbol, code_key, (reject_msg or "")[:120],
        )
        if reject_code in COMPLIANCE_REJECT_CODES:
            add_blocklist(
                conn, venue, symbol, reason="compliance",
                code=reject_code, now_ts=now_ts,
            )
            logger.warning(
                "[L7/blocklist] %s:%s blocklisted (compliance %s)",
                venue, symbol, reject_code,
            )
        return
    # Anomalous reject code → possible internal/client bug. Keep faulting.
    logger.error(
        "[L7/real] %s:%s anomalous reject code=%s — recording FAULT_REJECT",
        venue, symbol, code_key,
    )
    record_fault(
        conn, strategy_id=sig.strategy_id, fault_type=FAULT_REJECT,
        now_ts=now_ts,
        detail={"phase": "real_open_fill", "venue": venue,
                "symbol": symbol, "reject_code": code_key},
    )
    state.fault_events += 1


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
) -> SimulatedTrade | None:
    """A2 + K fix: AllocatorFence reservation → idempotent register → submit.

    ``real_roundtrip=True`` (P0 venue wire) submits a real demo order via the
    venue adapter instead of the local synthetic fill. The fence reserve →
    register → confirm → atomic-persist contract is identical; only the fill
    payload source changes.
    """
    _ = asset_class  # unused at this layer (Layer 5 already used it)
    # Task 3 / D2: skip a runtime-blocklisted (venue, symbol) BEFORE reserving —
    # the venue permanently refuses it (compliance), so reserving + submitting
    # would only churn. No reservation, no fault (it's an external decision).
    if is_blocklisted(conn, venue, symbol):
        logger.info("[L7/blocklist] %s:%s non-tradeable — skipping", venue, symbol)
        return None
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
                okx_adapter=okx_adapter, capital_session=capital_session,
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
            await _handle_open_reject(
                conn, fence=fence, state=state, sig=sig, venue=venue,
                symbol=symbol, reservation_id=reservation["reservation_id"],
                reject_code=attempt.reject_code, reject_msg=attempt.reject_msg,
                now_ts=now_ts,
            )
            return None
        fill, deal_id = attempt.fill, attempt.deal_id
        # P0-5 fix: persist the close-relevant venue ref so a restart can
        # reconstruct it. For Capital the close needs the position ``deal_id``
        # (which differs from the top-level confirm dealId); persist it as the
        # fill ``order_id`` so hydration recovers it. OKX closes by base_qty,
        # so its order_id is left as the OKX ``ordId``.
        if venue == "capital" and deal_id:
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
    position_id = f"pos_{sig.signal_id[:16]}_{now_ts}"
    trade.position_id = position_id
    trade.correlation_group = sig.correlation_group
    trade.underlying_group_id = underlying_group_id
    try:
        # autocommit-mode connection (init_db uses isolation_level=None) —
        # explicit BEGIN+COMMIT is an atomic boundary in SQLite. ROLLBACK
        # below if an exception fires.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO positions "
            "(position_id, venue, symbol, underlying_group_id, strategy_id, "
            " entry_strategy_id, active_strategy_id, side, qty, status, "
            " opened_ts, swap_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 0)",
            (
                position_id, venue, symbol, underlying_group_id,
                sig.strategy_id, sig.strategy_id, sig.strategy_id, sig.side,
                float(
                    fill.base_qty if fill.base_qty > 0
                    else notional_usd / max(last_price, 1e-6)
                ),
                now_ts,
            ),
        )
        # contribution_id ties the entry fill back to the position so the
        # close path can read the *exact* entry by position_id (P0 fix).
        persist_fill(conn, fill, is_close=False, contribution_id=position_id)
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
