"""Day 8 — venue open-reject classification helpers.

Split out of ``_production_pipeline.py`` to keep that file under the 500-LOC
budget (N3 pure refactor — zero behavior change). ``_production_pipeline``
re-exports ``EXTERNAL_NONFAULT_REJECT_CODES`` / ``COMPLIANCE_REJECT_CODES`` /
``_is_external_reject`` / ``_handle_open_reject`` so every existing import path
is preserved.

Classifies a real-open venue reject as an EXTERNAL event (compliance / balance
/ market-closed / no-fill → release reservation, no strategy fault,
flow_not_block) versus a possible internal/client bug (still records a
FAULT_REJECT so real anomalies can eventually halt). DEMO/PAPER only.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from polaris.core.isolation.blocklist import add_blocklist
from polaris.core.isolation.circuit_breaker import FAULT_REJECT, record_fault
from polaris.core.streams import resolve_stream
from polaris.strategies import RawSignal

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)


# Venue rejects that are EXTERNAL events, not strategy faults — they must NOT
# trip the per-strategy circuit breaker (flow_not_block / no_block_filter).
#   51155 = OKX US-region compliance (pair not tradeable in region)
#   51008 / 51131 = insufficient balance (portfolio/sizing, transient)
#   51201 = OKX SPOT 1000-USDT market-order cap (deterministic VENUE RULE, not
#           a strategy fault — FIX 1 splits >1000 USDT entries so it no longer
#           occurs, but classify it external so the residual race never faults)
#   insufficient_balance = FIX-2 pre-submit balance-clamp skip (wallet below min
#           notional → entry skipped cleanly, not a fault)
#   no_fill = order accepted but unfilled / liquidity / transport no-fill
# A reject code OUTSIDE this set is treated as a possible internal/client bug
# and still records a FAULT_REJECT so real anomalies can eventually halt.
EXTERNAL_NONFAULT_REJECT_CODES: frozenset[str] = frozenset(
    {"51155", "51008", "51131", "51201", "insufficient_balance", "no_fill"}
)
# OKX compliance reject → permanent blocklist (never auto-clears). Balance /
# no-fill codes are transient and are NOT blocklisted.
COMPLIANCE_REJECT_CODES: frozenset[str] = frozenset({"51155"})
# Capital's venue-specific external (non-fault) reject statuses now live in the
# StreamConfig SSOT (B_capital_cfd.external_reject_codes); _is_external_reject
# reads them via resolve_stream (design §2.1).


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
    # Stream SSOT (design §2.1): venue-specific external codes come from the
    # resolved stream's external_reject_codes (A=∅, B=Capital's 4 statuses) —
    # identical to the prior `venue == "capital" and code in <capital set>`.
    # Unknown venue → no stream → not external (matches the prior False branch).
    try:
        stream_codes = resolve_stream(venue).external_reject_codes
    except KeyError:
        return False
    return reject_code in stream_codes


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


__all__ = [
    "COMPLIANCE_REJECT_CODES",
    "EXTERNAL_NONFAULT_REJECT_CODES",
    "_handle_open_reject",
    "_is_external_reject",
]
