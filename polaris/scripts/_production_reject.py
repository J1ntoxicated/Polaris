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
from polaris.scripts._smoke_roundtrip_shared import record_venue_orphan
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
#   maker_no_fill = #77 weekend thin-book maker DELIBERATE skip: a post-only that
#           did not fill in the bounded reprice loop is CANCELLED (no taker
#           fallback) because the edge IS the passive deep-bid fill (a missed
#           fill = 0 realised cost). Releases the reservation cleanly, NEVER a
#           strategy fault (it is the designed behaviour, not an anomaly).
# A reject code OUTSIDE this set is treated as a possible internal/client bug
# and still records a FAULT_REJECT so real anomalies can eventually halt.
EXTERNAL_NONFAULT_REJECT_CODES: frozenset[str] = frozenset(
    {"51155", "51008", "51131", "51201", "insufficient_balance", "no_fill",
     "maker_no_fill"}
)
# OKX compliance reject → permanent blocklist (never auto-clears). Balance /
# no-fill codes are transient and are NOT blocklisted.
COMPLIANCE_REJECT_CODES: frozenset[str] = frozenset({"51155"})
# OKX PARAM / PRECISION reject codes (the entry-stall root-cause family): an
# order whose sz/px/lot-size is malformed for THIS instrument. Jin 2026-06-23:
# the ROOT fix is the submit-path min-size CLAMP-UP (OKXAdapter._round_px_sz →
# clamp_up_to_min) which bumps a sub-min order UP to the venue minimum so it
# FLOWS — so the 51020 below-min flood essentially stops occurring. The prior
# per-symbol cooldown SKIP is REMOVED (it was a bounded block/skip). A residual /
# rare param reject is still classified EXTERNAL (non-fault) so it never trips the
# circuit breaker — but with NO per-symbol skip (flow_not_block: never blocked).
#   51000 = parameter error  · 51100 = order amount below limit
#   51020 = order amount below the instrument MINIMUM (now pre-empted by the
#           clamp-up; a residual is external non-fault, NOT a strategy fault)
#   51121 = order qty must be a multiple of lotSz (precision)
#   51127 = available balance/quantity precision · 51820 = px precision
#   51006 = px outside allowed range (tick/limit)
OKX_PARAM_REJECT_CODES: frozenset[str] = frozenset(
    {"51000", "51006", "51020", "51100", "51121", "51127", "51820"}
)
# OKX AUTH / SYSTEM reject codes (the 50100-50114 credential family): a signed
# request the VENUE rejected for an authentication reason — an invalid /
# rotated / expired API key or passphrase, a stale request timestamp, or a
# demo↔live key mismatch. This is NEVER a strategy or client logic fault: when
# the demo credentials rotate, EVERY signed call (open AND /account/balance) on
# EVERY symbol gets the same code, so faulting would spuriously SOFT_HALT every
# strategy at once (the exact noise this fix removes). Classify EXTERNAL so the
# breaker stays ACTIVE — the entries resume the instant the credentials are
# refreshed (no per-strategy block to clear). The boot signed health-check
# (production_paper_loop) surfaces the credential regression to the operator.
#   50105 = OK-ACCESS-PASSPHRASE incorrect (the observed live regression)
#   50100-50104 / 50106-50114 = the surrounding auth/timestamp/system family
OKX_AUTH_REJECT_CODES: frozenset[str] = frozenset(
    {f"501{n:02d}" for n in range(0, 15)}
)
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
    # OKX param/precision rejects (sz/px/lot-size malformed for the instrument):
    # pre-empted at submit by the min-size clamp-up, so a residual is RARE — and
    # it is a VENUE rule, not a strategy fault. Classify external so it never
    # trips the breaker (Jin 2026-06-23: replaced the old per-symbol cooldown
    # skip; no per-symbol block, the strategy keeps flowing).
    if reject_code in OKX_PARAM_REJECT_CODES:
        return True
    # OKX auth/credential rejects (50100-50114): a venue-side authentication
    # failure (invalid/rotated/expired key or passphrase). Not a strategy fault —
    # classify external so a global credential regression never trips the breaker.
    if reject_code in OKX_AUTH_REJECT_CODES:
        return True
    # D-2: Capital HTTP/protocol-level failures (D-1 honest labels HTTP_429 /
    # HTTP_5xx / HTTP_TIMEOUT / HTTP_TRANSPORT / HTTP_CONFIRM) and a confirm
    # poll that never finalized (CONFIRM_STALL_PENDING) are venue/transport
    # events — the signal already passed G1-G7 + sizing, so halting the
    # strategy for them violates the integrity-only circuit philosophy (this
    # exact mislabel chain SOFT_HALTed 4 innocent strategies 55 times). A REAL
    # venue rejection arrives as 200+REJECTED (stream SSOT below, unchanged).
    # Capital-gated: OKX sCodes are numeric and Alpaca emits semantic tokens,
    # so an HTTP_ prefix cannot collide — but the venue gate keeps it explicit.
    if venue.lower() == "capital" and (
        reject_code.startswith("HTTP_") or reject_code == "CONFIRM_STALL_PENDING"
    ):
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
    venue_order_id: str | None = None,
    unfilled_qty: float = 0.0,
) -> None:
    """Release the reservation + classify a real-open no-fill (Task 2 / D1).

    EXTERNAL venue rejects (compliance / balance / market-closed / no-fill) are
    NOT strategy faults — they release the reservation and bump a telemetry
    counter, but they do NOT trip the circuit breaker (flow_not_block). A
    compliance reject (51155) also adds the (venue, symbol) to the permanent
    runtime blocklist. A reject code outside the external set is a possible
    internal/client bug and still records a FAULT_REJECT so real anomalies can
    eventually halt.

    ORPHAN NET (Alpaca open-side leak): when the order was ACCEPTED-but-unfilled
    (``venue_order_id`` present), the venue holds a LIVE order even after we
    release the reservation. Record it via ``record_venue_orphan`` (IDEMPOTENT
    on the order id) so the still-live order is handed to the reconciler/exit
    engine instead of vanishing — flow_not_block: ENABLE tracking, never a
    throttle. A GENUINE reject (no ``venue_order_id`` — nothing accepted at the
    venue) records NO orphan.
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
        # ③ anti-churn: a TRANSIENT external reject (buying_power / market_closed
        # / no_fill …) must STAMP the novelty key, exactly as a successful fill
        # would. The novelty key was previously written ONLY on a real fill
        # (_production_run_signal), so a reject left it None → every next tick saw
        # is_novel_reentry True (last_entry_bar is None → always novel) → the
        # re-entry cooldown was exempted → the SAME signal re-fired indefinitely
        # (42x churn; a reject INSERTs no positions row, so the cooldown / same-
        # side guards could not catch it either). Stamping (created_at_bar, side)
        # makes a same-bar same-side re-fire NOT novel → the cooldown applies when
        # an anchoring fill exists. A NEW bar or a side flip is still novel (entry resumes) —
        # flow_not_block, never a permanent block. The PERMANENT-blocklist
        # compliance reject (51155) is EXCLUDED: the blocklist is its mechanism
        # (the symbol is skipped before novelty), so stamping it would mislead.
        if reject_code not in COMPLIANCE_REJECT_CODES:
            state.last_entry_by_key[(venue, symbol, sig.strategy_id)] = (
                sig.created_at_bar, sig.side,
            )
        # Credential-root-cause log: an OKX auth code (50100-50114) means EVERY
        # signed call is being rejected by the venue — a credential regression
        # blocking all real orders, not a one-symbol blip. Surface it as a loud
        # ERROR (credential_root_cause_log) so the operator refreshes the demo
        # key/passphrase; the entries resume the instant they are valid again.
        if reject_code in OKX_AUTH_REJECT_CODES:
            logger.error(
                "[L7/CREDENTIAL] OKX AUTH FAIL code=%s — signed orders are being "
                "REJECTED venue-side (invalid/rotated/expired OKX_DEMO key or "
                "passphrase). ALL real OKX orders blocked until credentials are "
                "refreshed; this is NOT a strategy/code fault.",
                reject_code,
            )
        if venue_order_id is not None:
            # Accepted-but-unfilled order is still LIVE at the venue → record a
            # durable orphan (idempotent) so reconciliation can close it.
            record_venue_orphan(
                conn, strategy_id=sig.strategy_id, venue=venue, symbol=symbol,
                side=sig.side, phase="real_open_no_fill",
                venue_order_id=venue_order_id, deal_id=None,
                base_qty=unfilled_qty, now_ts=now_ts,
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
    # NOTE: OKX param/precision codes (OKX_PARAM_REJECT_CODES) are now handled by
    # the EXTERNAL branch above (_is_external_reject returns True for them) — the
    # min-size clamp-up pre-empts the 51020 flood at submit, so a residual is a
    # rare venue rule, not a strategy fault, and carries NO per-symbol cooldown
    # (the cooldown machinery is removed — Jin 2026-06-23, flow_not_block).
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
    "OKX_AUTH_REJECT_CODES",
    "OKX_PARAM_REJECT_CODES",
    "_handle_open_reject",
    "_is_external_reject",
]
