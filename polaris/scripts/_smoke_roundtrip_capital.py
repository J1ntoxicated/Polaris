"""Day 6 — Capital CFD real demo fill round-trip (open + immediate close).

Split out of ``_smoke_real_roundtrip`` to keep each module ≤500 LOC. The OKX
module re-exports the public Capital names so existing import paths keep
working. Defensive: returns ``{"ok": False, ...}`` on any exception so the
smoke loop never aborts; ``dry_run=True`` writes synthetic fills (CI no-creds).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from typing import Any

import httpx

from polaris.core.data.fill_normalizer import (
    Fill,
    FillNormalizationError,
    normalize_capital_confirm,
)
from polaris.core.data.fills_persist import persist_fill
from polaris.scripts._smoke_roundtrip_shared import (
    MIN_CAPITAL_LOT,
    CloseOrphan,
    OpenAttempt,
    PendingClose,
)
from polaris.venues.capital import CapitalAdapter, CapitalSession

logger = logging.getLogger(__name__)

__all__ = [
    "real_capital_close_fill",
    "real_capital_open_fill",
    "run_capital_round_trip",
]

# Capital open returns PENDING immediately; the position dealId only lands in the
# confirm once the deal finalizes. Poll the confirm up to this many times (×
# ``poll_delay_sec``) until it is no longer PENDING, so the close-leg deal_id is
# captured (a single early poll reads PENDING → dealId None → uncloseable).
_CONFIRM_POLLS = 6


def _synthetic_capital_fill(*, is_close: bool) -> Fill:
    direction = "sell" if is_close else "buy"
    return Fill(
        venue="capital",
        instrument_id="capital:EURUSD",
        strategy_id="day6_smoke",
        side=direction,  # type: ignore[arg-type]
        size_usd=MIN_CAPITAL_LOT * 10.0,
        fill_price=1.1052 if is_close else 1.1050,
        fee_usd=0.0,
        slippage_bps=1.0,
        ts_ms=int(time.time() * 1000),
        order_id=f"dry_{uuid.uuid4().hex[:12]}",
        client_order_id=f"polD6dry{uuid.uuid4().hex[:6]}",
        base_qty=MIN_CAPITAL_LOT,
        quote_qty=MIN_CAPITAL_LOT * 10.0,
        state="filled",
    )


def _capital_deal_id_from_confirm(confirm: dict[str, Any], open_resp: Any) -> str | None:
    affected = confirm.get("affectedDeals") or []
    deal_id: Any = None
    if isinstance(affected, list) and affected:
        first = affected[0] if isinstance(affected[0], dict) else {}
        deal_id = first.get("dealId")
    deal_id = deal_id or confirm.get("dealId") or getattr(open_resp, "deal_id", None)
    return str(deal_id) if deal_id else None


async def real_capital_open_fill(
    adapter: Any,
    *,
    epic: str,
    direction: str,
    size: float,
    strategy_id: str,
    pip_value_usd: float = 10.0,
    leverage: float = 1.0,
    last_price: float | None = None,
    poll_delay_sec: float = 0.5,
    contract_factor_usd: float | None = None,
) -> OpenAttempt:
    """Capital entry leg: open → confirm → normalize.

    Returns an ``OpenAttempt`` carrying the normalized ``fill`` + position
    ``deal_id`` on success, or the venue reject ``status``/``reason`` (e.g.
    ``"REJECTED"``, market-closed) — falling back to the ``"no_fill"`` sentinel
    — so the caller can classify an EXTERNAL venue reject apart from a fault.

    ``contract_factor_usd`` (Bug C fix): when set, the normalized fill records
    the REAL exposure ``size × level × factor`` (leverage excluded); ``None``
    keeps the legacy ``size × pip × leverage`` byte-identical.
    """
    open_resp = await adapter.open_position(epic=epic, direction=direction, size=size)
    if not open_resp.ok or not open_resp.deal_reference:
        return OpenAttempt(
            fill=None,
            reject_code=str(open_resp.status) if open_resp.status else "no_fill",
            reject_msg=open_resp.reason or None,
        )
    # Poll the confirm until the deal finalizes (not PENDING), so the position
    # deal_id (in ``affectedDeals``) is captured for the close leg. Bounded
    # retries — degrade-never-halt; a never-finalizing deal falls through to the
    # reject path below, never hangs.
    confirm: dict[str, Any] = {}
    deal_status = ""
    confirm_read_ok = False
    for _poll in range(_CONFIRM_POLLS):
        await asyncio.sleep(poll_delay_sec)
        try:
            confirm = await adapter.confirm(open_resp.deal_reference)
        except httpx.HTTPError as exc:
            # B1: this poll previously raised straight into the pipeline's
            # FAULT_EXCEPTION backstop (3/300s → HARD_HALT) on a venue 429
            # storm / timeout — a confirm READ failure is a venue/transport
            # event, not a strategy fault. Keep polling within the budget
            # (mirror of the close leg's try-guard). Non-httpx exceptions
            # still propagate (genuine code bug → integrity backstop).
            logger.warning(
                "[capital] open confirm %s poll failed %r — retrying in budget",
                open_resp.deal_reference, exc,
            )
            continue
        confirm_read_ok = True
        deal_status = str(confirm.get("dealStatus") or "")
        logger.debug(
            "[capital] confirm dealRef=%s status=%s affectedDeals=%s",
            open_resp.deal_reference, deal_status, confirm.get("affectedDeals"),
        )
        if deal_status and deal_status not in ("PENDING", "None"):
            break
    if deal_status not in ("ACCEPTED", "OPEN"):
        # Honest reject labels (D-1/B2). Both HTTP_CONFIRM and
        # CONFIRM_STALL_PENDING can leave a GHOST venue position if the deal
        # finalizes after we bail — the reconcile pass (position drift) covers
        # it, same as before this change.
        if not confirm_read_ok:
            # Every confirm poll failed at the HTTP/transport layer — the
            # HTTP_ prefix routes it to the external (non-fault) classifier.
            reject_code = "HTTP_CONFIRM"
        elif deal_status == "PENDING":
            # B2: the deal never finalized inside the poll budget — a venue
            # stall, NOT the (extinct) D-1 "PENDING" mislabel. Distinct honest
            # label, classified external (venue-side no-fill).
            reject_code = "CONFIRM_STALL_PENDING"
        elif deal_status and deal_status != "None":
            reject_code = deal_status
        else:
            reject_code = "no_fill"
        return OpenAttempt(
            fill=None,
            reject_code=reject_code,
            reject_msg=str(confirm.get("reason") or "") or None,
        )
    fill = normalize_capital_confirm(
        confirm, strategy_id=strategy_id, pip_value_usd=pip_value_usd,
        leverage=leverage, expected_price=last_price,
        contract_factor_usd=contract_factor_usd,
    )
    return OpenAttempt(
        fill=fill, deal_id=_capital_deal_id_from_confirm(confirm, open_resp),
    )


async def _confirm_pending_close(
    adapter: Any, *, pending_ref: str, strategy_id: str,
    pip_value_usd: float, leverage: float,
    contract_factor_usd: float | None = None,
) -> Fill | PendingClose | None:
    """BUG E-b: settle a previously-fired close ``deal_reference`` BEFORE
    firing a new ``close_position`` (the GOLD close-reject re-fire loop).

    * Settled (ACCEPTED/OPEN/CLOSED) → normalized ``Fill`` — no re-fire.
    * Still PENDING / confirm read failure → ``PendingClose`` again.
    * REJECTED (never executed) or an un-normalizable terminal variant →
      ``None`` — the caller may fire a fresh close.
    """
    try:
        confirm = await adapter.confirm(pending_ref)
    except Exception as exc:  # noqa: BLE001 — confirm read is best-effort
        logger.warning(
            "[capital/close] pending confirm %s failed %r — keep pending",
            pending_ref, exc,
        )
        return PendingClose(ref=pending_ref)
    status = str(confirm.get("dealStatus") or "")
    if not status or status in ("PENDING", "None"):
        return PendingClose(ref=pending_ref)
    if status == "REJECTED":
        return None
    try:
        return normalize_capital_confirm(
            confirm, strategy_id=strategy_id, pip_value_usd=pip_value_usd,
            leverage=leverage, contract_factor_usd=contract_factor_usd,
        )
    except FillNormalizationError as exc:
        logger.warning(
            "[capital/close] pending confirm %s terminal but un-normalizable "
            "(%s) %r — treating as dead, fresh close may fire",
            pending_ref, status, exc,
        )
        return None


async def _deal_id_absent(adapter: Any, deal_id: str) -> bool:
    """True only when ``list_positions`` CONFIRMS the deal is gone from the
    venue book. A listing failure returns False (keep the transient-retry
    semantics — never fabricate an orphan on a read error)."""
    try:
        body = await adapter.list_positions()
    except Exception as exc:  # noqa: BLE001 — listing is best-effort
        logger.warning(
            "[capital/close] list_positions failed %r — keep retrying", exc,
        )
        return False
    for entry in body.get("positions", []) or []:
        pos = entry.get("position", {}) or {}
        if str(pos.get("dealId") or "") == deal_id:
            return False
    return True


async def real_capital_close_fill(
    adapter: Any,
    *,
    deal_id: str,
    strategy_id: str,
    pip_value_usd: float = 10.0,
    leverage: float = 1.0,
    poll_delay_sec: float = 0.5,
    pending_ref: str | None = None,
    contract_factor_usd: float | None = None,
) -> Fill | PendingClose | CloseOrphan | None:
    """Capital close leg: close_position(deal_id) → confirm-poll → normalize.

    BUG E hardening:
    * ``pending_ref`` — a prior tick's unconfirmed close deal_reference is
      CONFIRMED first; only a REJECTED/dead pending close falls through to a
      fresh ``close_position`` (no duplicate close while one may still fill).
    * A close REJECT on a deal the venue no longer lists → ``CloseOrphan``
      (the deal is GONE — expired/auto-closed; it would reject forever, GOLD
      #1..#17). A still-listed deal keeps the transient ``None`` retry.
    * The confirm is POLLED up to ``_CONFIRM_POLLS`` (mirror of the open leg);
      a confirm still PENDING after the budget returns
      ``PendingClose(deal_reference)`` for the next tick's confirm-first path.
    """
    if pending_ref:
        settled = await _confirm_pending_close(
            adapter, pending_ref=pending_ref, strategy_id=strategy_id,
            pip_value_usd=pip_value_usd, leverage=leverage,
            contract_factor_usd=contract_factor_usd,
        )
        if settled is not None:
            return settled
        # Pending close is dead (REJECTED) — fall through to a fresh close.
    close_resp = await adapter.close_position(deal_id)
    if not close_resp.ok or not close_resp.deal_reference:
        if await _deal_id_absent(adapter, deal_id):
            logger.warning(
                "[capital/close] dealId=%s rejected AND no longer listed at the "
                "venue — deal is gone, reconciling (no infinite re-fire)",
                deal_id,
            )
            return CloseOrphan(available=0.0)
        return None
    deal_ref = str(close_resp.deal_reference)
    confirm: dict[str, Any] = {}
    status = ""
    for _poll in range(_CONFIRM_POLLS):
        await asyncio.sleep(poll_delay_sec)
        try:
            confirm = await adapter.confirm(deal_ref)
        except Exception as exc:  # noqa: BLE001 — keep pending on uncertainty
            logger.warning(
                "[capital/close] confirm %s failed %r — pending (confirm-first "
                "next tick)", deal_ref, exc,
            )
            return PendingClose(ref=deal_ref)
        status = str(confirm.get("dealStatus") or "")
        if status and status not in ("PENDING", "None"):
            break
    if not status or status in ("PENDING", "None"):
        return PendingClose(ref=deal_ref)
    if status == "REJECTED":
        return None  # close never executed — safe to re-fire next tick
    try:
        return normalize_capital_confirm(
            confirm, strategy_id=strategy_id, pip_value_usd=pip_value_usd,
            leverage=leverage, contract_factor_usd=contract_factor_usd,
        )
    except FillNormalizationError as exc:
        # Unknown terminal variant — never blind-re-fire; confirm again next
        # tick via the pending path (it resolves to fresh-close if truly dead).
        logger.warning(
            "[capital/close] confirm %s un-normalizable (%s) %r — pending",
            deal_ref, status, exc,
        )
        return PendingClose(ref=deal_ref)


async def run_capital_round_trip(
    *,
    conn: sqlite3.Connection,
    epic: str = "EURUSD",
    size: float = MIN_CAPITAL_LOT,
    pip_value_usd: float = 10.0,
    leverage: float = 1.0,
    strategy_id: str = "day6_smoke",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Real Capital demo open + close round-trip (1-lot EURUSD by default).

    Persists 2 fills rows. ``dry_run=True`` writes synthetic fills.
    """
    if dry_run:
        open_fill = _synthetic_capital_fill(is_close=False)
        close_fill = _synthetic_capital_fill(is_close=True)
        open_id = persist_fill(conn, open_fill, is_close=False)
        pnl = (close_fill.fill_price - open_fill.fill_price) * open_fill.base_qty * pip_value_usd
        close_id = persist_fill(conn, close_fill, is_close=True, pnl_usd=pnl)
        return {
            "ok": True,
            "dry_run": True,
            "open_fill_id": open_id,
            "close_fill_id": close_id,
            "pnl_usd": pnl,
        }

    api_key = os.environ.get("CAP_API_KEY", "")
    email = os.environ.get("CAP_EMAIL", "")
    password = os.environ.get("CAP_PASSWORD", "")
    base_url = os.environ.get(
        "CAP_BASE_DEMO", "https://demo-api-capital.backend-capital.com"
    )
    if not (api_key and email and password):
        return {"ok": False, "error": "missing CAP_* env"}
    open_id_outer: str = ""
    last_step: str = "init"
    try:
        async with CapitalSession(
            api_key=api_key, identifier=email, password=password, base_url=base_url
        ) as sess:
            last_step = "login"
            await sess.login()
            adapter = CapitalAdapter(sess)
            last_step = "open_place"
            open_resp = await adapter.open_position(
                epic=epic, direction="BUY", size=size
            )
            if not open_resp.ok or not open_resp.deal_reference:
                return {
                    "ok": False,
                    "step": "open",
                    "status": open_resp.status,
                    "reason": open_resp.reason,
                }
            last_step = "open_confirm"
            await asyncio.sleep(0.5)
            confirm = await adapter.confirm(open_resp.deal_reference)
            try:
                open_fill = normalize_capital_confirm(
                    confirm,
                    strategy_id=strategy_id,
                    pip_value_usd=pip_value_usd,
                    leverage=leverage,
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "step": "open_normalize", "error": repr(exc)}
            open_id = persist_fill(conn, open_fill, is_close=False)
            open_id_outer = open_id
            last_step = "close_place"
            # Resolve the actual deal_id from affectedDeals[0].
            deal_id = None
            affected = confirm.get("affectedDeals") or []
            if isinstance(affected, list) and affected:
                first = affected[0] if isinstance(affected[0], dict) else {}
                deal_id = first.get("dealId")
            deal_id = deal_id or confirm.get("dealId") or open_resp.deal_id
            if not deal_id or confirm.get("dealStatus") not in ("ACCEPTED", "OPEN"):
                return {
                    "ok": False,
                    "step": "close_no_deal_id",
                    "msg": str(confirm.get("dealStatus")),
                    "open_fill_id": open_id,
                }
            close_resp = await adapter.close_position(str(deal_id))
            if not close_resp.ok or not close_resp.deal_reference:
                return {
                    "ok": False,
                    "step": "close_place",
                    "status": close_resp.status,
                    "reason": close_resp.reason,
                    "open_fill_id": open_id,
                }
            last_step = "close_confirm"
            await asyncio.sleep(0.5)
            close_payload = await adapter.confirm(close_resp.deal_reference)
            try:
                close_fill = normalize_capital_confirm(
                    close_payload,
                    strategy_id=strategy_id,
                    pip_value_usd=pip_value_usd,
                    leverage=leverage,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "step": "close_normalize",
                    "error": repr(exc),
                    "open_fill_id": open_id,
                }
            pnl = (
                close_fill.fill_price - open_fill.fill_price
            ) * open_fill.base_qty * pip_value_usd
            close_id = persist_fill(
                conn, close_fill, is_close=True, pnl_usd=pnl
            )
            return {
                "ok": True,
                "open_fill_id": open_id,
                "close_fill_id": close_id,
                "open_deal_ref": open_resp.deal_reference,
                "close_payload": close_payload,
            }
    except Exception as exc:  # noqa: BLE001
        envelope: dict[str, Any] = {
            "ok": False,
            "step": last_step,
            "error": repr(exc),
        }
        if open_id_outer:
            envelope["open_fill_id"] = open_id_outer
        return envelope
